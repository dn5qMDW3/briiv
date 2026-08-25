# Briiv Air Purifier Integration for Home Assistant

[![HACS Validate](https://github.com/dn5qMDW3/briiv/actions/workflows/validate.yml/badge.svg)](https://github.com/dn5qMDW3/briiv/actions/workflows/validate.yml)

Custom Home Assistant integration for [Briiv](https://briiv.com/) air purifiers.

Connects locally over UDP, needing no account and no internet. Purifiers on a
different subnet from Home Assistant can be reached through a Briiv account
instead, since their broadcasts never arrive.

Based on [Briiv_HA](https://github.com/FiveCreate/Briiv_HA) by
[@FiveCreate](https://github.com/FiveCreate), which worked out the local UDP
protocol the device speaks. This version has been substantially rewritten
since, and adds the Briiv account connection.

## Features

- **Two ways to connect**: locally over UDP (no account, no cloud) or through a Briiv account, for purifiers on a different subnet
- **Fan control**: Power on/off and fan speed (25/50/75/100%), plus boost mode on a connection on this network
- **Sensors**: Temperature, humidity, PM1, PM2.5, PM4, PM10, CO2, VOC index, NOx index and boost end time, plus filter life for each of the three filters on a Briiv account connection
- **Auto-discovery**: Finds Briiv devices on your local network
- **Manual setup**: Configure by IP address and serial number
- **Reconfigurable**: Change a device's IP address without removing it
- **Availability tracking**: Entities go unavailable when a device stops reporting, rather than showing a stale reading
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

For a connection **on this network**:

- The purifier on the same network segment as Home Assistant
- UDP port 3334 reachable between the two, and free on the Home Assistant host

For a **Briiv account** connection:

- A Briiv account with the purifiers already set up in the Briiv app
- Outbound internet access from Home Assistant

## Troubleshooting

A connection on this network listens on UDP port 3334 and needs to bind it
exclusively. If another process on the Home Assistant host already holds that
port, setup fails with a retryable error rather than starting up deaf; free the
port and Home Assistant will retry on its own. A Briiv account connection does
not use the port at all.

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

The air quality readings come from the Pro's sensor suite. A standard Briiv
has no such hardware, so it gets the fan, the three filters and the boost end
time, and no air quality entities are created for it.

Boost is available on a local connection only. The cloud reports when a boost
ends but the field that starts one has not been confirmed, and sending a
guessed command is not worth the risk.

### About the CO2, VOC and NOx readings

The device reports carbon dioxide in ppm, under a field named `co` locally and
`DCo` through the cloud. Despite the local name it is not carbon monoxide: it
reads exactly 400, the atmospheric baseline, until the sensor warms up, and
then tracks room air. It is exposed as a CO2 sensor.

`voc` and `nox` are Sensirion gas indices rather than concentrations. They run
from 0 to 500 and have no unit, so they are exposed without a unit or device
class and named "VOC index" and "NOx index".

Versions before 1.2.0 published all three with the wrong types, so their
long term statistics restart after upgrading.

## Licence

Licensed under the Apache License, Version 2.0. See `LICENSE` for the full text
and `NOTICE` for attribution, including the upstream work this is based on.
