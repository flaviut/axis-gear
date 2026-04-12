"""
Set AXIS Gear shade position.

Usage:
    python set_position.py <position> [--mac XX:XX:XX:XX:XX:XX]

Position is 0-100 (0 = open, 100 = closed).
"""

import argparse
import asyncio
import sys

from bleak import BleakClient, BleakScanner

CONTROL_CHAR = "0003c2b1-0000-1000-8000-00805f9b0131"
FEEDBACK_CHAR = "0005cc10-0201-1100-0439-41f5a01ac001"
AXIS_MAC_PREFIX = "00:04:09"


def node_address_from_mac(mac: str) -> bytes:
    parts = mac.split(":")
    return bytes([int(parts[4], 16), int(parts[5], 16)])


async def scan_for_gear() -> list[dict]:
    print("Scanning for AXIS Gear devices (20s)...")
    devices = await BleakScanner.discover(timeout=20.0)
    gears = []
    for d in devices:
        if d.address.upper().startswith(AXIS_MAC_PREFIX.upper()) and d.name:
            gears.append({"address": d.address, "name": d.name})
            print(f"  Found: {d.name} ({d.address})")
    return gears


async def set_position(mac: str, position: int):
    node_addr = node_address_from_mac(mac)

    # ComputePositionCommand: [0x00 0x00] [addr] [0x00] [pos] [0x00 0x00]
    cmd = b"\x00\x00" + node_addr + b"\x00" + bytes([position]) + b"\x00\x00"

    print(f"Connecting to {mac}...")
    async with BleakClient(mac, timeout=20.0) as client:
        print(f"  Connected")
        print(f"  Setting position to {position}%")
        print(f"  → CONTROL write: {cmd.hex(' ')}")
        try:
            await client.write_gatt_char(CONTROL_CHAR, cmd, response=True)
        except Exception as e:
            print(f"    (GATT error suppressed: {e})")
        await asyncio.sleep(0.35)

        # Poll feedback to confirm
        fb = await client.read_gatt_char(FEEDBACK_CHAR)
        print(f"  Feedback: {fb.hex(' ')}")
        if len(fb) >= 3 and fb[0] == 0xAA and fb[1] == 0x01:
            print(f"  Current position: {fb[2]}%")

    print("Done.")


async def main():
    parser = argparse.ArgumentParser(description="Set AXIS Gear shade position")
    parser.add_argument("position", type=int, help="Position 0-100 (0=open, 100=closed)")
    parser.add_argument("--mac", help="Device MAC address")
    args = parser.parse_args()

    assert 0 <= args.position <= 100, "Position must be 0-100"

    if args.mac:
        mac = args.mac
    else:
        gears = await scan_for_gear()
        if not gears:
            print("No AXIS Gear devices found.")
            sys.exit(1)
        if len(gears) == 1:
            mac = gears[0]["address"]
        else:
            for i, g in enumerate(gears):
                print(f"  [{i}] {g['name']} ({g['address']})")
            idx = int(input("Select device: "))
            mac = gears[idx]["address"]

    await set_position(mac, args.position)


asyncio.run(main())
