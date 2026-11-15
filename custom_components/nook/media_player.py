"""One media player per paired nook TV."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, TypeGuard

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
    CALLBACK_TYPE,
    HomeAssistant,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import VolDictType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import (
    NookAuthError,
    NookError,
    NookNotFoundError,
    NookNowPlaying,
    NookOfflineError,
    NookResponseError,
    NookScopeError,
    NookSession,
)
from .browse import async_browse, async_search, display_title, item_target, pick_title
from .const import (
    ATTR_SOURCE,
    ATTR_TITLE,
    ATTR_TYPE,
    ATTR_YEAR,
    BACKEND_STREMIO,
    CONTROL_PAUSE,
    CONTROL_RESUME,
    CONTROL_STOP,
    DOMAIN,
    FOLLOW_UP_DELAY,
    KIND_LIVE,
    KIND_RADIO,
    LOGGER,
    NOOK_OPEN_LINK,
    PLAYABLE_TYPES,
    POSITION_TOLERANCE,
    SERVICE_PLAY_TITLE,
    SOURCE_TO_BACKEND,
    TYPE_EPISODE,
    TYPE_MOVIE,
    TYPE_SERIES,
    WAKE_POLL_INTERVAL,
    WAKE_TIMEOUT,
)
from .coordinator import NookConfigEntry, NookCoordinator, SessionArt, artwork_key
from .media_id import (
    ChannelTarget,
    LinkTarget,
    OpenTarget,
    PlayTarget,
    StationTarget,
    parse_media_id,
)

if TYPE_CHECKING:
    from datetime import datetime

# Entities only read the coordinator; actions may run side by side.
PARALLEL_UPDATES = 0

REMOTE_DOMAIN = "remote"
ATTR_ACTIVITY = "activity"

BASE_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.SEARCH_MEDIA
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
)

# The media_content_type of a browse image served through Home Assistant.
BROWSE_IMAGE_TYPE = "image"

type PositionKey = tuple[str, str, int | None, bool]

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


def _is_plain_http(url: str | None) -> TypeGuard[str]:
    """Return True for an http:// address, which an https frontend will not load."""
    return url is not None and url[:7].lower() == "http://"


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
        self._position_key: PositionKey | None = None
        self._cancel_follow_up: CALLBACK_TYPE | None = None
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
        self.async_on_remove(self._async_cancel_follow_up)
        self._update_attrs()

    @callback
    def _async_follow_up(self) -> None:
        """Poll again shortly after a play or a control, once the TV has reported.

        An immediate refresh would mostly see the old state, and would start
        the coordinator's 10 s cooldown for requested refreshes.
        """
        self._async_cancel_follow_up()
        self._cancel_follow_up = async_call_later(
            self.hass, FOLLOW_UP_DELAY, self._async_follow_up_refresh
        )

    async def _async_follow_up_refresh(self, _now: datetime) -> None:
        self._cancel_follow_up = None
        await self.coordinator.async_request_refresh()

    @callback
    def _async_cancel_follow_up(self) -> None:
        if self._cancel_follow_up is not None:
            self._cancel_follow_up()
            self._cancel_follow_up = None

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
        self._attr_media_artist = None
        self._attr_media_album_name = None
        self._attr_media_channel = None
        self._position_key = None

    @callback
    def _update_attrs(self) -> None:
        data = self.coordinator.data
        tv = data.tvs.get(self.device_id)
        session = data.sessions.get(self.device_id)
        now_playing = data.now_playing.get(self.device_id)
        if tv is None:
            self._attr_state = None
            self._clear_media()
        elif not tv.online:
            self._attr_state = MediaPlayerState.OFF
            self._clear_media()
        elif session is not None:
            # A film or episode wins over what the TV last said of other media.
            self._update_session(session)
        elif now_playing is not None:
            self._update_now_playing(now_playing)
        else:
            self._attr_state = MediaPlayerState.IDLE
            self._clear_media()
        # Home Assistant fetches an http:// image itself: on an https frontend
        # the browser would refuse to load it.
        self._attr_media_image_remotely_accessible = not _is_plain_http(
            self._attr_media_image_url
        )

    @callback
    def _update_session(self, session: NookSession) -> None:
        self._clear_media()
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
    def _update_now_playing(self, item: NookNowPlaying) -> None:
        """Show Live TV, a radio station or music, as /me/now-playing reports it."""
        previous = (
            self._position_key,
            self._attr_media_position,
            self._attr_media_position_updated_at,
        )
        self._clear_media()
        self._attr_state = (
            MediaPlayerState.PAUSED if item.paused else MediaPlayerState.PLAYING
        )
        self._attr_media_image_url = item.image_url
        if item.kind == KIND_LIVE:
            channel = self.coordinator.cached_channel(item.content_id, item.title)
            channel_id = item.content_id or (channel.id if channel else None)
            self._attr_media_content_type = MediaType.CHANNEL
            self._attr_media_channel = item.title or (channel.name if channel else None)
            # Home Assistant's media card shows a channel's title and then the
            # app name, never media_channel, so the channel leads the title.
            self._attr_media_title = (
                " · ".join(p for p in (self._attr_media_channel, item.subtitle) if p)
                or None
            )
            self._attr_media_content_id = (
                ChannelTarget(channel_id).media_content_id if channel_id else None
            )
            if item.image_url is None and channel is not None:
                self._attr_media_image_url = channel.logo_url
        elif item.kind == KIND_RADIO:
            self._attr_media_content_type = MediaType.MUSIC
            self._attr_media_channel = item.title
            self._attr_media_title = item.subtitle or item.title
            # The card's second line for music is the artist: the station,
            # under the track it plays. Left out when the title is the station.
            self._attr_media_artist = item.title if item.subtitle else None
            self._attr_media_content_id = (
                StationTarget(item.content_id, item.title).media_content_id
                if item.content_id
                else None
            )
        else:
            # A song: the report does not say which list it plays from, so
            # there is no media id to play it again with.
            self._attr_media_content_type = MediaType.MUSIC
            self._attr_media_title = item.title
            self._attr_media_artist = item.artist
            self._attr_media_album_name = item.album
        self._set_position(item, *previous)

    @callback
    def _set_position(
        self,
        item: NookNowPlaying,
        previous_key: PositionKey | None,
        previous_position: int | None,
        previous_at: datetime | None,
    ) -> None:
        """Set position and duration; the timestamp moves only when the position jumps.

        Home Assistant runs the progress bar itself from the last position and
        its timestamp, so a report that agrees with the running bar changes
        nothing (and writes no state).
        """
        self._attr_media_duration = (
            item.duration_ms // 1000 if item.duration_ms else None
        )
        if item.position_ms is None:
            return
        key: PositionKey = (
            item.kind,
            item.content_id or item.title or "",
            item.duration_ms,
            item.paused,
        )
        updated_at = (
            dt_util.utc_from_timestamp(item.updated_at / 1000)
            if item.updated_at
            else dt_util.utcnow()
        )
        position = item.position_ms / 1000
        if (
            key == previous_key
            and previous_position is not None
            and previous_at is not None
        ):
            elapsed = 0.0 if item.paused else (updated_at - previous_at).total_seconds()
            if abs(previous_position + elapsed - position) <= POSITION_TOLERANCE:
                self._position_key = key
                self._attr_media_position = previous_position
                self._attr_media_position_updated_at = previous_at
                return
        self._position_key = key
        self._attr_media_position = int(position)
        self._attr_media_position_updated_at = updated_at

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

    def _send_error(self, err: NookError, failed_key: str) -> HomeAssistantError:
        """Map a push to the TV that failed onto the error the user sees."""
        if isinstance(err, NookOfflineError):
            return self._offline_error()
        if isinstance(err, NookNotFoundError):
            return HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="tv_not_found",
                translation_placeholders={"name": self._tv_name},
            )
        if isinstance(err, NookScopeError):
            # The key works; the server predates Live TV, radio, music and
            # Stop / Pause from Home Assistant.
            return HomeAssistantError(
                translation_domain=DOMAIN, translation_key="server_unsupported"
            )
        if isinstance(err, NookAuthError):
            self.coordinator.config_entry.async_start_reauth(self.hass)
            return HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            )
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=failed_key,
            translation_placeholders={"error": str(err)},
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
        except NookError as err:
            raise self._send_error(err, "play_failed") from err
        self._async_follow_up()

    async def _async_link(self, target: OpenTarget) -> str:
        """Return the nook://open link a media id becomes."""
        if isinstance(target, LinkTarget):
            return target.link
        if isinstance(target, ChannelTarget):
            # The name is the TV's fallback when its channel ids differ.
            channel = self.coordinator.cached_channel(target.channel_id)
            if channel is None:
                try:
                    await self.coordinator.async_get_channels()
                except NookError as err:
                    LOGGER.debug("No channel list for the name of a channel: %s", err)
                channel = self.coordinator.cached_channel(target.channel_id)
            return target.link(channel.name if channel else None)
        return target.link()

    async def _async_open(self, target: OpenTarget) -> None:
        await self._async_ensure_online()
        link = await self._async_link(target)
        try:
            await self.coordinator.client.async_open_link(self.device_id, link)
        except NookResponseError as err:
            if err.status != 400:
                raise self._send_error(err, "play_failed") from err
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="link_refused",
                translation_placeholders={"link": link, "error": err.reason},
            ) from err
        except NookError as err:
            raise self._send_error(err, "play_failed") from err
        self._async_follow_up()

    async def _async_control(self, action: str) -> None:
        try:
            await self.coordinator.client.async_control(self.device_id, action)
        except NookError as err:
            raise self._send_error(err, "control_failed") from err
        self._async_follow_up()

    async def async_media_stop(self) -> None:
        """Stop whatever the TV plays."""
        await self._async_control(CONTROL_STOP)

    async def async_media_pause(self) -> None:
        """Pause a film, an episode, a station or music. Live TV ignores it."""
        await self._async_control(CONTROL_PAUSE)

    async def async_media_play(self) -> None:
        """Resume what was paused."""
        await self._async_control(CONTROL_RESUME)

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a nook media id on the TV: a title, a channel, a station or music."""
        target = parse_media_id(media_id)
        if isinstance(target, PlayTarget):
            await self._async_play(target)
        else:
            await self._async_open(target)

    @callback
    def _proxy_plain_http(self, nodes: Iterable[BrowseMedia]) -> None:
        """Serve http:// artwork through Home Assistant, as the entity picture is."""
        for node in nodes:
            if _is_plain_http(node.thumbnail):
                key = self.coordinator.register_image(node.thumbnail)
                node.thumbnail = self.get_browse_image_url(BROWSE_IMAGE_TYPE, key)
            if node.children:
                self._proxy_plain_http(node.children)

    async def async_get_browse_image(
        self,
        media_content_type: str,
        media_content_id: str,
        media_image_id: str | None = None,
    ) -> tuple[bytes | None, str | None]:
        """Fetch an http:// thumbnail the browse tree handed out."""
        if (url := self.coordinator.image_url(media_content_id)) is None:
            return None, None
        return await self._async_fetch_image(url)

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Return the browse tree."""
        node = await async_browse(self.coordinator, media_content_id)
        self._proxy_plain_http([node])
        return node

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        """Search the library, the channels, radio and music."""
        found = await async_search(self.coordinator, query)
        self._proxy_plain_http(found.result)
        return found

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
