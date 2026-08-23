"""Diagnostics support for the Briiv integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import BriivConfigEntry
from .const import CONF_SERIAL_NUMBER

TO_REDACT = {CONF_HOST, CONF_SERIAL_NUMBER, "host"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BriivConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    api = entry.runtime_data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "unique_id_set": entry.unique_id is not None,
        },
        "device": {
            "available": api.available,
            "seconds_since_last_packet": api.seconds_since_last_packet,
            "last_data": async_redact_data(api.last_data or {}, TO_REDACT),
        },
    }
