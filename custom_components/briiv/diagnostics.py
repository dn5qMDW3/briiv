"""Diagnostics support for the Briiv integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_HOST
from homeassistant.core import HomeAssistant

from . import BriivConfigEntry, is_cloud_entry
from .const import CONF_REFRESH_TOKEN, CONF_SERIAL_NUMBER

TO_REDACT = {
    CONF_HOST,
    CONF_SERIAL_NUMBER,
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    "host",
    # Cloud payload fields that identify the device or the home network.
    "Ip",
    "Link Code",
    "id",
    "serialNumber",
    "thingName",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BriivConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    if is_cloud_entry(entry):
        return _cloud_diagnostics(entry)
    return _local_diagnostics(entry)


def _local_diagnostics(entry: BriivConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a purifier reached over the local network."""
    api = entry.runtime_data

    return {
        "entry": {
            "connection": "local",
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "unique_id_set": entry.unique_id is not None,
        },
        "device": {
            "available": api.available,
            "seconds_since_last_packet": api.seconds_since_last_packet,
            "last_data": async_redact_data(api.last_data or {}, TO_REDACT),
        },
    }


def _cloud_diagnostics(entry: BriivConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a Briiv account reached through the cloud.

    The raw device payloads are included, with identifying values removed but
    field names intact. The cloud uses different field names from the local
    broadcast and they are not all catalogued yet, so this is how the remaining
    sensors get mapped.
    """
    coordinator = entry.runtime_data

    return {
        "entry": {
            "connection": "cloud",
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "unique_id_set": entry.unique_id is not None,
        },
        "connection": {
            "connected": coordinator.api.connected,
            "device_count": len(coordinator.data or {}),
        },
        "devices": [
            async_redact_data(device, TO_REDACT)
            for device in (coordinator.data or {}).values()
        ],
    }
