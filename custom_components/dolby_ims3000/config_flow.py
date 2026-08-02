"""Config and options flow for the Dolby IMS3000."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import IMSClient, IMSConnectionError, IMSError
from .const import (
    CONF_ALLOW_CONTROL,
    CONF_CATALOG_INTERVAL,
    CONF_POSITION_UNIT,
    CONF_TIMEOUT,
    DEFAULT_CATALOG_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    POSITION_UNITS,
    POSITION_UNIT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_ALLOW_CONTROL, default=True): cv.boolean,
    }
)


async def _async_probe(host: str, port: int) -> dict[str, Any]:
    """Connect and read identity.  Raises IMSError on failure."""
    client = IMSClient(host=host, port=port, timeout=DEFAULT_TIMEOUT)
    try:
        info = await client.product_info()
        if not info.get("product_name"):
            # Reachable but not speaking our dialect.
            raise IMSError("no product information returned")
        return info
    finally:
        await client.disconnect()


class DolbyIMS3000ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]

            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})

            try:
                info = await _async_probe(host, port)
            except IMSConnectionError:
                errors["base"] = "cannot_connect"
            except IMSError:
                errors["base"] = "invalid_response"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error probing %s:%s", host, port)
                errors["base"] = "unknown"
            else:
                serial = info.get("product_serial")
                if serial:
                    await self.async_set_unique_id(str(serial))
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: host, CONF_PORT: port}
                    )
                title = user_input.get(CONF_NAME) or info.get("product_name") or DEFAULT_NAME
                return self.async_create_entry(
                    title=title,
                    data={CONF_HOST: host, CONF_PORT: port},
                    options={
                        CONF_ALLOW_CONTROL: user_input[CONF_ALLOW_CONTROL],
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        CONF_CATALOG_INTERVAL: DEFAULT_CATALOG_INTERVAL,
                        CONF_TIMEOUT: DEFAULT_TIMEOUT,
                        CONF_POSITION_UNIT: POSITION_UNIT_SECONDS,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the host to be changed without losing entity history."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _async_probe(user_input[CONF_HOST], user_input[CONF_PORT])
            except IMSConnectionError:
                errors["base"] = "cannot_connect"
            except IMSError:
                errors["base"] = "invalid_response"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): cv.string,
                    vol.Optional(
                        CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)
                    ): cv.port,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DolbyIMS3000OptionsFlow()


class DolbyIMS3000OptionsFlow(OptionsFlow):
    """Tune polling and control behaviour after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ALLOW_CONTROL,
                    default=options.get(CONF_ALLOW_CONTROL, True),
                ): cv.boolean,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=2, max=300, step=1, mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Required(
                    CONF_CATALOG_INTERVAL,
                    default=options.get(CONF_CATALOG_INTERVAL, DEFAULT_CATALOG_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=500, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_TIMEOUT,
                    default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=60, step=1, mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Required(
                    CONF_POSITION_UNIT,
                    default=options.get(CONF_POSITION_UNIT, POSITION_UNIT_SECONDS),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=POSITION_UNITS,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="position_unit",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
