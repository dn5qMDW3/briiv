#!/usr/bin/env python3
"""Capture and analyse the UDP broadcasts a Briiv air purifier sends.

Briiv devices announce their whole state to UDP port 3334 a few times a minute.
This listens for those broadcasts and reports what the fields actually contain,
which is how the Home Assistant integration decides how to type each sensor.

It answers three questions the firmware alone cannot:

  * is "boost_end_time" an absolute timestamp, or seconds remaining?
  * is "co" ever non zero, on a device with no carbon monoxide sensor?
  * do "voc" and "nox" look like Sensirion indices (1-500) or densities?

Listening is passive and needs no special privileges. Nothing is sent unless
--boost is given.

Usage:
  python3 briiv_probe.py                     # listen 90s, then report
  python3 briiv_probe.py --duration 300      # listen longer
  python3 briiv_probe.py --raw               # also print every packet
  python3 briiv_probe.py --boost SERIAL      # switch boost on while capturing
  sudo python3 briiv_probe.py --iface wlan0  # only the purifier's network
  python3 briiv_probe.py --unicast 192.168.20.160 --serial BRI...
                                             # can it be reached across subnets?

Run it on a machine on the same network segment as the purifier. If Home
Assistant runs on this same host it already holds port 3334, so stop it first
or run this elsewhere.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import contextlib
from itertools import pairwise
import json
import socket
import struct
import sys
import time
from typing import Any

DEFAULT_PORT = 3334
DEFAULT_DURATION = 90

# Pinning a socket to one interface is spelled differently per platform.
SO_BINDTODEVICE = 25  # Linux, takes the interface name
IP_BOUND_IF = 25  # macOS and BSD, takes the interface index
IP_RECVDSTADDR = 7  # macOS and BSD, asks for the destination address
BROADCAST_ADDR = "255.255.255.255"

# Sensirion SEN5x reports VOC and NOx as an index over this range.
INDEX_MIN, INDEX_MAX = 0, 500
# Anything past this is far more likely to be a Unix epoch than a duration.
EPOCH_THRESHOLD = 1_000_000_000
# Outdoor CO2, and the value a CO2 part reports before it measures anything.
CO2_BASELINE_PPM = 400

OTHER_FIELDS = (
    "temp",
    "humid",
    "pm1",
    "pm2_5",
    "pm4",
    "pm10",
    "fan_speed",
    "power",
    "boost",
)


def enable_dest_capture(sock: socket.socket) -> bool:
    """Ask the kernel to report each packet's destination address.

    A plain UDP socket cannot tell a packet addressed to this host apart from
    one sent to the broadcast address, yet that distinction decides whether the
    purifier can ever reach Home Assistant without a relay.
    """
    if sys.platform.startswith("linux") and hasattr(socket, "IP_PKTINFO"):
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_PKTINFO, 1)
            return True
    if sys.platform in ("darwin", "freebsd"):
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.IPPROTO_IP, IP_RECVDSTADDR, 1)
            return True
    return False


def dest_from_ancdata(ancdata: list[tuple[int, int, bytes]]) -> str | None:
    """Pull the destination address out of a received packet's metadata."""
    for level, msg_type, payload in ancdata:
        if level != socket.IPPROTO_IP:
            continue
        # Linux IP_PKTINFO: ifindex, local address, then the header destination.
        if hasattr(socket, "IP_PKTINFO") and msg_type == socket.IP_PKTINFO:
            if len(payload) >= 12:
                return socket.inet_ntoa(struct.unpack("I4s4s", payload[:12])[2])
        # macOS and BSD IP_RECVDSTADDR: just the destination address.
        elif msg_type == IP_RECVDSTADDR and len(payload) >= 4:
            return socket.inet_ntoa(payload[:4])
    return None


