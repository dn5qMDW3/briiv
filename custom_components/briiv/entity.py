"""Base entity for the Briiv integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
    from .coordinator import BriivCloudCoordinator


def device_model(is_pro: bool | None) -> str:
    """Return the model name for a Briiv device."""
    return MODEL_BRIIV_PRO if is_pro else MODEL_BRIIV


def build_device_info(
    serial_number: str, is_pro: bool | None, name: str | None = None
) -> DeviceInfo:
    """Return the device registry entry shared by all of a device's entities.

    The identifier is the serial number for both transports, so a purifier
    added locally and through the cloud appears as one device rather than two.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, serial_number)},
        name=name or f"{MANUFACTURER} {serial_number}",
        manufacturer=MANUFACTURER,
        model=device_model(is_pro),
        serial_number=serial_number,
    )


def cloud_device_name(device: dict[str, Any]) -> str | None:
    """Return the name given to a device in the Briiv app, if it sends one."""
    for key in ("name", "deviceName", "Name", "roomName", "label"):
        value = device.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def cloud_is_pro(device: dict[str, Any]) -> bool | None:
    """Return whether a cloud device reports itself as a Pro."""
    for key in ("isPro", "is_pro", "isBriivPro", "is_briiv_pro"):
        if key in device:
            return bool(device[key])
    return None


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


class BriivCloudEntity(CoordinatorEntity["BriivCloudCoordinator"]):
    """Base class for entities backed by a device reached through the cloud."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BriivCloudCoordinator, serial: str) -> None:
        """Initialize the entity for one device on the account."""
        super().__init__(coordinator)
        self._serial = serial
        device = self.device
        self._attr_device_info = build_device_info(
            serial, cloud_is_pro(device), cloud_device_name(device)
        )
        if firmware := device.get("firmwareVersion"):
            self._attr_device_info["sw_version"] = str(firmware)

    @property
    def device(self) -> dict[str, Any]:
        """Return the latest payload for this device."""
        return (self.coordinator.data or {}).get(self._serial, {})

    @property
    def available(self) -> bool:
        """Return whether the device is currently connected.

        The account keeps reporting a purifier that has dropped off wifi, but
        its readings stop updating, so presenting them as current would be
        misleading. The cloud signals this with a "wifi" field of 0.
        """
        device = self.device
        if not device:
            return False
        if "wifi" in device and not device["wifi"]:
            return False
        return super().available
