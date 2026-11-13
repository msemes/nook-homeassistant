"""Media ids, the media browser tree and search for nook."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
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

from .api import NookClient, NookCwItem, NookError, NookItem
from .const import (
    BACKEND_LABEL,
    BACKENDS,
    BROWSE_PREFIX,
    DOMAIN,
    MEDIA_ID_SCHEME,
    PLAYABLE_TYPES,
    SOURCE_TO_BACKEND,
    TYPE_EPISODE,
    TYPE_MIXED,
    TYPE_MOVIE,
    TYPE_SERIES,
)

DIRECTORY_CONTENT_TYPE = "directory"

NODE_ROOT = "root"
NODE_CW = "cw"
NODE_WATCHLIST = "watchlist"
NODE_FAVORITES = "favorites"
NODE_MOVIES = "movies"
NODE_SHOWS = "shows"
NODE_CATALOG = "catalog/"

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


def _invalid_media_id(media_id: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="invalid_media_id",
        translation_placeholders={"media_id": media_id},
    )


def parse_play_id(media_id: str) -> PlayTarget:
    """Parse nook://<backend>/<movie|series>/<sourceId>, or raise."""
    if media_id.startswith(BROWSE_PREFIX):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_playable",
            translation_placeholders={"media_id": media_id},
        )
    if not media_id.startswith(MEDIA_ID_SCHEME):
        raise _invalid_media_id(media_id)
    # The source id may hold ":" or "/" (tmdb:697620), so split only twice.
    parts = media_id[len(MEDIA_ID_SCHEME) :].split("/", 2)
    if len(parts) != 3:
        raise _invalid_media_id(media_id)
    backend, media_type, source_id = parts
    if backend not in BACKENDS or media_type not in PLAYABLE_TYPES or not source_id:
        raise _invalid_media_id(media_id)
    return PlayTarget(backend=backend, type=media_type, source_id=source_id)


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
        search_media_classes=_SEARCH_CLASSES if can_search else None,
    )
    if children and children_media_class is None:
        directory.calculate_children_class()
    return directory


def catalog_node(catalog_id: str) -> str:
    """Return the browse node of a catalogue."""
    return f"{NODE_CATALOG}{quote(catalog_id, safe='')}"


def _root() -> BrowseMedia:
    return _directory(
        NODE_ROOT,
        "nook",
        children=[
            _directory(node, title, children_media_class=child_class)
            for node, title, child_class in _ROOT_NODES
        ],
        children_media_class=MediaClass.DIRECTORY,
        can_search=True,
    )


async def async_browse(client: NookClient, media_content_id: str | None) -> BrowseMedia:
    """Return one node of the browse tree."""
    if not media_content_id or media_content_id in (ROOT_ID, BROWSE_PREFIX.rstrip("/")):
        return _root()
    if not media_content_id.startswith(BROWSE_PREFIX):
        raise BrowseError(
            translation_domain=DOMAIN,
            translation_key="invalid_media_id",
            translation_placeholders={"media_id": media_content_id},
        )
    node = media_content_id[len(BROWSE_PREFIX) :]
    try:
        return await _async_browse_node(client, node, media_content_id)
    except NookError as err:
        raise BrowseError(
            translation_domain=DOMAIN,
            translation_key="browse_failed",
            translation_placeholders={"error": str(err)},
        ) from err


async def _async_browse_node(
    client: NookClient, node: str, media_content_id: str
) -> BrowseMedia:
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
    raise BrowseError(
        translation_domain=DOMAIN,
        translation_key="invalid_media_id",
        translation_placeholders={"media_id": media_content_id},
    )


def _list_directory(node: str, children: list[BrowseMedia]) -> BrowseMedia:
    return _directory(
        node,
        _LIST_TITLES[node],
        children=children,
        children_media_class=None if children else MediaClass.VIDEO,
    )


async def async_search(client: NookClient, query: SearchMediaQuery) -> SearchMedia:
    """Search the library; LOCAL and ONLINE hits stay separate entries."""
    try:
        items = await client.async_search(query.search_query)
    except NookError as err:
        raise SearchError(
            translation_domain=DOMAIN,
            translation_key="search_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    results = _item_leaves(items, mixed=True)
    if query.media_filter_classes:
        wanted = set(query.media_filter_classes)
        results = [result for result in results if result.media_class in wanted]
    return SearchMedia(result=results)
