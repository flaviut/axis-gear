# AXIS X Gear Motorized Window Shades — Hardware Overview

Two wireless chips on the board, operating in **mutually exclusive modes** (BLE or Zigbee, not both simultaneously).

## CYBLE-214009-00 — BLE 4.1 (Cypress EZ-BLE PSoC 4)

| Spec       | Value                              |
|------------|------------------------------------|
| Type       | BLE 4.1 SoC module                 |
| SoC        | CY8C4248LQI-BL583 (PSoC 4 BLE)    |
| CPU        | ARM Cortex-M0, up to 48 MHz        |
| Flash      | 256 KB                             |
| RAM        | 32 KB SRAM                         |
| Radio      | 2.4 GHz BLE 4.1                    |
| GPIOs      | Up to 25                           |
| Analog     | 12-bit SAR ADC, 4 op-amps, 2 comparators, CapSense |
| DMA        | 8 channels                         |
| Supply     | 1.9V–5.5V                          |
| Package    | 11 x 11 x 1.8 mm SMD              |
| Antenna    | Integrated trace antenna            |
| Toolchain  | Cypress PSoC Creator IDE            |

Handles BLE communication with the AXIS/RYSE mobile app. Custom GATT services — no public documentation exists for the BLE protocol.

### References

- [Datasheet (AllDatasheet)](https://www.alldatasheet.com/datasheet-pdf/pdf/823885/CYPRESS/CYBLE-214009-00.html)
- [DigiKey product page](https://www.digikey.com/en/product-highlight/c/cypress/cyble-214009-00-ez-ble-psoc-module)
- [FCC filing (WAP6045)](https://fccid.io/WAP6045/User-Manual/Users-Manual-3880583)
- [Infineon PSoC 4 BLE SoC](https://www.infineon.com/part/CY8C4248LQI-BL583)

## MMB Z357PA40 — Zigbee (Silicon Labs EM357)

| Spec           | Value                          |
|----------------|--------------------------------|
| Type           | Zigbee 802.15.4 transceiver module |
| SoC            | Silicon Labs EM357             |
| CPU            | ARM Cortex-M3 (32-bit)        |
| Flash          | 192 KB                        |
| RAM            | 12 KB                         |
| Radio          | 2.4 GHz ISM band              |
| TX power       | +20 dBm                       |
| RX sensitivity | -106 dBm                      |
| Protocols      | Zigbee, 802.15.4              |
| Interfaces     | UART, SPI, I2C                |
| Supply         | 3.3V                          |

Used in "hub mode" for Zigbee connectivity. Zigbee model number: **GR-ZB01-W**.

### Zigbee Protocol (documented)

Uses the standard **Window Covering cluster** (`closuresWindowCovering`):

| Expose    | Type   | Values            |
|-----------|--------|-------------------|
| `state`   | enum   | OPEN, CLOSE, STOP |
| `position`| number | 0–100             |
| `battery` | number | 0–100%            |

Works with Zigbee2MQTT, ZHA, Hubitat, SmartThings. Known issue: device goes to sleep and can become unresponsive.

### References

- [DigiKey product page](https://www.digikey.com/product-detail/en/mmb-networks/Z357PA40-SMT-P-NC-N/1096-1027-ND/5482737)
- [MMB Networks EM357 datasheet (PDF)](https://media.digikey.com/pdf/Data%20Sheets/MMB%20Research%20PDFs/Z357PA20_21_SE.pdf)
- [Silicon Labs EM357 SoC](https://www.silabs.com/wireless/zigbee/em35x-zigbee-socs/device.em357)
- [Zigbee2MQTT device page](https://www.zigbee2mqtt.io/devices/GR-ZB01-W.html)

