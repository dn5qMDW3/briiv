"""Support for Briiv switches."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BriivConfigEntry, is_cloud_entry
from .const import CLOUD_AQI_LIGHT_KEY
from .coordinator import BriivCloudCoordinator
from .entity import BriivCloudEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BriivConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Briiv switches.

    Only a cloud entry has any: the local broadcast carries no light state and
    the firmware accepts only power, fan speed and boost over the network.
    """
    if not is_cloud_entry(entry):
        return

    coordinator = entry.runtime_data
    if TYPE_CHECKING:
        assert isinstance(coordinator, BriivCloudCoordinator)

    async_add_entities(
        BriivCloudLightSwitch(coordinator, serial)
        for serial, device in (coordinator.data or {}).items()
        if CLOUD_AQI_LIGHT_KEY in device
    )


class BriivCloudLightSwitch(BriivCloudEntity, SwitchEntity):
    """The ring of light that shows air quality on the front of a purifier.

    This is a setting on the device rather than a lamp, so it belongs with the
    device's configuration rather than among the lights in the house.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "aqi_light"

    def __init__(self, coordinator: BriivCloudCoordinator, serial: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_cloud_aqi_light"

    @property
    def is_on(self) -> bool | None:
        """Return whether the light is lit, or nothing if not yet reported."""
        value = self.device.get(CLOUD_AQI_LIGHT_KEY)
        if value is None:
            return None
        return bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Light the ring."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the ring off."""
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        """Ask the service to change the light.

        The device reports back on its own schedule, which can be the better
        part of an hour, so the switch keeps showing the old state until the
        change is confirmed rather than pretending it has already happened.
        """
        await self.coordinator.api.async_update_device(
            self._serial, {CLOUD_AQI_LIGHT_KEY: int(on)}
        )
