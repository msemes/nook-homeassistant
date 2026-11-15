"""The media browser tree and search for nook."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote

from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaType,
    SearchError,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.exceptions import ServiceValidationError

from .api import (
    NookAlbum,
    NookArtist,
    NookChannel,
    NookClient,
    NookCwItem,
    NookError,
    NookItem,
    NookPage,
    NookPlaylist,
    NookStation,
    NookTrack,
    NookTrackList,
)
from .const import (
    BACKEND_LABEL,
    BACKENDS,
    BROWSE_PREFIX,
    CATALOG_PAGE_LIMIT,
    DOMAIN,
    LOGGER,
    MUSIC_PREFIX,
    PLAYABLE_TYPES,
    SOURCE_TO_BACKEND,
    TYPE_EPISODE,
    TYPE_MIXED,
    TYPE_MOVIE,
    TYPE_SERIES,
)
from .media_id import (
    MUSIC_ALBUM,
    MUSIC_ARTIST,
    MUSIC_LIKED,
    MUSIC_PLAYLIST,
    MUSIC_SHUFFLE,
    ChannelTarget,
    MusicTarget,
    PlayTarget,
    StationTarget,
    parse_music_id,
    track_media_id,
)

if TYPE_CHECKING:
    from .coordinator import NookCoordinator

DIRECTORY_CONTENT_TYPE = "directory"

NODE_ROOT = "root"
NODE_CW = "cw"
NODE_WATCHLIST = "watchlist"
NODE_FAVORITES = "favorites"
NODE_MOVIES = "movies"
NODE_SHOWS = "shows"
NODE_CATALOG = "catalog/"

NODE_LIVE = "live"
NODE_LIVE_FAVORITES = "live/favorites"
NODE_LIVE_OTHER = "live/other"
NODE_LIVE_GROUP = "live/group/"
NODE_RADIO = "radio"
NODE_MUSIC = "music"
NODE_MUSIC_PLAYLISTS = "music/playlists"
NODE_MUSIC_RECENTLY_PLAYED = "music/recently-played"
NODE_MUSIC_RECENTLY_ADDED = "music/recently-added"
NODE_MUSIC_TOP_ARTISTS = "music/top-artists"
NODE_MUSIC_ALBUMS = "music/albums"
NODE_MUSIC_ARTISTS = "music/artists"
# Liked songs open as nook://music/liked; its later pages are music/liked/<offset>.
NODE_MUSIC_LIKED = "music/liked"

ROOT_ID = f"{BROWSE_PREFIX}{NODE_ROOT}"

# Root children: node, title, class of their children before they are opened.
_ROOT_NODES: tuple[tuple[str, str, MediaClass], ...] = (
    (NODE_CW, "Continue Watching", MediaClass.VIDEO),
    (NODE_WATCHLIST, "Watchlist", MediaClass.VIDEO),
    (NODE_FAVORITES, "Favourites", MediaClass.VIDEO),
    (NODE_MOVIES, "Movies", MediaClass.DIRECTORY),
    (NODE_SHOWS, "Shows", MediaClass.DIRECTORY),
)
_LIST_TITLES = {node: title for node, title, _ in _ROOT_NODES}
_SEARCH_CLASSES = [MediaClass.MOVIE, MediaClass.TV_SHOW]
_MUSIC_SEARCH_CLASSES = [MediaClass.ARTIST, MediaClass.ALBUM, MediaClass.TRACK]

TITLE_LIVE = "Live TV"
TITLE_RADIO = "Radio"
TITLE_MUSIC = "Music"
TITLE_LIKED = "Liked songs"
TITLE_MORE = "More"
_MUSIC_TITLES = {
    NODE_MUSIC_PLAYLISTS: "Playlists",
    NODE_MUSIC_RECENTLY_PLAYED: "Recently played",
    NODE_MUSIC_RECENTLY_ADDED: "Recently added",
    NODE_MUSIC_TOP_ARTISTS: "Top artists",
    NODE_MUSIC_ALBUMS: "Albums",
    NODE_MUSIC_ARTISTS: "Artists",
}


def item_target(item: NookItem) -> PlayTarget | None:
    """Return the play target of a card, or None for what cannot be played."""
    if item.backend not in BACKENDS or item.type not in PLAYABLE_TYPES:
        return None
    return PlayTarget(backend=item.backend, type=item.type, source_id=item.source_id)


def cw_target(entry: NookCwItem) -> PlayTarget | None:
    """Return the play target of a Continue Watching entry.

    An episode is played by sending its show whole: the TV then plays the
    episode its Continue button names.
    """
    if entry.backend not in BACKENDS:
        return None
    if entry.type == TYPE_EPISODE:
        if not entry.series_id:
            return None
        return PlayTarget(entry.backend, TYPE_SERIES, entry.series_id)
    if entry.type in PLAYABLE_TYPES:
        return PlayTarget(entry.backend, entry.type, entry.id)
    return None


def display_title(item: NookItem, *, mixed: bool = False) -> str:
    """Return "Dune (2021)", with " · Local" or " · Online" in a mixed list."""
    title = f"{item.title} ({item.year})" if item.year else item.title
    return _with_source(title, item.backend) if mixed else title


def _with_source(title: str, backend: str) -> str:
    label = BACKEND_LABEL.get(backend)
    return f"{title} · {label}" if label else title


def pick_title(
    items: Iterable[NookItem],
    *,
    media_type: str | None = None,
    year: int | None = None,
    source: str | None = None,
) -> NookItem | None:
    """Return the first playable hit that matches every given filter."""
    backend = SOURCE_TO_BACKEND.get(source) if source else None
    for item in items:
        if item_target(item) is None:
            continue
        if media_type and item.type != media_type:
            continue
        if year is not None and item.year != year:
            continue
        if backend and item.backend != backend:
            continue
        return item
    return None


def _leaf(target: PlayTarget, title: str, thumbnail: str | None) -> BrowseMedia:
    return BrowseMedia(
        media_class=target.media_class,
        media_content_id=target.media_content_id,
        media_content_type=target.media_type,
        title=title,
        can_play=True,
        can_expand=False,
        thumbnail=thumbnail,
    )


def _item_leaves(items: Iterable[NookItem], *, mixed: bool) -> list[BrowseMedia]:
    leaves: list[BrowseMedia] = []
    for item in items:
        if (target := item_target(item)) is not None:
            leaves.append(
                _leaf(target, display_title(item, mixed=mixed), item.poster_url)
            )
    return leaves


def _cw_leaves(entries: Iterable[NookCwItem]) -> list[BrowseMedia]:
    leaves: list[BrowseMedia] = []
    for entry in entries:
        if (target := cw_target(entry)) is None:
            continue
        title = f"{entry.title} {entry.subtitle}" if entry.subtitle else entry.title
        leaves.append(
            _leaf(target, _with_source(title, entry.backend), entry.poster_url)
        )
    return leaves


def _directory(
    node: str,
    title: str,
    *,
    children: list[BrowseMedia] | None = None,
    children_media_class: MediaClass | None = None,
    can_search: bool = False,
    search_classes: list[MediaClass] | None = None,
) -> BrowseMedia:
    directory = BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=f"{BROWSE_PREFIX}{node}",
        media_content_type=DIRECTORY_CONTENT_TYPE,
        title=title,
        can_play=False,
        can_expand=True,
        children=children,
        children_media_class=children_media_class,
        can_search=can_search,
        search_media_classes=(search_classes or _SEARCH_CLASSES)
        if can_search
        else None,
    )
    if children and children_media_class is None:
        directory.calculate_children_class()
    return directory


def catalog_node(catalog_id: str) -> str:
    """Return the browse node of a catalogue."""
    return f"{NODE_CATALOG}{quote(catalog_id, safe='')}"


def live_group_node(group: str) -> str:
    """Return the browse node of a Live TV channel group."""
    return f"{NODE_LIVE_GROUP}{quote(group, safe='')}"


async def _soft[T](call: Awaitable[T], what: str) -> T | None:
    """Return the answer, or None when the server cannot give it.

    For what phase 2 added: a server without it answers 403 or 404, and that
    must not break the films and shows around it.
    """
    try:
        return await call
    except NookError as err:
        LOGGER.debug("No %s from nook: %s", what, err)
        return None


async def _async_root(coordinator: NookCoordinator) -> BrowseMedia:
    """Return the root; Live TV, Radio and Music only when they hold something."""
    client = coordinator.client
    channels, stations, home = await asyncio.gather(
        _soft(coordinator.async_get_channels(), "channels"),
        _soft(client.async_get_radio_favorites(), "radio favourites"),
        _soft(client.async_get_music_home(), "music"),
    )
    children = [
        _directory(node, title, children_media_class=child_class)
        for node, title, child_class in _ROOT_NODES
    ]
    search_classes = list(_SEARCH_CLASSES)
    if channels:
        children.append(
            _directory(NODE_LIVE, TITLE_LIVE, children_media_class=MediaClass.CHANNEL)
        )
        search_classes.append(MediaClass.CHANNEL)
    if stations is not None:
        # Radio search reaches TuneIn even without a favourite.
        search_classes.append(MediaClass.MUSIC)
    if stations:
        children.append(
            _directory(NODE_RADIO, TITLE_RADIO, children_media_class=MediaClass.MUSIC)
        )
    if home is not None and home.track_count > 0:
        children.append(
            _directory(
                NODE_MUSIC, TITLE_MUSIC, children_media_class=MediaClass.DIRECTORY
            )
        )
        search_classes.extend(_MUSIC_SEARCH_CLASSES)
    return _directory(
        NODE_ROOT,
        "nook",
        children=children,
        children_media_class=MediaClass.DIRECTORY,
        can_search=True,
        search_classes=search_classes,
    )


async def async_browse(
    coordinator: NookCoordinator, media_content_id: str | None
) -> BrowseMedia:
    """Return one node of the browse tree."""
    if not media_content_id or media_content_id in (ROOT_ID, BROWSE_PREFIX.rstrip("/")):
        return await _async_root(coordinator)
    try:
        if media_content_id.startswith(MUSIC_PREFIX):
            target = parse_music_id(media_content_id)
            if target.is_list:
                return await _async_browse_music_list(coordinator.client, target)
        elif media_content_id.startswith(BROWSE_PREFIX):
            node = media_content_id[len(BROWSE_PREFIX) :]
            return await _async_browse_node(coordinator, node, media_content_id)
    except NookError as err:
        raise BrowseError(
            translation_domain=DOMAIN,
            translation_key="browse_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    except ServiceValidationError:
        pass
    raise _invalid_browse_id(media_content_id)


def _invalid_browse_id(media_content_id: str) -> BrowseError:
    return BrowseError(
        translation_domain=DOMAIN,
        translation_key="invalid_media_id",
        translation_placeholders={"media_id": media_content_id},
    )


async def _async_browse_node(
    coordinator: NookCoordinator, node: str, media_content_id: str
) -> BrowseMedia:
    client = coordinator.client
    if node == NODE_CW:
        children = _cw_leaves(await client.async_get_continue_watching())
        return _list_directory(node, children)
    if node == NODE_WATCHLIST:
        children = _item_leaves(await client.async_get_watchlist(), mixed=True)
        return _list_directory(node, children)
    if node == NODE_FAVORITES:
        children = _item_leaves(await client.async_get_favorites(), mixed=True)
        return _list_directory(node, children)
    if node in (NODE_MOVIES, NODE_SHOWS):
        wanted = TYPE_MOVIE if node == NODE_MOVIES else TYPE_SERIES
        item_class = MediaClass.MOVIE if node == NODE_MOVIES else MediaClass.TV_SHOW
        # hidden_from_home only keeps a catalogue's titles out of Continue
        # Watching; the catalogue itself is browsed like any other (see
        # MetaCatalogDto in the server's MetaApiDtos.kt). A "mixed" catalogue
        # holds both kinds, so it is listed under both.
        catalogs = [
            catalog
            for catalog in await client.async_get_catalogs()
            if catalog.type in (wanted, TYPE_MIXED)
        ]
        return _directory(
            node,
            _LIST_TITLES[node],
            children=[
                _directory(
                    catalog_node(catalog.id),
                    catalog.name,
                    children_media_class=item_class,
                )
                for catalog in catalogs
            ],
            children_media_class=MediaClass.DIRECTORY,
        )
    if node.startswith(NODE_CATALOG) and len(node) > len(NODE_CATALOG):
        catalog_id = unquote(node[len(NODE_CATALOG) :])
        catalogs, items = await asyncio.gather(
            client.async_get_catalogs(), client.async_get_catalog_items(catalog_id)
        )
        catalog = next((entry for entry in catalogs if entry.id == catalog_id), None)
        item_class = (
            MediaClass.TV_SHOW
            if catalog is not None and catalog.type == TYPE_SERIES
            else MediaClass.MOVIE
        )
        children = _item_leaves(items, mixed=False)
        return _directory(
            node,
            catalog.name if catalog is not None else catalog_id,
            children=children,
            children_media_class=None if children else item_class,
        )
    if node == NODE_RADIO:
        stations = await client.async_get_radio_favorites()
        return _media_directory(
            node, TITLE_RADIO, [_station_leaf(s) for s in stations], MediaClass.MUSIC
        )
    found: BrowseMedia | None = None
    if node == NODE_LIVE or node.startswith(f"{NODE_LIVE}/"):
        found = await _async_browse_live(coordinator, node)
    elif node == NODE_MUSIC or node.startswith(f"{NODE_MUSIC}/"):
        found = await _async_browse_music(client, node)
    if found is None:
        raise _invalid_browse_id(media_content_id)
    return found


def _list_directory(node: str, children: list[BrowseMedia]) -> BrowseMedia:
    return _directory(
        node,
        _LIST_TITLES[node],
        children=children,
        children_media_class=None if children else MediaClass.VIDEO,
    )


def _media_directory(
    node: str, title: str, children: list[BrowseMedia], child_class: MediaClass
) -> BrowseMedia:
    return _directory(
        node,
        title,
        children=children,
        children_media_class=None if children else child_class,
    )


# ── Live TV ────────────────────────────────────────────────────────────────


def channel_title(channel: NookChannel) -> str:
    """Return "5 · HRT 1 · Dnevnik": number, name and the programme on now."""
    title = (
        f"{channel.number} · {channel.name}"
        if channel.number is not None
        else channel.name
    )
    return f"{title} · {channel.now_title}" if channel.now_title else title


def _channel_leaf(channel: NookChannel) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.CHANNEL,
        media_content_id=ChannelTarget(channel.id).media_content_id,
        media_content_type=MediaType.CHANNEL,
        title=channel_title(channel),
        can_play=True,
        can_expand=False,
        thumbnail=channel.logo_url,
    )


def _channel_groups(channels: Iterable[NookChannel]) -> list[str]:
    """Return the channel groups in the order the TV lists their first channel."""
    return list(dict.fromkeys(c.group for c in channels if c.group))


async def _async_browse_live(
    coordinator: NookCoordinator, node: str
) -> BrowseMedia | None:
    channels = await coordinator.async_get_channels()
    groups = _channel_groups(channels)
    other_title = "Other" if groups else "All channels"
    if node == NODE_LIVE:
        favorites = any(c.favorite for c in channels)
        ungrouped = any(c.group is None for c in channels)
        if not favorites and not groups:
            # One folder would hold every channel: list them here instead.
            return _channel_directory(node, TITLE_LIVE, channels)
        children = []
        if favorites:
            children.append(
                _directory(
                    NODE_LIVE_FAVORITES,
                    "Favourites",
                    children_media_class=MediaClass.CHANNEL,
                )
            )
        children.extend(
            _directory(
                live_group_node(group), group, children_media_class=MediaClass.CHANNEL
            )
            for group in groups
        )
        if ungrouped:
            children.append(
                _directory(
                    NODE_LIVE_OTHER,
                    other_title,
                    children_media_class=MediaClass.CHANNEL,
                )
            )
        return _directory(
            node,
            TITLE_LIVE,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )
    if node == NODE_LIVE_FAVORITES:
        return _channel_directory(
            node, "Favourites", [c for c in channels if c.favorite]
        )
    if node == NODE_LIVE_OTHER:
        return _channel_directory(
            node, other_title, [c for c in channels if c.group is None]
        )
    if node.startswith(NODE_LIVE_GROUP) and len(node) > len(NODE_LIVE_GROUP):
        group = unquote(node[len(NODE_LIVE_GROUP) :])
        return _channel_directory(
            node, group, [c for c in channels if c.group == group]
        )
    return None


def _channel_directory(
    node: str, title: str, channels: Iterable[NookChannel]
) -> BrowseMedia:
    return _media_directory(
        node, title, [_channel_leaf(c) for c in channels], MediaClass.CHANNEL
    )


# ── Radio ──────────────────────────────────────────────────────────────────


def _station_leaf(station: NookStation) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.MUSIC,
        media_content_id=StationTarget(station.id, station.name).media_content_id,
        media_content_type=MediaType.MUSIC,
        title=station.name,
        can_play=True,
        can_expand=False,
        thumbnail=station.logo_url,
    )


# ── Music ──────────────────────────────────────────────────────────────────


def _music_list_item(
    target: MusicTarget, title: str, thumbnail: str | None
) -> BrowseMedia:
    """Return an album, playlist, artist or the liked songs: play it, or open it."""
    return BrowseMedia(
        media_class=target.media_class,
        media_content_id=target.media_content_id,
        media_content_type=target.media_type,
        title=title,
        can_play=True,
        can_expand=True,
        children_media_class=MediaClass.TRACK,
        thumbnail=thumbnail,
    )


def _album_item(album: NookAlbum) -> BrowseMedia:
    title = f"{album.title} · {album.artist}" if album.artist else album.title
    return _music_list_item(MusicTarget(MUSIC_ALBUM, album.id), title, album.image_url)


def _artist_item(artist: NookArtist) -> BrowseMedia:
    return _music_list_item(
        MusicTarget(MUSIC_ARTIST, artist.id), artist.name, artist.image_url
    )


def _playlist_item(playlist: NookPlaylist) -> BrowseMedia:
    return _music_list_item(
        MusicTarget(MUSIC_PLAYLIST, playlist.id), playlist.name, playlist.image_url
    )


def _liked_item() -> BrowseMedia:
    return _music_list_item(MusicTarget(MUSIC_LIKED), TITLE_LIKED, None)


def _shuffle_leaf() -> BrowseMedia:
    target = MusicTarget(MUSIC_SHUFFLE)
    return BrowseMedia(
        media_class=target.media_class,
        media_content_id=target.media_content_id,
        media_content_type=target.media_type,
        title="Shuffle all",
        can_play=True,
        can_expand=False,
    )


def _song_title(track: NookTrack, context: str | None) -> str:
    """Return a song's title as its list shows it."""
    if context == MUSIC_ALBUM:
        return (
            f"{track.track_number}. {track.title}"
            if track.track_number
            else track.title
        )
    other = track.album if context == MUSIC_ARTIST else track.artist
    return f"{track.title} · {other}" if other else track.title


