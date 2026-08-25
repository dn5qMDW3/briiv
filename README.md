# Briiv Air Purifier Integration for Home Assistant

[![HACS Validate](https://github.com/dn5qMDW3/briiv/actions/workflows/validate.yml/badge.svg)](https://github.com/dn5qMDW3/briiv/actions/workflows/validate.yml)

Custom Home Assistant integration for [Briiv](https://briiv.com/) air purifiers. Communicates locally over UDP -- no cloud required.

Originally created by [@FiveCreate](https://github.com/FiveCreate) ([Briiv_HA](https://github.com/FiveCreate/Briiv_HA)).

## Features

- **Two ways to connect**: locally over UDP (no account, no cloud) or through a Briiv account, for purifiers on a different subnet
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
3. Choose how to connect:

**On this network** (recommended) — discovers purifiers broadcasting on your
LAN, or add one manually by IP address and serial number. Needs no account and
no internet, but only sees devices on the same network segment.

**Briiv account** — sign in with the email address you use in the Briiv app.
Briiv emails a 6 digit code; enter it and every purifier on the account is
added. Use this when the purifiers sit on a different subnet from Home
Assistant, so their broadcasts never reach it.

Both can be used together: a purifier added twice appears as one device in Home
Assistant, with a separate set of entities per connection.

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

### Signing in again

The Briiv account sign-in is passwordless, so there is no password to store.
Home Assistant keeps the refresh token and renews the session silently. If that
token eventually expires, Home Assistant asks you to sign in again and Briiv
emails a fresh code. Each code is only valid for a few minutes.

### Cloud entities

A cloud connection exposes the fan, temperature, humidity, PM1, PM2.5, PM4,
PM10, CO2, VOC index, NOx index, the three filters (coconut, matrix and moss)
and the boost end time.

Readings only arrive while a purifier is connected to wifi. An offline one
still reports its filters, so its sensors are shown as unavailable rather than
presenting a stale reading as current.

Boost is available on a local connection only. The cloud reports when a boost
ends but the field that starts one has not been confirmed, and sending a
guessed command is not worth the risk.

### About the CO2, VOC and NOx readings

The device broadcasts a field named `co`, but it carries carbon dioxide in ppm,
not carbon monoxide. It reads exactly 400, the atmospheric baseline, until the
sensor warms up, and then tracks room air. It is exposed as a CO2 sensor.

`voc` and `nox` are Sensirion gas indices rather than concentrations. They run
from 0 to 500 and have no unit, so they are exposed without a unit or device
class and named "VOC index" and "NOx index".

Versions before 1.2.0 published all three with the wrong types, so their
long term statistics restart after upgrading.
