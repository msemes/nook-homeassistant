"""Live TV, Radio and Music in the media browser, and in search."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import (
    ClientSessionGenerator,
    WebSocketGenerator,
)

from custom_components.nook.api import (
    NookConnectionError,
    NookMusicSearch,
    NookNotFoundError,
    NookScopeError,
    NookStation,
)
from homeassistant.core import HomeAssistant

from .conftest import (
    ALBUM_ABBEY,
    ALBUM_EMPTY,
    ARTIST_BEATLES,
    CH_CAM,
    CH_HRT1,
    ENTITY_LIVING,
    ST_BBC1,
    TRACK_COME,
    TRACK_DEMO,
    FakeNook,
    album,
    channel,
    music_home,
    track,
)
from .test_browse import _browse, _ids, _titles

PROXY_PREFIX = f"/api/media_player_proxy/{ENTITY_LIVING}/browse_media/image/"
VOD_ROOT = ["Continue Watching", "Watchlist", "Favourites", "Movies", "Shows"]


async def test_root_leaves_out_empty_sections(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """No channels, no favourite station, no songs: no Live TV, Radio or Music."""
    patch_client.channels = []
    patch_client.radio_favorites = []
    patch_client.music_home = music_home(track_count=0)
    root = await _browse(hass, hass_ws_client)
    assert _titles(root) == VOD_ROOT
    # TuneIn search still works without a favourite.
    assert root["search_media_classes"] == ["movie", "tv_show", "music"]


@pytest.mark.parametrize(
    "error", [NookScopeError("scope"), NookNotFoundError("HTTP 404")]
)
async def test_root_on_an_older_server(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
    error: Exception,
) -> None:
    """A server without phase 2 still browses films and shows."""
    patch_client.channels_error = error
    patch_client.radio_error = error
    patch_client.music_error = error
    root = await _browse(hass, hass_ws_client)
    assert _titles(root) == VOD_ROOT
    assert root["search_media_classes"] == ["movie", "tv_show"]


async def test_browse_live(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Live TV: Favourites, then the groups in the TV's order, then the rest."""
    live = await _browse(hass, hass_ws_client, "nook://browse/live")
    assert live["title"] == "Live TV"
    assert _titles(live) == ["Favourites", "Croatia", "UK", "Other"]
    assert _ids(live) == [
        "nook://browse/live/favorites",
        "nook://browse/live/group/Croatia",
        "nook://browse/live/group/UK",
        "nook://browse/live/other",
    ]
    assert all(child["children_media_class"] == "channel" for child in live["children"])

    favorites = await _browse(hass, hass_ws_client, "nook://browse/live/favorites")
    assert _titles(favorites) == ["1 · HRT 1 · Dnevnik"]
    hrt1 = favorites["children"][0]
    assert hrt1["media_content_id"] == "nook://live/HRT1.hr"
    assert hrt1["media_class"] == "channel"
    assert hrt1["media_content_type"] == "channel"
    assert hrt1["can_play"] is True
    assert hrt1["can_expand"] is False
    assert hrt1["thumbnail"] == CH_HRT1.logo_url

    croatia = await _browse(hass, hass_ws_client, "nook://browse/live/group/Croatia")
    assert _titles(croatia) == ["1 · HRT 1 · Dnevnik", "2 · HRT 2"]
    # An http:// logo is served through Home Assistant.
    assert croatia["children"][1]["thumbnail"].startswith(PROXY_PREFIX)

    other = await _browse(hass, hass_ws_client, "nook://browse/live/other")
    assert _titles(other) == ["6 · Garden cam"]


