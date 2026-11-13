"""A small client for the nook server API (the /api/me surface an API key reaches)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

from .const import (
    CATALOG_PAGE_LIMIT,
    DEVICE_STATUS_PAIRED,
    DEVICE_TYPE_TV,
    LOGGER,
    REQUEST_TIMEOUT,
)


class NookError(Exception):
    """Base error of the nook client."""


class NookConnectionError(NookError):
    """The server could not be reached, timed out, or failed (5xx)."""

    def __init__(self, message: str, *, blocked_by_proxy: bool = False) -> None:
        """Initialize the error."""
        super().__init__(message)
        self.blocked_by_proxy = blocked_by_proxy


class NookAuthError(NookError):
    """The API key was refused (401), or its scope does not reach the endpoint."""


class NookOfflineError(NookError):
    """The target TV is offline (409 on a play)."""


class NookNotFoundError(NookError):
    """The server does not know the device or item (404)."""


class NookResponseError(NookError):
    """Any other unexpected answer (a 4xx, or a body that is not JSON)."""

    def __init__(self, status: int, message: str) -> None:
        """Initialize the error."""
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


@dataclass(frozen=True, slots=True)
class NookDevice:
    """One of the key user's devices (GET /me/devices)."""

    id: str
    name: str
    device_type: str
    status: str
    online: bool
    last_seen_at: int | None

    @property
    def is_paired_tv(self) -> bool:
        """Return True for a paired TV, the only kind that becomes an entity."""
        return (
            self.device_type == DEVICE_TYPE_TV and self.status == DEVICE_STATUS_PAIRED
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NookDevice | None:
        """Parse a device, or None when it lacks an id."""
        if not (device_id := _str(data.get("id"))):
            return None
        return cls(
            id=device_id,
            name=_str(data.get("name")) or device_id,
            device_type=_str(data.get("deviceType")) or "",
            status=_str(data.get("status")) or "",
            online=data.get("online") is True,
            last_seen_at=_int(data.get("lastSeenAt")),
        )


@dataclass(frozen=True, slots=True)
class NookSession:
    """A live VOD playback session on a device (GET /me/playback/sessions)."""

    device_id: str
    device_name: str
    backend: str
    content_id: str
    type: str
    series_imdb_id: str | None
    title: str | None
    position_ms: int
    duration_ms: int | None
    state: str
    updated_at: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NookSession | None:
        """Parse a session, or None when it lacks the device or content."""
        device_id = _str(data.get("deviceId"))
        content_id = _str(data.get("contentId"))
        if not device_id or not content_id:
            return None
        return cls(
            device_id=device_id,
            device_name=_str(data.get("deviceName")) or device_id,
            backend=_str(data.get("backend")) or "",
            content_id=content_id,
            type=_str(data.get("type")) or "",
            series_imdb_id=_str(data.get("seriesImdbId")),
            title=_str(data.get("title")),
            position_ms=_int(data.get("positionMs")) or 0,
            duration_ms=_int(data.get("durationMs")),
            state=_str(data.get("state")) or "",
            updated_at=_int(data.get("updatedAt")) or 0,
        )


@dataclass(frozen=True, slots=True)
class NookItem:
    """A film or show card (MetaItemDto)."""

    id: str
    backend: str
    source_id: str
    type: str
    title: str
    year: int | None
    poster_url: str | None
    backdrop_url: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NookItem | None:
        """Parse an item, or None when a required field is missing."""
        item_id = _str(data.get("id"))
        backend = _str(data.get("backend"))
        source_id = _str(data.get("sourceId"))
        item_type = _str(data.get("type"))
        title = _str(data.get("title"))
        if not (item_id and backend and source_id and item_type and title):
            return None
        return cls(
            id=item_id,
            backend=backend,
            source_id=source_id,
            type=item_type,
            title=title,
            year=_int(data.get("year")),
            poster_url=_str(data.get("posterUrl")),
            backdrop_url=_str(data.get("backdropUrl")),
        )


@dataclass(frozen=True, slots=True)
class NookCwItem:
    """A Continue Watching entry (CwItemDto); an episode carries its series id."""

    backend: str
    id: str
    type: str
    title: str
    subtitle: str | None
    poster_url: str | None
    series_id: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NookCwItem | None:
        """Parse an entry, or None when a required field is missing."""
        backend = _str(data.get("backend"))
        item_id = _str(data.get("id"))
        item_type = _str(data.get("type"))
        title = _str(data.get("title"))
        if not (backend and item_id and item_type and title):
            return None
        return cls(
            backend=backend,
            id=item_id,
            type=item_type,
            title=title,
            subtitle=_str(data.get("subtitle")),
            poster_url=_str(data.get("posterUrl")),
            series_id=_str(data.get("seriesId")),
        )


@dataclass(frozen=True, slots=True)
class NookCatalog:
    """A catalogue (MetaCatalogDto)."""

    id: str
    backend: str
    name: str
    type: str
    item_count: int
    hidden_from_home: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NookCatalog | None:
        """Parse a catalogue, or None when it lacks an id."""
        if not (catalog_id := _str(data.get("id"))):
            return None
        return cls(
            id=catalog_id,
            backend=_str(data.get("backend")) or "",
            name=_str(data.get("name")) or catalog_id,
            type=_str(data.get("type")) or "",
            item_count=_int(data.get("itemCount")) or 0,
            hidden_from_home=data.get("hiddenFromHome") is True,
        )


def _parse_list[T](
    payload: Any, key: str, parse: Callable[[dict[str, Any]], T | None]
) -> list[T]:
    raw = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    return [
        parsed for entry in raw if isinstance(entry, dict) and (parsed := parse(entry))
    ]


class NookClient:
    """Talks to one nook server with one API key."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, api_key: str
    ) -> None:
        """Initialize the client. base_url is the server root, without /api."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @property
    def base_url(self) -> str:
        """Return the server root."""
        return self._base_url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request and return the decoded JSON body."""
        url = URL(f"{self._base_url}/api{path}", encoded=True)
        if params:
            url = url.with_query(params)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                text = await resp.text()
        except TimeoutError as err:
            raise NookConnectionError(f"Timed out talking to {self._base_url}") from err
        except aiohttp.ClientError as err:
            raise NookConnectionError(f"Cannot reach {self._base_url}: {err}") from err

        if status >= 400:
            self._raise_for_status(status, text)
        if not text.strip():
            return {}
        try:
            return json.loads(text)
        except ValueError as err:
            raise NookResponseError(status, "the answer is not JSON") from err

    def _raise_for_status(self, status: int, text: str) -> None:
        """Map an error answer onto a client exception."""
        error: dict[str, Any] = {}
        try:
            decoded = json.loads(text)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            error = decoded
        message = _str(error.get("error")) or text.strip()[:200] or f"HTTP {status}"

        if status == 401:
            raise NookAuthError(message)
        if status == 403:
            if error.get("code") == "API_KEY_SCOPE":
                raise NookAuthError(
                    f"The API key's scope does not allow this: {message}"
                )
            if "error code: 1010" in text:
                LOGGER.warning(
                    "A proxy in front of %s (Cloudflare error 1010) blocked this client",
                    self._base_url,
                )
                raise NookConnectionError(
                    "A proxy in front of the server blocked this client (error 1010)",
                    blocked_by_proxy=True,
                )
            raise NookResponseError(status, message)
        if status == 404:
            raise NookNotFoundError(message)
        if status == 409:
            # Any 409 on a play means the TV is offline. The code that names it
            # has moved between fields, so the status alone decides.
            raise NookOfflineError(message)
        if status >= 500:
            raise NookConnectionError(f"Server error {status}: {message}")
        raise NookResponseError(status, message)

    async def async_get_devices(self) -> list[NookDevice]:
        """Return every device of the key's user."""
        payload = await self._request("GET", "/me/devices")
        return _parse_list(payload, "items", NookDevice.from_dict)

    async def async_get_sessions(self) -> list[NookSession]:
        """Return the live VOD sessions of the user's other devices."""
        payload = await self._request("GET", "/me/playback/sessions")
        return _parse_list(payload, "sessions", NookSession.from_dict)

    async def async_play(
        self,
        device_id: str,
        backend: str,
        source_id: str,
        media_type: str,
        *,
        resume: bool = True,
    ) -> dict[str, Any]:
        """Push a film or show to a TV (the server's Play on...)."""
        payload = await self._request(
            "POST",
            f"/me/play-targets/{quote(device_id, safe='')}/play",
            body={
                "backend": backend,
                "sourceId": source_id,
                "type": media_type,
                "resume": resume,
            },
        )
        return payload if isinstance(payload, dict) else {}

    async def async_search(self, query: str) -> list[NookItem]:
        """Search the library: LOCAL hits first, then ONLINE."""
        payload = await self._request("GET", "/me/search", params={"q": query})
        return _parse_list(payload, "items", NookItem.from_dict)

    async def async_get_continue_watching(self) -> list[NookCwItem]:
        """Return Continue Watching."""
        payload = await self._request("GET", "/me/cw")
        return _parse_list(payload, "items", NookCwItem.from_dict)

    async def async_get_watchlist(self) -> list[NookItem]:
        """Return the watchlist, resolved to cards."""
        payload = await self._request("GET", "/me/vod/watchlist/resolved")
        return _parse_list(payload, "items", NookItem.from_dict)

    async def async_get_favorites(self) -> list[NookItem]:
        """Return the favourites, resolved to cards."""
        payload = await self._request("GET", "/me/vod/favorites/resolved")
        return _parse_list(payload, "items", NookItem.from_dict)

    async def async_get_catalogs(self) -> list[NookCatalog]:
        """Return the catalogues the user can browse."""
        payload = await self._request("GET", "/me/library")
        return _parse_list(payload, "catalogs", NookCatalog.from_dict)

    async def async_get_catalog_items(
        self, catalog_id: str, *, offset: int = 0, limit: int = CATALOG_PAGE_LIMIT
    ) -> list[NookItem]:
        """Return one page of a catalogue."""
        payload = await self._request(
            "GET",
            f"/me/library/{quote(catalog_id, safe='')}",
            params={"offset": offset, "limit": max(1, min(limit, 200))},
        )
        return _parse_list(payload, "items", NookItem.from_dict)

    async def async_get_item(self, item_id: str) -> NookItem | None:
        """Return one title's card (GET /me/item/{id})."""
        payload = await self._request("GET", f"/me/item/{quote(item_id, safe='')}")
        raw = payload.get("item") if isinstance(payload, dict) else None
        return NookItem.from_dict(raw) if isinstance(raw, dict) else None
