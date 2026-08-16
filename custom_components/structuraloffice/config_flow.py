"""Config flow for StructuralOffice."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers import selector

from .const import (
    CONF_CATCH_UP_HOURS,
    CONF_COMPANY_ADDRESS,
    CONF_COMPANY_EMAIL,
    CONF_COMPANY_NAME,
    CONF_DEFAULT_PAYMENT_TERM_DAYS,
    CONF_DEFAULT_REMINDER_TIME,
    CONF_NOTIFY_TARGETS,
    CONF_SEPA_DATE_AS_DUE_DATE,
    DEFAULT_CATCH_UP_HOURS,
    DEFAULT_COMPANY_ADDRESS,
    DEFAULT_COMPANY_EMAIL,
    DEFAULT_COMPANY_NAME,
    DEFAULT_PAYMENT_TERM_DAYS,
    DEFAULT_REMINDER_TIME,
    DEFAULT_SEPA_DATE_AS_DUE_DATE,
    DOMAIN,
    NAME,
)


def _options_schema(values: dict[str, Any]) -> vol.Schema:
    """Build the configuration schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_NOTIFY_TARGETS,
                default=values.get(CONF_NOTIFY_TARGETS, []),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="notify", multiple=True)
            ),
            vol.Required(
                CONF_DEFAULT_REMINDER_TIME,
                default=values.get(CONF_DEFAULT_REMINDER_TIME, DEFAULT_REMINDER_TIME),
            ): selector.TimeSelector(),
            vol.Required(
                CONF_CATCH_UP_HOURS,
                default=values.get(CONF_CATCH_UP_HOURS, DEFAULT_CATCH_UP_HOURS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=168,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEFAULT_PAYMENT_TERM_DAYS,
                default=values.get(
                    CONF_DEFAULT_PAYMENT_TERM_DAYS, DEFAULT_PAYMENT_TERM_DAYS
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=365,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SEPA_DATE_AS_DUE_DATE,
                default=values.get(
                    CONF_SEPA_DATE_AS_DUE_DATE, DEFAULT_SEPA_DATE_AS_DUE_DATE
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_COMPANY_NAME,
                default=values.get(CONF_COMPANY_NAME, DEFAULT_COMPANY_NAME),
            ): str,
            vol.Optional(
                CONF_COMPANY_ADDRESS,
                default=values.get(CONF_COMPANY_ADDRESS, DEFAULT_COMPANY_ADDRESS),
            ): str,
            vol.Optional(
                CONF_COMPANY_EMAIL,
                default=values.get(CONF_COMPANY_EMAIL, DEFAULT_COMPANY_EMAIL),
            ): str,
        }
    )


class StructuralOfficeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for StructuralOffice."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the single StructuralOffice config entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title=NAME, data={}, options=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_options_schema({}),
        )

    @staticmethod
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        """Return the options flow."""
        return StructuralOfficeOptionsFlow()


class StructuralOfficeOptionsFlow(OptionsFlow):
    """Handle StructuralOffice options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage StructuralOffice options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
        )
