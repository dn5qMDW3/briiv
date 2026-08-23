"""Constants for the Briiv integration."""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    PERCENTAGE,
    Platform,
    UnitOfTemperature,
    UnitOfTime,
)

DOMAIN: Final = "briiv"
LOGGER = logging.getLogger(__package__)

DEFAULT_PORT: Final = 3334
DISCOVERY_DURATION: Final = 15
# Once a device has answered, stop discovery early if nothing new shows up.
DISCOVERY_SETTLE_TIME: Final = 3

# Devices broadcast their state unprompted. Entities are marked unavailable
# when nothing has been heard from a device for this long.
DEVICE_TIMEOUT: Final = 180

CONF_SERIAL_NUMBER: Final = "serial_number"
CONF_IS_PRO: Final = "is_pro"

MANUFACTURER: Final = "Briiv"
MODEL_BRIIV: Final = "Briiv"
MODEL_BRIIV_PRO: Final = "Briiv Pro"

PLATFORMS: Final = [Platform.FAN, Platform.SENSOR]

PRESET_MODE_BOOST: Final = "boost"

# The firmware's only sensor driver is a Sensirion SEN5x (main/sensors/SEN5X in
# the vendor firmware), which reports VOC and NOx as unitless indices from 1 to
# 500 rather than densities, so those two carry no device class or unit.
SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="temp",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="humid",
        translation_key="humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pm1",
        translation_key="pm1",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pm2_5",
        translation_key="pm2_5",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pm4",
        translation_key="pm4",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM4,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pm10",
        translation_key="pm10",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM10,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="voc",
        translation_key="voc",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="co",
        translation_key="carbon_monoxide",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.CO,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="nox",
        translation_key="nitrogen_oxides",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="boost_end_time",
        translation_key="boost_end_time",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)
