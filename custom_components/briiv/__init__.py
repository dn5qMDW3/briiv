"""The Briiv Air Purifier integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .api import BriivAPI, BriivError, DataCallback
from .const import CONF_IS_PRO, CONF_SERIAL_NUMBER, LOGGER, PLATFORMS
from .entity import build_device_info

type BriivConfigEntry = ConfigEntry[BriivAPI]


async def async_setup_entry(hass: HomeAssistant, entry: BriivConfigEntry) -> bool:
    """Set up Briiv from a config entry."""
    api = BriivAPI(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        serial_number=entry.data[CONF_SERIAL_NUMBER],
    )

    try:
        await api.start_listening(hass.loop)
    except (BriivError, OSError) as err:
        await api.stop_listening()
        raise ConfigEntryNotReady(
            f"Could not start listening for Briiv broadcasts: {err}"
        ) from err

    entry.runtime_data = api
    entry.async_on_unload(api.register_callback(_make_model_listener(hass, entry)))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _make_model_listener(hass: HomeAssistant, entry: BriivConfigEntry) -> DataCallback:
    """Build a callback that keeps the stored device model in sync.

    Whether a device is a Pro is only known once it broadcasts, and manually
    added devices start out without that flag, so correct it on the first
    packet that reports it.
    """

    async def _async_update_model(data: dict[str, Any]) -> None:
        if "is_briiv_pro" not in data:
            return

        is_pro = bool(data["is_briiv_pro"])
        if entry.data.get(CONF_IS_PRO) == is_pro:
            return

        serial = entry.data[CONF_SERIAL_NUMBER]
        LOGGER.debug("Updating stored model for %s (is_pro=%s)", serial, is_pro)

        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_IS_PRO: is_pro}
        )
        dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            **build_device_info(serial, is_pro),
        )

    return _async_update_model


async def async_unload_entry(hass: HomeAssistant, entry: BriivConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.stop_listening()
    return unload_ok
