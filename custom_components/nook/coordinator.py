"""Polls the nook server for the TVs and what they play."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NookAuthError, NookClient, NookDevice, NookError, NookItem, NookSession
from .const import (
    BACKEND_STREMIO,
    CONF_LINKED_REMOTES,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    TYPE_EPISODE,
)

type NookConfigEntry = ConfigEntry[NookCoordinator]


@dataclass(slots=True)
class NookData:
    """One poll: the paired TVs by id, and the live session per TV."""

    tvs: dict[str, NookDevice] = field(default_factory=dict)
    sessions: dict[str, NookSession] = field(default_factory=dict)


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

    @property
    def linked_remotes(self) -> dict[str, str]:
        """Return the linked Android TV Remote entity per TV id."""
        return dict(self.config_entry.options.get(CONF_LINKED_REMOTES, {}))

    async def _async_update_data(self) -> NookData:
        """Fetch the devices and the sessions concurrently."""
        try:
            devices, sessions = await asyncio.gather(
                self.client.async_get_devices(),
                self.client.async_get_sessions(),
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
        return NookData(tvs=tvs, sessions=by_device)

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
