"""One media player per paired nook TV."""

from __future__ import annotations

import asyncio
from typing import Any

# Home Assistant 2026.10 and later validate with probatio, earlier releases
# with voluptuous; the two share this API.
try:
    import probatio as vol
except ImportError:  # pragma: no cover
    import voluptuous as vol  # type: ignore[no-redef]

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import (
    HomeAssistant,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import VolDictType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import (
    NookAuthError,
    NookError,
    NookNotFoundError,
    NookOfflineError,
    NookSession,
)
from .browse import (
    PlayTarget,
    async_browse,
    async_search,
    display_title,
    item_target,
    parse_play_id,
    pick_title,
)
from .const import (
    ATTR_SOURCE,
    ATTR_TITLE,
    ATTR_TYPE,
    ATTR_YEAR,
    BACKEND_STREMIO,
    DOMAIN,
    NOOK_OPEN_LINK,
    PLAYABLE_TYPES,
    SERVICE_PLAY_TITLE,
    SOURCE_TO_BACKEND,
    TYPE_EPISODE,
    TYPE_MOVIE,
    TYPE_SERIES,
    WAKE_POLL_INTERVAL,
    WAKE_TIMEOUT,
)
from .coordinator import NookConfigEntry, NookCoordinator, SessionArt, artwork_key

# Entities only read the coordinator; actions may run side by side.
PARALLEL_UPDATES = 0

REMOTE_DOMAIN = "remote"
ATTR_ACTIVITY = "activity"

BASE_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.SEARCH_MEDIA
)

PLAY_TITLE_SCHEMA: VolDictType = {
    vol.Required(ATTR_TITLE): vol.All(cv.string, vol.Length(min=1)),
    vol.Optional(ATTR_TYPE): vol.In(PLAYABLE_TYPES),
    vol.Optional(ATTR_YEAR): vol.All(vol.Coerce(int), vol.Range(min=1870, max=2200)),
    vol.Optional(ATTR_SOURCE): vol.In(tuple(SOURCE_TO_BACKEND)),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NookConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a media player per paired TV, and the ones paired later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_tvs() -> None:
        new = [tv_id for tv_id in coordinator.data.tvs if tv_id not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(NookMediaPlayer(coordinator, tv_id) for tv_id in new)

    _add_new_tvs()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_tvs))

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_PLAY_TITLE,
        PLAY_TITLE_SCHEMA,
        "async_play_title",
        supports_response=SupportsResponse.OPTIONAL,
    )


def _episode_numbers(session: NookSession) -> tuple[str, str] | None:
    """Return (season, episode) from a Stremio episode id "tt...:S:E"."""
    if session.backend != BACKEND_STREMIO or session.type != TYPE_EPISODE:
        return None
    parts = session.content_id.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    return str(int(parts[1])), str(int(parts[2]))


