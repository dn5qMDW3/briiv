"""Support for Briiv fan."""

from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING, Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BriivConfigEntry, is_cloud_entry
from .const import PRESET_MODE_BOOST
from .coordinator import BriivCloudCoordinator
from .entity import BriivCloudEntity, BriivEntity

# The firmware only accepts these fan speeds, expressed as a percentage.
SPEED_STEP = 25
DEFAULT_SPEED = 25


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BriivConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Briiv fan based on config entry."""
    if is_cloud_entry(entry):
        coordinator = entry.runtime_data
        if TYPE_CHECKING:
            assert isinstance(coordinator, BriivCloudCoordinator)
        async_add_entities(
            BriivCloudFan(coordinator, serial) for serial in coordinator.data or {}
        )
        return

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


class BriivCloudFan(BriivCloudEntity, FanEntity):
    """A purifier's fan, controlled through the Briiv cloud.

    Boost is deliberately not offered here. The cloud reports when a boost ends
    but the field that starts one has not been confirmed, and guessing it would
    mean sending the service a command that may not mean what we intend. Boost
    is available on a local entry, and can be added once the field is known.
    """

    _attr_name = None
    _attr_speed_count = 100 // SPEED_STEP
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: BriivCloudCoordinator, serial: str) -> None:
        """Initialize the fan."""
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_cloud_fan"

    @property
    def _fan_speed(self) -> int:
        """Return the speed the cloud last reported, as a percentage."""
        try:
            return int(float(self.device.get("fanSpeed", 0)))
        except (TypeError, ValueError):
            return 0

    @property
    def is_on(self) -> bool:
        """Return whether the fan is running."""
        return self._fan_speed > 0

    @property
    def percentage(self) -> int:
        """Return the current speed percentage."""
        return self._fan_speed

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed, rounding up to one the firmware accepts."""
        speed = (
            0
            if percentage == 0
            else min(100, ceil(percentage / SPEED_STEP) * SPEED_STEP)
        )
        await self.coordinator.api.async_update_device(
            self._serial, {"fanSpeed": speed}
        )

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on."""
        await self.async_set_percentage(percentage or DEFAULT_SPEED)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        await self.coordinator.api.async_update_device(self._serial, {"fanSpeed": 0})
