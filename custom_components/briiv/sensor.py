"""Support for Briiv sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BriivConfigEntry
from .const import SENSOR_TYPES, BriivSensorEntityDescription
from .entity import BriivEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BriivConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Briiv sensors."""
    async_add_entities(BriivSensor(entry, description) for description in SENSOR_TYPES)


class BriivSensor(BriivEntity, SensorEntity):
    """Representation of a Briiv sensor."""

    entity_description: BriivSensorEntityDescription

    def __init__(
        self, entry: BriivConfigEntry, description: BriivSensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(entry)
        self.entity_description = description
        self._attr_unique_id = f"{self._serial}_{description.key}"

    async def _handle_update(self, data: dict[str, Any]) -> None:
        """Handle updated data from device."""
        if (value := data.get(self.entity_description.key)) is not None:
            self._attr_native_value = self.entity_description.value_fn(value)
            self.async_write_ha_state()
