"""Live TV, radio and music in the entity state; Stop / Pause / Play; playing the new ids."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.nook.api import (
    NookAuthError,
    NookConnectionError,
    NookNotFoundError,
    NookNowPlaying,
    NookOfflineError,
    NookResponseError,
    NookScopeError,
)
from custom_components.nook.const import CONF_LINKED_REMOTES
from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_STOP,
    STATE_IDLE,
    STATE_OFF,
    STATE_PAUSED,
    STATE_PLAYING,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .conftest import (
    ENTITY_BEDROOM,
    ENTITY_LIVING,
    TV_BEDROOM,
    TV_LIVING,
    UPDATED_AT_MS,
    FakeNook,
    now_playing,
    session,
)
from .test_media_player import _play, _refresh, _register_remote

T0 = dt_util.utc_from_timestamp(UPDATED_AT_MS / 1000)


def _live(**overrides: Any) -> NookNowPlaying:
    """Build a Live TV entry: HRT 1, ten minutes into Dnevnik."""
    return now_playing(
        **{
            "kind": "live",
            "title": "HRT 1",
            "subtitle": "Dnevnik",
            "artist": None,
            "album": None,
            "image_url": "https://img.example.com/hrt1.png",
            "content_id": "HRT1.hr",
            "position_ms": 600_000,
            "duration_ms": 1_800_000,
            **overrides,
        }
    )


async def test_live_tv_state(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """Live TV: channel and programme as title, the channel, its id and the programme's span."""
    patch_client.now_playing = [_live()]
    await _refresh(hass, init_integration)

    state = hass.states.get(ENTITY_LIVING)
    assert state.state == STATE_PLAYING
    attrs = state.attributes
    assert attrs["media_title"] == "HRT 1 · Dnevnik"
    assert attrs["media_channel"] == "HRT 1"
    assert attrs["media_content_type"] == "channel"
    assert attrs["media_content_id"] == "nook://live/HRT1.hr"
    assert attrs["media_position"] == 600
    assert attrs["media_duration"] == 1800
    assert attrs["media_position_updated_at"] == T0
    assert attrs["entity_picture"] == "https://img.example.com/hrt1.png"
    assert "media_artist" not in attrs


