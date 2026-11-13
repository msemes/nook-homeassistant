"""Shared fixtures: a fake nook server behind the client interface."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nook.api import (
    NookCatalog,
    NookCwItem,
    NookDevice,
    NookItem,
    NookNotFoundError,
    NookSession,
)
from custom_components.nook.const import DOMAIN
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant

BASE_URL = "https://nook.example.com"
API_KEY = "nk_test_key"

TV_LIVING = "tv-living"
TV_BEDROOM = "tv-bedroom"
ENTITY_LIVING = "media_player.living_room"
ENTITY_BEDROOM = "media_player.bedroom"

UPDATED_AT_MS = 1_790_000_000_000


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components in every test."""


def device(
    device_id: str,
    name: str,
    *,
    device_type: str = "TV",
    status: str = "PAIRED",
    online: bool = True,
) -> NookDevice:
    """Build a device."""
    return NookDevice(
        id=device_id,
        name=name,
        device_type=device_type,
        status=status,
        online=online,
        last_seen_at=UPDATED_AT_MS,
    )


def item(
    backend: str,
    source_id: str,
    item_type: str,
    title: str,
    year: int | None = None,
) -> NookItem:
    """Build a card."""
    return NookItem(
        id=f"{backend}:{source_id}",
        backend=backend,
        source_id=source_id,
        type=item_type,
        title=title,
        year=year,
        poster_url=f"https://img.example.com/{source_id}.jpg",
        backdrop_url=None,
    )


def session(**overrides: Any) -> NookSession:
    """Build a session on the living-room TV (a film, playing)."""
    base = NookSession(
        device_id=TV_LIVING,
        device_name="Living Room",
        backend="stremio",
        content_id="tt1160419",
        type="movie",
        series_imdb_id=None,
        title="Dune",
        position_ms=600_500,
        duration_ms=9_300_000,
        state="playing",
        updated_at=UPDATED_AT_MS,
    )
    return replace(base, **overrides)


DUNE_LOCAL = item("jellyfin", "a1b2c3d4", "movie", "Dune", 1984)
DUNE_ONLINE = item("stremio", "tt1160419", "movie", "Dune", 2021)
DUNE_PROPHECY = item("stremio", "tmdb:90228", "series", "Dune: Prophecy", 2024)
AMERICAN_DAD = item("stremio", "tt0397306", "series", "American Dad!", 2005)

CATALOG_MOVIES = NookCatalog(
    id="jellyfin:lib:abc:movie",
    backend="jellyfin",
    name="Movies",
    type="movie",
    item_count=2,
    hidden_from_home=False,
)
CATALOG_KIDS = NookCatalog(
    id="jellyfin:lib:kids:movie",
    backend="jellyfin",
    name="Movies Kids",
    type="movie",
    item_count=1,
    hidden_from_home=True,
)
CATALOG_TRENDING = NookCatalog(
    id="mdblist:trending-test",
    backend="mdblist",
    name="Trending",
    type="movie",
    item_count=1,
    hidden_from_home=False,
)
CATALOG_SHOWS = NookCatalog(
    id="jellyfin:lib:def:series",
    backend="jellyfin",
    name="Shows",
    type="series",
    item_count=1,
    hidden_from_home=False,
)
CATALOG_MIXED = NookCatalog(
    id="user:catalogue:weekend",
    backend="stremio",
    name="Weekend",
    type="mixed",
    item_count=2,
    hidden_from_home=False,
)


