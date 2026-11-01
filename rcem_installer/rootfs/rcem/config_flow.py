"""Config flow for RCEm."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ESTIMATE,
    CONF_EXPORT_ENTITY,
    CONF_USE_CORRECTED,
    CONF_VAT,
    DEFAULT_ESTIMATE,
    DEFAULT_USE_CORRECTED,
    DEFAULT_VAT,
    DOMAIN,
    NAME,
)


def _schema(values: dict[str, Any]) -> vol.Schema:
    export = values.get(CONF_EXPORT_ENTITY)
    return vol.Schema(
        {
            vol.Optional(
                CONF_EXPORT_ENTITY,
                description={"suggested_value": export} if export else None,
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor", device_class=SensorDeviceClass.ENERGY
                )
            ),
            vol.Required(CONF_VAT, default=values.get(CONF_VAT, DEFAULT_VAT)): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=100, step=0.5, unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_USE_CORRECTED,
                default=values.get(CONF_USE_CORRECTED, DEFAULT_USE_CORRECTED),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ESTIMATE, default=values.get(CONF_ESTIMATE, DEFAULT_ESTIMATE)
            ): selector.BooleanSelector(),
        }
    )


class RcemConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the RCEm config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Single step: pick the export energy sensor and VAT."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={}, options=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return RcemOptionsFlow()


class RcemOptionsFlow(OptionsFlow):
    """Change the export sensor, VAT or price handling."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=_schema(dict(self.config_entry.options))
        )
