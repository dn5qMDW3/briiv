"""Support for Briiv fan."""

from __future__ import annotations

from math import ceil
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BriivConfigEntry
from .const import PRESET_MODE_BOOST
from .entity import BriivEntity

# The firmware only accepts these fan speeds, expressed as a percentage.
SPEED_STEP = 25
DEFAULT_SPEED = 25


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BriivConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Briiv fan based on config entry."""
    async_add_entities([BriivFan(entry)])


class BriivFan(BriivEntity, FanEntity):
    """Representation of a Briiv fan."""

    _attr_name = None
    _attr_preset_modes = [PRESET_MODE_BOOST]
    _attr_speed_count = 100 // SPEED_STEP
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, entry: BriivConfigEntry) -> None:
        """Initialize the fan."""
        super().__init__(entry)
        self._attr_unique_id = self._serial
        self._attr_is_on = False
        self._attr_percentage = 0
        self._attr_preset_mode = None
        self._fan_speed = 0

    async def _handle_update(self, data: dict[str, Any]) -> None:
        """Handle updated data from device."""
        changed = False

        if "power" in data:
            power_state = bool(data["power"])
            if power_state != self._attr_is_on:
                self._attr_is_on = power_state
                if not power_state:
                    self._attr_percentage = 0
                changed = True

        if "fan_speed" in data:
            new_speed = int(data["fan_speed"])
            if new_speed != self._fan_speed:
                self._fan_speed = new_speed
                # Boost pins the fan at full speed, so leave the reported
                # percentage alone until boost ends.
                if self._attr_preset_mode != PRESET_MODE_BOOST:
                    self._attr_percentage = new_speed if self._attr_is_on else 0
                changed = True

        if "boost" in data:
            boost_active = bool(data["boost"])
            if boost_active != (self._attr_preset_mode == PRESET_MODE_BOOST):
                if boost_active:
                    self._attr_preset_mode = PRESET_MODE_BOOST
                    self._attr_is_on = True
                    self._attr_percentage = 100
                else:
                    self._attr_preset_mode = None
                    self._attr_percentage = self._fan_speed if self._attr_is_on else 0
                changed = True

        if changed:
            self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan."""
        if percentage == 0:
            await self.async_turn_off()
            return

        firmware_speed = min(100, ceil(percentage / SPEED_STEP) * SPEED_STEP)

        if not self._attr_is_on:
            await self._api.set_power(True)

        if self._attr_preset_mode == PRESET_MODE_BOOST:
            await self._api.set_boost(False)
            self._attr_preset_mode = None

        await self._api.set_fan_speed(firmware_speed)
        self._fan_speed = firmware_speed
        self._attr_percentage = firmware_speed
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
            return

        if percentage is not None:
            await self.async_set_percentage(percentage)
            return

        await self._api.set_power(True)
        await self._api.set_fan_speed(DEFAULT_SPEED)
        self._fan_speed = DEFAULT_SPEED
        self._attr_percentage = DEFAULT_SPEED
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        if self._attr_preset_mode == PRESET_MODE_BOOST:
            await self._api.set_boost(False)
            self._attr_preset_mode = None

        await self._api.set_power(False)
        self._attr_is_on = False
        self._attr_percentage = 0
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        self._valid_preset_mode_or_raise(preset_mode)

        if not self._attr_is_on:
            await self._api.set_power(True)

        await self._api.set_boost(True)
        self._attr_preset_mode = PRESET_MODE_BOOST
        self._attr_is_on = True
        self._attr_percentage = 100
        self.async_write_ha_state()
