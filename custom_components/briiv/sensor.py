"""Support for Briiv sensors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BriivConfigEntry, is_cloud_entry
from .const import (
    CLOUD_AIR_QUALITY_KEYS,
    CLOUD_SENSOR_TYPES,
    CONF_IS_PRO,
    PRO_ONLY_SENSOR_KEYS,
    SENSOR_TYPES,
    BriivSensorEntityDescription,
)
from .coordinator import BriivCloudCoordinator
from .entity import BriivCloudEntity, BriivEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BriivConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Briiv sensors."""
    if is_cloud_entry(entry):
        coordinator = entry.runtime_data
        if TYPE_CHECKING:
            assert isinstance(coordinator, BriivCloudCoordinator)
        # Every device reports its fan, filters and boost. The air quality
        # readings come from the Pro's sensor suite, so they are added only for
        # a device that actually sends them.
        async_add_entities(
            BriivCloudSensor(coordinator, serial, description)
            for serial, device in (coordinator.data or {}).items()
            for description in CLOUD_SENSOR_TYPES
            if description.key not in CLOUD_AIR_QUALITY_KEYS
            or any(key in device for key in CLOUD_AIR_QUALITY_KEYS)
        )
        return

    # A standard Briiv reports the sensor fields as zeros rather than omitting
    # them, so go by the model. When it is not yet known, which happens for a
    # manually added device before its first broadcast, create them all and let
    # the reading speak for itself.
    is_pro = entry.data.get(CONF_IS_PRO)
    async_add_entities(
        BriivSensor(entry, description)
        for description in SENSOR_TYPES
        if is_pro is not False or description.key not in PRO_ONLY_SENSOR_KEYS
    )


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


class BriivCloudSensor(BriivCloudEntity, SensorEntity):
    """A sensor for a device reached through the Briiv cloud."""

    entity_description: BriivSensorEntityDescription

    def __init__(
        self,
        coordinator: BriivCloudCoordinator,
        serial: str,
        description: BriivSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, serial)
        self.entity_description = description
        # Distinct from the local entity's id, so configuring both transports
        # for one purifier does not collide.
        self._attr_unique_id = f"{serial}_cloud_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the current reading."""
        value = self.device.get(self.entity_description.key)
        if value is None:
            return None
        return self.entity_description.value_fn(value)