def _track_leaf(track: NookTrack, within: MusicTarget | None = None) -> BrowseMedia:
    """Return a song, played from its list; without one, from its album.

    A song without a list or an album is listed, but cannot be played.
    """
    if within is None and track.album_id:
        within = MusicTarget(MUSIC_ALBUM, track.album_id)
        context = None
    else:
        context = within.kind if within is not None else None
    target = within.with_song(track.id) if within is not None else None
    return BrowseMedia(
        media_class=MediaClass.TRACK,
        media_content_id=(
            target.media_content_id if target else track_media_id(track.id)
        ),
        media_content_type=MediaType.TRACK,
        title=_song_title(track, context),
        can_play=target is not None,
        can_expand=False,
        thumbnail=track.image_url,
    )


def _with_more[T](
    children: list[BrowseMedia],
    page: NookPage[T],
    offset: int,
    node: str,
    child_class: MediaClass,
) -> list[BrowseMedia]:
    """Add a "More" folder when the server holds more than this page."""
    next_offset = offset + len(page.items)
    if page.items and next_offset < page.total:
        children.append(
            _directory(
                f"{node}/{next_offset}", TITLE_MORE, children_media_class=child_class
            )
        )
    return children


def _paged_node(node: str, base: str) -> int | None:
    """Return the offset of base or base/<offset>, or None when node is neither."""
    if node == base:
        return 0
    prefix = f"{base}/"
    if node.startswith(prefix) and node[len(prefix) :].isdigit():
        return int(node[len(prefix) :])
    return None


