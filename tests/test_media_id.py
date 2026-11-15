"""Media ids: parsing, the ids they print, and the nook://open links they become."""

from __future__ import annotations

import pytest

from custom_components.nook.media_id import (
    ChannelTarget,
    LinkTarget,
    MediaTarget,
    MusicTarget,
    PlayTarget,
    StationTarget,
    link_value,
    parse_media_id,
)
from homeassistant.exceptions import ServiceValidationError


@pytest.mark.parametrize(
    ("target", "media_id", "link"),
    [
        (
            ChannelTarget("HRT1.hr"),
            "nook://live/HRT1.hr",
            "nook://open?channelId=HRT1.hr",
        ),
        (
            ChannelTarget("pl/1 x"),
            "nook://live/pl%2F1%20x",
            "nook://open?channelId=pl%2F1%20x",
        ),
        (
            StationTarget("s18415", "Radio Šljeme"),
            "nook://radio/s18415/Radio%20%C5%A0ljeme",
            "nook://open?station=s18415&stationName=Radio%20%C5%A0ljeme",
        ),
        (StationTarget("s18415"), "nook://radio/s18415", "nook://open?station=s18415"),
        (
            MusicTarget("album", "jellyfin:al1"),
            "nook://music/album/jellyfin:al1",
            "nook://open?album=jellyfin%3Aal1",
        ),
        (
            MusicTarget("album", "jellyfin:al1", "jellyfin:t1"),
            "nook://music/album/jellyfin:al1/song/jellyfin:t1",
            "nook://open?album=jellyfin%3Aal1&song=jellyfin%3At1",
        ),
        (
            MusicTarget("playlist", "0b6f6c1e-run"),
            "nook://music/playlist/0b6f6c1e-run",
            "nook://open?playlist=0b6f6c1e-run",
        ),
        (
            MusicTarget("playlist", "0b6f6c1e-run", "jellyfin:t2"),
            "nook://music/playlist/0b6f6c1e-run/song/jellyfin:t2",
            "nook://open?playlist=0b6f6c1e-run&song=jellyfin%3At2",
        ),
        (
            MusicTarget("artist", "jellyfin:ar1", "jellyfin:t1"),
            "nook://music/artist/jellyfin:ar1/song/jellyfin:t1",
            "nook://open?artist=jellyfin%3Aar1&song=jellyfin%3At1",
        ),
        (MusicTarget("liked"), "nook://music/liked", "nook://open?music=liked"),
        (
            MusicTarget("liked", song="jellyfin:t2"),
            "nook://music/liked/song/jellyfin:t2",
            "nook://open?music=liked&song=jellyfin%3At2",
        ),
        (MusicTarget("shuffle"), "nook://music/shuffle", "nook://open?music=shuffle"),
    ],
)
async def test_round_trip_and_link(
    target: ChannelTarget | StationTarget | MusicTarget, media_id: str, link: str
) -> None:
    """Each id parses back to what printed it, and becomes its TV link."""
    assert target.media_content_id == media_id
    assert parse_media_id(media_id) == target
    assert target.link() == link


async def test_channel_link_carries_the_name() -> None:
    """The channel name rides along as the TV's fallback; an empty one is left out."""
    target = ChannelTarget("HRT1.hr")
    assert target.link("HRT 1") == "nook://open?channelId=HRT1.hr&channel=HRT%201"
    assert target.link("  ") == "nook://open?channelId=HRT1.hr"


@pytest.mark.parametrize(
    "media_id",
    ["nook://open", "nook://open?channel=5", "NOOK://OPEN?screen=radio"],
)
async def test_raw_link_is_sent_as_is(media_id: str) -> None:
    """A nook://open id is a link the server checks; it is not rewritten."""
    assert parse_media_id(media_id) == LinkTarget(media_id)


@pytest.mark.parametrize(
    ("media_id", "expected"),
    [
        (
            "nook://stremio/series/tmdb:697620",
            PlayTarget("stremio", "series", "tmdb:697620"),
        ),
        (
            "nook://jellyfin/movie/a1b2c3d4",
            PlayTarget("jellyfin", "movie", "a1b2c3d4"),
        ),
    ],
)
async def test_films_and_shows_unchanged(media_id: str, expected: MediaTarget) -> None:
    """Phase 1 ids still parse to a Play on... target."""
    assert parse_media_id(media_id) == expected


@pytest.mark.parametrize(
    ("media_id", "key"),
    [
        ("nook://browse/live", "not_playable"),
        ("nook://music/track/jellyfin:t9", "track_not_playable"),
        ("nook://live/", "invalid_media_id"),
        ("nook://radio/", "invalid_media_id"),
        ("nook://radio//Otvoreni", "invalid_media_id"),
        ("nook://music/", "invalid_media_id"),
        ("nook://music/album", "invalid_media_id"),
        ("nook://music/album/", "invalid_media_id"),
        ("nook://music/album/x/song", "invalid_media_id"),
        ("nook://music/album/x/track/y", "invalid_media_id"),
        ("nook://music/liked/x", "invalid_media_id"),
        ("nook://music/shuffle/x", "invalid_media_id"),
        ("nook://music/mix/1", "invalid_media_id"),
        ("nook://opener", "invalid_media_id"),
    ],
)
async def test_rejected_ids(media_id: str, key: str) -> None:
    """A malformed id fails with the key that names why."""
    with pytest.raises(ServiceValidationError) as err:
        parse_media_id(media_id)
    assert err.value.translation_key == key


async def test_link_values_fit_the_server_check() -> None:
    """Control characters go, and a value stops at 100 UTF-16 units."""
    assert link_value(" HRT\t1\n ") == "HRT1"
    assert link_value("x" * 120) == "x" * 100
    # A character outside the basic plane counts twice, as in Kotlin.
    clef = "\U0001d11e"
    assert link_value("x" * 99 + clef) == "x" * 99
    assert link_value("x" * 98 + clef) == "x" * 98 + clef
