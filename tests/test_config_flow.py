"""Config flow, reauth and options."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nook.api import NookAuthError, NookConnectionError
from custom_components.nook.const import CONF_LINKED_REMOTES, CONF_SCAN_INTERVAL, DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import API_KEY, BASE_URL, TV_BEDROOM, TV_LIVING, FakeNook, make_entry


@pytest.fixture
def no_setup():
    """Keep the created entry from setting itself up."""
    with patch("custom_components.nook.async_setup_entry", return_value=True) as mock:
        yield mock


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow_success(
    hass: HomeAssistant, patch_client: FakeNook, no_setup
) -> None:
    """A working key creates the entry; the URL is normalized."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: " https://Nook.Example.com/ ", CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "nook (nook.example.com)"
    assert result["data"] == {
        CONF_URL: "https://Nook.Example.com",
        CONF_API_KEY: API_KEY,
    }
    assert result["result"].unique_id == "nook.example.com"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NookAuthError("401"), "invalid_auth"),
        (NookConnectionError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors_then_recover(
    hass: HomeAssistant,
    patch_client: FakeNook,
    no_setup,
    error: Exception,
    expected: str,
) -> None:
    """An error shows on the form; fixing it finishes the flow."""
    flow_id = await _start(hass)
    patch_client.devices_error = error
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: BASE_URL, CONF_API_KEY: "bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    patch_client.devices_error = None
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: BASE_URL, CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    "url", ["nook.example.com", "ftp://nook.example.com", "https://"]
)
async def test_user_flow_invalid_url(
    hass: HomeAssistant, patch_client: FakeNook, url: str
) -> None:
    """A URL without http(s) and a host is refused before any request."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: url, CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, patch_client: FakeNook
) -> None:
    """One entry per server host."""
    make_entry().add_to_hass(hass)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: "https://nook.example.com/", CONF_API_KEY: "other"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"json": {"error": "unauthorized"}}, "invalid_auth"),
        (403, {"json": {"error": "scope", "code": "API_KEY_SCOPE"}}, "invalid_auth"),
        (403, {"text": "error code: 1010"}, "cannot_connect"),
        (503, {"text": "unavailable"}, "cannot_connect"),
    ],
)
async def test_user_flow_http_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    body: dict,
    expected: str,
) -> None:
    """The real client maps HTTP answers onto the form errors."""
    aioclient_mock.get(f"{BASE_URL}/api/me/devices", status=status, **body)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: BASE_URL, CONF_API_KEY: API_KEY}
    )
    assert result["errors"] == {"base": expected}


async def test_reauth(hass: HomeAssistant, patch_client: FakeNook, no_setup) -> None:
    """Reauth asks for a new key and stores it."""
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    patch_client.devices_error = NookAuthError("still bad")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "still-bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    patch_client.devices_error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "new-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new-key"
    assert entry.data[CONF_URL] == BASE_URL


async def test_options_flow(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Options show a field per TV and store links by device id."""
    hass.states.async_set("remote.bedroom", "off")
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    fields = [str(key) for key in result["data_schema"].schema]
    assert fields == [CONF_SCAN_INTERVAL, "Bedroom", "Living Room"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, "Bedroom": "remote.bedroom"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {
        CONF_SCAN_INTERVAL: 30,
        CONF_LINKED_REMOTES: {TV_BEDROOM: "remote.bedroom"},
    }
    await hass.async_block_till_done()
    # The reload picked the new options up.
    coordinator = init_integration.runtime_data
    assert coordinator.update_interval.total_seconds() == 30
    assert coordinator.linked_remotes == {TV_BEDROOM: "remote.bedroom"}
    assert TV_LIVING not in coordinator.linked_remotes


async def test_options_flow_unlinks(
    hass: HomeAssistant, patch_client: FakeNook
) -> None:
    """Clearing a TV's field removes its link."""
    entry = make_entry(
        {CONF_SCAN_INTERVAL: 10, CONF_LINKED_REMOTES: {TV_BEDROOM: "remote.bedroom"}}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 10}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LINKED_REMOTES] == {}
