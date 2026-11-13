"""Config flow for nook: server URL + API key, reauth, and options."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Home Assistant 2026.10 and later validate with probatio, earlier releases
# with voluptuous; the two share this API.
try:
    import probatio as vol
except ImportError:  # pragma: no cover
    import voluptuous as vol  # type: ignore[no-redef]
from yarl import URL

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.typing import VolDictType

from .api import NookAuthError, NookClient, NookError
from .const import (
    CONF_LINKED_REMOTES,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): _URL_SELECTOR,
        vol.Required(CONF_API_KEY): _KEY_SELECTOR,
    }
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): _KEY_SELECTOR})


def normalize_url(raw: str) -> str | None:
    """Return the server root without a trailing slash, or None when it is no http(s) URL."""
    url = raw.strip().rstrip("/")
    try:
        parsed = URL(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.host:
        return None
    return url


async def _async_validate(hass: HomeAssistant, url: str, api_key: str) -> str | None:
    """Try the key against GET /me/devices; return an error key, or None when it works."""
    client = NookClient(async_get_clientsession(hass), url, api_key)
    try:
        await client.async_get_devices()
    except NookAuthError:
        return "invalid_auth"
    except NookError as err:
        LOGGER.debug("Cannot connect to %s: %s", url, err)
        return "cannot_connect"
    except Exception:
        LOGGER.exception("Unexpected error while checking %s", url)
        return "unknown"
    return None


class NookConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a nook server."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NookOptionsFlow:
        """Return the options flow."""
        return NookOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the server URL and an API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            if url is None:
                errors[CONF_URL] = "invalid_url"
            else:
                host = (URL(url).host or "").lower()
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                error = await _async_validate(self.hass, url, user_input[CONF_API_KEY])
                if error is None:
                    return self.async_create_entry(
                        title=f"nook ({host})",
                        data={CONF_URL: url, CONF_API_KEY: user_input[CONF_API_KEY]},
                    )
                errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, {CONF_URL: (user_input or {}).get(CONF_URL)}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth when the server refuses the key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _async_validate(
                self.hass, entry.data[CONF_URL], user_input[CONF_API_KEY]
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"url": entry.data[CONF_URL]},
        )


class NookOptionsFlow(OptionsFlowWithReload):
    """Poll interval, and the Android TV Remote entity linked to each TV."""

    def _tv_fields(self) -> dict[str, str]:
        """Return a form field per known TV, named after it: field -> device id."""
        coordinator = getattr(self.config_entry, "runtime_data", None)
        tvs = (
            coordinator.data.tvs if coordinator is not None and coordinator.data else {}
        )
        fields: dict[str, str] = {}
        for tv in sorted(tvs.values(), key=lambda tv: tv.name.casefold()):
            key = tv.name
            if key in fields or key == CONF_SCAN_INTERVAL:
                key = f"{tv.name} ({tv.id[:8]})"
            fields[key] = tv.id
        return fields

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the options."""
        options = self.config_entry.options
        linked: dict[str, str] = dict(options.get(CONF_LINKED_REMOTES, {}))
        fields = self._tv_fields()

        if user_input is not None:
            for key, device_id in fields.items():
                if entity_id := user_input.get(key):
                    linked[device_id] = entity_id
                else:
                    linked.pop(device_id, None)
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_LINKED_REMOTES: linked,
                }
            )

        schema: VolDictType = {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
        }
        for key, device_id in fields.items():
            schema[
                vol.Optional(
                    key, description={"suggested_value": linked.get(device_id)}
                )
            ] = EntitySelector(EntitySelectorConfig(domain="remote"))
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
