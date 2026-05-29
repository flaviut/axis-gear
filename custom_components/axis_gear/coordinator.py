"""DataUpdateCoordinator for Axis Gear BLE blinds."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ble import AxisGearBLE, node_address_from_mac
from .const import CONF_NODE_ADDRESS

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(minutes=5)


@dataclass
class AxisGearState:
    """Current state of an Axis Gear device."""

    position: int | None = None  # 0=open, 100=closed (Axis convention)
    battery_aa: int | None = None
    battery_lipo: int | None = None


class AxisGearCoordinator(DataUpdateCoordinator[AxisGearState]):
    """Coordinator that polls an Axis Gear device over BLE."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Axis Gear {entry.data[CONF_ADDRESS]}",
            update_interval=POLL_INTERVAL,
        )
        self._address: str = entry.data[CONF_ADDRESS]
        self._node_addr = bytes.fromhex(entry.data[CONF_NODE_ADDRESS])
        self.data = AxisGearState()

    def _get_ble_device(self) -> AxisGearBLE:
        ble_device = async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(f"Device {self._address} not available")
        device = AxisGearBLE(ble_device)
        device._cmd_addr = self._node_addr
        return device

    async def _async_update_data(self) -> AxisGearState:
        device = self._get_ble_device()
        await device.connect()
        try:
            position = await device.read_position()
        finally:
            await device.disconnect()
        if position is not None:
            self.data.position = position
        return self.data

    async def async_set_position(self, position: int) -> None:
        """Send a position command (0=open, 100=closed in Axis convention)."""
        device = self._get_ble_device()
        await device.connect()
        try:
            await device.set_position(position)
            new_pos = await device.read_position()
        finally:
            await device.disconnect()
        if new_pos is not None:
            self.data.position = new_pos
        else:
            self.data.position = position
        self.async_set_updated_data(self.data)
