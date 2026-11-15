"""Diagnostics."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant

from .conftest import API_KEY, BASE_URL, TV_LIVING, FakeNook, now_playing, session


async def test_diagnostics_redacts_key(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    patch_client: FakeNook,
    hass_client: ClientSessionGenerator,
) -> None:
    """The API key is redacted; TVs and sessions are listed."""
    patch_client.sessions = [session()]
    patch_client.now_playing = [now_playing(device_id=TV_LIVING)]
    await init_integration.runtime_data.async_refresh()

    diag = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
    assert diag["entry"]["data"] == {CONF_URL: BASE_URL, CONF_API_KEY: "**REDACTED**"}
    assert API_KEY not in str(diag)
    assert {tv["id"] for tv in diag["tvs"]} == {TV_LIVING, "tv-bedroom"}
    assert diag["sessions"][0]["content_id"] == "tt1160419"
    assert diag["now_playing"][0]["kind"] == "music"
