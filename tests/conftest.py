"""Shared fixtures: a fake nook server behind the client interface."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nook.api import (
    NookAlbum,
    NookArtist,
    NookCatalog,
    NookChannel,
    NookCwItem,
    NookDevice,
    NookItem,
    NookMusicHome,
    NookMusicSearch,
    NookNotFoundError,
    NookNowPlaying,
    NookPage,
    NookPlaylist,
    NookSession,
    NookStation,
    NookTrack,
    NookTrackList,
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


def now_playing(**overrides: Any) -> NookNowPlaying:
    """Build a now-playing entry on the living-room TV (a song, playing)."""
    base = NookNowPlaying(
        device_id=TV_LIVING,
        kind="music",
        title="Come Together",
        subtitle=None,
        artist="The Beatles",
        album="Abbey Road",
        image_url="https://img.example.com/abbey.jpg",
        content_id="jellyfin:t1",
        paused=False,
        position_ms=60_000,
        duration_ms=259_000,
        updated_at=UPDATED_AT_MS,
    )
    return replace(base, **overrides)


def channel(
    channel_id: str,
    name: str,
    *,
    number: int | None = None,
    group: str | None = None,
    favorite: bool = False,
    now_title: str | None = None,
    logo_url: str | None = None,
) -> NookChannel:
    """Build a Live TV channel."""
    return NookChannel(
        id=channel_id,
        name=name,
        number=number,
        logo_url=logo_url,
        group=group,
        favorite=favorite,
        now_title=now_title,
    )


def track(
    track_id: str,
    title: str,
    *,
    number: int | None = None,
    album_id: str | None = "jellyfin:al1",
) -> NookTrack:
    """Build a song of Abbey Road (or of no album)."""
    return NookTrack(
        id=track_id,
        title=title,
        artist="The Beatles",
        album="Abbey Road" if album_id else None,
        album_id=album_id,
        track_number=number,
        image_url="https://img.example.com/abbey.jpg",
    )


def album(album_id: str, title: str, *, tracks: int = 2) -> NookAlbum:
    """Build an album."""
    return NookAlbum(
        id=album_id,
        title=title,
        artist="The Beatles",
        image_url=f"https://img.example.com/{album_id}.jpg",
        track_count=tracks,
    )


CH_HRT1 = channel(
    "HRT1.hr",
    "HRT 1",
    number=1,
    group="Croatia",
    favorite=True,
    now_title="Dnevnik",
    logo_url="https://img.example.com/hrt1.png",
)
CH_HRT2 = channel(
    "HRT2.hr",
    "HRT 2",
    number=2,
    group="Croatia",
    logo_url="http://logos.example.com/hrt2.png",
)
CH_BBC = channel("BBCOne.uk", "BBC One", number=5, group="UK")
CH_CAM = channel("cam-1", "Garden cam", number=6)

ST_OTVORENI = NookStation(
    id="s18415", name="Otvoreni", logo_url="http://logos.example.com/otvoreni.png"
)
ST_BBC1 = NookStation(
    id="s24939", name="BBC Radio 1", logo_url="https://img.example.com/bbc1.png"
)

TRACK_COME = track("jellyfin:t1", "Come Together", number=1)
TRACK_SOMETHING = track("jellyfin:t2", "Something", number=2)
TRACK_DEMO = track("jellyfin:t9", "Demo", album_id=None)
ALBUM_ABBEY = album("jellyfin:al1", "Abbey Road")
ALBUM_EMPTY = album("jellyfin:al0", "Nothing yet", tracks=0)
ARTIST_BEATLES = NookArtist(
    id="jellyfin:ar1",
    name="The Beatles",
    image_url="https://img.example.com/beatles.jpg",
    track_count=2,
)
PLAYLIST_RUN = NookPlaylist(
    id="0b6f6c1e-run", name="Running", image_url=None, track_count=2
)
PLAYLIST_EMPTY = NookPlaylist(
    id="0b6f6c1e-empty", name="Empty", image_url=None, track_count=0
)


def music_home(**overrides: Any) -> NookMusicHome:
    """Build the Music home page: something in every row."""
    base = NookMusicHome(
        recently_played=[TRACK_COME, TRACK_DEMO],
        recently_added=[ALBUM_ABBEY],
        playlists=[PLAYLIST_RUN, PLAYLIST_EMPTY],
        top_artists=[ARTIST_BEATLES],
        liked_track_count=2,
        track_count=3,
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

        # Phase 2: Live TV, radio, music, links and controls.
        self.now_playing: list[NookNowPlaying] = []
        self.now_playing_error: Exception | None = None
        self.now_playing_calls = 0
        self.channels: list[NookChannel] = [CH_HRT1, CH_HRT2, CH_BBC, CH_CAM]
        self.channels_error: Exception | None = None
        self.channel_calls = 0
        self.radio_favorites: list[NookStation] = [ST_OTVORENI, ST_BBC1]
        self.radio_search: dict[str, list[NookStation]] = {}
        self.radio_error: Exception | None = None
        self.music_home = music_home()
        self.music_search: dict[str, NookMusicSearch] = {}
        self.music_error: Exception | None = None
        self.playlists: list[NookPlaylist] = [PLAYLIST_RUN, PLAYLIST_EMPTY]
        self.albums: list[NookAlbum] = [ALBUM_ABBEY, ALBUM_EMPTY]
        self.artists: list[NookArtist] = [ARTIST_BEATLES]
        self.liked: list[NookTrack] = [TRACK_SOMETHING, TRACK_COME]
        self.track_lists: dict[str, NookTrackList] = {
            f"album:{ALBUM_ABBEY.id}": NookTrackList(
                "Abbey Road", [TRACK_COME, TRACK_SOMETHING]
            ),
            f"playlist:{PLAYLIST_RUN.id}": NookTrackList(
                "Running", [TRACK_SOMETHING, TRACK_COME]
            ),
            f"artist:{ARTIST_BEATLES.id}": NookTrackList(
                "The Beatles", [TRACK_COME, TRACK_SOMETHING]
            ),
        }
        self.page_calls: list[tuple[str, int, int]] = []
        self.open_error: Exception | None = None
        self.open_calls: list[tuple[str, str]] = []
        self.control_error: Exception | None = None
        self.control_calls: list[tuple[str, str]] = []

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

    async def async_open_link(self, device_id: str, link: str) -> dict[str, Any]:
        """Record a link sent to a TV."""
        if self.open_error is not None:
            raise self.open_error
        self.open_calls.append((device_id, link))
        return {"id": device_id, "name": "TV"}

    async def async_control(self, device_id: str, action: str) -> dict[str, Any]:
        """Record a stop, pause or resume."""
        if self.control_error is not None:
            raise self.control_error
        self.control_calls.append((device_id, action))
        return {"id": device_id, "name": "TV"}

    async def async_get_now_playing(self) -> list[NookNowPlaying]:
        """Return what the TVs play besides films and episodes."""
        self.now_playing_calls += 1
        if self.now_playing_error is not None:
            raise self.now_playing_error
        return list(self.now_playing)

    async def async_get_channels(self) -> list[NookChannel]:
        """Return the channel list."""
        self.channel_calls += 1
        if self.channels_error is not None:
            raise self.channels_error
        return list(self.channels)

    def _radio(self) -> None:
        if self.radio_error is not None:
            raise self.radio_error

    async def async_get_radio_favorites(self) -> list[NookStation]:
        """Return the favourite stations."""
        self._radio()
        return list(self.radio_favorites)

    async def async_search_radio(self, query: str) -> list[NookStation]:
        """Return the TuneIn hits for a query."""
        self._radio()
        return list(self.radio_search.get(query, []))

    def _music(self) -> None:
        if self.music_error is not None:
            raise self.music_error

    def _page[T](
        self, name: str, items: list[T], offset: int, limit: int
    ) -> NookPage[T]:
        self._music()
        self.page_calls.append((name, offset, limit))
        return NookPage(items=items[offset : offset + limit], total=len(items))

    async def async_get_music_home(self) -> NookMusicHome:
        """Return the Music home page."""
        self._music()
        return self.music_home

    async def async_search_music(self, query: str) -> NookMusicSearch:
        """Return the music hits for a query."""
        self._music()
        return self.music_search.get(query, NookMusicSearch([], [], []))

    async def async_get_playlists(self) -> list[NookPlaylist]:
        """Return the playlists."""
        self._music()
        return list(self.playlists)

    def _track_list(self, kind: str, list_id: str) -> NookTrackList:
        self._music()
        if (found := self.track_lists.get(f"{kind}:{list_id}")) is None:
            raise NookNotFoundError(f"no such {kind}")
        return found

    async def async_get_playlist(self, playlist_id: str) -> NookTrackList:
        """Return a playlist's songs."""
        return self._track_list("playlist", playlist_id)

    async def async_get_album(self, album_id: str) -> NookTrackList:
        """Return an album's songs."""
        return self._track_list("album", album_id)

    async def async_get_artist(self, artist_id: str) -> NookTrackList:
        """Return an artist's songs."""
        return self._track_list("artist", artist_id)

    async def async_get_albums(
        self, *, offset: int = 0, limit: int = 100
    ) -> NookPage[NookAlbum]:
        """Return a page of albums."""
        return self._page("albums", self.albums, offset, limit)

    async def async_get_artists(
        self, *, offset: int = 0, limit: int = 100
    ) -> NookPage[NookArtist]:
        """Return a page of artists."""
        return self._page("artists", self.artists, offset, limit)

    async def async_get_liked_tracks(
        self, *, offset: int = 0, limit: int = 100
    ) -> NookPage[NookTrack]:
        """Return a page of liked songs."""
        return self._page("liked", self.liked, offset, limit)


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
