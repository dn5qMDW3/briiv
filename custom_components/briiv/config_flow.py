"""Config flow for Briiv integration."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_PORT
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import BriivAPI, BriivError
from .const import (
    CONF_IS_PRO,
    CONF_SERIAL_NUMBER,
    DEFAULT_PORT,
    DISCOVERY_DURATION,
    DOMAIN,
    LOGGER,
    MANUFACTURER,
)
from .entity import device_model

# Sentinel option values, kept distinct from any real serial number.
ACTION_SEARCH_AGAIN = "__search_again__"
ACTION_MANUAL = "__manual__"


class BriivConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Briiv Air Purifier."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices: dict[str, dict[str, Any]] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user initiated discovery."""
        if user_input is not None:
            selected = user_input[CONF_DEVICE]

            if selected == ACTION_MANUAL:
                return await self.async_step_manual()

            if selected != ACTION_SEARCH_AGAIN:
                return await self._async_create_discovered_entry(selected)

        errors: dict[str, str] = {}
        try:
            devices = await BriivAPI.discover(duration=DISCOVERY_DURATION)
        except (BriivError, OSError) as err:
            LOGGER.debug("Discovery error: %s", err)
            errors["base"] = "discovery_error"
        else:
            self._discovered_devices = {
                device["serial_number"]: device for device in devices
            }

        configured = self._async_current_ids()
        options = [
            SelectOptionDict(
                value=serial,
                label=f"{device_model(device['is_pro'])} ({serial})",
            )
            for serial, device in sorted(self._discovered_devices.items())
            if serial not in configured
        ]
        new_devices = len(options)
        options.extend(
            (
                SelectOptionDict(value=ACTION_SEARCH_AGAIN, label="Search again"),
                SelectOptionDict(value=ACTION_MANUAL, label="Manual configuration"),
            )
        )

        already_configured = [
            f"{device_model(device['is_pro'])} ({serial})"
            for serial, device in sorted(self._discovered_devices.items())
            if serial in configured
        ]

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE, default=options[0]["value"]): (
                        SelectSelector(
                            SelectSelectorConfig(
                                options=options, mode=SelectSelectorMode.LIST
                            )
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "new_devices": str(new_devices),
                "configured_devices": "\n".join(
                    f"- {device}" for device in already_configured
                )
                or "None",
            },
        )

    async def _async_create_discovered_entry(self, serial: str) -> ConfigFlowResult:
        """Create an entry for a device found by discovery."""
        device = self._discovered_devices[serial]

        await self.async_set_unique_id(serial)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"{device_model(device['is_pro'])} ({serial})",
            data={
                CONF_HOST: device["host"],
                CONF_PORT: DEFAULT_PORT,
                CONF_SERIAL_NUMBER: serial,
                CONF_IS_PRO: device["is_pro"],
            },
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual device entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            serial = user_input[CONF_SERIAL_NUMBER].strip()

            if not _is_valid_host(host):
                errors[CONF_HOST] = "invalid_host"
            elif not serial:
                errors[CONF_SERIAL_NUMBER] = "invalid_serial"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"{MANUFACTURER} {serial}",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: DEFAULT_PORT,
                        CONF_SERIAL_NUMBER: serial,
                    },
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_SERIAL_NUMBER): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing device."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            if not _is_valid_host(host):
                errors[CONF_HOST] = "invalid_host"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "serial_number": entry.data[CONF_SERIAL_NUMBER],
            },
        )


def _is_valid_host(host: str) -> bool:
    """Return whether the given string is a usable IPv4 address."""
    try:
        ip_address(host)
    except ValueError:
        return False
    return True
