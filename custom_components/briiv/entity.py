"""Base entity for the Briiv integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import (
    CONF_IS_PRO,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    MANUFACTURER,
    MODEL_BRIIV,
    MODEL_BRIIV_PRO,
)

if TYPE_CHECKING:
    from . import BriivConfigEntry


def device_model(is_pro: bool | None) -> str:
    """Return the model name for a Briiv device."""
    return MODEL_BRIIV_PRO if is_pro else MODEL_BRIIV


def build_device_info(serial_number: str, is_pro: bool | None) -> DeviceInfo:
    """Return the device registry entry shared by all of a device's entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, serial_number)},
        name=f"{MANUFACTURER} {serial_number}",
        manufacturer=MANUFACTURER,
        model=device_model(is_pro),
        serial_number=serial_number,
    )


class BriivEntity(Entity):
    """Base class for entities backed by a Briiv device."""

    _attr_has_entity_name = True
    # Devices push their state over UDP; nothing is ever polled.
    _attr_should_poll = False

    def __init__(self, entry: BriivConfigEntry) -> None:
        """Initialize the entity."""
        self._api = entry.runtime_data
        self._serial = entry.data[CONF_SERIAL_NUMBER]
        self._attr_device_info = build_device_info(
            self._serial, entry.data.get(CONF_IS_PRO)
        )

    @property
    def available(self) -> bool:
        """Return whether the device has been heard from recently."""
        return self._api.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to device updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self._api.register_callback(self._handle_update))
        self.async_on_remove(
            self._api.register_availability_callback(self.async_write_ha_state)
        )

    async def _handle_update(self, data: dict[str, Any]) -> None:
        """Handle a state broadcast from the device."""
        raise NotImplementedError
