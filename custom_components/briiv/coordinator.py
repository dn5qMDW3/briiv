"""Coordinator for Briiv devices reached through the cloud.

The cloud service pushes device state over a WebSocket, so this coordinator
does not poll. It connects once, waits for the first device list, and then
hands each pushed update straight to the entities.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .cloud import BriivCloudAPI, BriivCloudAuthError, BriivCloudError
from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from . import BriivConfigEntry

# How long to wait for the service to send the first device list.
FIRST_UPDATE_TIMEOUT = 30


class BriivCloudCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Keeps the cloud connection open and publishes what it pushes."""

    config_entry: BriivConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BriivConfigEntry,
        api: BriivCloudAPI,
    ) -> None:
        """Initialize the coordinator for one Briiv account."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=entry,
            # No update_interval: the service pushes, so nothing is polled.
        )
        self.api = api
        self._first_update = asyncio.Event()

    async def _async_setup(self) -> None:
        """Open the connection and wait for the first device list."""
        self.api.register_callback(self._handle_push)

        try:
            await self.api.async_connect()
        except BriivCloudAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BriivCloudError as err:
            raise ConfigEntryNotReady(str(err)) from err

        try:
            async with asyncio.timeout(FIRST_UPDATE_TIMEOUT):
                await self._first_update.wait()
        except TimeoutError as err:
            await self.api.async_disconnect()
            raise ConfigEntryNotReady(
                "The Briiv service did not send any devices"
            ) from err

    def _handle_push(self, devices: dict[str, dict[str, Any]]) -> None:
        """Publish a pushed device set to the entities."""
        self._first_update.set()
        self.async_set_updated_data(dict(devices))

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Ask for a fresh device list, used for manual refreshes only."""
        try:
            await self.api.async_refresh_devices()
        except BriivCloudAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BriivCloudError as err:
            LOGGER.debug("Could not refresh Briiv devices: %s", err)

        # Pushed messages update the data; return what is already known.
        return self.data or dict(self.api.devices)

    async def async_shutdown(self) -> None:
        """Close the cloud connection."""
        await super().async_shutdown()
        await self.api.async_disconnect()
