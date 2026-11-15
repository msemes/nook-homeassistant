"""Media ids: what each nook:// id names, and the nook://open link it becomes.

Every id the integration hands out or accepts is parsed here, in
`parse_media_id`. The forms are listed in the README:

- nook://<backend>/<movie|series>/<sourceId>: a film or a show (Play on...);
- nook://open?...: a raw TV link, sent as it is;
- nook://live/<channelId>: a Live TV channel;
- nook://radio/<stationId>[/<name>]: a radio station;
- nook://music/<album|playlist|artist>/<id>[/song/<trackId>],
  nook://music/liked[/song/<trackId>] and nook://music/shuffle: music;
- nook://browse/<node>: a folder of the media browser, never played.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import unicodedata
from urllib.parse import quote, unquote, urlencode

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.exceptions import ServiceValidationError

from .const import (
    BACKENDS,
    BROWSE_PREFIX,
    DOMAIN,
    LINK_VALUE_MAX,
    LIVE_PREFIX,
    MEDIA_ID_SCHEME,
    MUSIC_PREFIX,
    NOOK_OPEN_LINK,
    PLAYABLE_TYPES,
    RADIO_PREFIX,
    TYPE_MOVIE,
)

MUSIC_ALBUM = "album"
MUSIC_PLAYLIST = "playlist"
MUSIC_ARTIST = "artist"
MUSIC_LIKED = "liked"
MUSIC_SHUFFLE = "shuffle"
# A song known without an album, playlist or liked list: listed, never played.
MUSIC_TRACK = "track"
MUSIC_SONG = "song"

# Kinds whose id names a list of songs, and the link key that opens it.
_MUSIC_LIST_KEYS = {
    MUSIC_ALBUM: "album",
    MUSIC_PLAYLIST: "playlist",
    MUSIC_ARTIST: "artist",
}
_MUSIC_CLASSES = {
    MUSIC_ALBUM: MediaClass.ALBUM,
    MUSIC_PLAYLIST: MediaClass.PLAYLIST,
    MUSIC_ARTIST: MediaClass.ARTIST,
    MUSIC_LIKED: MediaClass.PLAYLIST,
    MUSIC_SHUFFLE: MediaClass.MUSIC,
}
_MUSIC_TYPES = {
    MUSIC_ALBUM: MediaType.ALBUM,
    MUSIC_PLAYLIST: MediaType.PLAYLIST,
    MUSIC_ARTIST: MediaType.ARTIST,
    MUSIC_LIKED: MediaType.PLAYLIST,
    MUSIC_SHUFFLE: MediaType.MUSIC,
}


def _segment(value: str) -> str:
    """Encode one path segment of a media id; nook's own ids keep their colon."""
    return quote(value, safe=":")


def link_value(value: str) -> str:
    """Return a value the server's link check takes: no control characters, at most 100.

    The server counts UTF-16 units, as Kotlin does, so a character outside the
    basic plane counts twice.
    """
    text = "".join(ch for ch in value if unicodedata.category(ch) != "Cc").strip()
    text = text[:LINK_VALUE_MAX]
    while len(text.encode("utf-16-le")) // 2 > LINK_VALUE_MAX:
        text = text[:-1]
    return text.strip()


def open_link(params: dict[str, str | None]) -> str:
    """Return nook://open with the given keys; empty values are left out."""
    values = {
        key: cleaned
        for key, value in params.items()
        if value is not None and (cleaned := link_value(value))
    }
    if not values:
        return NOOK_OPEN_LINK
    return f"{NOOK_OPEN_LINK}?{urlencode(values, quote_via=quote)}"


@dataclass(frozen=True, slots=True)
class PlayTarget:
    """What a playable media id names: one film or one show of one backend."""

    backend: str
    type: str
    source_id: str

    @property
    def media_content_id(self) -> str:
        """Return the media id, nook://<backend>/<type>/<sourceId>."""
        return f"{MEDIA_ID_SCHEME}{self.backend}/{self.type}/{self.source_id}"

    @property
    def media_class(self) -> MediaClass:
        """Return the media class of the title."""
        return MediaClass.MOVIE if self.type == TYPE_MOVIE else MediaClass.TV_SHOW

    @property
    def media_type(self) -> MediaType:
        """Return the media content type of the title."""
        return MediaType.MOVIE if self.type == TYPE_MOVIE else MediaType.TVSHOW


@dataclass(frozen=True, slots=True)
class LinkTarget:
    """A raw nook://open link, sent to the TV as it is; the server checks it."""

    link: str

    @property
    def media_content_id(self) -> str:
        """Return the link itself."""
        return self.link


@dataclass(frozen=True, slots=True)
class ChannelTarget:
    """A Live TV channel, by the id the server's channel list gives it."""

    channel_id: str

    @property
    def media_content_id(self) -> str:
        """Return nook://live/<channelId>."""
        return f"{LIVE_PREFIX}{quote(self.channel_id, safe='')}"

    def link(self, name: str | None = None) -> str:
        """Return the TV link; the name is the TV's fallback when the id differs."""
        return open_link({"channelId": self.channel_id, "channel": name})


@dataclass(frozen=True, slots=True)
class StationTarget:
    """A radio station: its TuneIn id, and its name for one that is no favourite."""

    station_id: str
    name: str | None = None

    @property
    def media_content_id(self) -> str:
        """Return nook://radio/<stationId>[/<name>]."""
        base = f"{RADIO_PREFIX}{quote(self.station_id, safe='')}"
        return f"{base}/{quote(self.name, safe='')}" if self.name else base

    def link(self) -> str:
        """Return the TV link."""
        return open_link({"station": self.station_id, "stationName": self.name})


