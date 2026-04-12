"""
AXIS Gear BLE initial pairing and calibration.

Faithfully replicates the app's InitialSetupActivity → BluetoothGeneralHelper flow.

Usage:
    python pair_and_calibrate.py [--mac XX:XX:XX:XX:XX:XX]
"""

import argparse
import asyncio
import sys

from bleak import BleakClient, BleakScanner

# UUIDs (from decompiled app)
CONTROL_CHAR = "0003c2b1-0000-1000-8000-00805f9b0131"
ADDRESS_CHAR = "0003ccb1-0000-1000-8000-00805f9b0131"
FEEDBACK_CHAR = "0005cc10-0201-1100-0439-41f5a01ac001"
BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"
FW_VERSION_CHAR = "00002a26-0000-1000-8000-00805f9b34fb"
MODEL_CHAR = "00002a24-0000-1000-8000-00805f9b34fb"
DEVICE_NAME_CHAR = "00002a00-0000-1000-8000-00805f9b34fb"

AXIS_MAC_PREFIX = "00:04:09"
WRITE_DELAY = 0.35  # 350ms between writes, per app's loopWQueues


def node_address_from_mac(mac: str) -> bytes:
    """Derive 2-byte node address from last two MAC octets.
    Matches AxisGearDevice.getNodeAddress(String btAddress)."""
    parts = mac.split(":")
    return bytes([int(parts[4], 16), int(parts[5], 16)])


async def write_control(client: BleakClient, node_addr: bytes, suffix: bytes):
    """Write to CONTROL_CHARACTERISTIC.

    The app uses WRITE_TYPE_DEFAULT (write-with-response) and processes writes
    sequentially with 350ms delays, without waiting for write responses. On
    Linux/bluez, write-with-response returns Insufficient Resource (0x11) but
    the device still processes the command. We suppress the error to match the
    app's fire-and-forget behavior.
    """
    cmd = b"\x00\x00" + node_addr + suffix
    print(f"  → CONTROL write: {cmd.hex(' ')}")
    try:
        await client.write_gatt_char(CONTROL_CHAR, cmd, response=True)
    except Exception as e:
        print(f"    (GATT error suppressed: {e})")
    await asyncio.sleep(WRITE_DELAY)


async def scan_for_gear() -> list[dict]:
    print("Scanning for AXIS Gear devices (20s)...")
    devices = await BleakScanner.discover(timeout=20.0)
    gears = []
    for d in devices:
        if d.address.upper().startswith(AXIS_MAC_PREFIX.upper()) and d.name:
            gears.append({"address": d.address, "name": d.name})
            print(f"  Found: {d.name} ({d.address})")
    return gears


