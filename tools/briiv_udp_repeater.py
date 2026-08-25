#!/usr/bin/env python3
"""Briiv UDP broadcast repeater for cross-subnet device discovery.

Relays Briiv air purifier UDP broadcasts (port 3334) between two networks,
enabling Home Assistant on one subnet to discover and control Briiv devices
on another subnet.

Typical setup on a dual-homed Raspberry Pi:
  - eth0  (192.168.30.x) -> Home Assistant network
  - wlan0 (192.168.20.x) -> Briiv device network

Usage:
  sudo python3 briiv_udp_repeater.py --device-iface wlan0 --ha-iface eth0
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import contextlib
import fcntl
import hashlib
import ipaddress
import logging
import socket
import struct
import time
from typing import NamedTuple

DEFAULT_PORT = 3334
LOOP_CACHE_TTL = 2.0  # seconds to remember forwarded packets
LOOP_CACHE_MAX = 256
SIOCGIFADDR = 0x8915  # ioctl to get interface address (Linux)
SO_BINDTODEVICE = 25  # Linux socket option

logger = logging.getLogger("briiv-repeater")


class ForwardedPacket(NamedTuple):
    digest: str
    timestamp: float


def get_interface_ip(iface: str) -> str:
    """Get the IPv4 address assigned to a network interface (Linux only)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr = fcntl.ioctl(
            s.fileno(),
            SIOCGIFADDR,
            struct.pack("256s", iface[:15].encode("utf-8")),
        )
        s.close()
        return socket.inet_ntoa(addr[20:24])
    except OSError as err:
        raise SystemExit(f"Cannot get IP for interface {iface!r}: {err}") from err


def compute_broadcast(ip: str) -> str:
    """Compute the /24 broadcast address for a given IP."""
    net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
    return str(net.broadcast_address)


def make_socket(iface: str, port: int) -> socket.socket:
    """Create a UDP socket bound to 0.0.0.0 but pinned to a specific interface.

    Binding to 0.0.0.0 is required to receive broadcast packets (sent to
    255.255.255.255). SO_BINDTODEVICE ensures each socket only sees traffic
    arriving on its designated interface.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with contextlib.suppress(AttributeError, OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    # Pin socket to a specific network interface (requires root on Linux)
    sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, iface.encode("utf-8") + b"\0")
    sock.bind(("0.0.0.0", port))
    sock.setblocking(False)
    return sock


class BriivRepeater:
    """Bidirectional UDP broadcast repeater for Briiv devices."""

    def __init__(
        self,
        device_iface: str,
        ha_iface: str,
        port: int = DEFAULT_PORT,
    ) -> None:
        self.device_iface = device_iface
        self.ha_iface = ha_iface
        self.port = port

        self.device_ip = get_interface_ip(device_iface)
        self.ha_ip = get_interface_ip(ha_iface)
        self.device_broadcast = compute_broadcast(self.device_ip)
        self.ha_broadcast = compute_broadcast(self.ha_ip)

        # IPs that belong to this host — packets from these are ours
        self.local_ips = {self.device_ip, self.ha_ip, "127.0.0.1"}

        # Recent packet hashes to prevent loops
        self._recent: deque[ForwardedPacket] = deque(maxlen=LOOP_CACHE_MAX)

        self._device_sock: socket.socket | None = None
        self._ha_sock: socket.socket | None = None

    def _is_duplicate(self, data: bytes, addr: tuple[str, int]) -> bool:
        """Check if we recently forwarded this exact packet."""
        digest = hashlib.md5(data + addr[0].encode()).hexdigest()
        now = time.monotonic()

        # Purge expired entries
        while self._recent and now - self._recent[0].timestamp > LOOP_CACHE_TTL:
            self._recent.popleft()

        for entry in self._recent:
            if entry.digest == digest:
                return True

        self._recent.append(ForwardedPacket(digest, now))
        return False

    def _is_own_packet(self, addr: tuple[str, int]) -> bool:
        """Check if the packet came from one of our own IPs."""
        return addr[0] in self.local_ips

    async def _relay_loop(
        self,
        recv_sock: socket.socket,
        send_sock: socket.socket,
        broadcast_dest: str,
        direction: str,
    ) -> None:
        """Receive on one socket, re-broadcast on the other."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                data, addr = await loop.sock_recvfrom(recv_sock, 4096)
            except (BlockingIOError, ConnectionError):
                await asyncio.sleep(0.05)
                continue
            except asyncio.CancelledError:
                break
            except OSError as err:
                logger.error("Socket error on %s: %s", direction, err)
                await asyncio.sleep(1)
                continue

            if self._is_own_packet(addr):
                continue

            if self._is_duplicate(data, addr):
                continue

            try:
                dest = (broadcast_dest, self.port)
                await loop.sock_sendto(send_sock, data, dest)
                logger.info(
                    "[%s] %s:%d -> %s:%d (%d bytes)",
                    direction,
                    addr[0],
                    addr[1],
                    dest[0],
                    dest[1],
                    len(data),
                )
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("  payload: %s", data.decode(errors="replace"))
            except OSError as err:
                logger.warning("Failed to relay %s: %s", direction, err)

    async def run(self) -> None:
        """Start the repeater."""
        logger.info(
            "Starting Briiv UDP repeater\n"
            "  Device iface: %s (%s, broadcast %s)\n"
            "  HA iface:     %s (%s, broadcast %s)\n"
            "  Port:         %d",
            self.device_iface,
            self.device_ip,
            self.device_broadcast,
            self.ha_iface,
            self.ha_ip,
            self.ha_broadcast,
            self.port,
        )

        self._device_sock = make_socket(self.device_iface, self.port)
        self._ha_sock = make_socket(self.ha_iface, self.port)

        logger.info("Listening for packets...")

        try:
            await asyncio.gather(
                self._relay_loop(
                    self._device_sock,
                    self._ha_sock,
                    self.ha_broadcast,
                    "device->HA",
                ),
                self._relay_loop(
                    self._ha_sock,
                    self._device_sock,
                    self.device_broadcast,
                    "HA->device",
                ),
            )
        finally:
            self._device_sock.close()
            self._ha_sock.close()
            logger.info("Repeater stopped")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Briiv UDP broadcast repeater for cross-subnet discovery"
    )
    parser.add_argument(
        "--device-iface",
        required=True,
        help="Network interface on the Briiv device network (e.g. wlan0)",
    )
    parser.add_argument(
        "--ha-iface",
        required=True,
        help="Network interface on the Home Assistant network (e.g. eth0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"UDP port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging (includes packet payloads)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    repeater = BriivRepeater(args.device_iface, args.ha_iface, args.port)

    try:
        asyncio.run(repeater.run())
    except KeyboardInterrupt:
        logger.info("Interrupted")


if __name__ == "__main__":
    main()
