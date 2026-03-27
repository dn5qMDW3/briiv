"""The Briiv Air Purifier integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import BriivAPI
from .const import CONF_SERIAL_NUMBER, DOMAIN, PLATFORMS

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
    except OSError as err:
        await api.stop_listening()
        raise ConfigEntryNotReady from err

    entry.runtime_data = api
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: BriivConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.stop_listening()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