async def test_live_tv_falls_back_on_the_channel_list(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """Without an id or logo in the report, the channel list by name gives both."""
    await init_integration.runtime_data.async_get_channels()
    patch_client.now_playing = [
        _live(content_id=None, image_url=None, subtitle=None, position_ms=None)
    ]
    await _refresh(hass, init_integration)

    attrs = hass.states.get(ENTITY_LIVING).attributes
    assert attrs["media_title"] == "HRT 1"
    assert attrs["media_content_id"] == "nook://live/HRT1.hr"
    assert attrs["entity_picture"] == "https://img.example.com/hrt1.png"
    assert "media_position" not in attrs


async def test_position_moves_only_on_a_jump(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A report that agrees with the running position keeps the old timestamp."""
    patch_client.now_playing = [_live()]
    await _refresh(hass, init_integration)

    patch_client.now_playing = [
        _live(position_ms=610_500, updated_at=UPDATED_AT_MS + 10_000)
    ]
    await _refresh(hass, init_integration)
    attrs = hass.states.get(ENTITY_LIVING).attributes
    assert attrs["media_position"] == 600
    assert attrs["media_position_updated_at"] == T0

    patch_client.now_playing = [
        _live(position_ms=900_000, updated_at=UPDATED_AT_MS + 20_000)
    ]
    await _refresh(hass, init_integration)
    attrs = hass.states.get(ENTITY_LIVING).attributes
    assert attrs["media_position"] == 900
    assert attrs["media_position_updated_at"] == T0 + timedelta(seconds=20)


async def test_radio_state(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """Radio: the track line as title, the station as channel; an http logo is proxied."""
    patch_client.now_playing = [
        now_playing(
            kind="radio",
            title="Otvoreni",
            subtitle="Dua Lipa - Houdini",
            artist=None,
            album=None,
            image_url="http://logos.example.com/otvoreni.png",
            content_id="s18415",
            paused=True,
            position_ms=None,
            duration_ms=None,
        )
    ]
    await _refresh(hass, init_integration)

    state = hass.states.get(ENTITY_LIVING)
    assert state.state == STATE_PAUSED
    attrs = state.attributes
    assert attrs["media_title"] == "Dua Lipa - Houdini"
    assert attrs["media_channel"] == "Otvoreni"
    # The card's second line for music is the artist: the station here.
    assert attrs["media_artist"] == "Otvoreni"
    assert attrs["media_content_type"] == "music"
    assert attrs["media_content_id"] == "nook://radio/s18415/Otvoreni"
    assert attrs["entity_picture"].startswith(
        f"/api/media_player_proxy/{ENTITY_LIVING}?token="
    )
    assert "media_position" not in attrs
    assert "media_duration" not in attrs


async def test_music_state(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """Music: song, artist, album, position and duration; no id to play it again."""
    patch_client.now_playing = [now_playing()]
    await _refresh(hass, init_integration)

    state = hass.states.get(ENTITY_LIVING)
    assert state.state == STATE_PLAYING
    attrs = state.attributes
    assert attrs["media_title"] == "Come Together"
    assert attrs["media_artist"] == "The Beatles"
    assert attrs["media_album_name"] == "Abbey Road"
    assert attrs["media_content_type"] == "music"
    assert attrs["media_position"] == 60
    assert attrs["media_duration"] == 259
    assert attrs["media_position_updated_at"] == T0
    assert attrs["entity_picture"] == "https://img.example.com/abbey.jpg"
    assert "media_content_id" not in attrs
    assert "media_channel" not in attrs


async def test_vod_session_wins(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A film playing wins over music the TV reported before; music fields clear."""
    patch_client.now_playing = [now_playing()]
    await _refresh(hass, init_integration)
    patch_client.sessions = [session()]
    await _refresh(hass, init_integration)

    attrs = hass.states.get(ENTITY_LIVING).attributes
    assert attrs["media_title"] == "Dune"
    assert attrs["media_content_type"] == "movie"
    assert "media_artist" not in attrs
    assert "media_album_name" not in attrs


async def test_now_playing_edge_cases(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """An offline TV is off, and an unknown kind is nothing."""
    patch_client.now_playing = [
        now_playing(device_id=TV_BEDROOM),
        now_playing(kind="none"),
    ]
    await _refresh(hass, init_integration)
    assert hass.states.get(ENTITY_BEDROOM).state == STATE_OFF
    assert hass.states.get(ENTITY_LIVING).state == STATE_IDLE


@pytest.mark.parametrize(
    "error", [NookScopeError("scope"), NookNotFoundError("HTTP 404")]
)
async def test_now_playing_on_an_older_server(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """403 or 404 is no data, said once in the debug log; films still show."""
    caplog.set_level(logging.DEBUG, logger="custom_components.nook")
    patch_client.now_playing_error = error
    await _refresh(hass, init_integration)
    await _refresh(hass, init_integration)

    assert hass.states.get(ENTITY_LIVING).state == STATE_IDLE
    assert init_integration.runtime_data.last_update_success
    assert caplog.text.count("does not report Live TV") == 1
    assert not hass.config_entries.flow.async_progress()

    patch_client.sessions = [session()]
    await _refresh(hass, init_integration)
    assert hass.states.get(ENTITY_LIVING).state == STATE_PLAYING


async def test_now_playing_failure_keeps_the_last_answer(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """One failed read does not flip a TV that plays music to idle."""
    patch_client.now_playing = [now_playing()]
    await _refresh(hass, init_integration)
    patch_client.now_playing_error = NookConnectionError("timeout")
    await _refresh(hass, init_integration)
    assert hass.states.get(ENTITY_LIVING).state == STATE_PLAYING


async def test_controls(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """Stop, pause and play go to the TV; the state follows 2 s later, not a poll later."""
    for service in (SERVICE_MEDIA_STOP, SERVICE_MEDIA_PAUSE, SERVICE_MEDIA_PLAY):
        await hass.services.async_call(
            MP_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_LIVING}, blocking=True
        )
    assert patch_client.control_calls == [
        (TV_LIVING, "stop"),
        (TV_LIVING, "pause"),
        (TV_LIVING, "resume"),
    ]
    polls = patch_client.now_playing_calls
    patch_client.now_playing = [now_playing(paused=True)]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    # Three controls in a row poll once.
    assert patch_client.now_playing_calls == polls + 1
    assert hass.states.get(ENTITY_LIVING).state == STATE_PAUSED


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (NookOfflineError("offline"), "tv_offline"),
        (NookNotFoundError("unknown device"), "tv_not_found"),
        (NookScopeError("scope"), "server_unsupported"),
        (NookConnectionError("down"), "control_failed"),
    ],
)
async def test_control_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    error: Exception,
    key: str,
) -> None:
    """A failed control says why; an older server does not ask for a new key."""
    patch_client.control_error = error
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            MP_DOMAIN,
            SERVICE_MEDIA_PAUSE,
            {ATTR_ENTITY_ID: ENTITY_LIVING},
            blocking=True,
        )
    assert err.value.translation_key == key
    assert not hass.config_entries.flow.async_progress()


async def test_control_refused_key_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A key refused outright (401) opens the reauth flow, as a play does."""
    patch_client.control_error = NookAuthError("revoked")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            MP_DOMAIN,
            SERVICE_MEDIA_STOP,
            {ATTR_ENTITY_ID: ENTITY_LIVING},
            blocking=True,
        )
    assert err.value.translation_key == "auth_failed"
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


@pytest.mark.parametrize(
    ("media_id", "link"),
    [
        ("nook://open?channel=5", "nook://open?channel=5"),
        ("nook://open?screen=radio", "nook://open?screen=radio"),
        (
            "nook://live/HRT1.hr",
            "nook://open?channelId=HRT1.hr&channel=HRT%201",
        ),
        ("nook://live/gone", "nook://open?channelId=gone"),
        (
            "nook://radio/s18415/Otvoreni",
            "nook://open?station=s18415&stationName=Otvoreni",
        ),
        ("nook://music/playlist/0b6f6c1e-run", "nook://open?playlist=0b6f6c1e-run"),
        (
            "nook://music/album/jellyfin:al1/song/jellyfin:t1",
            "nook://open?album=jellyfin%3Aal1&song=jellyfin%3At1",
        ),
        ("nook://music/artist/jellyfin:ar1", "nook://open?artist=jellyfin%3Aar1"),
        (
            "nook://music/liked/song/jellyfin:t2",
            "nook://open?music=liked&song=jellyfin%3At2",
        ),
        ("nook://music/liked", "nook://open?music=liked"),
        ("nook://music/shuffle", "nook://open?music=shuffle"),
    ],
)
async def test_play_sends_the_link(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    media_id: str,
    link: str,
) -> None:
    """Each new id becomes its nook://open link, sent to that TV."""
    await _play(hass, ENTITY_LIVING, media_id)
    assert patch_client.open_calls == [(TV_LIVING, link)]
    assert patch_client.play_calls == []


async def test_play_channel_without_the_channel_list(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """When the channel list cannot be read, the link names the id alone."""
    patch_client.channels_error = NookScopeError("scope")
    await _play(hass, ENTITY_LIVING, "nook://live/HRT1.hr")
    assert patch_client.open_calls == [(TV_LIVING, "nook://open?channelId=HRT1.hr")]


async def test_open_refused_by_the_server(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A 400 names the link and the server's reason."""
    patch_client.open_error = NookResponseError(400, "unknown parameter colour")
    with pytest.raises(HomeAssistantError) as err:
        await _play(hass, ENTITY_LIVING, "nook://open?colour=red")
    assert err.value.translation_key == "link_refused"
    assert err.value.translation_placeholders == {
        "link": "nook://open?colour=red",
        "error": "unknown parameter colour",
    }


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (NookScopeError("scope"), "server_unsupported"),
        (NookOfflineError("offline"), "tv_offline"),
        (NookResponseError(422, "odd"), "play_failed"),
    ],
)
async def test_open_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    error: Exception,
    key: str,
) -> None:
    """An older server, a TV gone offline, anything else: each says so."""
    patch_client.open_error = error
    with pytest.raises(HomeAssistantError) as err:
        await _play(hass, ENTITY_LIVING, "nook://music/shuffle")
    assert err.value.translation_key == key
    assert not hass.config_entries.flow.async_progress()


@pytest.mark.parametrize(
    "entry_options", [{CONF_LINKED_REMOTES: {TV_BEDROOM: "remote.bedroom"}}]
)
async def test_open_wakes_the_tv_through_its_remote(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """A channel sent to a TV that is off wakes it first, like a film does."""
    calls = _register_remote(hass, lambda: patch_client.set_online(TV_BEDROOM, True))
    with patch("custom_components.nook.media_player.WAKE_POLL_INTERVAL", 0.01):
        await _play(hass, ENTITY_BEDROOM, "nook://open?channel=5")
    assert len(calls) == 1
    assert patch_client.open_calls == [(TV_BEDROOM, "nook://open?channel=5")]


async def test_open_on_an_offline_tv_without_remote(
    hass: HomeAssistant, init_integration: MockConfigEntry, patch_client: FakeNook
) -> None:
    """No remote, TV off: tv_offline, and nothing is sent."""
    with pytest.raises(HomeAssistantError) as err:
        await _play(hass, ENTITY_BEDROOM, "nook://music/shuffle")
    assert err.value.translation_key == "tv_offline"
    assert patch_client.open_calls == []