async def pair_and_calibrate(mac: str):
    node_addr = node_address_from_mac(mac)

    print(f"\nConnecting to {mac}...")
    async with BleakClient(mac, timeout=20.0) as client:
        print(f"  Connected: {client.is_connected}")

        # === Step 1: Read mesh address (calibrationReadMeshAddress) ===
        # App does this on ACTION_GATT_SERVICES_DISCOVERED
        print("\n--- Step 1: Read mesh address ---")
        current_addr = await client.read_gatt_char(ADDRESS_CHAR)
        print(f"  MAC-derived node address: {node_addr.hex(' ')}")
        print(f"  Current address on device: {current_addr.hex(' ')}")

        # === Step 2: Node address check & assignment (InitialSetupActivity.dataRead) ===
        # CRITICAL: The app uses [0x11, 0x11] as the node address for all commands
        # during initial calibration. The MAC-derived address is only written at
        # finalization. See InitialSetupActivity.java lines 720, 728.
        print("\n--- Step 2: Node address assignment ---")
        needs_calibration = False
        if current_addr in (b"\x00\x00", b"\xff\xff"):
            # Unconfigured / factory reset → write placeholder [0x11, 0x11]
            print("  Device unconfigured — writing placeholder [0x11, 0x11]")
            await client.write_gatt_char(ADDRESS_CHAR, b"\x11\x11")
            cmd_addr = b"\x11\x11"  # app uses placeholder for commands
            needs_calibration = True
        elif current_addr == b"\x11\x11":
            # Previously set placeholder — rewrite it
            print("  Placeholder address found — rewriting [0x11, 0x11]")
            await client.write_gatt_char(ADDRESS_CHAR, b"\x11\x11")
            cmd_addr = b"\x11\x11"
            needs_calibration = True
        elif current_addr != node_addr:
            # Configured by another phone — overwrite with MAC-derived
            print(f"  Configured by another phone — writing MAC-derived address {node_addr.hex(' ')}")
            await client.write_gatt_char(ADDRESS_CHAR, node_addr)
            cmd_addr = node_addr
            # App reads feedback after 500ms delay here
            await asyncio.sleep(0.5)
        else:
            print("  Already configured with correct address")
            cmd_addr = node_addr

        # === Step 3: Feedback check & master assignment ===
        print("\n--- Step 3: Feedback / master assignment ---")
        fb = await client.read_gatt_char(FEEDBACK_CHAR)
        print(f"  Feedback: {fb.hex(' ')}")
        if len(fb) >= 1 and fb[0] == 0x00:
            print("  No master — claiming master (writing 0x01 to FEEDBACK)")
            await client.write_gatt_char(FEEDBACK_CHAR, b"\x01")

        # === Step 4: initCalibrationReading — battery, battery, FW, model ===
        # App reads these sequentially with specific delays
        print("\n--- Step 4: Device info (initCalibrationReading) ---")
        battery_chars = [
            c for c in client.services.characteristics.values()
            if c.uuid == BATTERY_CHAR
        ]
        for i, bc in enumerate(battery_chars):
            val = await client.read_gatt_char(bc)
            label = ["AA", "LiPo/Solar"][i] if i < 2 else f"#{i}"
            print(f"  Battery ({label}): {val[0]}%")

        await asyncio.sleep(0.5)  # app: 500ms delay before firmware read
        fw = await client.read_gatt_char(FW_VERSION_CHAR)
        fw_str = fw.decode("ascii", errors="replace")
        print(f"  Firmware: {fw_str}")

        await asyncio.sleep(1.0)  # app: 1000ms delay before model read
        model = await client.read_gatt_char(MODEL_CHAR)
        print(f"  Model: {model.decode('ascii', errors='replace')}")

        # === Step 5: sendAddedCommand ===
        print("\n--- Step 5: Gear added command ---")
        await write_control(client, cmd_addr, b"\x0a\xdd\xed\x00")

        # === Step 6: Calibration ===
        if not needs_calibration:
            print("\nDevice already calibrated.")
            choice = input("Recalibrate? [y/N]: ").strip().lower()
            if choice != "y":
                print("Done.")
                return
            # Recalibration: send recalibrate command first
            print("\n--- Recalibration ---")
            await write_control(client, cmd_addr, b"\x00\x00\xff\xff")

        # Enter calibration mode (sendCalibratedModeCommand)
        print("\n--- Entering calibration mode ---")
        await write_control(client, cmd_addr, b"\xca\xca\xca\x00")

        # === Open position (SetupOpenPos) ===
        print("\n=== SET OPEN POSITION ===")
        print("  Use [u]p/[d]own to jog, [r]everse direction, [n]ext when positioned")
        while True:
            choice = input("  > ").strip().lower()
            if choice == "u":
                # calibrationUp: start moving up
                await write_control(client, cmd_addr, b"\x00\x00\x04\x06")
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("    [Enter to stop]"))
                # calibrationStopTop: stop and mark top position
                await write_control(client, cmd_addr, b"\x00\x01\x04\x06")
            elif choice == "d":
                # calibrationDown: start moving down
                await write_control(client, cmd_addr, b"\x00\x00\x04\x05")
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("    [Enter to stop]"))
                # calibrationStopTop (open pos uses stopTop for both directions)
                await write_control(client, cmd_addr, b"\x00\x01\x04\x06")
            elif choice == "r":
                # calibrationReverse
                await write_control(client, cmd_addr, b"\x01\x00\x00\x00")
            elif choice == "n":
                break

        # === Closed position (SetupClosePos) ===
        print("\n=== SET CLOSED POSITION ===")
        print("  Use [u]p/[d]own to jog, [n]ext when positioned")
        while True:
            choice = input("  > ").strip().lower()
            if choice == "u":
                await write_control(client, cmd_addr, b"\x00\x00\x04\x06")
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("    [Enter to stop]"))
                # calibrationStopBottom for close position
                await write_control(client, cmd_addr, b"\x00\x01\x04\x05")
            elif choice == "d":
                await write_control(client, cmd_addr, b"\x00\x00\x04\x05")
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("    [Enter to stop]"))
                # calibrationStopBottom
                await write_control(client, cmd_addr, b"\x00\x01\x04\x05")
            elif choice == "n":
                break

        # === Finalize calibration ===
        print("\n--- Finalizing calibration ---")
        # deviceCalibration(addr, 4) → finalize
        await write_control(client, cmd_addr, b"\x00\x00\x01\x01")

        # For firmware >= 5.x: set MAC-derived address on device
        await client.write_gatt_char(ADDRESS_CHAR, node_addr)
        print(f"  Wrote permanent node address: {node_addr.hex(' ')}")

        await asyncio.sleep(1.0)  # app: 1000ms delay before disconnect

    print("\nDisconnected. Calibration complete!")


async def main():
    parser = argparse.ArgumentParser(description="AXIS Gear BLE pairing & calibration")
    parser.add_argument("--mac", help="Device MAC address (e.g. 00:04:09:XX:XX:XX)")
    args = parser.parse_args()

    if args.mac:
        mac = args.mac
    else:
        gears = await scan_for_gear()
        if not gears:
            print("No AXIS Gear devices found.")
            sys.exit(1)
        if len(gears) == 1:
            mac = gears[0]["address"]
            print(f"\nUsing: {gears[0]['name']} ({mac})")
        else:
            for i, g in enumerate(gears):
                print(f"  [{i}] {g['name']} ({g['address']})")
            idx = int(input("Select device: "))
            mac = gears[idx]["address"]

    await pair_and_calibrate(mac)


asyncio.run(main())