async def test_browse_live_without_groups(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Without groups and favourites the channels are listed under Live TV itself."""
    patch_client.channels = [
        channel("a", "Alpha", number=1),
        channel("b", "Beta"),
    ]
    live = await _browse(hass, hass_ws_client, "nook://browse/live")
    assert _titles(live) == ["1 · Alpha", "Beta"]
    assert _ids(live) == ["nook://live/a", "nook://live/b"]


async def test_browse_radio_and_its_logo_proxy(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_client: ClientSessionGenerator,
) -> None:
    """Radio lists the favourites; an http:// logo comes through Home Assistant.

    aioclient_mock comes before the integration, so its session is the mock.
    """
    radio = await _browse(hass, hass_ws_client, "nook://browse/radio")
    assert radio["title"] == "Radio"
    assert _titles(radio) == ["Otvoreni", "BBC Radio 1"]
    assert _ids(radio) == [
        "nook://radio/s18415/Otvoreni",
        "nook://radio/s24939/BBC%20Radio%201",
    ]
    assert radio["children"][0]["media_class"] == "music"
    proxied = radio["children"][0]["thumbnail"]
    assert proxied.startswith(PROXY_PREFIX)
    assert radio["children"][1]["thumbnail"] == ST_BBC1.logo_url

    aioclient_mock.get(
        "http://logos.example.com/otvoreni.png",
        content=b"logo-bytes",
        headers={"Content-Type": "image/png"},
    )
    client = await hass_client()
    response = await client.get(proxied)
    assert response.status == 200
    assert await response.read() == b"logo-bytes"

    unknown = await client.get(f"{PROXY_PREFIX}0123456789abcdef")
    assert unknown.status == 404


async def test_browse_music(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Music: lists to open, Liked songs to play or open, and Shuffle all."""
    music = await _browse(hass, hass_ws_client, "nook://browse/music")
    assert music["title"] == "Music"
    assert _titles(music) == [
        "Playlists",
        "Liked songs",
        "Recently played",
        "Recently added",
        "Top artists",
        "Albums",
        "Artists",
        "Shuffle all",
    ]
    by_title = {child["title"]: child for child in music["children"]}
    liked = by_title["Liked songs"]
    assert liked["media_content_id"] == "nook://music/liked"
    assert liked["can_play"] is True
    assert liked["can_expand"] is True
    shuffle = by_title["Shuffle all"]
    assert shuffle["media_content_id"] == "nook://music/shuffle"
    assert shuffle["can_play"] is True
    assert shuffle["can_expand"] is False
    assert not by_title["Albums"]["can_play"]


async def test_browse_music_leaves_out_empty_rows(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Rows with nothing in them (or only empty playlists) are not listed."""
    patch_client.music_home = music_home(
        recently_played=[],
        recently_added=[],
        playlists=[p for p in patch_client.playlists if p.track_count == 0],
        top_artists=[],
        liked_track_count=0,
    )
    music = await _browse(hass, hass_ws_client, "nook://browse/music")
    assert _titles(music) == ["Albums", "Artists", "Shuffle all"]


async def test_browse_music_lists(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Playlists, albums and artists play whole, or open to songs that start there."""
    playlists = await _browse(hass, hass_ws_client, "nook://browse/music/playlists")
    assert _titles(playlists) == ["Running"]
    running = playlists["children"][0]
    assert running["media_content_id"] == "nook://music/playlist/0b6f6c1e-run"
    assert running["media_class"] == "playlist"
    assert running["can_play"] is True
    assert running["can_expand"] is True

    opened = await _browse(hass, hass_ws_client, running["media_content_id"])
    assert opened["title"] == "Running"
    assert opened["can_play"] is True
    assert _titles(opened) == [
        "Something · The Beatles",
        "Come Together · The Beatles",
    ]
    assert _ids(opened) == [
        "nook://music/playlist/0b6f6c1e-run/song/jellyfin:t2",
        "nook://music/playlist/0b6f6c1e-run/song/jellyfin:t1",
    ]
    assert opened["children"][0]["media_class"] == "track"

    abbey = await _browse(hass, hass_ws_client, "nook://music/album/jellyfin:al1")
    assert abbey["title"] == "Abbey Road"
    assert _titles(abbey) == ["1. Come Together", "2. Something"]
    assert _ids(abbey)[0] == "nook://music/album/jellyfin:al1/song/jellyfin:t1"

    beatles = await _browse(hass, hass_ws_client, "nook://music/artist/jellyfin:ar1")
    assert _titles(beatles) == ["Come Together · Abbey Road", "Something · Abbey Road"]
    assert _ids(beatles)[1] == "nook://music/artist/jellyfin:ar1/song/jellyfin:t2"

    liked = await _browse(hass, hass_ws_client, "nook://music/liked")
    assert liked["title"] == "Liked songs"
    assert _ids(liked) == [
        "nook://music/liked/song/jellyfin:t2",
        "nook://music/liked/song/jellyfin:t1",
    ]


async def test_browse_music_home_rows(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A recently played song plays from its album; one without an album cannot."""
    played = await _browse(hass, hass_ws_client, "nook://browse/music/recently-played")
    assert _titles(played) == ["Come Together · The Beatles", "Demo · The Beatles"]
    come, demo = played["children"]
    assert come["media_content_id"] == (
        "nook://music/album/jellyfin:al1/song/jellyfin:t1"
    )
    assert come["can_play"] is True
    assert demo["media_content_id"] == "nook://music/track/jellyfin:t9"
    assert demo["can_play"] is False

    added = await _browse(hass, hass_ws_client, "nook://browse/music/recently-added")
    assert _titles(added) == ["Abbey Road · The Beatles"]
    assert added["children"][0]["media_class"] == "album"

    top = await _browse(hass, hass_ws_client, "nook://browse/music/top-artists")
    assert _ids(top) == ["nook://music/artist/jellyfin:ar1"]


async def test_browse_music_pages(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Albums come 100 at a time with a More folder; empty albums are left out."""
    patch_client.albums = [ALBUM_EMPTY] + [
        album(f"jellyfin:al{n:03}", f"Album {n:03}") for n in range(100)
    ]
    first = await _browse(hass, hass_ws_client, "nook://browse/music/albums")
    assert first["title"] == "Albums"
    assert len(first["children"]) == 100
    assert first["children"][0]["title"] == "Album 000 · The Beatles"
    more = first["children"][-1]
    assert more["title"] == "More"
    assert more["media_content_id"] == "nook://browse/music/albums/100"

    second = await _browse(hass, hass_ws_client, more["media_content_id"])
    assert _titles(second) == ["Album 099 · The Beatles"]
    assert patch_client.page_calls == [("albums", 0, 100), ("albums", 100, 100)]

    artists = await _browse(hass, hass_ws_client, "nook://browse/music/artists")
    assert _titles(artists) == ["The Beatles"]

    patch_client.liked = [
        track(f"jellyfin:l{n:03}", f"Song {n:03}") for n in range(101)
    ]
    liked = await _browse(hass, hass_ws_client, "nook://music/liked")
    assert liked["children"][-1]["media_content_id"] == "nook://browse/music/liked/100"
    rest = await _browse(hass, hass_ws_client, "nook://browse/music/liked/100")
    assert _ids(rest) == ["nook://music/liked/song/jellyfin:l100"]


@pytest.mark.parametrize(
    "media_id",
    [
        "nook://music/shuffle",
        "nook://music/album/jellyfin:al1/song/jellyfin:t1",
        "nook://music/nothing",
        "nook://browse/music/nothing",
        "nook://browse/live/nothing",
        "nook://browse/music/albums/x",
    ],
)
async def test_browse_what_cannot_be_opened(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    media_id: str,
) -> None:
    """A leaf or an unknown node is an error, not an empty list."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "media_player/browse_media",
            "entity_id": ENTITY_LIVING,
            "media_content_type": "music",
            "media_content_id": media_id,
        }
    )
    response = await client.receive_json()
    assert not response["success"]


async def test_browse_music_failure_is_an_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Opening Music on a server that fails says so."""
    patch_client.music_error = NookConnectionError("down")
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "media_player/browse_media",
            "entity_id": ENTITY_LIVING,
            "media_content_type": "directory",
            "media_content_id": "nook://browse/music",
        }
    )
    response = await client.receive_json()
    assert not response["success"]
    assert "down" in response["error"]["message"]


async def _search(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    query: str,
    **extra: Any,
) -> list[dict[str, Any]]:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "media_player/search_media",
            "entity_id": ENTITY_LIVING,
            "search_query": query,
            **extra,
        }
    )
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]["result"]


