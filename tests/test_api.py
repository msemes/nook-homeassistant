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
