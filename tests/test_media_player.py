"""The media player entities: state, play, wake and play_title."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nook.api import NookAuthError, NookOfflineError
from custom_components.nook.const import CONF_LINKED_REMOTES, DOMAIN
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
)
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_TURN_ON,
    STATE_IDLE,
    STATE_OFF,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import (
    ENTITY_BEDROOM,
    ENTITY_LIVING,
    TV_BEDROOM,
    TV_LIVING,
    UPDATED_AT_MS,
    FakeNook,
    device,
    session,
)

BASE_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.SEARCH_MEDIA
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
)


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def _play(hass: HomeAssistant, entity_id: str, media_id: str) -> None:
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: entity_id,
            ATTR_MEDIA_CONTENT_TYPE: "movie",
            ATTR_MEDIA_CONTENT_ID: media_id,
        },
        blocking=True,
    )


def _register_remote(hass: HomeAssistant, on_turn_on: Any = None) -> list[ServiceCall]:
    """Register a fake remote.turn_on that records calls."""
    calls: list[ServiceCall] = []

    async def _handle(call: ServiceCall) -> None:
        calls.append(call)
        if on_turn_on is not None:
            on_turn_on()

    hass.services.async_register("remote", SERVICE_TURN_ON, _handle)
    return calls


async def test_entities_and_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Only paired TVs become entities, named after the TV."""
    entries = er.async_entries_for_config_entry(
        entity_registry, init_integration.entry_id
    )
    assert sorted(entry.unique_id for entry in entries) == [TV_BEDROOM, TV_LIVING]
    assert entity_registry.async_get(ENTITY_LIVING).unique_id == TV_LIVING

    tv = device_registry.async_get_device_by_identifier(
        (DOMAIN, TV_LIVING), init_integration.entry_id
    )
    assert tv is not None
    assert tv.name == "Living Room"
    assert tv.manufacturer == "nook"
    assert tv.model == "Android TV"


