"""API for communicating with Briiv air purifiers over UDP."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import contextlib
import json
import logging
import socket
from typing import Any, ClassVar

from homeassistant.exceptions import HomeAssistantError

from .const import DEFAULT_PORT, DOMAIN

LOGGER = logging.getLogger(f"homeassistant.components.{DOMAIN}.api")


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
    """API class to handle UDP communication with Briiv devices."""

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
        self.callbacks: list[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]] = []

        if serial_number:
            self._instances[serial_number] = self

    async def send_command(self, command: dict[str, Any]) -> None:
        """Send a command to the Briiv device."""
        if not self._shared_socket:
            raise BriivError("Shared socket not initialized")

        serial = command.get("serial_number")
        if not serial:
            raise BriivError("Command missing serial number")

        try:
            data = json.dumps(command).encode()
            dest_addr = self._device_addresses.get(
                serial, ("255.255.255.255", self.port)
            )
            await asyncio.get_running_loop().sock_sendto(
                self._shared_socket, data, dest_addr
            )
        except (OSError, json.JSONDecodeError) as err:
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
        await self.send_command(
            BriivCommands.boost_command(self.serial_number, boost)
        )

    @classmethod
    def _create_and_bind_socket(cls) -> socket.socket:
        """Create and bind the shared socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind(("0.0.0.0", DEFAULT_PORT))
        except OSError:
            try:
                sock.bind(("127.0.0.1", DEFAULT_PORT))
            except OSError as err:
                sock.close()
                raise BriivError(f"Failed to bind socket: {err}") from err

        sock.setblocking(False)
        return sock

    @classmethod
    async def start_shared_listener(cls, loop: asyncio.AbstractEventLoop) -> None:
        """Start a shared UDP listener for all instances."""
        if cls._is_listening:
            return

        try:
            cls._shared_socket = cls._create_and_bind_socket()
            cls._shared_read_task = asyncio.create_task(cls._shared_read_loop(loop))
            cls._is_listening = True
        except OSError as err:
            cls.cleanup_shared_socket()
            raise BriivError(f"Failed to start listener: {err}") from err

    @classmethod
    async def _handle_device_data(
        cls, json_data: dict[str, Any], addr: tuple[str, int]
    ) -> None:
        """Handle received device data and trigger callbacks."""
        serial = json_data.get("serial_number")
        if not serial:
            return

        cls._device_addresses[serial] = addr

        if serial not in cls._discovered_devices:
            cls._discovered_devices[serial] = {
                "host": addr[0],
                "serial_number": serial,
                "is_pro": bool(json_data.get("is_briiv_pro", 0)),
            }

        if serial in cls._instances:
            instance = cls._instances[serial]
            callback_tasks = [
                asyncio.create_task(callback(json_data))
                for callback in instance.callbacks
            ]
            if callback_tasks:
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
                except json.JSONDecodeError:
                    LOGGER.warning("Error decoding JSON from %s", addr[0])
            except (BlockingIOError, ConnectionError):
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except OSError as err:
                LOGGER.error("Socket error in shared read loop: %s", err)
                await asyncio.sleep(1)

    @classmethod
    async def discover(cls, timeout: int = 15) -> list[dict[str, Any]]:
        """Discover Briiv devices on the network using shared socket."""
        cls._discovered_devices.clear()

        if not cls._shared_socket:
            try:
                cls._shared_socket = cls._create_and_bind_socket()
            except OSError as err:
                LOGGER.error("Failed to create discovery socket: %s", err)
                return []

        if not cls._is_listening:
            cls._shared_read_task = asyncio.create_task(
                cls._shared_read_loop(asyncio.get_running_loop())
            )
            cls._is_listening = True

        try:
            await asyncio.sleep(timeout)
            return list(cls._discovered_devices.values())
        except (TimeoutError, OSError) as err:
            LOGGER.error("Network error during discovery: %s", err)
            return []

    async def start_listening(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start listening using the shared socket."""
        await self.start_shared_listener(loop)

    def register_callback(
        self, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        """Register callback for data updates."""
        if callback not in self.callbacks:
            self.callbacks.append(callback)

    def remove_callback(
        self, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        """Remove callback from updates."""
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    @classmethod
    def cleanup_shared_socket(cls) -> None:
        """Clean up shared socket resources."""
        if cls._shared_socket:
            try:
                cls._shared_socket.close()
            except OSError as err:
                LOGGER.error("Error closing shared socket: %s", err)
            finally:
                cls._shared_socket = None
        cls._is_listening = False
        cls._device_addresses.clear()

    async def stop_listening(self) -> None:
        """Stop listening and clean up resources."""
        if self.serial_number in self._instances:
            del self._instances[self.serial_number]

        if not self._instances:
            if self._shared_read_task and not self._shared_read_task.done():
                self._shared_read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._shared_read_task
            self.cleanup_shared_socket()
