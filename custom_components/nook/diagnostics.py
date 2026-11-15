"""Diagnostics for nook; the API key never leaves Home Assistant."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .coordinator import NookConfigEntry

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NookConfigEntry
) -> dict[str, Any]:
    """Return the entry, its options and the last poll."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "tvs": [asdict(tv) for tv in data.tvs.values()],
        "sessions": [asdict(session) for session in data.sessions.values()],
        "now_playing": [asdict(item) for item in data.now_playing.values()],
    }
