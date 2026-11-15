"""Polls the nook server for the TVs and what they play."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    NookAuthError,
    NookChannel,
    NookClient,
    NookDevice,
    NookError,
    NookItem,
    NookNotFoundError,
    NookNowPlaying,
    NookScopeError,
    NookSession,
)
from .const import (
    BACKEND_STREMIO,
    CHANNEL_CACHE_SECONDS,
    CONF_LINKED_REMOTES,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    IMAGE_PROXY_SIZE,
    LOGGER,
    NOW_PLAYING_KINDS,
    TYPE_EPISODE,
)

type NookConfigEntry = ConfigEntry[NookCoordinator]


@dataclass(slots=True)
class NookData:
    """One poll: the paired TVs by id, the VOD session and the other media per TV."""

    tvs: dict[str, NookDevice] = field(default_factory=dict)
    sessions: dict[str, NookSession] = field(default_factory=dict)
    now_playing: dict[str, NookNowPlaying] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionArt:
    """What a session's title lookup gave: artwork and, for an episode, the show."""

    image_url: str | None
    series_title: str | None


def artwork_key(session: NookSession) -> str:
    """Return the /me/item id that describes what a session plays."""
    if (
        session.backend == BACKEND_STREMIO
        and session.type == TYPE_EPISODE
        and session.series_imdb_id
    ):
        return f"{BACKEND_STREMIO}:{session.series_imdb_id}"
    return f"{session.backend}:{session.content_id}"


class NookCoordinator(DataUpdateCoordinator[NookData]):
    """Fetches devices and sessions together, every scan interval."""

    config_entry: NookConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: NookConfigEntry, client: NookClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self._art: dict[str, SessionArt | None] = {}
        self._art_pending: dict[str, asyncio.Future[SessionArt | None]] = {}
        self._now_playing_unsupported = False
        self._channels: list[NookChannel] | None = None
        self._channels_at = 0.0
        self._images: OrderedDict[str, str] = OrderedDict()

    @property
    def linked_remotes(self) -> dict[str, str]:
        """Return the linked Android TV Remote entity per TV id."""
        return dict(self.config_entry.options.get(CONF_LINKED_REMOTES, {}))

    async def _async_update_data(self) -> NookData:
        """Fetch the devices, the sessions and the other media concurrently."""
        try:
            devices, sessions, now_playing = await asyncio.gather(
                self.client.async_get_devices(),
                self.client.async_get_sessions(),
                self._async_get_now_playing(),
            )
        except NookAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except NookError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        tvs = {device.id: device for device in devices if device.is_paired_tv}
        by_device: dict[str, NookSession] = {}
        for session in sessions:
            if session.device_id not in tvs:
                continue
            current = by_device.get(session.device_id)
            if current is None or session.updated_at > current.updated_at:
                by_device[session.device_id] = session

        if now_playing is None:
            playing = dict(self.data.now_playing) if self.data else {}
        else:
            playing = {}
            for item in now_playing:
                if item.device_id not in tvs or item.kind not in NOW_PLAYING_KINDS:
                    continue
                known = playing.get(item.device_id)
                if known is None or item.updated_at > known.updated_at:
                    playing[item.device_id] = item
        playing = {tv_id: item for tv_id, item in playing.items() if tv_id in tvs}
        return NookData(tvs=tvs, sessions=by_device, now_playing=playing)

    async def _async_get_now_playing(self) -> list[NookNowPlaying] | None:
        """Return Live TV, radio and music per TV; None keeps the last answer.

        A server older than phase 2 answers 403 (API_KEY_SCOPE) or 404: that is
        no data, said once in the debug log. Any other failure keeps what the
        last poll had, so one bad answer does not flip a TV to idle.
        """
        try:
            items = await self.client.async_get_now_playing()
        except (NookScopeError, NookNotFoundError) as err:
            if not self._now_playing_unsupported:
                self._now_playing_unsupported = True
                LOGGER.debug(
                    "The nook server does not report Live TV, radio or music: %s", err
                )
            return []
        except NookError as err:
            LOGGER.debug("Could not read what the TVs play: %s", err)
            return None
        self._now_playing_unsupported = False
        return items

    async def async_get_channels(
        self, *, max_age: float | None = CHANNEL_CACHE_SECONDS
    ) -> list[NookChannel]:
        """Return the channel list, fetched again once older than max_age seconds.

        max_age None takes any list fetched before. Raises NookError when there
        is none and the fetch fails.
        """
        now = time.monotonic()
        if self._channels is not None and (
            max_age is None or now - self._channels_at < max_age
        ):
            return self._channels
        channels = await self.client.async_get_channels()
        self._channels = channels
        self._channels_at = now
        return channels

    def cached_channel(
        self, channel_id: str | None = None, name: str | None = None
    ) -> NookChannel | None:
        """Return a channel of the last fetched list, by id, else by name."""
        channels = self._channels or []
        if channel_id:
            for channel in channels:
                if channel.id == channel_id:
                    return channel
        if name:
            wanted = name.casefold()
            for channel in channels:
                if channel.name.casefold() == wanted:
                    return channel
        return None

    def register_image(self, url: str) -> str:
        """Remember an image address for the browse proxy; return its key."""
        key = hashlib.sha256(url.encode()).hexdigest()[:32]
        self._images[key] = url
        self._images.move_to_end(key)
        while len(self._images) > IMAGE_PROXY_SIZE:
            self._images.popitem(last=False)
        return key

    def image_url(self, key: str) -> str | None:
        """Return the address behind a browse proxy key, or None when unknown."""
        return self._images.get(key)

    def cached_art(self, key: str) -> tuple[bool, SessionArt | None]:
        """Return (known, art) for a title; a failed lookup is known as None."""
        if key in self._art:
            return True, self._art[key]
        return False, None

    async def async_get_art(self, key: str) -> SessionArt | None:
        """Look a title up once; a failure is cached as None and never retried."""
        if key in self._art:
            return self._art[key]
        task = self._art_pending.get(key)
        if task is None:
            task = self.hass.async_create_task(
                self._async_fetch_art(key), f"{DOMAIN} artwork {key}", eager_start=True
            )
            self._art_pending[key] = task
        return await asyncio.shield(task)

    async def _async_fetch_art(self, key: str) -> SessionArt | None:
        art: SessionArt | None = None
        try:
            item: NookItem | None = await self.client.async_get_item(key)
        except NookError as err:
            LOGGER.debug("No artwork for %s: %s", key, err)
        else:
            if item is not None:
                art = SessionArt(
                    image_url=item.poster_url or item.backdrop_url,
                    series_title=item.title,
                )
        finally:
            self._art_pending.pop(key, None)
        self._art[key] = art
        return art
