"""Constants for the nook integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "nook"
LOGGER = logging.getLogger(__package__)

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_LINKED_REMOTES: Final = "linked_remotes"

DEFAULT_SCAN_INTERVAL: Final = 10
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 120

REQUEST_TIMEOUT: Final = 15

# Backends as the nook server names them. LOCAL = Jellyfin, ONLINE = Stremio.
BACKEND_JELLYFIN: Final = "jellyfin"
BACKEND_STREMIO: Final = "stremio"
BACKENDS: Final = (BACKEND_JELLYFIN, BACKEND_STREMIO)

SOURCE_LOCAL: Final = "local"
SOURCE_ONLINE: Final = "online"
SOURCE_TO_BACKEND: Final = {
    SOURCE_LOCAL: BACKEND_JELLYFIN,
    SOURCE_ONLINE: BACKEND_STREMIO,
}
BACKEND_LABEL: Final = {
    BACKEND_JELLYFIN: "Local",
    BACKEND_STREMIO: "Online",
}

TYPE_MOVIE: Final = "movie"
TYPE_SERIES: Final = "series"
TYPE_EPISODE: Final = "episode"
# A catalogue that holds films and shows (catalogues only, never a title).
TYPE_MIXED: Final = "mixed"
PLAYABLE_TYPES: Final = (TYPE_MOVIE, TYPE_SERIES)

DEVICE_TYPE_TV: Final = "TV"
DEVICE_STATUS_PAIRED: Final = "PAIRED"

# Media ids: nook://<backend>/<type>/<sourceId> and nook://browse/<node>.
MEDIA_ID_SCHEME: Final = "nook://"
BROWSE_PREFIX: Final = "nook://browse/"

# What the linked Android TV Remote entity launches on turn_on.
NOOK_OPEN_LINK: Final = "nook://open"

# Waking an offline TV through its linked remote before a play.
WAKE_TIMEOUT: Final = 20.0
WAKE_POLL_INTERVAL: Final = 2.0

CATALOG_PAGE_LIMIT: Final = 100

SERVICE_PLAY_TITLE: Final = "play_title"
ATTR_TITLE: Final = "title"
ATTR_TYPE: Final = "type"
ATTR_YEAR: Final = "year"
ATTR_SOURCE: Final = "source"
