"""The HTTP client: requests it sends and how it maps errors."""

from __future__ import annotations

import logging

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from yarl import URL

from custom_components.nook.api import (
    NookAuthError,
    NookClient,
    NookConnectionError,
    NookNotFoundError,
    NookOfflineError,
    NookResponseError,
    NookScopeError,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import API_KEY, BASE_URL

DEVICES_URL = f"{BASE_URL}/api/me/devices"
PLAY_URL = f"{BASE_URL}/api/me/play-targets/tv-1/play"


def _client(hass: HomeAssistant) -> NookClient:
    return NookClient(async_get_clientsession(hass), f"{BASE_URL}/", API_KEY)


async def test_devices_parsed_with_bearer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Devices parse; malformed rows are skipped; the key goes as a bearer."""
    aioclient_mock.get(
        DEVICES_URL,
        json={
            "items": [
                {
                    "id": "tv-1",
                    "name": "Living Room",
                    "deviceType": "TV",
                    "status": "PAIRED",
                    "online": True,
                    "lastSeenAt": 1,
                },
                {"name": "no id"},
                "junk",
            ]
        },
    )
    devices = await _client(hass).async_get_devices()
    assert [d.id for d in devices] == ["tv-1"]
    assert devices[0].is_paired_tv
    assert devices[0].online
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert url == URL(DEVICES_URL)
    assert headers["Authorization"] == f"Bearer {API_KEY}"


async def test_play_sends_exact_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A play posts the Play on... body unchanged, source id with a colon included."""
    aioclient_mock.post(PLAY_URL, json={"id": "tv-1", "name": "Living Room"})
    result = await _client(hass).async_play("tv-1", "stremio", "tmdb:697620", "series")
    assert result == {"id": "tv-1", "name": "Living Room"}
    method, _, body, _ = aioclient_mock.mock_calls[0]
    assert method == "POST"
    assert body == {
        "backend": "stremio",
        "sourceId": "tmdb:697620",
        "type": "series",
        "resume": True,
    }


async def test_catalog_and_item_ids_are_encoded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Ids with colons are percent-encoded into one path segment."""
    aioclient_mock.get(
        URL(f"{BASE_URL}/api/me/library/jellyfin%3Alib%3Aabc%3Amovie", encoded=True),
        json={
            "catalogId": "jellyfin:lib:abc:movie",
            "offset": 0,
            "limit": 100,
            "totalCount": 1,
            "items": [
                {
                    "id": "jellyfin:a1b2",
                    "backend": "jellyfin",
                    "sourceId": "a1b2",
                    "type": "movie",
                    "title": "Dune",
                    "year": 1984,
                }
            ],
        },
    )
    aioclient_mock.get(
        URL(f"{BASE_URL}/api/me/item/stremio%3Att1160419", encoded=True),
        json={
            "item": {
                "id": "stremio:tt1160419",
                "backend": "stremio",
                "sourceId": "tt1160419",
                "type": "movie",
                "title": "Dune",
                "posterUrl": "https://img/p.jpg",
            }
        },
    )
    client = _client(hass)
    items = await client.async_get_catalog_items("jellyfin:lib:abc:movie")
    assert [i.title for i in items] == ["Dune"]
    _, url, _, _ = aioclient_mock.mock_calls[0]
    assert url.query == {"offset": "0", "limit": "100"}
    found = await client.async_get_item("stremio:tt1160419")
    assert found is not None
    assert found.poster_url == "https://img/p.jpg"


async def test_search_passes_query(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Search sends q and parses the cards."""
    aioclient_mock.get(
        f"{BASE_URL}/api/me/search",
        json={
            "items": [
                {
                    "id": "stremio:tt1",
                    "backend": "stremio",
                    "sourceId": "tt1",
                    "type": "series",
                    "title": "The Bear",
                }
            ]
        },
    )
    items = await _client(hass).async_search("the bear")
    assert items[0].type == "series"
    _, url, _, _ = aioclient_mock.mock_calls[0]
    assert url.query["q"] == "the bear"


@pytest.mark.parametrize(
    ("status", "kwargs", "error"),
    [
        (401, {"json": {"error": "unauthorized"}}, NookAuthError),
        (403, {"json": {"error": "scope", "code": "API_KEY_SCOPE"}}, NookAuthError),
        (403, {"text": "error code: 1010"}, NookConnectionError),
        (403, {"json": {"error": "forbidden"}}, NookResponseError),
        (404, {"json": {"error": "unknown device"}}, NookNotFoundError),
        (
            409,
            {"json": {"error": "device is offline", "code": "TARGET_OFFLINE"}},
            NookOfflineError,
        ),
        # Production names the code under "id" today; the status alone decides.
        (
            409,
            {"json": {"error": "device is offline", "id": "TARGET_OFFLINE"}},
            NookOfflineError,
        ),
        (400, {"json": {"error": "unsupported type"}}, NookResponseError),
        (502, {"text": "bad gateway"}, NookConnectionError),
    ],
)
async def test_error_mapping(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    kwargs: dict,
    error: type[Exception],
) -> None:
    """Each error answer maps onto its exception."""
    aioclient_mock.post(PLAY_URL, status=status, **kwargs)
    with pytest.raises(error):
        await _client(hass).async_play("tv-1", "stremio", "tt1160419", "movie")


async def test_proxy_block_is_logged(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Cloudflare 1010 says so in the log and flags the error."""
    aioclient_mock.get(DEVICES_URL, status=403, text="error code: 1010")
    with caplog.at_level(logging.WARNING), pytest.raises(NookConnectionError) as err:
        await _client(hass).async_get_devices()
    assert err.value.blocked_by_proxy
    assert "1010" in caplog.text


@pytest.mark.parametrize(
    "exc", [TimeoutError(), aiohttp.ClientConnectionError("refused")]
)
async def test_transport_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, exc: Exception
) -> None:
    """Timeouts and connection errors become NookConnectionError."""
    aioclient_mock.get(DEVICES_URL, exc=exc)
    with pytest.raises(NookConnectionError):
        await _client(hass).async_get_devices()


async def test_non_json_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An HTML page where JSON belongs is an unexpected response."""
    aioclient_mock.get(DEVICES_URL, text="<html>login</html>")
    with pytest.raises(NookResponseError):
        await _client(hass).async_get_devices()


async def test_open_and_control_send_exact_bodies(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A link and a control post their one field to the TV's path."""
    aioclient_mock.post(
        f"{BASE_URL}/api/me/play-targets/tv-1/open", json={"id": "tv-1", "name": "TV"}
    )
    aioclient_mock.post(
        f"{BASE_URL}/api/me/play-targets/tv-1/control",
        json={"id": "tv-1", "name": "TV"},
    )
    client = _client(hass)
    assert await client.async_open_link("tv-1", "nook://open?channel=5") == {
        "id": "tv-1",
        "name": "TV",
    }
    await client.async_control("tv-1", "pause")
    assert [call[2] for call in aioclient_mock.mock_calls] == [
        {"link": "nook://open?channel=5"},
        {"action": "pause"},
    ]


async def test_open_refused_and_older_server(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """400 keeps the server's reason; 403 API_KEY_SCOPE is a scope error."""
    url = f"{BASE_URL}/api/me/play-targets/tv-1/open"
    aioclient_mock.post(url, status=400, json={"error": "unknown parameter colour"})
    with pytest.raises(NookResponseError) as err:
        await _client(hass).async_open_link("tv-1", "nook://open?colour=red")
    assert err.value.status == 400
    assert err.value.reason == "unknown parameter colour"

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        url, status=403, json={"error": "not for this key", "code": "API_KEY_SCOPE"}
    )
    with pytest.raises(NookScopeError) as scope:
        await _client(hass).async_open_link("tv-1", "nook://open")
    assert isinstance(scope.value, NookAuthError)


async def test_now_playing_and_channels_parse(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Missing fields take their defaults; rows without their keys are skipped."""
    aioclient_mock.get(
        f"{BASE_URL}/api/me/now-playing",
        json={
            "items": [
                {
                    "deviceId": "tv-1",
                    "kind": "live",
                    "title": "HRT 1",
                    "subtitle": "Dnevnik",
                    "contentId": "HRT1.hr",
                    "positionMs": 600000,
                    "durationMs": 1800000,
                    "updatedAt": 5,
                },
                {"deviceId": "tv-2", "kind": "radio", "updatedAt": 6, "paused": True},
                {"kind": "music"},
            ]
        },
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/me/channels/brief",
        json={
            "channels": [
                {
                    "id": "HRT1.hr",
                    "name": "HRT 1",
                    "number": 1,
                    "logoUrl": "http://l/hrt1.png",
                    "group": "Croatia",
                    "favorite": True,
                    "nowTitle": "Dnevnik",
                },
                {"id": "x", "name": "X"},
            ]
        },
    )
    client = _client(hass)
    live, radio = await client.async_get_now_playing()
    assert (live.kind, live.title, live.subtitle, live.content_id) == (
        "live",
        "HRT 1",
        "Dnevnik",
        "HRT1.hr",
    )
    assert (live.position_ms, live.duration_ms, live.paused) == (600000, 1800000, False)
    assert radio.paused is True
    assert radio.position_ms is None

    first, second = await client.async_get_channels()
    assert (first.number, first.group, first.favorite, first.now_title) == (
        1,
        "Croatia",
        True,
        "Dnevnik",
    )
    assert (second.number, second.group, second.favorite) == (None, None, False)


async def test_radio_parses_favourites_and_search(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Favourites are under items, search hits under stations; an empty logo is none."""
    aioclient_mock.get(
        f"{BASE_URL}/api/me/radio/favorites",
        json={
            "items": [{"stationUuid": "s18415", "name": "Otvoreni", "faviconUrl": ""}]
        },
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/me/radio/search",
        json={
            "stations": [
                {
                    "stationUuid": "s24939",
                    "name": "BBC Radio 1",
                    "streamUrl": "",
                    "faviconUrl": "http://l/bbc.png",
                }
            ]
        },
    )
    client = _client(hass)
    (favorite,) = await client.async_get_radio_favorites()
    assert (favorite.id, favorite.name, favorite.logo_url) == (
        "s18415",
        "Otvoreni",
        None,
    )
    (hit,) = await client.async_search_radio("bbc")
    assert hit.logo_url == "http://l/bbc.png"
    assert aioclient_mock.mock_calls[1][1].query["q"] == "bbc"


async def test_music_parses(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Home, pages and an album: nook ids, counts, the playlist's first image."""
    aioclient_mock.get(
        f"{BASE_URL}/api/me/music/home",
        json={
            "recentlyPlayed": [
                {
                    "id": "jellyfin:t1",
                    "sourceId": "t1",
                    "title": "Come Together",
                    "artist": "The Beatles",
                    "albumId": "jellyfin:al1",
                }
            ],
            "playlists": [
                {
                    "id": "p1",
                    "name": "Running",
                    "trackCount": 2,
                    "imageUrls": ["a", "b"],
                }
            ],
            "likedTrackCount": 4,
            "trackCount": 17,
        },
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/me/music/albums",
        json={
            "items": [{"id": "jellyfin:al1", "sourceId": "al1", "title": "Abbey Road"}],
            "total": 250,
        },
    )
    aioclient_mock.get(
        URL(f"{BASE_URL}/api/me/music/albums/jellyfin%3Aal1", encoded=True),
        json={
            "album": {"id": "jellyfin:al1", "sourceId": "al1", "title": "Abbey Road"},
            "tracks": [
                {"id": "jellyfin:t1", "sourceId": "t1", "title": "Come Together"}
            ],
        },
    )
    client = _client(hass)
    home = await client.async_get_music_home()
    assert home.track_count == 17
    assert home.liked_track_count == 4
    assert home.recently_played[0].album_id == "jellyfin:al1"
    assert home.playlists[0].image_url == "a"
    assert home.recently_added == []

    page = await client.async_get_albums(offset=100, limit=100)
    assert page.total == 250
    assert page.items[0].track_count == 0
    assert aioclient_mock.mock_calls[1][1].query == {
        "offset": "100",
        "limit": "100",
        "sort": "title",
    }

    found = await client.async_get_album("jellyfin:al1")
    assert found.name == "Abbey Road"
    assert [t.id for t in found.tracks] == ["jellyfin:t1"]
