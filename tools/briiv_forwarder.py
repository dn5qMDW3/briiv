#!/usr/bin/env python3
"""Forward Briiv broadcasts to a Home Assistant on another subnet.

Briiv purifiers announce their state by UDP broadcast, and broadcasts do not
cross subnets. Where Home Assistant sits on a different segment from the
purifiers, something on the boundary has to carry those packets across.

This forwards each broadcast straight to Home Assistant as a unicast packet.
Nothing else on that subnet wanted them, so re-broadcasting there only adds
noise, and unicast works regardless of how the segment treats broadcast
traffic.

Only broadcasts are carried. Commands go directly from Home Assistant to the
purifier, so long as the integration knows the device's address: add the device
by IP rather than by discovery, and it addresses commands there instead of
replying to wherever the packet came from. That keeps this one directional and
much simpler than a two-way relay.

Typical setup on a dual homed Raspberry Pi:
  eth0  (192.168.30.x) -> Home Assistant network
  wlan0 (192.168.20.x) -> Briiv device network

Usage:
  sudo python3 briiv_forwarder.py --device-iface wlan0 --ha-host 192.168.30.5

Requires root on Linux, because pinning a socket to one interface does.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import socket

DEFAULT_PORT = 3334
SO_BINDTODEVICE = 25  # Linux socket option

logger = logging.getLogger("briiv-forwarder")


def local_addresses() -> set[str]:
    """Return this host's own addresses, so its packets are not echoed on."""
    addresses = {"127.0.0.1"}
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    return addresses


def make_listener(iface: str, port: int) -> socket.socket:
    """Listen for broadcasts arriving on one interface.

    The socket binds 0.0.0.0 because that is what receives packets sent to the
    broadcast address; pinning it to an interface keeps it to the purifiers'
    network rather than everything this host can see.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with contextlib.suppress(AttributeError, OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    try:
        sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, iface.encode() + b"\0")
    except PermissionError as err:
        raise SystemExit(f"Binding to {iface!r} needs root: {err}") from err
    except OSError as err:
        raise SystemExit(f"Cannot bind to interface {iface!r}: {err}") from err

    try:
        sock.bind(("0.0.0.0", port))
    except OSError as err:
        raise SystemExit(f"Cannot bind UDP port {port}: {err}") from err

    sock.setblocking(False)
    return sock


async def forward(iface: str, ha_host: str, port: int) -> None:
    """Carry each broadcast to Home Assistant until interrupted."""
    listener = make_listener(iface, port)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.setblocking(False)
    mine = local_addresses()
    destination = (ha_host, port)
    loop = asyncio.get_running_loop()
    carried = 0

    logger.info("Listening on %s:%d, forwarding to %s:%d", iface, port, ha_host, port)

    try:
        while True:
            try:
                data, addr = await loop.sock_recvfrom(listener, 4096)
            except (BlockingIOError, ConnectionError):
                await asyncio.sleep(0.05)
                continue
            except asyncio.CancelledError:
                break
            except OSError as err:
                logger.error("Receive failed: %s", err)
                await asyncio.sleep(1)
                continue

            if addr[0] in mine:
                continue

            try:
                await loop.sock_sendto(sender, data, destination)
            except OSError as err:
                logger.warning("Could not forward from %s: %s", addr[0], err)
                continue

            carried += 1
            if carried == 1 or carried % 100 == 0:
                logger.info("Forwarded %d packets (latest from %s)", carried, addr[0])
            logger.debug("%s -> %s (%d bytes)", addr[0], ha_host, len(data))
    finally:
        listener.close()
        sender.close()
        logger.info("Stopped after forwarding %d packets", carried)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Forward Briiv UDP broadcasts to Home Assistant on another subnet"
    )
    parser.add_argument(
        "--device-iface",
        required=True,
        help="Interface on the Briiv device network, e.g. wlan0",
    )
    parser.add_argument(
        "--ha-host",
        required=True,
        help="Address of the Home Assistant host to forward to",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"UDP port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Log every packet"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        asyncio.run(forward(args.device_iface, args.ha_host, args.port))
    except KeyboardInterrupt:
        logger.info("Interrupted")


if __name__ == "__main__":
    main()