class FakeNook:
    """Stands in for NookClient; tests change its fields between polls."""

    def __init__(self) -> None:
        """Start with two TVs (one online), a phone, and some library."""
        self.base_url = BASE_URL
        self.devices: list[NookDevice] = [
            device(TV_LIVING, "Living Room", online=True),
            device(TV_BEDROOM, "Bedroom", online=False),
            device("phone-1", "Pixel", device_type="PHONE"),
            device("tv-pending", "New TV", status="PENDING_PAIRING"),
        ]
        self.sessions: list[NookSession] = []
        self.items: dict[str, NookItem] = {
            "stremio:tt1160419": DUNE_ONLINE,
            "stremio:tt0397306": AMERICAN_DAD,
        }
        self.search_results: list[NookItem] = [DUNE_LOCAL, DUNE_ONLINE, DUNE_PROPHECY]
        self.cw: list[NookCwItem] = [
            NookCwItem(
                backend="stremio",
                id="tt0397306:2:5",
                type="episode",
                title="American Dad!",
                subtitle="S02E05",
                poster_url="https://img.example.com/ad.jpg",
                series_id="tt0397306",
            ),
            NookCwItem(
                backend="jellyfin",
                id="a1b2c3d4",
                type="movie",
                title="Dune",
                subtitle=None,
                poster_url=None,
                series_id=None,
            ),
            NookCwItem(
                backend="stremio",
                id="tt999:1:1",
                type="episode",
                title="No series id",
                subtitle="S01E01",
                poster_url=None,
                series_id=None,
            ),
        ]
        self.watchlist: list[NookItem] = [DUNE_LOCAL, AMERICAN_DAD]
        self.favorites: list[NookItem] = []
        self.catalogs = [
            CATALOG_MOVIES,
            CATALOG_KIDS,
            CATALOG_TRENDING,
            CATALOG_SHOWS,
            CATALOG_MIXED,
        ]
        self.catalog_items: dict[str, list[NookItem]] = {
            CATALOG_MOVIES.id: [DUNE_LOCAL, DUNE_ONLINE],
            CATALOG_SHOWS.id: [AMERICAN_DAD],
        }
        self.devices_error: Exception | None = None
        self.play_error: Exception | None = None
        self.play_calls: list[tuple[str, str, str, str, bool]] = []
        self.item_calls: list[str] = []
        self.catalog_calls: list[tuple[str, int, int]] = []
        self.search_calls: list[str] = []

    def set_online(self, device_id: str, online: bool) -> None:
        """Flip a device online or offline."""
        self.devices = [
            replace(entry, online=online) if entry.id == device_id else entry
            for entry in self.devices
        ]

    async def async_get_devices(self) -> list[NookDevice]:
        """Return the devices."""
        if self.devices_error is not None:
            raise self.devices_error
        return list(self.devices)

    async def async_get_sessions(self) -> list[NookSession]:
        """Return the sessions."""
        return list(self.sessions)

    async def async_play(
        self,
        device_id: str,
        backend: str,
        source_id: str,
        media_type: str,
        *,
        resume: bool = True,
    ) -> dict[str, Any]:
        """Record a play."""
        if self.play_error is not None:
            raise self.play_error
        self.play_calls.append((device_id, backend, source_id, media_type, resume))
        return {"id": device_id, "name": "TV"}

    async def async_search(self, query: str) -> list[NookItem]:
        """Return the search hits."""
        self.search_calls.append(query)
        return list(self.search_results)

    async def async_get_continue_watching(self) -> list[NookCwItem]:
        """Return Continue Watching."""
        return list(self.cw)

    async def async_get_watchlist(self) -> list[NookItem]:
        """Return the watchlist."""
        return list(self.watchlist)

    async def async_get_favorites(self) -> list[NookItem]:
        """Return the favourites."""
        return list(self.favorites)

    async def async_get_catalogs(self) -> list[NookCatalog]:
        """Return the catalogues."""
        return list(self.catalogs)

    async def async_get_catalog_items(
        self, catalog_id: str, *, offset: int = 0, limit: int = 100
    ) -> list[NookItem]:
        """Return a catalogue page."""
        self.catalog_calls.append((catalog_id, offset, limit))
        return list(self.catalog_items.get(catalog_id, []))

    async def async_get_item(self, item_id: str) -> NookItem | None:
        """Return a card, or 404."""
        self.item_calls.append(item_id)
        if item_id not in self.items:
            raise NookNotFoundError("unknown item")
        return self.items[item_id]


@pytest.fixture
def fake_nook() -> FakeNook:
    """Return the fake server."""
    return FakeNook()


@pytest.fixture
def patch_client(fake_nook: FakeNook) -> Generator[FakeNook]:
    """Make the integration and the config flow talk to the fake server."""
    with (
        patch("custom_components.nook.NookClient", return_value=fake_nook),
        patch("custom_components.nook.config_flow.NookClient", return_value=fake_nook),
    ):
        yield fake_nook


def make_entry(options: dict[str, Any] | None = None) -> MockConfigEntry:
    """Return a config entry for the fake server."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="nook (nook.example.com)",
        unique_id="nook.example.com",
        data={CONF_URL: BASE_URL, CONF_API_KEY: API_KEY},
        options=options or {},
    )


@pytest.fixture
def entry_options() -> dict[str, Any]:
    """Options of the entry under test; override in a test module."""
    return {}


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, patch_client: FakeNook, entry_options: dict[str, Any]
) -> MockConfigEntry:
    """Set the integration up against the fake server."""
    entry = make_entry(entry_options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