async def test_off_and_idle(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Offline TV is off; online without a session is idle; no turn_on without a remote."""
    living = hass.states.get(ENTITY_LIVING)
    assert living.state == STATE_IDLE
    assert living.attributes[ATTR_SUPPORTED_FEATURES] == BASE_FEATURES
    assert hass.states.get(ENTITY_BEDROOM).state == STATE_OFF


async def test_playing_movie(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A playing film fills title, ids, seconds and the server's timestamp."""
    patch_client.sessions = [session()]
    await _refresh(hass, init_integration)

    state = hass.states.get(ENTITY_LIVING)
    assert state.state == STATE_PLAYING
    attrs = state.attributes
    assert attrs["media_title"] == "Dune"
    assert attrs["media_content_type"] == "movie"
    assert attrs["media_content_id"] == "nook://stremio/movie/tt1160419"
    assert attrs["media_duration"] == 9300
    assert attrs["media_position"] == 600
    assert attrs["media_position_updated_at"] == dt_util.utc_from_timestamp(
        UPDATED_AT_MS / 1000
    )
    assert attrs["entity_picture"] == "https://img.example.com/tt1160419.jpg"
    assert patch_client.item_calls == ["stremio:tt1160419"]


async def test_paused_episode(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A Stremio episode: season/episode from its id, show title and art from the show."""
    patch_client.sessions = [
        session(
            content_id="tt0397306:2:5",
            type="episode",
            series_imdb_id="tt0397306",
            title="Fight and Flight",
            state="paused",
            duration_ms=None,
        )
    ]
    await _refresh(hass, init_integration)

    state = hass.states.get(ENTITY_LIVING)
    assert state.state == STATE_PAUSED
    attrs = state.attributes
    assert attrs["media_title"] == "Fight and Flight"
    assert attrs["media_content_type"] == "tvshow"
    assert attrs["media_content_id"] == "nook://stremio/series/tt0397306"
    assert attrs["media_season"] == "2"
    assert attrs["media_episode"] == "5"
    assert attrs["media_series_title"] == "American Dad!"
    assert "media_duration" not in attrs
    assert attrs["entity_picture"] == "https://img.example.com/tt0397306.jpg"
    assert patch_client.item_calls == ["stremio:tt0397306"]


async def test_local_episode_has_no_imdb_play_id(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A LOCAL episode's show is a Jellyfin item: an IMDb id would play nothing."""
    patch_client.sessions = [
        session(
            backend="jellyfin",
            content_id="5f2c0e9a1b",
            type="episode",
            series_imdb_id="tt0397306",
            title="Fight and Flight",
        )
    ]
    await _refresh(hass, init_integration)

    attrs = hass.states.get(ENTITY_LIVING).attributes
    assert attrs["media_title"] == "Fight and Flight"
    assert attrs["media_content_type"] == "tvshow"
    assert "media_content_id" not in attrs


async def test_artwork_failure_cached(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """An item lookup that fails is never repeated."""
    patch_client.sessions = [
        session(backend="jellyfin", content_id="abc123", title="Home video")
    ]
    for _ in range(3):
        await _refresh(hass, init_integration)
    state = hass.states.get(ENTITY_LIVING)
    assert state.state == STATE_PLAYING
    assert "entity_picture" not in state.attributes
    assert patch_client.item_calls == ["jellyfin:abc123"]


async def test_session_on_offline_tv_is_off(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A session left over on a TV that went offline does not make it play."""
    patch_client.sessions = [session(device_id=TV_BEDROOM)]
    await _refresh(hass, init_integration)
    state = hass.states.get(ENTITY_BEDROOM)
    assert state.state == STATE_OFF
    assert "media_title" not in state.attributes


async def test_vanished_tv_unavailable_and_new_tv_added(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A TV gone from the list is unavailable; a newly paired one gets an entity."""
    patch_client.devices = [d for d in patch_client.devices if d.id != TV_LIVING] + [
        device("tv-kitchen", "Kitchen", online=True)
    ]
    await _refresh(hass, init_integration)

    assert hass.states.get(ENTITY_LIVING).state == STATE_UNAVAILABLE
    assert hass.states.get("media_player.kitchen").state == STATE_IDLE


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A refused key during a poll opens the reauth flow."""
    patch_client.devices_error = NookAuthError("revoked")
    await _refresh(hass, init_integration)
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_play_media(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A playable id goes to the server as backend, source id and type."""
    await _play(hass, ENTITY_LIVING, "nook://stremio/movie/tt1160419")
    await _play(hass, ENTITY_LIVING, "nook://stremio/series/tmdb:697620")
    assert patch_client.play_calls == [
        (TV_LIVING, "stremio", "tt1160419", "movie", True),
        (TV_LIVING, "stremio", "tmdb:697620", "series", True),
    ]


@pytest.mark.parametrize(
    ("media_id", "key"),
    [
        ("nook://browse/cw", "not_playable"),
        ("nook://stremio/episode/tt1:1:1", "invalid_media_id"),
        ("nook://plex/movie/1", "invalid_media_id"),
        ("nook://stremio/movie/", "invalid_media_id"),
        ("media-source://whatever", "invalid_media_id"),
        ("nook://live/", "invalid_media_id"),
        ("nook://music/album", "invalid_media_id"),
        ("nook://music/track/jellyfin:t9", "track_not_playable"),
    ],
)
async def test_play_media_rejects_ids(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    media_id: str,
    key: str,
) -> None:
    """A folder, a malformed id or a song without a list is refused."""
    with pytest.raises(HomeAssistantError) as err:
        await _play(hass, ENTITY_LIVING, media_id)
    assert err.value.translation_key == key
    assert patch_client.play_calls == []
    assert patch_client.open_calls == []


async def test_play_media_offline_without_remote(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """An off TV without a linked remote fails with tv_offline, nothing is sent."""
    with pytest.raises(HomeAssistantError) as err:
        await _play(hass, ENTITY_BEDROOM, "nook://stremio/movie/tt1160419")
    assert err.value.translation_key == "tv_offline"
    assert err.value.translation_placeholders == {"name": "Bedroom"}
    assert "Bedroom is offline" in str(err.value)
    assert patch_client.play_calls == []


async def test_play_media_409(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A 409 from the server (TV dropped meanwhile) is the same tv_offline error."""
    patch_client.play_error = NookOfflineError("device is offline")
    with pytest.raises(HomeAssistantError) as err:
        await _play(hass, ENTITY_LIVING, "nook://jellyfin/movie/a1b2c3d4")
    assert err.value.translation_key == "tv_offline"
    assert err.value.translation_placeholders == {"name": "Living Room"}


@pytest.mark.parametrize(
    "entry_options", [{CONF_LINKED_REMOTES: {TV_BEDROOM: "remote.bedroom"}}]
)
async def test_play_media_wakes_tv_through_remote(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """With a linked remote: turn on with nook://open, wait for online, then play."""
    state = hass.states.get(ENTITY_BEDROOM)
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == (
        BASE_FEATURES | MediaPlayerEntityFeature.TURN_ON
    )
    assert (
        hass.states.get(ENTITY_LIVING).attributes[ATTR_SUPPORTED_FEATURES]
        == BASE_FEATURES
    )

    calls = _register_remote(hass, lambda: patch_client.set_online(TV_BEDROOM, True))
    with patch("custom_components.nook.media_player.WAKE_POLL_INTERVAL", 0.01):
        await _play(hass, ENTITY_BEDROOM, "nook://stremio/movie/tt1160419")

    assert len(calls) == 1
    assert calls[0].data == {
        ATTR_ENTITY_ID: "remote.bedroom",
        "activity": "nook://open",
    }
    assert patch_client.play_calls == [
        (TV_BEDROOM, "stremio", "tt1160419", "movie", True)
    ]


@pytest.mark.parametrize(
    "entry_options", [{CONF_LINKED_REMOTES: {TV_BEDROOM: "remote.bedroom"}}]
)
async def test_play_media_wake_timeout(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A TV that never comes online fails with wake_timeout and is not played on."""
    calls = _register_remote(hass)
    with (
        patch("custom_components.nook.media_player.WAKE_POLL_INTERVAL", 0.01),
        patch("custom_components.nook.media_player.WAKE_TIMEOUT", 0.05),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _play(hass, ENTITY_BEDROOM, "nook://stremio/movie/tt1160419")
    assert err.value.translation_key == "wake_timeout"
    assert len(calls) == 1
    assert patch_client.play_calls == []


@pytest.mark.parametrize(
    "entry_options", [{CONF_LINKED_REMOTES: {TV_BEDROOM: "remote.bedroom"}}]
)
async def test_turn_on(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """turn_on drives the linked remote with the nook app link."""
    calls = _register_remote(hass)
    await hass.services.async_call(
        MP_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_BEDROOM}, blocking=True
    )
    assert len(calls) == 1
    assert calls[0].data["activity"] == "nook://open"


async def _play_title(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN,
        "play_title",
        {ATTR_ENTITY_ID: ENTITY_LIVING, **data},
        blocking=True,
        return_response=True,
    )
    return response[ENTITY_LIVING]


@pytest.mark.parametrize(
    ("data", "expected", "sent"),
    [
        (
            {"title": "Dune"},
            {
                "title": "Dune (1984)",
                "media_content_id": "nook://jellyfin/movie/a1b2c3d4",
            },
            ("jellyfin", "a1b2c3d4", "movie"),
        ),
        (
            {"title": "Dune", "year": 2021},
            {
                "title": "Dune (2021)",
                "media_content_id": "nook://stremio/movie/tt1160419",
            },
            ("stremio", "tt1160419", "movie"),
        ),
        (
            {"title": "Dune", "source": "online"},
            {
                "title": "Dune (2021)",
                "media_content_id": "nook://stremio/movie/tt1160419",
            },
            ("stremio", "tt1160419", "movie"),
        ),
        (
            {"title": "Dune", "type": "series"},
            {
                "title": "Dune: Prophecy (2024)",
                "media_content_id": "nook://stremio/series/tmdb:90228",
            },
            ("stremio", "tmdb:90228", "series"),
        ),
    ],
)
async def test_play_title(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    data: dict[str, Any],
    expected: dict[str, str],
    sent: tuple[str, str, str],
) -> None:
    """play_title takes the first hit that fits the filters and says what it played."""
    assert await _play_title(hass, **data) == expected
    assert patch_client.search_calls == ["Dune"]
    assert patch_client.play_calls == [(TV_LIVING, *sent, True)]


async def test_play_title_without_response(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """The action also runs from a button, without asking for the response."""
    await hass.services.async_call(
        DOMAIN,
        "play_title",
        {ATTR_ENTITY_ID: ENTITY_LIVING, "title": "Dune"},
        blocking=True,
    )
    assert len(patch_client.play_calls) == 1


async def test_play_title_no_match(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """No hit that fits raises no_match."""
    with pytest.raises(HomeAssistantError) as err:
        await _play_title(hass, title="Dune", year=1999)
    assert err.value.translation_key == "no_match"
    assert patch_client.play_calls == []