@dataclass(frozen=True, slots=True)
class MusicTarget:
    """Music: an album, playlist, artist, the liked songs or shuffle-all.

    With a song, the list plays from that song on.
    """

    kind: str
    id: str | None = None
    song: str | None = None

    @property
    def media_content_id(self) -> str:
        """Return nook://music/<kind>[/<id>][/song/<trackId>]."""
        media_id = f"{MUSIC_PREFIX}{self.kind}"
        if self.id:
            media_id = f"{media_id}/{_segment(self.id)}"
        if self.song:
            media_id = f"{media_id}/{MUSIC_SONG}/{_segment(self.song)}"
        return media_id

    @property
    def media_class(self) -> MediaClass:
        """Return the media class: a track when a song is named."""
        return MediaClass.TRACK if self.song else _MUSIC_CLASSES[self.kind]

    @property
    def media_type(self) -> MediaType:
        """Return the media content type."""
        return MediaType.TRACK if self.song else _MUSIC_TYPES[self.kind]

    @property
    def is_list(self) -> bool:
        """Return True for a list of songs that the media browser can open."""
        return self.song is None and self.kind != MUSIC_SHUFFLE

    def with_song(self, track_id: str) -> MusicTarget:
        """Return the same list, starting at a song."""
        return replace(self, song=track_id)

    def link(self) -> str:
        """Return the TV link."""
        if self.kind == MUSIC_SHUFFLE:
            return open_link({"music": "shuffle"})
        params: dict[str, str | None] = (
            {"music": "liked"}
            if self.kind == MUSIC_LIKED
            else {_MUSIC_LIST_KEYS[self.kind]: self.id}
        )
        params["song"] = self.song
        return open_link(params)


type OpenTarget = LinkTarget | ChannelTarget | StationTarget | MusicTarget
type MediaTarget = PlayTarget | OpenTarget


def invalid_media_id(media_id: str) -> ServiceValidationError:
    """Return the error for an id that is no nook media id."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="invalid_media_id",
        translation_placeholders={"media_id": media_id},
    )


def _not_playable(media_id: str, key: str = "not_playable") -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"media_id": media_id},
    )


def _is_open_link(media_id: str) -> bool:
    lower = media_id.lower()
    return lower == NOOK_OPEN_LINK or lower.startswith(
        (f"{NOOK_OPEN_LINK}?", f"{NOOK_OPEN_LINK}/")
    )


def parse_media_id(media_id: str) -> MediaTarget:
    """Parse any playable nook media id, or raise ServiceValidationError."""
    media_id = media_id.strip()
    if media_id.startswith(BROWSE_PREFIX):
        raise _not_playable(media_id)
    if _is_open_link(media_id):
        return LinkTarget(media_id)
    if media_id.startswith(LIVE_PREFIX):
        if not (channel_id := unquote(media_id[len(LIVE_PREFIX) :])):
            raise invalid_media_id(media_id)
        return ChannelTarget(channel_id)
    if media_id.startswith(RADIO_PREFIX):
        station_id, _, name = media_id[len(RADIO_PREFIX) :].partition("/")
        if not (station_id := unquote(station_id)):
            raise invalid_media_id(media_id)
        return StationTarget(station_id, unquote(name) or None)
    if media_id.startswith(MUSIC_PREFIX):
        return parse_music_id(media_id)
    return parse_play_id(media_id)


def parse_music_id(media_id: str) -> MusicTarget:
    """Parse nook://music/..., or raise ServiceValidationError."""
    parts = [unquote(part) for part in media_id[len(MUSIC_PREFIX) :].split("/")]
    if not all(parts):
        raise invalid_media_id(media_id)
    kind, rest = parts[0], parts[1:]
    if kind == MUSIC_SHUFFLE and not rest:
        return MusicTarget(MUSIC_SHUFFLE)
    if kind == MUSIC_LIKED:
        if not rest:
            return MusicTarget(MUSIC_LIKED)
        if len(rest) == 2 and rest[0] == MUSIC_SONG:
            return MusicTarget(MUSIC_LIKED, song=rest[1])
    if kind in _MUSIC_LIST_KEYS:
        if len(rest) == 1:
            return MusicTarget(kind, rest[0])
        if len(rest) == 3 and rest[1] == MUSIC_SONG:
            return MusicTarget(kind, rest[0], rest[2])
    if kind == MUSIC_TRACK and len(rest) == 1:
        raise _not_playable(media_id, "track_not_playable")
    raise invalid_media_id(media_id)


def track_media_id(track_id: str) -> str:
    """Return the id of a song that has no list to play it from."""
    return f"{MUSIC_PREFIX}{MUSIC_TRACK}/{_segment(track_id)}"


def parse_play_id(media_id: str) -> PlayTarget:
    """Parse nook://<backend>/<movie|series>/<sourceId>, or raise."""
    if media_id.startswith(BROWSE_PREFIX):
        raise _not_playable(media_id)
    if not media_id.startswith(MEDIA_ID_SCHEME):
        raise invalid_media_id(media_id)
    # The source id may hold ":" or "/" (tmdb:697620), so split only twice.
    parts = media_id[len(MEDIA_ID_SCHEME) :].split("/", 2)
    if len(parts) != 3:
        raise invalid_media_id(media_id)
    backend, media_type, source_id = parts
    if backend not in BACKENDS or media_type not in PLAYABLE_TYPES or not source_id:
        raise invalid_media_id(media_id)
    return PlayTarget(backend=backend, type=media_type, source_id=source_id)