async def _async_browse_music(client: NookClient, node: str) -> BrowseMedia | None:
    if node in (
        NODE_MUSIC,
        NODE_MUSIC_RECENTLY_PLAYED,
        NODE_MUSIC_RECENTLY_ADDED,
        NODE_MUSIC_TOP_ARTISTS,
    ):
        home = await client.async_get_music_home()
        if node == NODE_MUSIC_RECENTLY_PLAYED:
            return _media_directory(
                node,
                _MUSIC_TITLES[node],
                [_track_leaf(t) for t in home.recently_played],
                MediaClass.TRACK,
            )
        if node == NODE_MUSIC_RECENTLY_ADDED:
            return _media_directory(
                node,
                _MUSIC_TITLES[node],
                [_album_item(a) for a in home.recently_added if a.track_count > 0],
                MediaClass.ALBUM,
            )
        if node == NODE_MUSIC_TOP_ARTISTS:
            return _media_directory(
                node,
                _MUSIC_TITLES[node],
                [_artist_item(a) for a in home.top_artists if a.track_count > 0],
                MediaClass.ARTIST,
            )
        children: list[BrowseMedia] = []
        if any(p.track_count > 0 for p in home.playlists):
            children.append(
                _directory(
                    NODE_MUSIC_PLAYLISTS,
                    _MUSIC_TITLES[NODE_MUSIC_PLAYLISTS],
                    children_media_class=MediaClass.PLAYLIST,
                )
            )
        if home.liked_track_count > 0:
            children.append(_liked_item())
        for sub, items, child_class in (
            (NODE_MUSIC_RECENTLY_PLAYED, home.recently_played, MediaClass.TRACK),
            (NODE_MUSIC_RECENTLY_ADDED, home.recently_added, MediaClass.ALBUM),
            (NODE_MUSIC_TOP_ARTISTS, home.top_artists, MediaClass.ARTIST),
        ):
            if items:
                children.append(
                    _directory(
                        sub, _MUSIC_TITLES[sub], children_media_class=child_class
                    )
                )
        if home.track_count > 0:
            children.extend(
                (
                    _directory(
                        NODE_MUSIC_ALBUMS,
                        _MUSIC_TITLES[NODE_MUSIC_ALBUMS],
                        children_media_class=MediaClass.ALBUM,
                    ),
                    _directory(
                        NODE_MUSIC_ARTISTS,
                        _MUSIC_TITLES[NODE_MUSIC_ARTISTS],
                        children_media_class=MediaClass.ARTIST,
                    ),
                    _shuffle_leaf(),
                )
            )
        return _directory(
            node,
            TITLE_MUSIC,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )
    if node == NODE_MUSIC_PLAYLISTS:
        playlists = await client.async_get_playlists()
        return _media_directory(
            node,
            _MUSIC_TITLES[node],
            [_playlist_item(p) for p in playlists if p.track_count > 0],
            MediaClass.PLAYLIST,
        )
    if (offset := _paged_node(node, NODE_MUSIC_ALBUMS)) is not None:
        albums = await client.async_get_albums(offset=offset, limit=CATALOG_PAGE_LIMIT)
        children = [_album_item(a) for a in albums.items if a.track_count > 0]
        return _media_directory(
            node,
            _MUSIC_TITLES[NODE_MUSIC_ALBUMS],
            _with_more(children, albums, offset, NODE_MUSIC_ALBUMS, MediaClass.ALBUM),
            MediaClass.ALBUM,
        )
    if (offset := _paged_node(node, NODE_MUSIC_ARTISTS)) is not None:
        artists = await client.async_get_artists(
            offset=offset, limit=CATALOG_PAGE_LIMIT
        )
        children = [_artist_item(a) for a in artists.items if a.track_count > 0]
        return _media_directory(
            node,
            _MUSIC_TITLES[NODE_MUSIC_ARTISTS],
            _with_more(
                children, artists, offset, NODE_MUSIC_ARTISTS, MediaClass.ARTIST
            ),
            MediaClass.ARTIST,
        )
    if (offset := _paged_node(node, NODE_MUSIC_LIKED)) is not None:
        return await _async_liked_page(client, offset, node)
    return None


