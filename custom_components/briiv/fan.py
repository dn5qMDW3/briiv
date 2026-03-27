"""Support for Briiv fan."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BriivConfigEntry
from .api import BriivAPI
from .const import DOMAIN, LOGGER, PRESET_MODE_BOOST


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BriivConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Briiv fan based on config entry."""
    async_add_entities([BriivFan(entry.runtime_data, entry.data["serial_number"])])


class BriivFan(FanEntity):
    """Representation of a Briiv fan."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_preset_modes = [PRESET_MODE_BOOST]
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, api: BriivAPI, serial_number: str) -> None:
        """Initialize the fan."""
        self._api = api
        self._serial = serial_number
        self._attr_unique_id = serial_number
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial_number)},
            name=f"Briiv {serial_number}",
            manufacturer="Briiv",
            model="Air Filter",
        )
        self._attr_is_on = False
        self._attr_percentage = None
        self._attr_preset_mode = None
        self._fan_speed = 0

    async def async_added_to_hass(self) -> None:
        """Register callback when entity is added to hass."""
        self._api.register_callback(self._handle_update)

    async def _handle_update(self, data: dict[str, Any]) -> None:
        """Handle updated data from device."""
        update_state = False

        if "power" in data:
            power_state = bool(data["power"])
            if power_state != self._attr_is_on:
                self._attr_is_on = power_state
                if not power_state:
                    self._attr_percentage = 0
                update_state = True

        if "fan_speed" in data:
            new_speed = data["fan_speed"]
            if new_speed != self._fan_speed:
                self._fan_speed = new_speed
                self._attr_percentage = 0 if new_speed == 0 else new_speed
                update_state = True

        if "boost" in data:
            boost_active = bool(data["boost"])
            if boost_active:
                self._attr_preset_mode = PRESET_MODE_BOOST
                self._attr_is_on = True
                self._attr_percentage = 100
            else:
                self._attr_preset_mode = None
            update_state = True

        if update_state:
            self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan."""
        if percentage is None or percentage == 0:
            await self.async_turn_off()
            return

        if percentage <= 25:
            firmware_speed = 25
        elif percentage <= 50:
            firmware_speed = 50
        elif percentage <= 75:
            firmware_speed = 75
        else:
            firmware_speed = 100

        if not self._attr_is_on:
            await self._api.set_power(True)

        if self._attr_preset_mode == PRESET_MODE_BOOST:
            await self._api.set_boost(False)
            self._attr_preset_mode = None

        await self._api.set_fan_speed(firmware_speed)
        self._attr_percentage = firmware_speed
        self._fan_speed = firmware_speed
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        await self._api.set_power(True)
        self._attr_is_on = True

        if preset_mode == PRESET_MODE_BOOST:
            await self._api.set_boost(True)
            self._attr_preset_mode = PRESET_MODE_BOOST
            self._attr_percentage = 100
        elif percentage is not None:
            await self.async_set_percentage(percentage)
        else:
            await self._api.set_fan_speed(25)
            self._fan_speed = 25
            self._attr_percentage = 25

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        if (
            self._attr_preset_mode != PRESET_MODE_BOOST
            and self._attr_percentage is not None
        ):
            await self._api.set_fan_speed(self._attr_percentage)
            self._fan_speed = self._attr_percentage

        if self._attr_preset_mode == PRESET_MODE_BOOST:
            await self._api.set_boost(False)
            self._attr_preset_mode = None

        await self._api.set_power(False)
        self._attr_is_on = False
        self._attr_percentage = 0
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        if preset_mode == PRESET_MODE_BOOST:
            if not self._attr_is_on:
                await self._api.set_power(True)
            await self._api.set_boost(True)
            self._attr_preset_mode = PRESET_MODE_BOOST
            self._attr_is_on = True
            self._attr_percentage = 100
        elif self._attr_preset_mode == PRESET_MODE_BOOST:
            await self._api.set_boost(False)
            self._attr_preset_mode = None
            if self._fan_speed > 0:
                await self._api.set_fan_speed(self._fan_speed)

        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Remove callback when entity is being removed."""
        self._api.remove_callback(self._handle_update)