def bind_to_interface(sock: socket.socket, iface: str) -> None:
    """Pin a socket to one network interface.

    Useful on a multi homed host, such as the Raspberry Pi running the
    repeater, where only one interface faces the purifier's network.
    """
    try:
        index = socket.if_nametoindex(iface)
    except OSError as err:
        available = ", ".join(name for _, name in socket.if_nameindex())
        raise SystemExit(
            f"No such interface {iface!r}: {err}\nAvailable: {available}"
        ) from err

    try:
        if sys.platform.startswith("linux"):
            sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, iface.encode() + b"\0")
        elif sys.platform in ("darwin", "freebsd"):
            sock.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, index)
        else:
            raise SystemExit(
                f"--iface is not supported on {sys.platform}; "
                "omit it to listen on every interface"
            )
    except PermissionError as err:
        raise SystemExit(
            f"Binding to {iface!r} needs elevated privileges: {err}\n"
            f"Try: sudo python3 {sys.argv[0]} --iface {iface}"
        ) from err
    except OSError as err:
        raise SystemExit(f"Cannot bind to interface {iface!r}: {err}") from err


def make_socket(port: int, iface: str | None = None) -> socket.socket:
    """Bind a UDP socket able to receive the device's broadcasts."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Allows coexisting with the repeater, or Home Assistant, on the same host.
    with contextlib.suppress(AttributeError, OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    if iface:
        bind_to_interface(sock, iface)

    # Binding to 0.0.0.0 is required to receive packets sent to the broadcast
    # address; the interface pin above still limits which ones arrive.
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as err:
        raise SystemExit(
            f"Cannot bind UDP port {port}: {err}\n"
            "Something else is already listening, most likely Home Assistant."
        ) from err
    sock.settimeout(1.0)
    return sock


def send_boost(sock: socket.socket, serial: str, port: int, on: bool) -> None:
    """Switch boost mode on or off, so boost_end_time becomes meaningful."""
    payload = {
        "serial_number": serial,
        "command": "boost",
        "boost": 1 if on else 0,
    }
    sock.sendto(json.dumps(payload).encode(), ("255.255.255.255", port))
    print(f"  -> sent boost {'on' if on else 'off'} to {serial}")


def send_noop(sock: socket.socket, serial: str, host: str, port: int) -> None:
    """Send a command that changes nothing, addressed to one host.

    Switching boost off when it is already off is the only no-op the firmware
    offers, since it accepts just power, fan_speed and boost.
    """
    payload = {"serial_number": serial, "command": "boost", "boost": 0}
    sock.sendto(json.dumps(payload).encode(), (host, port))
    print(f"  -> sent no-op (boost off) directly to {host}:{port}")


def report_unicast(host: str, by_serial: dict[str, list[dict[str, Any]]]) -> None:
    """Say whether the device answered a directed command."""
    sources = {p["_source"] for pkts in by_serial.values() for p in pkts}
    print("\n" + "=" * 68)
    print("UNICAST REACHABILITY")
    print("=" * 68)
    print(f"  packet sources seen: {sorted(sources) or 'none'}")

    if host in sources:
        print(f"  verdict : {host} DID answer a directed command")
        print("            state can reach Home Assistant across subnets")
        print("            without relaying broadcasts")
        return

    print(f"  verdict : INCONCLUSIVE, nothing came back from {host}")
    print("            silence has two explanations and this test cannot")
    print("            tell them apart:")
    print("              1. the command never arrived, because nothing routes")
    print("                 from here to that address")
    print("              2. it arrived and the device simply never answers")
    print("                 anything directly, only broadcasting")
    print()
    print("            to separate them:")
    print("              * check the command landed. It switched boost off, so")
    print("                if boost was on and is now off, it arrived")
    print("              * run this on the device's own subnet, where the")
    print("                destination summary above shows whether packets go")
    print("                to 255.255.255.255 or to a host directly")


def capture(
    sock: socket.socket, duration: int, raw: bool, want_dest: bool = False
) -> tuple[dict[str, list[dict[str, Any]]], list[float]]:
    """Collect packets per serial number until the capture window closes."""
    by_serial: dict[str, list[dict[str, Any]]] = defaultdict(list)
    arrivals: list[float] = []
    deadline = time.monotonic() + duration

    while time.monotonic() < deadline:
        try:
            if want_dest:
                data, ancdata, _flags, addr = sock.recvmsg(4096, socket.CMSG_SPACE(64))
                dest = dest_from_ancdata(ancdata)
            else:
                data, addr = sock.recvfrom(4096)
                dest = None
        except TimeoutError:
            continue
        except KeyboardInterrupt:
            print("\nInterrupted, reporting what was captured so far")
            break
        except OSError as err:
            print(f"  socket error: {err}")
            continue

        try:
            packet = json.loads(data.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            print(f"  non JSON packet from {addr[0]} ({len(data)} bytes)")
            continue

        serial = packet.get("serial_number")
        if not serial:
            continue

        # Commands carry a "command" key; state broadcasts do not. Skip them so
        # our own no-op, and anything Home Assistant sends, is never mistaken
        # for the device reporting in.
        if "command" in packet:
            continue

        now = time.time()
        packet["_received_at"] = now
        packet["_source"] = addr[0]
        packet["_dest"] = dest
        by_serial[serial].append(packet)
        arrivals.append(now)

        remaining = int(deadline - time.monotonic())
        if raw:
            print(f"  [{len(arrivals):3}] {addr[0]}  {json.dumps(packet)}")
        else:
            print(
                f"  [{len(arrivals):3}] {serial} from {addr[0]}  ({remaining}s left)",
                end="\r",
            )

    return by_serial, arrivals


def describe(values: list[Any]) -> str:
    """Summarise a numeric series."""
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return "no numeric samples"
    lo, hi = min(nums), max(nums)
    if lo == hi:
        return f"constant {lo}"
    return f"min {lo}, max {hi}, last {nums[-1]}"


def _report_epoch(value: float) -> None:
    """Explain a boost_end_time that is a wall clock timestamp."""
    delta = value - time.time()
    print(f"    epoch?  : yes, {value} is a Unix timestamp")
    print(f"              {delta:+.0f}s relative to this machine's clock")
    print("    verdict : ABSOLUTE UNIX TIMESTAMP")
    print("              type it as SensorDeviceClass.TIMESTAMP")


def _report_relative(series: list[tuple[float, float, Any, Any]]) -> None:
    """Tell a countdown apart from a deadline on the device's own clock.

    The device's "timestamp" field counts uptime rather than wall clock, so a
    deadline may be expressed on that same scale. That looks nothing like a
    Unix epoch, and sits still rather than counting down, so it is easy to
    mistake for a fixed duration.
    """
    live = [(t, v, ts) for t, v, boost, ts in series if v and boost]
    if len(live) < 2:
        print("    verdict : too few samples with boost on, capture longer")
        return

    (first_t, first_v, first_ts) = live[0]
    (last_t, last_v, last_ts) = live[-1]
    elapsed = last_t - first_t

    if elapsed < 5:
        print("    verdict : capture window too short to see movement")
        return

    drift = (first_v - last_v) / elapsed
    print(f"    drift   : {drift:+.2f} units per second of wall clock")

    uptime_known = isinstance(first_ts, (int, float)) and isinstance(
        last_ts, (int, float)
    )
    if uptime_known:
        print(f"    device  : timestamp went {first_ts} -> {last_ts}")

    if 0.5 < drift < 1.5:
        print("    verdict : COUNTDOWN IN SECONDS")
        print("              seconds remaining; DURATION in seconds is correct")
        return

    if abs(drift) < 0.1 and uptime_known and last_v > last_ts:
        remaining = last_v - last_ts
        print(f"    offset  : value - device timestamp = {remaining:.0f}s")
        print("    verdict : DEADLINE ON THE DEVICE'S UPTIME CLOCK")
        print("              not a duration and not a Unix epoch; seconds")
        print("              remaining = boost_end_time - timestamp")
        return

    if abs(drift) < 0.1:
        print("    verdict : constant while observed")
        print("              likely the duration chosen when boost started")
        return

    print("    verdict : moves at an unexpected rate, inspect manually")


def analyse_boost_end_time(packets: list[dict[str, Any]]) -> None:
    """Decide what boost_end_time counts."""
    print("\n  boost_end_time")
    series = [
        (p["_received_at"], p["boost_end_time"], p.get("boost"), p.get("timestamp"))
        for p in packets
        if isinstance(p.get("boost_end_time"), (int, float))
    ]
    if not series:
        print("    field never seen")
        return

    values = [v for _, v, _, _ in series]
    print(f"    samples : {describe(values)}")
    print(f"    boost on: {'yes' if any(b for _, _, b, _ in series) else 'no'}")

    if not any(values):
        print("    verdict : always zero, nothing to infer")
        print("              re-run with --boost so the field is populated")
        return

    biggest = max(v for v in values if v)
    if biggest >= EPOCH_THRESHOLD:
        _report_epoch(biggest)
        return

    print(f"    largest : {biggest} (too small for a Unix epoch)")
    _report_relative(series)


def analyse_co(packets: list[dict[str, Any]]) -> None:
    """Report what the co field actually carries.

    The hardware has no carbon monoxide sensor, so this is either a constant
    placeholder or a differently named gas reading. 400 is the textbook
    atmospheric CO2 baseline, which a CO2 capable part reports before it has
    anything better to say.
    """
    print("\n  co")
    co = [p["co"] for p in packets if isinstance(p.get("co"), (int, float))]
    if not co:
        print("    field never seen")
        return

    print(f"    samples : {describe(co)}")
    distinct = sorted(set(co))

    if len(distinct) == 1:
        value = distinct[0]
        print(f"    verdict : CONSTANT at {value} across {len(co)} packets")
        if value == 0:
            print("              placeholder, correctly not exposed")
        elif value == CO2_BASELINE_PPM:
            print(f"              {CO2_BASELINE_PPM} is the atmospheric CO2 baseline")
            print("              a fixed default, not a live measurement")
        else:
            print("              a fixed default, not a live measurement")
        print("              capture for longer to be sure it never moves")
    else:
        lo, hi = min(distinct), max(distinct)
        print(f"    verdict : VARIES over {len(distinct)} distinct values")
        if lo >= 300 and hi <= 5000:
            print("              range and baseline look like CO2 in ppm,")
            print("              not carbon monoxide; worth exposing as CO2")
        else:
            print("              inspect manually, the range fits no obvious gas")


def analyse_index_field(key: str, packets: list[dict[str, Any]]) -> None:
    """Report whether a field looks like a Sensirion index or a density."""
    print(f"\n  {key}")
    vals = [p[key] for p in packets if isinstance(p.get(key), (int, float))]
    if not vals:
        print("    field never seen")
        return

    print(f"    samples : {describe(vals)}")
    if all(INDEX_MIN <= v <= INDEX_MAX for v in vals):
        print(f"    verdict : within {INDEX_MIN}-{INDEX_MAX}, consistent with")
        print("              a Sensirion index (no unit, no device class)")
    else:
        print("    verdict : outside the index range, may be a density")


def analyse_timestamp(packets: list[dict[str, Any]]) -> None:
    """Report what the device clock is counting."""
    print("\n  timestamp")
    ts = [
        p["timestamp"] for p in packets if isinstance(p.get("timestamp"), (int, float))
    ]
    if not ts:
        print("    field never seen")
        return

    newest = max(ts)
    kind = "Unix epoch" if newest >= EPOCH_THRESHOLD else "uptime or counter"
    print(f"    latest  : {newest} ({kind})")
    if newest >= EPOCH_THRESHOLD:
        print(f"    skew    : {newest - time.time():+.0f}s versus this machine")


def analyse_device(serial: str, packets: list[dict[str, Any]]) -> None:
    """Print every finding for a single device."""
    print(f"\n{'-' * 68}\ndevice {serial}  ({len(packets)} packets)")
    pro = packets[-1].get("is_briiv_pro")
    print(f"  model   : {'Briiv Pro' if pro else 'Briiv'} (is_briiv_pro={pro})")
    print(f"  fields  : {sorted(k for k in packets[-1] if not k.startswith('_'))}")

    analyse_co(packets)
    for key in ("voc", "nox"):
        analyse_index_field(key, packets)
    analyse_boost_end_time(packets)
    analyse_timestamp(packets)

    print("\n  other fields")
    for key in OTHER_FIELDS:
        vals = [p[key] for p in packets if key in p]
        if vals:
            print(f"    {key:10}: {describe(vals)}")


def analyse(by_serial: dict[str, list[dict[str, Any]]], arrivals: list[float]) -> None:
    """Print the findings for every device seen."""
    print("\n" + "=" * 68)
    print("RESULTS")
    print("=" * 68)

    if not by_serial:
        print(
            "\nNo Briiv broadcasts seen.\n"
            "  * is this machine on the same subnet as the purifier?\n"
            "  * is the purifier powered on and joined to wifi?\n"
            "  * is a firewall dropping inbound UDP 3334?"
        )
        return

    if len(arrivals) >= 2:
        gaps = [b - a for a, b in pairwise(arrivals) if b - a > 0.01]
        if gaps:
            print(
                f"\nbroadcast interval: min {min(gaps):.1f}s, "
                f"max {max(gaps):.1f}s, mean {sum(gaps) / len(gaps):.1f}s"
            )
            print("  (integration marks a device unavailable after 180s)")

    dests = {p["_dest"] for pkts in by_serial.values() for p in pkts if p.get("_dest")}
    if dests:
        print(f"\npacket destinations: {sorted(dests)}")
        if dests == {BROADCAST_ADDR}:
            print("  every packet went to the broadcast address, so the device")
            print("  never addresses this host directly; reaching it from another")
            print("  subnet needs a relay or a router that forwards UDP 3334")
        elif BROADCAST_ADDR not in dests:
            print("  packets were addressed to this host directly, so the device")
            print("  can reach Home Assistant without relaying broadcasts")
        else:
            print("  a mix of broadcast and directly addressed packets")

    for serial, packets in by_serial.items():
        analyse_device(serial, packets)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture and analyse Briiv UDP broadcasts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"seconds to listen (default: {DEFAULT_DURATION})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"UDP port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--iface",
        metavar="NAME",
        help="only listen on this interface, e.g. wlan0 (may need sudo)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="print every packet as it arrives",
    )
    parser.add_argument(
        "--unicast",
        metavar="HOST",
        help="send a no-op command straight to this address and report whether "
        "the device answers; needs --serial. Run this from the Home "
        "Assistant network with any relay stopped.",
    )
    parser.add_argument(
        "--serial",
        metavar="SERIAL",
        help="serial number to address, required by --unicast",
    )
    parser.add_argument(
        "--boost",
        metavar="SERIAL",
        help="switch boost on for this serial during capture, then off again",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sock = make_socket(args.port, args.iface)
    want_dest = enable_dest_capture(sock)

    where = f" on {args.iface}" if args.iface else ""
    print(f"Listening on UDP {args.port}{where} for {args.duration}s ...")
    if not want_dest:
        print("(kernel will not report packet destinations on this platform)")
    if not args.raw:
        print("(pass --raw to see every packet)")

    if args.unicast and not args.serial:
        raise SystemExit("--unicast also needs --serial")

    boosted = False
    try:
        if args.unicast:
            send_noop(sock, args.serial, args.unicast, args.port)

        if args.boost:
            send_boost(sock, args.boost, args.port, on=True)
            boosted = True
            time.sleep(1)

        by_serial, arrivals = capture(sock, args.duration, args.raw, want_dest)
    finally:
        if boosted:
            try:
                print()  # leave the in place progress line alone
                send_boost(sock, args.boost, args.port, on=False)
            except OSError as err:
                print(f"  could not switch boost back off: {err}")
        sock.close()

    analyse(by_serial, arrivals)

    if args.unicast:
        report_unicast(args.unicast, by_serial)


if __name__ == "__main__":
    main()
