# Home Assistant integration for Axis Gear

This is a Bluetooth-based Home Assistant integration for the Axis Gear shade driver. Unlike the standard/intended integration, which works over a standardized ZigBee protocol (referred to [in the docs as "Smart Home Mode"][smart-home-mode]), this uses the main App mode, which runs over Bluetooth.

Note: this integration is unofficial and unsupported. It is not endorsed by Axis, and their support staff will not be able to help with it.

[smart-home-mode]: https://support.helloryse.com/en/articles/5193909-placing-axis-gear-in-out-of-smart-home-mode

## Why

For me, and [for others](https://community.hubitat.com/t/axis-gear-does-the-right-thing/98212/53), the ZigBee implementation constantly goes offline and falls off the network. The hope is that the Bluetooth protocol will be more reliable.

## Installation

### HACS (recommended)

1. Install [HACS](https://hacs.xyz/) if you haven't already
2. In HACS, go to **Integrations** → **⋮** (top right) → **Custom repositories**
3. Add `https://github.com/flaviut/axis-gear` with category **Integration**
4. Search for "Axis Gear" in HACS and install it
5. Restart Home Assistant

### Manual

1. Copy the `components/axis_gear` directory to your Home Assistant `custom_components/axis_gear` directory
2. Restart Home Assistant

### Setup

HASS should automatically prompt you about the new device. If that doesn't happen,

1. Make sure it is charged
2. Power it on by holding the X button for 5 seconds
3. Press the pair button

If it still doesn't work, [make sure it is not in Smart Home Mode][smart-home-mode].

If this is your first time using this controller, I suggest pairing it with the app first. Make sure the firmware is fully up to date, and calibrate it for your installation. Even without the app, the device settings in HASS has a wizard to take you through calibrating it for the first time.