async def test_search_everything_in_order(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Films and shows first, then channels, radio and music."""
    patch_client.channels = [*patch_client.channels, channel("bbc-x", "BBC Dune")]
    patch_client.radio_search = {"bbc": [ST_BBC1]}
    patch_client.music_search = {
        "bbc": NookMusicSearch(
            artists=[ARTIST_BEATLES], albums=[ALBUM_ABBEY], tracks=[TRACK_COME]
        )
    }
    results = await _search(hass, hass_ws_client, "bbc")
    assert [r["media_class"] for r in results] == [
        "movie",
        "movie",
        "tv_show",
        "channel",
        "channel",
        "music",
        "artist",
        "album",
        "track",
    ]
    assert [r["title"] for r in results[3:]] == [
        "5 · BBC One",
        "BBC Dune",
        "BBC Radio 1",
        "The Beatles",
        "Abbey Road · The Beatles",
        "Come Together · The Beatles",
    ]
    assert results[-1]["media_content_id"] == (
        "nook://music/album/jellyfin:al1/song/jellyfin:t1"
    )


async def test_search_channels_by_number_and_name(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A number finds that channel first; a name matches anywhere, any case."""
    by_number = await _search(
        hass, hass_ws_client, "5", media_filter_classes=["channel"]
    )
    assert [r["media_content_id"] for r in by_number] == ["nook://live/BBCOne.uk"]

    by_name = await _search(
        hass, hass_ws_client, "hrt", media_filter_classes=["channel"]
    )
    assert [r["title"] for r in by_name] == ["1 · HRT 1 · Dnevnik", "2 · HRT 2"]
    assert by_name[1]["thumbnail"].startswith(PROXY_PREFIX)


async def test_search_radio_favourites_first(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Favourites that match come first; TuneIn hits follow, without repeats."""
    world = NookStation(id="s50646", name="BBC World Service", logo_url=None)
    patch_client.radio_search = {"BBC": [world, ST_BBC1]}
    results = await _search(hass, hass_ws_client, "BBC", media_filter_classes=["music"])
    assert [r["title"] for r in results] == ["BBC Radio 1", "BBC World Service"]
    assert results[1]["media_content_id"] == (
        "nook://radio/s50646/BBC%20World%20Service"
    )


async def test_search_music(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Artists, albums, then songs; a song without an album is listed, not playable."""
    patch_client.music_search = {
        "beatles": NookMusicSearch(
            artists=[ARTIST_BEATLES],
            albums=[ALBUM_ABBEY, ALBUM_EMPTY],
            tracks=[TRACK_COME, TRACK_DEMO],
        )
    }
    results = await _search(hass, hass_ws_client, "beatles")
    music = [r for r in results if r["media_class"] in ("artist", "album", "track")]
    assert [r["media_content_id"] for r in music] == [
        "nook://music/artist/jellyfin:ar1",
        "nook://music/album/jellyfin:al1",
        "nook://music/album/jellyfin:al1/song/jellyfin:t1",
        "nook://music/track/jellyfin:t9",
    ]
    assert [r["can_play"] for r in music] == [True, True, True, False]

    albums = await _search(
        hass, hass_ws_client, "beatles", media_filter_classes=["album"]
    )
    assert [r["title"] for r in albums] == ["Abbey Road · The Beatles"]


async def test_search_filter_asks_only_what_it_needs(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A channel-only search does not search films and shows."""
    results = await _search(
        hass, hass_ws_client, "garden", media_filter_classes=["channel"]
    )
    assert [r["media_content_id"] for r in results] == [f"nook://live/{CH_CAM.id}"]
    assert patch_client.search_calls == []


async def test_search_on_an_older_server(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Without phase 2 on the server, search still finds films and shows."""
    error = NookScopeError("scope")
    patch_client.channels_error = error
    patch_client.radio_error = error
    patch_client.music_error = error
    results = await _search(hass, hass_ws_client, "dune")
    assert [r["media_class"] for r in results] == ["movie", "movie", "tv_show"]


async def test_channel_list_is_reused(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Browsing and searching within a minute fetch the channel list once."""
    await _browse(hass, hass_ws_client)
    await _browse(hass, hass_ws_client, "nook://browse/live")
    await _search(hass, hass_ws_client, "hrt", media_filter_classes=["channel"])
    assert patch_client.channel_calls == 1
