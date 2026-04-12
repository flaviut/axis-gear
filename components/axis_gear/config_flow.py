"""Config flow for the Axis Gear integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .ble import AxisGearBLE, node_address_from_mac
from .const import AXIS_MAC_PREFIX, CONF_NODE_ADDRESS, DOMAIN

_LOGGER = logging.getLogger(__name__)

JOG_SCHEMA = vol.Schema(
    {
        vol.Required("direction", default="up"): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value="up", label="Up"),
                    SelectOptionDict(value="down", label="Down"),
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required("duration", default=2.0): NumberSelector(
            NumberSelectorConfig(
                min=0.5,
                max=30.0,
                step=0.5,
                unit_of_measurement="seconds",
                mode=NumberSelectorMode.SLIDER,
            )
        ),
    }
)


class AxisGearConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Axis Gear.

    The config flow never connects to the device. It only uses BLE
    advertisement data to identify devices. Calibration and device
    communication happen post-setup via the options flow or coordinator.
    """

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AxisGearOptionsFlow:
        """Get the options flow handler."""
        return AxisGearOptionsFlow(config_entry)

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle bluetooth discovery."""
        if not discovery_info.address.upper().startswith(AXIS_MAC_PREFIX.upper()):
            return self.async_abort(reason="not_supported")

        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": f"Axis Gear {discovery_info.address[-8:]}"
        }

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm bluetooth discovery."""
        assert self._discovery_info is not None

        if user_input is not None:
            address = self._discovery_info.address
            return self.async_create_entry(
                title=f"Axis Gear {address[-8:]}",
                data={
                    CONF_ADDRESS: address,
                    CONF_NODE_ADDRESS: node_address_from_mac(address).hex(),
                },
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "address": self._discovery_info.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual user setup — pick from discovered devices."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(format_mac(address))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Axis Gear {address[-8:]}",
                data={
                    CONF_ADDRESS: address,
                    CONF_NODE_ADDRESS: node_address_from_mac(address).hex(),
                },
            )

        devices: dict[str, str] = {}
        for info in async_discovered_service_info(self.hass):
            if info.address.upper().startswith(AXIS_MAC_PREFIX.upper()):
                devices[info.address] = (
                    f"{info.name} ({info.address})" if info.name else info.address
                )

        if not devices:
            return self.async_abort(reason="no_devices_found")

        options = [
            SelectOptionDict(value=addr, label=label)
            for addr, label in devices.items()
        ]

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )


class AxisGearOptionsFlow(OptionsFlow):
    """Handle recalibration via options flow.

    Keeps a single BLE connection open for the entire calibration session,
    matching the app's behavior. The connection is established in init and
    held through open/close position setting until finalization.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._device: AxisGearBLE | None = None

    async def _ensure_connected(self) -> AxisGearBLE:
        """Get or create a connected BLE client."""
        if self._device is not None:
            return self._device
        address = self._config_entry.data[CONF_ADDRESS]
        ble_device = async_ble_device_from_address(
            self.hass, address, connectable=True
        )
        assert ble_device is not None
        self._device = AxisGearBLE(ble_device)
        await self._device.connect()
        return self._device

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connect, run full setup sequence, enter calibration mode."""
        if user_input is not None:
            device = await self._ensure_connected()
            # Full pairing sequence (matches discovery script steps 1-5)
            await device.read_device_info()
            # Recalibrate + enter calibration mode
            await device.recalibrate()
            return await self.async_step_calibrate_open()

        return self.async_show_form(step_id="init")

    # -- Open position: menu --

    async def async_step_calibrate_open(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show menu for setting the open position."""
        return self.async_show_menu(
            step_id="calibrate_open",
            menu_options=["open_jog", "open_reverse", "open_set"],
        )

    async def async_step_open_jog(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Jog the shade while setting open position."""
        if user_input is not None:
            device = await self._ensure_connected()
            direction_up = user_input["direction"] == "up"
            duration = user_input["duration"]
            await device.calibration_nudge_open(direction_up=direction_up, duration=duration)
            return await self.async_step_calibrate_open()

        return self.async_show_form(
            step_id="open_jog",
            data_schema=JOG_SCHEMA,
        )

    async def async_step_open_set(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Mark current position as open and advance to close."""
        return await self.async_step_calibrate_close()

    async def async_step_open_reverse(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reverse motor direction during open calibration."""
        device = await self._ensure_connected()
        await device.calibration_reverse()
        return await self.async_step_calibrate_open()

    # -- Close position: menu --

    async def async_step_calibrate_close(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show menu for setting the closed position."""
        return self.async_show_menu(
            step_id="calibrate_close",
            menu_options=["close_jog", "close_reverse", "close_set"],
        )

    async def async_step_close_jog(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Jog the shade while setting closed position."""
        if user_input is not None:
            device = await self._ensure_connected()
            direction_up = user_input["direction"] == "up"
            duration = user_input["duration"]
            await device.calibration_nudge_close(direction_up=direction_up, duration=duration)
            return await self.async_step_calibrate_close()

        return self.async_show_form(
            step_id="close_jog",
            data_schema=JOG_SCHEMA,
        )

    async def async_step_close_set(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Mark current position as closed and finalize."""
        device = await self._ensure_connected()
        await device.calibration_finalize()
        await device.disconnect()
        self._device = None
        return self.async_create_entry(data={})

    async def async_step_close_reverse(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reverse motor direction during close calibration."""
        device = await self._ensure_connected()
        await device.calibration_reverse()
        return await self.async_step_calibrate_close()