async def _async_liked_page(client: NookClient, offset: int, node: str) -> BrowseMedia:
    liked = MusicTarget(MUSIC_LIKED)
    page = await client.async_get_liked_tracks(offset=offset, limit=CATALOG_PAGE_LIMIT)
    children = _with_more(
        [_track_leaf(t, liked) for t in page.items],
        page,
        offset,
        NODE_MUSIC_LIKED,
        MediaClass.TRACK,
    )
    return _media_directory(node, TITLE_LIKED, children, MediaClass.TRACK)


async def _async_browse_music_list(
    client: NookClient, target: MusicTarget
) -> BrowseMedia:
    """Open an album, playlist, artist or the liked songs: its songs, in order."""
    if target.kind == MUSIC_LIKED:
        page = await client.async_get_liked_tracks(offset=0, limit=CATALOG_PAGE_LIMIT)
        title: str = TITLE_LIKED
        children = _with_more(
            [_track_leaf(t, target) for t in page.items],
            page,
            0,
            NODE_MUSIC_LIKED,
            MediaClass.TRACK,
        )
    else:
        fetch = {
            MUSIC_ALBUM: client.async_get_album,
            MUSIC_PLAYLIST: client.async_get_playlist,
            MUSIC_ARTIST: client.async_get_artist,
        }[target.kind]
        found: NookTrackList = await fetch(target.id or "")
        title = found.name or target.id or target.kind
        children = [_track_leaf(t, target) for t in found.tracks]
    return BrowseMedia(
        media_class=target.media_class,
        media_content_id=target.media_content_id,
        media_content_type=target.media_type,
        title=title,
        can_play=True,
        can_expand=True,
        children=children,
        children_media_class=MediaClass.TRACK,
    )


