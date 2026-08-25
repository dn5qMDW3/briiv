"""Constants for the Briiv integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    EntityCategory,
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

# Which transport a config entry uses. Local entries talk to one device over
# UDP; a cloud entry covers every device on a Briiv account.
CONF_CONNECTION: Final = "connection"
CONNECTION_LOCAL: Final = "local"
CONNECTION_CLOUD: Final = "cloud"

# The sign-in code Briiv emails; CONF_EMAIL comes from homeassistant.const.
CONF_CODE: Final = "code"
# The name of a config entry key, not a credential in itself.
CONF_REFRESH_TOKEN: Final = "refresh_token"  # noqa: S105

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

    value_fn: Callable[[Any], StateType | datetime] = lambda value: value


# A standard Briiv has no sensor hardware. Unlike the cloud, which omits these
# fields for one, the local broadcast still carries every field the Pro sends
# but fills them with zeros, and carbon dioxide with its 400 default. Creating
# these for a standard unit would give a row of sensors that only ever read
# zero, with the carbon dioxide one looking like a real measurement.
PRO_ONLY_SENSOR_KEYS: Final = frozenset(
    {"temp", "humid", "pm1", "pm2_5", "pm4", "pm10", "voc", "co", "nox"}
)

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


def _mean_samples(value: Any) -> float | None:
    """Average the comma separated samples the cloud sends for a gas reading."""
    if isinstance(value, (int, float)):
        return round(float(value), 1)
    if not isinstance(value, str):
        return None

    samples = []
    for part in value.split(","):
        try:
            samples.append(float(part))
        except ValueError:
            continue
    if not samples:
        return None
    return round(sum(samples) / len(samples), 1)


def _cloud_deadline(value: Any) -> datetime | None:
    """Convert a cloud timestamp to a datetime, tolerating seconds or millis."""
    if not value:
        return None
    try:
        moment = float(value)
    except (TypeError, ValueError):
        return None
    # The cloud mixes the two: boostEnd looks like seconds, filter timestamps
    # are milliseconds. Anything past the year 5138 in seconds is millis.
    if moment > 1e11:
        moment /= 1000

    deadline = dt_util.utc_from_timestamp(moment)
    # The device keeps the last boost's end time after it has passed. Reporting
    # a stale moment as though a boost were pending is worse than nothing.
    if deadline <= dt_util.utcnow():
        return None
    return deadline


# Sensors for a cloud entry.
#
# The cloud payload names fields differently from the local broadcast. A "D"
# prefix marks the current reading; the unprefixed name holds a comma joined
# history of the same measurement, so the D fields are what a sensor wants.
#
# The four particulate fields spell out their size: Po=one, Pt=two (2.5),
# Pf=four, Pe=ten. DPo reads consistently lower than the other three, which is
# what PM1 should do, and the history arrays agree.
#
# Only a device that is online sends readings at all; an offline one reports
# just its filters and firmware. Sensors are created for every device
# regardless, and report nothing until their device is heard from.
# Readings only the Pro's sensor suite produces. A standard Briiv has no such
# hardware and never sends these, so creating them for one would leave a row of
# entities that can never have a value.
CLOUD_AIR_QUALITY_KEYS: Final = frozenset(
    {"DTe", "DHu", "DPo", "DPt", "DPf", "DPe", "DCo", "DVo", "DNo"}
)

CLOUD_SENSOR_TYPES: tuple[BriivSensorEntityDescription, ...] = (
    BriivSensorEntityDescription(
        key="DTe",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="DHu",
        translation_key="humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="DPo",
        translation_key="pm1",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="DPt",
        translation_key="pm2_5",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="DPf",
        translation_key="pm4",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM4,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="DPe",
        translation_key="pm10",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM10,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="DCo",
        translation_key="carbon_dioxide",
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        device_class=SensorDeviceClass.CO2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BriivSensorEntityDescription(
        key="DVo",
        translation_key="voc",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    BriivSensorEntityDescription(
        key="DNo",
        translation_key="nitrogen_oxides",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    BriivSensorEntityDescription(
        key="coconutFilter",
        translation_key="coconut_filter",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    BriivSensorEntityDescription(
        key="matrixFilter",
        translation_key="matrix_filter",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    BriivSensorEntityDescription(
        key="mossFilter",
        translation_key="moss_filter",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    BriivSensorEntityDescription(
        key="boostEnd",
        translation_key="boost_end_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_cloud_deadline,
    ),
    # Signal strength as the service reports it, roughly zero to four bars, so
    # it carries no unit and no device class. It is deliberately not used to
    # decide availability: the account can be hours behind the device, and a
    # stale zero would hide a purifier that is working perfectly well.
    BriivSensorEntityDescription(
        key="wifi",
        translation_key="wifi_signal",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
)
