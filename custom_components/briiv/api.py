"""API for communicating with Briiv air purifiers over UDP."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import contextlib
import json
import socket
import time
from typing import Any, ClassVar

from homeassistant.exceptions import HomeAssistantError

from .const import (
    DEFAULT_PORT,
    DEVICE_TIMEOUT,
    DISCOVERY_DURATION,
    DISCOVERY_SETTLE_TIME,
    LOGGER,
)

type DataCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
type AvailabilityCallback = Callable[[], None]


class BriivError(HomeAssistantError):
    """Briiv base error."""


class BriivCommands:
    """Command definitions for Briiv devices."""

    @staticmethod
    def power_command(serial_number: str, state: bool) -> dict[str, Any]:
        """Create power on/off command."""
        return {
            "serial_number": serial_number,
            "command": "power",
            "power": 1 if state else 0,
        }

    @staticmethod
    def fan_speed_command(serial_number: str, speed: int) -> dict[str, Any]:
        """Create fan speed command."""
        return {
            "serial_number": serial_number,
            "command": "fan_speed",
            "fan_speed": speed,
        }

    @staticmethod
    def boost_command(serial_number: str, boost: bool) -> dict[str, Any]:
        """Create boost mode command."""
        return {
            "serial_number": serial_number,
            "command": "boost",
            "boost": 1 if boost else 0,
        }


class BriivAPI:
    """API class to handle UDP communication with Briiv devices.

    Briiv devices broadcast their state to a single well known UDP port, so all
    config entries share one socket and one read loop. The class level state
    below is that shared listener; per instance state tracks a single device.
    """

    _instances: ClassVar[dict[str, BriivAPI]] = {}
    _shared_socket: ClassVar[socket.socket | None] = None
    _shared_read_task: ClassVar[asyncio.Task[None] | None] = None
    _is_listening: ClassVar[bool] = False
    _device_addresses: ClassVar[dict[str, tuple[str, int]]] = {}
    _discovered_devices: ClassVar[dict[str, dict[str, Any]]] = {}

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        serial_number: str | None = None,
    ) -> None:
        """Initialize the API."""
        self.host = host
        self.port = port
        self.serial_number = serial_number
        self.callbacks: list[DataCallback] = []
        self.last_data: dict[str, Any] | None = None
        self.last_seen: float | None = None

        self._available = False
        self._availability_callbacks: list[AvailabilityCallback] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stale_timer: asyncio.TimerHandle | None = None

        if serial_number:
            self._instances[serial_number] = self

    @property
    def available(self) -> bool:
        """Return whether the device has been heard from recently."""
        return self._available

    @property
    def seconds_since_last_packet(self) -> float | None:
        """Return how long ago the last packet arrived, if ever."""
        if self.last_seen is None:
            return None
        return time.monotonic() - self.last_seen

    def _command_destination(self, serial: str) -> tuple[str, int]:
        """Return where a command for this device should be sent.

        A configured address is preferred over the one a broadcast arrived
        from. They are normally the same, but they differ when something
        relays broadcasts on the device's behalf: the packet then carries the
        relay's address, and commands sent there never reach the purifier.
        Preferring the configured address means a relay only has to carry
        broadcasts one way, and commands can go straight to the device.

        Falling back to a broadcast only helps on the device's own segment, so
        it is a last resort for a device that has not been heard from and has
        no address configured.
        """
        if self.host and self.host != "0.0.0.0":
            return (self.host, self.port)
        return self._device_addresses.get(serial, ("255.255.255.255", self.port))

    async def send_command(self, command: dict[str, Any]) -> None:
        """Send a command to the Briiv device."""
        if not self._shared_socket:
            raise BriivError("Shared socket not initialized")

        serial = command.get("serial_number")
        if not serial:
            raise BriivError("Command missing serial number")

        try:
            data = json.dumps(command).encode()
            await asyncio.get_running_loop().sock_sendto(
                self._shared_socket, data, self._command_destination(serial)
            )
        except OSError as err:
            raise BriivError(f"Failed to send command: {err}") from err

    async def set_power(self, state: bool) -> None:
        """Set power state."""
        if not self.serial_number:
            raise BriivError("Serial number not set")
        await self.send_command(BriivCommands.power_command(self.serial_number, state))

    async def set_fan_speed(self, speed: int) -> None:
        """Set fan speed."""
        if not self.serial_number:
            raise BriivError("Serial number not set")
        await self.send_command(
            BriivCommands.fan_speed_command(self.serial_number, speed)
        )

    async def set_boost(self, boost: bool) -> None:
        """Set boost mode."""
        if not self.serial_number:
            raise BriivError("Serial number not set")
        await self.send_command(BriivCommands.boost_command(self.serial_number, boost))

    def register_callback(self, callback: DataCallback) -> Callable[[], None]:
        """Register a callback for data updates and return an unsubscriber."""
        self.callbacks.append(callback)

        def _unsubscribe() -> None:
            if callback in self.callbacks:
                self.callbacks.remove(callback)

        return _unsubscribe

    def register_availability_callback(
        self, callback: AvailabilityCallback
    ) -> Callable[[], None]:
        """Register a callback for availability changes."""
        self._availability_callbacks.append(callback)

        def _unsubscribe() -> None:
            if callback in self._availability_callbacks:
                self._availability_callbacks.remove(callback)

        return _unsubscribe

    def _set_available(self, available: bool) -> None:
        """Update availability and notify listeners when it changes."""
        if self._available == available:
            return

        self._available = available
        if not available and self.serial_number:
            LOGGER.debug(
                "No data from %s for %s seconds, marking unavailable",
                self.serial_number,
                DEVICE_TIMEOUT,
            )

        for callback in list(self._availability_callbacks):
            callback()

    def _mark_seen(self) -> None:
        """Record that a packet arrived and restart the staleness timer."""
        self.last_seen = time.monotonic()

        if self._stale_timer is not None:
            self._stale_timer.cancel()
        if self._loop is not None:
            self._stale_timer = self._loop.call_later(
                DEVICE_TIMEOUT, self._set_available, False
            )

        self._set_available(True)

    @classmethod
    def _create_and_bind_socket(cls) -> socket.socket:
        """Create and bind the shared socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind(("0.0.0.0", DEFAULT_PORT))
        except OSError as err:
            sock.close()
            raise BriivError(f"Failed to bind UDP port {DEFAULT_PORT}: {err}") from err

        sock.setblocking(False)
        return sock

    @classmethod
    async def start_shared_listener(cls, loop: asyncio.AbstractEventLoop) -> None:
        """Start the shared UDP listener used by all instances."""
        if cls._is_listening:
            return

        cls._shared_socket = cls._create_and_bind_socket()
        cls._is_listening = True
        cls._shared_read_task = loop.create_task(cls._shared_read_loop(loop))

    @classmethod
    async def stop_shared_listener(cls) -> None:
        """Stop the shared UDP listener and release the socket."""
        cls._is_listening = False

        task = cls._shared_read_task
        cls._shared_read_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        cls._cleanup_shared_socket()

    @classmethod
    def _cleanup_shared_socket(cls) -> None:
        """Close the shared socket and forget cached device addresses."""
        if cls._shared_socket:
            try:
                cls._shared_socket.close()
            except OSError as err:
                LOGGER.debug("Error closing shared socket: %s", err)
            finally:
                cls._shared_socket = None
        cls._device_addresses.clear()

    @classmethod
    async def _handle_device_data(
        cls, json_data: dict[str, Any], addr: tuple[str, int]
    ) -> None:
        """Handle received device data and trigger callbacks."""
        serial = json_data.get("serial_number")
        if not serial:
            return

        cls._device_addresses[serial] = addr

        device = cls._discovered_devices.setdefault(
            serial, {"serial_number": serial, "is_pro": False}
        )
        device["host"] = addr[0]
        if "is_briiv_pro" in json_data:
            device["is_pro"] = bool(json_data["is_briiv_pro"])

        instance = cls._instances.get(serial)
        if instance is None:
            return

        instance.last_data = json_data
        instance._mark_seen()

        if callback_tasks := [
            asyncio.create_task(callback(json_data)) for callback in instance.callbacks
        ]:
            await asyncio.gather(*callback_tasks, return_exceptions=True)

    @classmethod
    async def _shared_read_loop(cls, loop: asyncio.AbstractEventLoop) -> None:
        """Shared read loop for all instances."""
        while cls._is_listening and cls._shared_socket:
            try:
                data, addr = await loop.sock_recvfrom(cls._shared_socket, 4096)
                try:
                    json_data = json.loads(data.decode())
                    await cls._handle_device_data(json_data, addr)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    LOGGER.warning("Error decoding JSON from %s", addr[0])
            except (BlockingIOError, ConnectionError):
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except OSError as err:
                LOGGER.error("Socket error in shared read loop: %s", err)
                await asyncio.sleep(1)

    @classmethod
    async def discover(cls, duration: int = DISCOVERY_DURATION) -> list[dict[str, Any]]:
        """Discover Briiv devices on the network.

        Reuses the shared listener when config entries are already loaded, and
        otherwise starts one for the duration of the discovery only.
        """
        cls._discovered_devices.clear()
        loop = asyncio.get_running_loop()

        started_listener = not cls._is_listening
        if started_listener:
            await cls.start_shared_listener(loop)

        try:
            deadline = loop.time() + duration
            settle_deadline: float | None = None
            seen = 0

            while loop.time() < deadline:
                await asyncio.sleep(0.25)

                if len(cls._discovered_devices) != seen:
                    seen = len(cls._discovered_devices)
                    settle_deadline = loop.time() + DISCOVERY_SETTLE_TIME
                elif settle_deadline is not None and loop.time() >= settle_deadline:
                    break

            return list(cls._discovered_devices.values())
        finally:
            if started_listener:
                await cls.stop_shared_listener()

    async def start_listening(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start listening using the shared socket."""
        self._loop = loop
        await self.start_shared_listener(loop)

    async def stop_listening(self) -> None:
        """Stop listening and release resources owned by this instance."""
        if self._stale_timer is not None:
            self._stale_timer.cancel()
            self._stale_timer = None

        self._set_available(False)

        # Only drop the registration if it still points at this instance; a
        # reload may already have installed its replacement.
        if self.serial_number and self._instances.get(self.serial_number) is self:
            del self._instances[self.serial_number]

        if not self._instances:
            await self.stop_shared_listener()
