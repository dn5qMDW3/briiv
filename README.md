# Briiv Air Purifier Integration for Home Assistant

[![HACS Validate](https://github.com/dn5qMDW3/briiv/actions/workflows/validate.yml/badge.svg)](https://github.com/dn5qMDW3/briiv/actions/workflows/validate.yml)

Custom Home Assistant integration for [Briiv](https://briiv.com/) air purifiers. Communicates locally over UDP -- no cloud required.

Originally created by [@FiveCreate](https://github.com/FiveCreate) ([Briiv_HA](https://github.com/FiveCreate/Briiv_HA)).

## Features

- **Fan control**: Power on/off, fan speed (25/50/75/100%), and boost mode
- **Sensors**: Temperature, humidity, PM1, PM2.5, PM10, VOC, CO, and NOx
- **Auto-discovery**: Finds Briiv devices on your local network
- **Manual setup**: Configure by IP address and serial number
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

- Briiv air purifier on the same local network as Home Assistant
- UDP port 3334 must be accessible between Home Assistant and the Briiv device
