# Briiv Air Purifier Integration for Home Assistant

[![HACS Validate](https://github.com/dn5qMDW3/briiv/actions/workflows/validate.yml/badge.svg)](https://github.com/dn5qMDW3/briiv/actions/workflows/validate.yml)

Custom Home Assistant integration for [Briiv](https://briiv.com/) air purifiers. Communicates locally over UDP -- no cloud required.

Originally created by [@FiveCreate](https://github.com/FiveCreate) ([Briiv_HA](https://github.com/FiveCreate/Briiv_HA)).

## Features

- **Fan control**: Power on/off, fan speed (25/50/75/100%), and boost mode
- **Sensors**: Temperature, humidity, PM1, PM2.5, PM4, PM10, CO2, VOC index, NOx index, and boost end time
- **Auto-discovery**: Finds Briiv devices on your local network
- **Manual setup**: Configure by IP address and serial number
- **Reconfigurable**: Change a device's IP address without removing it
- **Availability tracking**: Entities go unavailable if a device stops broadcasting
- **Diagnostics**: Downloadable diagnostics for troubleshooting
- Supports both Briiv and Briiv Pro models

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu in the top right and select **Custom repositories**
3. Add this repository URL with category **Integration**
4. Search for "Briiv" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/briiv` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for "Briiv"
3. The integration will discover devices automatically, or you can configure manually with the device IP and serial number

## Requirements

- Home Assistant 2026.1.0 or newer
- Briiv air purifier on the same local network as Home Assistant
- UDP port 3334 must be accessible between Home Assistant and the Briiv device

## Troubleshooting

The integration listens on UDP port 3334, and needs to bind it exclusively.
If another process on the Home Assistant host is already bound to that port,
setup fails with a retryable error rather than starting up deaf; free the port
and Home Assistant will retry on its own.

Devices on a different subnet will not be discovered, because they announce
themselves by broadcast; discovery only sees the local network segment.

### About the CO2, VOC and NOx readings

The device broadcasts a field named `co`, but it carries carbon dioxide in ppm,
not carbon monoxide. It reads exactly 400, the atmospheric baseline, until the
sensor warms up, and then tracks room air. It is exposed as a CO2 sensor.

`voc` and `nox` are Sensirion gas indices rather than concentrations. They run
from 0 to 500 and have no unit, so they are exposed without a unit or device
class and named "VOC index" and "NOx index".

Versions before 1.2.0 published all three with the wrong types, so their
long term statistics restart after upgrading.
