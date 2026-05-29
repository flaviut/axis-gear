"""BLE communication for AXIS Gear smart blinds.

Protocol reverse-engineered from the AXIS Android app. The device uses a
Cypress CYBLE-214009-00 (BLE 4.1, PSoC 4) with custom GATT services.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    close_stale_connections_by_address,
    establish_connection,
)

_LOGGER = logging.getLogger(__name__)

# GATT characteristic UUIDs (from decompiled app)
CONTROL_CHAR = "0003c2b1-0000-1000-8000-00805f9b0131"
ADDRESS_CHAR = "0003ccb1-0000-1000-8000-00805f9b0131"
FEEDBACK_CHAR = "0005cc10-0201-1100-0439-41f5a01ac001"
BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"
FW_VERSION_CHAR = "00002a26-0000-1000-8000-00805f9b34fb"
MODEL_CHAR = "00002a24-0000-1000-8000-00805f9b34fb"
DEVICE_NAME_CHAR = "00002a00-0000-1000-8000-00805f9b34fb"

# Calibration command suffixes (appended after [0x00 0x00] [node_addr])
CMD_CALIBRATION_UP = b"\x00\x00\x04\x06"
CMD_CALIBRATION_DOWN = b"\x00\x00\x04\x05"
CMD_CALIBRATION_STOP_TOP = b"\x00\x01\x04\x06"
CMD_CALIBRATION_STOP_BOTTOM = b"\x00\x01\x04\x05"
CMD_CALIBRATION_REVERSE = b"\x01\x00\x00\x00"
CMD_CALIBRATION_FINALIZE = b"\x00\x00\x01\x01"
CMD_ENTER_CALIBRATION = b"\xca\xca\xca\x00"
CMD_RECALIBRATE = b"\x00\x00\xff\xff"
CMD_GEAR_ADDED = b"\x0a\xdd\xed\x00"

WRITE_DELAY = 0.35  # 350ms between writes, per app's loopWQueues

# Placeholder address used during initial calibration
PLACEHOLDER_ADDR = b"\x11\x11"
UNCONFIGURED_ADDRS = (b"\x00\x00", b"\xff\xff")


def node_address_from_mac(mac: str) -> bytes:
    """Derive 2-byte node address from last two MAC octets."""
    parts = mac.upper().split(":")
    return bytes([int(parts[4], 16), int(parts[5], 16)])


@dataclass
class AxisGearInfo:
    """Device information read during setup."""

    mac: str
    name: str
    firmware: str
    model: str
    battery_aa: int
    battery_lipo: int
    needs_calibration: bool
    node_address: bytes


class AxisGearBLE:
    """BLE client for an AXIS Gear device."""

    def __init__(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device
        self._client: BleakClient | None = None
        self._cmd_addr: bytes = PLACEHOLDER_ADDR
        self._node_addr: bytes = node_address_from_mac(ble_device.address)

    @property
    def address(self) -> str:
        return self._ble_device.address

    async def connect(self) -> None:
        """Connect to the device using bleak-retry-connector."""
        await close_stale_connections_by_address(self._ble_device.address)
        self._client = await establish_connection(
            BleakClient,
            self._ble_device,
            self._ble_device.address,
        )
        # The device (and ESP32 BLE proxies) need time after connect
        # before GATT reads succeed. The original app waits 500ms.
        await asyncio.sleep(1.0)

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    async def _write_control(self, suffix: bytes) -> None:
        """Write a command to the control characteristic."""
        assert self._client is not None
        cmd = b"\x00\x00" + self._cmd_addr + suffix
        _LOGGER.debug("CONTROL write: %s", cmd.hex(" "))
        # Write-with-response returns Insufficient Resource on Linux/bluez
        # but the device still processes the command.
        try:
            await self._client.write_gatt_char(CONTROL_CHAR, cmd, response=True)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("GATT write error suppressed (expected on bluez)")
        await asyncio.sleep(WRITE_DELAY)

    async def read_device_info(self) -> AxisGearInfo:
        """Read device info and determine calibration state.

        Also performs node address assignment (pairing step 2-3).
        """
        assert self._client is not None
        client = self._client

        # Step 1: Read mesh address
        current_addr = await client.read_gatt_char(ADDRESS_CHAR)
        _LOGGER.debug("Current address: %s", current_addr.hex(" "))

        # Step 2: Node address check & assignment
        needs_calibration = False
        if current_addr in UNCONFIGURED_ADDRS or current_addr == PLACEHOLDER_ADDR:
            await client.write_gatt_char(ADDRESS_CHAR, PLACEHOLDER_ADDR)
            self._cmd_addr = PLACEHOLDER_ADDR
            needs_calibration = True
        elif current_addr != self._node_addr:
            await client.write_gatt_char(ADDRESS_CHAR, self._node_addr)
            self._cmd_addr = self._node_addr
            await asyncio.sleep(0.5)
        else:
            self._cmd_addr = self._node_addr

        # Step 3: Feedback / master assignment
        fb = await client.read_gatt_char(FEEDBACK_CHAR)
        if len(fb) >= 1 and fb[0] == 0x00:
            await client.write_gatt_char(FEEDBACK_CHAR, b"\x01")

        # Step 4: Battery, firmware, model
        battery_chars = [
            c
            for c in client.services.characteristics.values()
            if c.uuid == BATTERY_CHAR
        ]
        battery_aa = 0
        battery_lipo = 0
        for i, bc in enumerate(battery_chars):
            val = await client.read_gatt_char(bc)
            if i == 0:
                battery_aa = val[0]
            elif i == 1:
                battery_lipo = val[0]

        await asyncio.sleep(0.5)
        fw = (await client.read_gatt_char(FW_VERSION_CHAR)).decode(
            "ascii", errors="replace"
        )
        await asyncio.sleep(1.0)
        model = (await client.read_gatt_char(MODEL_CHAR)).decode(
            "ascii", errors="replace"
        )

        # Step 5: Gear added command
        await self._write_control(CMD_GEAR_ADDED)

        name = (await client.read_gatt_char(DEVICE_NAME_CHAR)).decode(
            "ascii", errors="replace"
        ).rstrip("\x00")

        return AxisGearInfo(
            mac=self._ble_device.address,
            name=name,
            firmware=fw,
            model=model,
            battery_aa=battery_aa,
            battery_lipo=battery_lipo,
            needs_calibration=needs_calibration,
            node_address=self._node_addr,
        )

    async def enter_calibration_mode(self) -> None:
        """Enter calibration mode on the device."""
        await self._write_control(CMD_ENTER_CALIBRATION)

    async def recalibrate(self) -> None:
        """Send recalibrate command (for already-calibrated devices)."""
        await self._write_control(CMD_RECALIBRATE)
        await self._write_control(CMD_ENTER_CALIBRATION)

    async def calibration_jog_up(self) -> None:
        """Start moving the shade up."""
        await self._write_control(CMD_CALIBRATION_UP)

    async def calibration_jog_down(self) -> None:
        """Start moving the shade down."""
        await self._write_control(CMD_CALIBRATION_DOWN)

    async def calibration_stop_top(self) -> None:
        """Stop and mark the current position as the top (open)."""
        await self._write_control(CMD_CALIBRATION_STOP_TOP)

    async def calibration_stop_bottom(self) -> None:
        """Stop and mark the current position as the bottom (closed)."""
        await self._write_control(CMD_CALIBRATION_STOP_BOTTOM)

    async def calibration_nudge_open(
        self, direction_up: bool, duration: float = 1.0
    ) -> None:
        """Nudge shade during open-position calibration (start + stop_top)."""
        if direction_up:
            await self._write_control(CMD_CALIBRATION_UP)
        else:
            await self._write_control(CMD_CALIBRATION_DOWN)
        await asyncio.sleep(duration)
        await self._write_control(CMD_CALIBRATION_STOP_TOP)

    async def calibration_nudge_close(
        self, direction_up: bool, duration: float = 1.0
    ) -> None:
        """Nudge shade during close-position calibration (start + stop_bottom)."""
        if direction_up:
            await self._write_control(CMD_CALIBRATION_UP)
        else:
            await self._write_control(CMD_CALIBRATION_DOWN)
        await asyncio.sleep(duration)
        await self._write_control(CMD_CALIBRATION_STOP_BOTTOM)

    async def calibration_reverse(self) -> None:
        """Reverse motor direction."""
        await self._write_control(CMD_CALIBRATION_REVERSE)

    async def calibration_finalize(self) -> None:
        """Finalize calibration and write permanent node address."""
        assert self._client is not None
        await self._write_control(CMD_CALIBRATION_FINALIZE)
        await self._client.write_gatt_char(ADDRESS_CHAR, self._node_addr)
        await asyncio.sleep(1.0)

    async def set_position(self, position: int) -> None:
        """Set shade position (0=open, 100=closed)."""
        assert self._client is not None
        cmd = (
            b"\x00\x00"
            + self._cmd_addr
            + b"\x00"
            + bytes([position])
            + b"\x00\x00"
        )
        try:
            await self._client.write_gatt_char(CONTROL_CHAR, cmd, response=True)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("GATT write error suppressed")
        await asyncio.sleep(WRITE_DELAY)

    async def read_position(self) -> int | None:
        """Read current shade position from feedback characteristic."""
        assert self._client is not None
        fb = await self._client.read_gatt_char(FEEDBACK_CHAR)
        if len(fb) >= 3 and fb[0] == 0xAA and fb[1] == 0x01:
            return fb[2]
        return None
