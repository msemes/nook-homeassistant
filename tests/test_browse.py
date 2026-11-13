"""The media browser tree and search, through the websocket API the frontend uses."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.nook.browse import catalog_node
from homeassistant.core import HomeAssistant

from .conftest import CATALOG_MOVIES, ENTITY_LIVING, FakeNook


async def _browse(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, media_id: str | None = None
) -> dict[str, Any]:
    client = await hass_ws_client(hass)
    msg: dict[str, Any] = {
        "type": "media_player/browse_media",
        "entity_id": ENTITY_LIVING,
    }
    if media_id is not None:
        msg["media_content_type"] = "directory"
        msg["media_content_id"] = media_id
    await client.send_json_auto_id(msg)
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]


def _ids(node: dict[str, Any]) -> list[str]:
    return [child["media_content_id"] for child in node["children"]]


def _titles(node: dict[str, Any]) -> list[str]:
    return [child["title"] for child in node["children"]]


async def test_browse_root(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """The root lists the five sections and can be searched."""
    root = await _browse(hass, hass_ws_client)
    assert root["title"] == "nook"
    assert root["can_search"] is True
    assert root["children_media_class"] == "directory"
    assert _titles(root) == [
        "Continue Watching",
        "Watchlist",
        "Favourites",
        "Movies",
        "Shows",
    ]
    assert _ids(root) == [
        "nook://browse/cw",
        "nook://browse/watchlist",
        "nook://browse/favorites",
        "nook://browse/movies",
        "nook://browse/shows",
    ]
    assert all(
        child["can_expand"] and not child["can_play"] for child in root["children"]
    )


async def test_browse_movies_and_shows_list_their_catalogs(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Movies lists every film catalogue, Shows every show one; mixed ones in both.

    A catalogue hidden from home is listed too: the flag only keeps its titles
    out of Continue Watching.
    """
    movies = await _browse(hass, hass_ws_client, "nook://browse/movies")
    assert _titles(movies) == ["Movies", "Movies Kids", "Trending", "Weekend"]
    assert movies["children"][0]["media_content_id"] == (
        "nook://browse/catalog/jellyfin%3Alib%3Aabc%3Amovie"
    )
    assert movies["children"][0]["children_media_class"] == "movie"

    shows = await _browse(hass, hass_ws_client, "nook://browse/shows")
    assert _titles(shows) == ["Shows", "Weekend"]
    assert shows["children"][0]["children_media_class"] == "tv_show"


async def test_browse_catalog(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A catalogue opens to its first 100 items, without a source suffix."""
    node = await _browse(
        hass, hass_ws_client, f"nook://browse/{catalog_node(CATALOG_MOVIES.id)}"
    )
    assert node["title"] == "Movies"
    assert patch_client.catalog_calls == [(CATALOG_MOVIES.id, 0, 100)]
    assert _titles(node) == ["Dune (1984)", "Dune (2021)"]
    assert _ids(node) == [
        "nook://jellyfin/movie/a1b2c3d4",
        "nook://stremio/movie/tt1160419",
    ]
    assert node["children_media_class"] == "movie"
    first = node["children"][0]
    assert first["can_play"] is True
    assert first["can_expand"] is False
    assert first["media_class"] == "movie"
    assert first["thumbnail"] == "https://img.example.com/a1b2c3d4.jpg"


async def test_browse_continue_watching(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """An episode plays as its show; entries without a show id are left out."""
    node = await _browse(hass, hass_ws_client, "nook://browse/cw")
    assert _titles(node) == ["American Dad! S02E05 · Online", "Dune · Local"]
    assert _ids(node) == [
        "nook://stremio/series/tt0397306",
        "nook://jellyfin/movie/a1b2c3d4",
    ]
    assert [child["media_class"] for child in node["children"]] == ["tv_show", "movie"]
    assert node["children_media_class"] == "directory"


async def test_browse_watchlist_and_empty_favorites(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Mixed lists carry the source; an empty list is still a directory."""
    watchlist = await _browse(hass, hass_ws_client, "nook://browse/watchlist")
    assert _titles(watchlist) == [
        "Dune (1984) · Local",
        "American Dad! (2005) · Online",
    ]

    favorites = await _browse(hass, hass_ws_client, "nook://browse/favorites")
    assert favorites["children"] == []
    assert favorites["title"] == "Favourites"


async def test_browse_unknown_node(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """An unknown node is an error, not an empty list."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "media_player/browse_media",
            "entity_id": ENTITY_LIVING,
            "media_content_type": "directory",
            "media_content_id": "nook://browse/nowhere",
        }
    )
    response = await client.receive_json()
    assert not response["success"]


async def _search(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, **extra: Any
) -> list[dict[str, Any]]:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "media_player/search_media",
            "entity_id": ENTITY_LIVING,
            "search_query": "dune",
            **extra,
        }
    )
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]["result"]


async def test_search(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Search keeps the server order and labels LOCAL and ONLINE."""
    results = await _search(hass, hass_ws_client)
    assert patch_client.search_calls == ["dune"]
    assert [r["title"] for r in results] == [
        "Dune (1984) · Local",
        "Dune (2021) · Online",
        "Dune: Prophecy (2024) · Online",
    ]
    assert results[2]["media_content_id"] == "nook://stremio/series/tmdb:90228"
    assert all(r["can_play"] for r in results)


async def test_search_filter_classes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """media_filter_classes keeps only films or only shows."""
    shows = await _search(hass, hass_ws_client, media_filter_classes=["tv_show"])
    assert [r["title"] for r in shows] == ["Dune: Prophecy (2024) · Online"]

    movies = await _search(hass, hass_ws_client, media_filter_classes=["movie"])
    assert [r["media_class"] for r in movies] == ["movie", "movie"]

    music = await _search(hass, hass_ws_client, media_filter_classes=["music"])
    assert music == []


async def test_search_media_action(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The media_player.search_media action returns the ids to put on a button."""
    response = await hass.services.async_call(
        "media_player",
        "search_media",
        {
            "entity_id": ENTITY_LIVING,
            "search_query": "dune",
            "media_filter_classes": "tv_show",
        },
        blocking=True,
        return_response=True,
    )
    result = response[ENTITY_LIVING].result
    assert [r.media_content_id for r in result] == ["nook://stremio/series/tmdb:90228"]