class NookMediaPlayer(CoordinatorEntity[NookCoordinator], MediaPlayerEntity):
    """A nook TV: what it plays, and a target to play on."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_media_image_remotely_accessible = True

    def __init__(self, coordinator: NookCoordinator, device_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.device_id = device_id
        self._attr_unique_id = device_id
        tv = coordinator.data.tvs[device_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=tv.name,
            manufacturer="nook",
            model="Android TV",
        )
        self._art_key: str | None = None
        self._art: SessionArt | None = None
        self._update_attrs()

    @property
    def available(self) -> bool:
        """Return False once the TV is gone from the server's device list."""
        return super().available and self.device_id in self.coordinator.data.tvs

    @property
    def _linked_remote(self) -> str | None:
        return self.coordinator.linked_remotes.get(self.device_id) or None

    @property
    def _tv_name(self) -> str:
        tv = self.coordinator.data.tvs.get(self.device_id)
        if tv is not None:
            return tv.name
        return (self.device_info or {}).get("name") or self.device_id

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Return the features; turning on needs a linked remote."""
        if self._linked_remote:
            return BASE_FEATURES | MediaPlayerEntityFeature.TURN_ON
        return BASE_FEATURES

    async def async_added_to_hass(self) -> None:
        """Start the artwork lookup for what plays at start-up."""
        await super().async_added_to_hass()
        self._update_attrs()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_attrs()
        super()._handle_coordinator_update()

    @callback
    def _clear_media(self) -> None:
        self._attr_media_title = None
        self._attr_media_series_title = None
        self._attr_media_season = None
        self._attr_media_episode = None
        self._attr_media_content_type = None
        self._attr_media_content_id = None
        self._attr_media_duration = None
        self._attr_media_position = None
        self._attr_media_position_updated_at = None
        self._attr_media_image_url = None

    @callback
    def _update_attrs(self) -> None:
        data = self.coordinator.data
        tv = data.tvs.get(self.device_id)
        session = data.sessions.get(self.device_id)
        if tv is None:
            self._attr_state = None
            self._clear_media()
            return
        if not tv.online:
            self._attr_state = MediaPlayerState.OFF
            self._clear_media()
            return
        if session is None:
            self._attr_state = MediaPlayerState.IDLE
            self._clear_media()
            return

        self._attr_state = (
            MediaPlayerState.PAUSED
            if session.state == "paused"
            else MediaPlayerState.PLAYING
        )
        art = self._art_for(session)
        is_movie = session.type == TYPE_MOVIE
        self._attr_media_content_type = (
            MediaType.MOVIE if is_movie else MediaType.TVSHOW
        )
        if is_movie:
            target: PlayTarget | None = PlayTarget(
                session.backend, TYPE_MOVIE, session.content_id
            )
        elif session.backend == BACKEND_STREMIO and session.series_imdb_id:
            # An ONLINE show is named by its IMDb id. A LOCAL (Jellyfin) show
            # by its Jellyfin item id, which a session does not carry: an IMDb
            # id would name nothing the TV can open, so there is no id then.
            target = PlayTarget(session.backend, TYPE_SERIES, session.series_imdb_id)
        else:
            target = None
        self._attr_media_content_id = target.media_content_id if target else None

        numbers = _episode_numbers(session)
        self._attr_media_season, self._attr_media_episode = numbers or (None, None)
        series_title = art.series_title if art and numbers else None
        self._attr_media_series_title = series_title
        self._attr_media_title = session.title or series_title
        self._attr_media_image_url = art.image_url if art else None

        self._attr_media_duration = (
            session.duration_ms // 1000 if session.duration_ms else None
        )
        self._attr_media_position = session.position_ms // 1000
        self._attr_media_position_updated_at = (
            dt_util.utc_from_timestamp(session.updated_at / 1000)
            if session.updated_at
            else None
        )

    @callback
    def _art_for(self, session: NookSession) -> SessionArt | None:
        """Return the cached art of a session; start its lookup when unknown."""
        key = artwork_key(session)
        if key == self._art_key:
            return self._art
        self._art_key = key
        known, art = self.coordinator.cached_art(key)
        self._art = art
        if not known and self.hass is not None:
            # Not eager: the lookup writes the state itself once it is back,
            # after the update that asked for it has finished.
            self.coordinator.config_entry.async_create_background_task(
                self.hass,
                self._async_load_art(key),
                f"{DOMAIN} artwork {key}",
                eager_start=False,
            )
        elif not known:
            # Not added yet: async_added_to_hass looks it up.
            self._art_key = None
        return self._art

    async def _async_load_art(self, key: str) -> None:
        art = await self.coordinator.async_get_art(key)
        if key != self._art_key or self.hass is None:
            return
        self._art = art
        self._update_attrs()
        self.async_write_ha_state()

    def _offline_error(self) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="tv_offline",
            translation_placeholders={"name": self._tv_name},
        )

    async def _async_turn_on_remote(self, remote_entity_id: str) -> None:
        await self.hass.services.async_call(
            REMOTE_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: remote_entity_id, ATTR_ACTIVITY: NOOK_OPEN_LINK},
            blocking=True,
        )

    async def async_turn_on(self) -> None:
        """Power the TV on and open nook, through the linked remote."""
        if (remote := self._linked_remote) is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_linked_remote",
                translation_placeholders={"name": self._tv_name},
            )
        await self._async_turn_on_remote(remote)
        await self.coordinator.async_request_refresh()

    async def _async_ensure_online(self) -> None:
        """Return once the TV is online; wake it through its remote if one is linked."""
        tv = self.coordinator.data.tvs.get(self.device_id)
        if tv is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="tv_not_found",
                translation_placeholders={"name": self._tv_name},
            )
        if tv.online:
            return
        if (remote := self._linked_remote) is None:
            raise self._offline_error()

        await self._async_turn_on_remote(remote)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WAKE_TIMEOUT
        while loop.time() < deadline:
            await asyncio.sleep(WAKE_POLL_INTERVAL)
            await self.coordinator.async_refresh()
            tv = self.coordinator.data.tvs.get(self.device_id)
            if tv is not None and tv.online:
                return
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="wake_timeout",
            translation_placeholders={
                "name": self._tv_name,
                "seconds": str(int(WAKE_TIMEOUT)),
            },
        )

    async def _async_play(self, target: PlayTarget) -> None:
        await self._async_ensure_online()
        try:
            await self.coordinator.client.async_play(
                self.device_id,
                target.backend,
                target.source_id,
                target.type,
                resume=True,
            )
        except NookOfflineError as err:
            raise self._offline_error() from err
        except NookNotFoundError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="tv_not_found",
                translation_placeholders={"name": self._tv_name},
            ) from err
        except NookAuthError as err:
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except NookError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="play_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_request_refresh()

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a nook://<backend>/<movie|series>/<sourceId> id on the TV."""
        await self._async_play(parse_play_id(media_id))

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Return the browse tree."""
        return await async_browse(self.coordinator.client, media_content_id)

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        """Search the library."""
        return await async_search(self.coordinator.client, query)

    async def async_play_title(
        self,
        title: str,
        type: str | None = None,  # noqa: A002 - the action field is called type
        year: int | None = None,
        source: str | None = None,
    ) -> ServiceResponse:
        """Search a title, take the first hit that fits the filters, play it."""
        try:
            items = await self.coordinator.client.async_search(title)
        except NookError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="search_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        item = pick_title(items, media_type=type, year=year, source=source)
        target = item_target(item) if item is not None else None
        if item is None or target is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_match",
                translation_placeholders={"title": title},
            )
        await self._async_play(target)
        return {
            "title": display_title(item),
            "media_content_id": target.media_content_id,
        }
