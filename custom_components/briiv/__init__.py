"""The Briiv Air Purifier integration.

An entry reaches its devices one of two ways. A local entry listens for a
single purifier's UDP broadcasts, which needs no account and no internet but
only works on the device's own network segment. A cloud entry signs in to a
Briiv account and covers every purifier on it, including from away from home.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BriivAPI, BriivError, DataCallback
from .cloud import BriivCloudAPI
from .const import (
    CLOUD_AIR_QUALITY_KEYS,
    CONF_CONNECTION,
    CONF_IS_PRO,
    CONF_REFRESH_TOKEN,
    CONF_SERIAL_NUMBER,
    CONNECTION_CLOUD,
    DOMAIN,
    LOGGER,
    PLATFORMS,
    PRO_ONLY_SENSOR_KEYS,
)
from .coordinator import BriivCloudCoordinator
from .entity import build_device_info

type BriivConfigEntry = ConfigEntry[BriivAPI | BriivCloudCoordinator]


def is_cloud_entry(entry: ConfigEntry) -> bool:
    """Return whether an entry talks to the Briiv cloud rather than the LAN."""
    return entry.data.get(CONF_CONNECTION) == CONNECTION_CLOUD


async def async_setup_entry(hass: HomeAssistant, entry: BriivConfigEntry) -> bool:
    """Set up Briiv from a config entry."""
    if is_cloud_entry(entry):
        return await _async_setup_cloud(hass, entry)
    return await _async_setup_local(hass, entry)


async def _async_setup_local(hass: HomeAssistant, entry: BriivConfigEntry) -> bool:
    """Set up one purifier reached over the local network."""
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

    if entry.data.get(CONF_IS_PRO) is False:
        _async_prune_sensors(hass, entry.data[CONF_SERIAL_NUMBER], PRO_ONLY_SENSOR_KEYS)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_setup_cloud(hass: HomeAssistant, entry: BriivConfigEntry) -> bool:
    """Set up every purifier on a Briiv account, reached through the cloud."""

    def _store_rotated_token(token: str) -> None:
        """Keep the refreshed sign-in, so no new email code is needed."""
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
        )

    api = BriivCloudAPI(
        async_get_clientsession(hass),
        entry.data[CONF_REFRESH_TOKEN],
        on_token_rotated=_store_rotated_token,
    )
    coordinator = BriivCloudCoordinator(hass, entry, api)

    # Raises ConfigEntryAuthFailed if the sign-in expired, which starts the
    # reauth flow and asks the user for a fresh code.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    for serial, device in (coordinator.data or {}).items():
        if not any(key in device for key in CLOUD_AIR_QUALITY_KEYS):
            _async_prune_sensors(hass, serial, CLOUD_AIR_QUALITY_KEYS, prefix="cloud_")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _async_prune_sensors(
    hass: HomeAssistant,
    serial: str,
    keys: frozenset[str],
    prefix: str = "",
) -> None:
    """Remove sensors a device turns out not to have.

    A device's model is only known once it has been heard from, so entities can
    already exist for readings it cannot produce. Left alone they sit at
    unknown for ever, and one of them reports a carbon dioxide default that
    looks like a real measurement, so they are cleared rather than hidden.
    """
    registry = er.async_get(hass)

    for key in keys:
        entity_id = registry.async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{serial}_{prefix}{key}"
        )
        if entity_id:
            LOGGER.debug("Removing %s: this device does not report %s", entity_id, key)
            registry.async_remove(entity_id)


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
    if not unload_ok:
        return False

    runtime = entry.runtime_data
    if isinstance(runtime, BriivCloudCoordinator):
        await runtime.async_shutdown()
    else:
        await runtime.stop_listening()

    return True
