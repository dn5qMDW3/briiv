"""Constants for the Briiv integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    Platform,
    UnitOfTemperature,
)
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

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

# Field semantics were established by capturing the device's own broadcasts.
#
# "voc" and "nox" are Sensirion gas indices, seen climbing from 0 to their
# working range as the sensor warmed up, so they carry no unit or device class.
#
# "co" is carbon dioxide in ppm despite its name. It sits at exactly 400, the
# atmospheric baseline a CO2 part reports before its first real measurement,
# until the sensor warms up, then tracks room air (~1200 ppm when observed).
# It is not carbon monoxide: that concentration would be acutely dangerous,
# and no reading resembling carbon monoxide appears anywhere in the vendor's
# own app.
#
# "boost_end_time" is an absolute Unix timestamp, constant while boost runs
# rather than counting down, and zero when boost is off. Note this is a
# different clock from "timestamp", which counts milliseconds since boot.


def _boost_deadline(value: float) -> datetime | None:
    """Convert the boost deadline to a datetime, or None when boost is off."""
    return dt_util.utc_from_timestamp(value) if value else None


@dataclass(frozen=True, kw_only=True)
class BriivSensorEntityDescription(SensorEntityDescription):
    """Describes a Briiv sensor, with an optional conversion of the raw value."""

    value_fn: Callable[[float], StateType | datetime] = lambda value: value


SENSOR_TYPES: tuple[BriivSensorEntityDescription, ...] = (
    BriivSensorEntityDescription(
        key="temp",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="humid",
        translation_key="humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="pm1",
        translation_key="pm1",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="pm2_5",
        translation_key="pm2_5",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="pm4",
        translation_key="pm4",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM4,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="pm10",
        translation_key="pm10",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM10,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="voc",
        translation_key="voc",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    BriivSensorEntityDescription(
        key="co",
        translation_key="carbon_dioxide",
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        device_class=SensorDeviceClass.CO2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="nox",
        translation_key="nitrogen_oxides",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    BriivSensorEntityDescription(
        key="boost_end_time",
        translation_key="boost_end_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_boost_deadline,
    ),
)