# ── Search ─────────────────────────────────────────────────────────────────


def _match_channels(channels: Iterable[NookChannel], text: str) -> list[NookChannel]:
    """Return the channels whose number is text, then those whose name holds it."""
    wanted = text.casefold()
    by_number = [c for c in channels if c.number is not None and str(c.number) == text]
    by_name = [
        c for c in channels if wanted in c.name.casefold() and c not in by_number
    ]
    return by_number + by_name


async def async_search(
    coordinator: NookCoordinator, query: SearchMediaQuery
) -> SearchMedia:
    """Search films and shows, then channels, radio and music.

    LOCAL and ONLINE hits stay separate entries. Only the sources that can give
    a wanted media class are asked; a source the server lacks is left out.
    """
    client = coordinator.client
    text = query.search_query.strip()
    wanted = set(query.media_filter_classes or ())

    def asks(*classes: MediaClass) -> bool:
        return not wanted or not wanted.isdisjoint(classes)

    async def films_and_shows() -> list[BrowseMedia]:
        if not asks(MediaClass.MOVIE, MediaClass.TV_SHOW):
            return []
        try:
            items = await client.async_search(query.search_query)
        except NookError as err:
            raise SearchError(
                translation_domain=DOMAIN,
                translation_key="search_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        return _item_leaves(items, mixed=True)

    async def channels() -> list[BrowseMedia]:
        if not text or not asks(MediaClass.CHANNEL):
            return []
        found = await _soft(coordinator.async_get_channels(), "channels")
        return [_channel_leaf(c) for c in _match_channels(found or [], text)]

    async def radio() -> list[BrowseMedia]:
        if not text or not asks(MediaClass.MUSIC):
            return []
        favorites, found = await asyncio.gather(
            _soft(client.async_get_radio_favorites(), "radio favourites"),
            _soft(client.async_search_radio(text), "radio search"),
        )
        wanted_name = text.casefold()
        stations = [s for s in favorites or [] if wanted_name in s.name.casefold()]
        seen = {s.id for s in stations}
        stations.extend(s for s in found or [] if s.id not in seen)
        return [_station_leaf(s) for s in stations]

    async def music() -> list[BrowseMedia]:
        if not text or not asks(*_MUSIC_SEARCH_CLASSES):
            return []
        found = await _soft(client.async_search_music(text), "music search")
        if found is None:
            return []
        return [
            *(_artist_item(a) for a in found.artists if a.track_count > 0),
            *(_album_item(a) for a in found.albums if a.track_count > 0),
            *(_track_leaf(t) for t in found.tracks),
        ]

    parts = await asyncio.gather(films_and_shows(), channels(), radio(), music())
    results = [result for part in parts for result in part]
    if wanted:
        results = [result for result in results if result.media_class in wanted]
    return SearchMedia(result=results)
