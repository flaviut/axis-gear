# AXIS Gear BLE Protocol

Reverse-engineered from the AXIS/RYSE Android app (`life.axis`). The device uses a Cypress CYBLE-214009-00 (BLE 4.1, PSoC 4) with custom GATT services. No official protocol documentation exists.

## Device Discovery

Devices are found via BLE scan by checking **manufacturer data** for the string `"0904"` in the hex-encoded manufacturer-specific data keys. Additionally, filter by MAC address prefix `"00:04:09"` and requires a non-null device name.

Scan timeout: 20 seconds (`0x4E20` ms).

## Connection Sequence

From `BluetoothLeService.connectToDevice()` and `BluetoothGeneralHelper.initializeGattReading()`:

1. `connectGatt(context, autoConnect=false, callback, TRANSPORT_LE=2)`
2. Wait for `onConnectionStateChange` → CONNECTED
3. `discoverServices()`
4. Wait for `onServicesDiscovered`
5. Read queue (sequential): battery → model number → firmware version → serial → feedback char → device name → node address
6. Default MTU 23 works for basic control.
7. **No BLE pairing/bonding** for normal operation. Standard BLE pairing is not supported by the device (`bluetoothctl pair` fails with `AuthenticationFailed`). However, firmware versions `03.28.17` and `04.04.17` require device bonding before OTA (triggered automatically by `BluetoothOTAHelper`).
8. **No CCCD/notification setup** during normal operation — the feedback characteristic is polled via reads, not notifications (despite what the app code structure implies).

### Write behavior

The app uses `setWriteType(WRITE_TYPE_DEFAULT=2)` (write-with-response) for all characteristic writes. The write queue (`BluetoothGeneralHelper.loopWQueues()`) processes writes sequentially with a **350ms delay** between each write, without waiting for write responses.

## Physical Buttons on the Gear

The device has three physical buttons: **SET**, **PAIR**, and **GROUP**. There is also a power button at the AXIS logo location.

| Action | Buttons | Effect |
|--------|---------|--------|
| Power on | Press and hold power button (AXIS logo) | Blue flashing LEDs indicate device is on and advertising |
| Factory reset | Hold **SET + PAIR + GROUP** for 10 seconds | Clears all settings; node address resets to `[0x00, 0x00]` |
| Smart Home mode toggle | Hold **GROUP + PAIR** | Enters/exits smart home (hub) mode (firmware 1076+) |
| Hub pairing | Press **PAIR** | Makes device discoverable by smart home hub for 30 seconds |
| Cancel OTA | Hold **GROUP** | Powers off if white LEDs are stuck after failed update |
| Self-test (post-update) | Hold **SET + PAIR + GROUP** until green lights | One-time check when updating from v3.1 |

## Initial Pairing & Setup Flow

From `PairingActivity` → `InitialSetupActivity` → `BluetoothGeneralHelper`. This is the sequence when a new device is paired for the first time.

**Prerequisites:** Gear must be powered on (hold power button until blue LEDs flash). The device advertises as BLE with manufacturer data key `0x0409` and MAC prefix `00:04:09`. Default device name is `"GEAR UP!"`.

### Discovery & Selection

- `PairingActivity` scans for BLE devices
- Filters: manufacturer data contains `"0904"` AND MAC starts with `"00:04:09"` AND device name is non-null
- User taps a device → creates `AxisGearDevice(btDevice)` which derives the node address from the MAC (last 2 octets)
- Launches `InitialSetupActivity`

### Connection & Service Discovery

- `BluetoothGeneralHelper.addQueueList(btDevice)` connects via GATT (`connectGatt(context, autoConnect=false, callback, TRANSPORT_LE)`)
- On `ACTION_GATT_CONNECTED`: waits 500ms, then calls `discoverServices()`
- On `ACTION_GATT_SERVICES_DISCOVERED`: calls `calibrationReadMeshAddress(address)`
- Reads ADDRESS_CHARACTERISTIC (`0003CCB1`) on MESH_SERVICE (`0003CCB0`)

### Node Address Check & Assignment

The read node address is checked and set via `InitialSetupActivity.dataRead()`:

- **`[0x00, 0x00]` or `[0xFF, 0xFF]`** (unconfigured / factory reset):
  - Calls `calibrationSetRandomMeshAddress()` → writes `[0x11, 0x11]` to ADDRESS_CHARACTERISTIC
  - This is a temporary placeholder address used during calibration
  - Sets `calibrated = false` → user will go through full calibration
- **`[0x11, 0x11]`** (previously set random/placeholder address):
  - Same as above — rewrites `[0x11, 0x11]`
  - If device name is not `"GEAR UP!"`, considers it previously named → `setupMode = 2` (calibration only)
- **Any other address ≠ MAC-derived address** (configured by another phone):
  - Calls `calibrationSetMeshAddress()` → writes MAC-derived node address to ADDRESS_CHARACTERISTIC
  - Sets `calibrated = true`
  - Then reads feedback characteristic (500ms delay)
- **Address matches MAC-derived address** (already fully configured):
  - Sets `calibrated = true`, proceeds to read feedback immediately

### Feedback Check & Master Assignment

After node address is set, reads FEEDBACK_CHARACTERISTIC (`0005CC10`):

- If feedback == `[0x00]`: device has no master → writes `[0x01]` to FEEDBACK_CHARACTERISTIC to claim master, sets `groupID = -1`
- Sets `mDevice.setMaster(1)` regardless

### Battery & Device Info Reading

After master assignment, `CallReading()` → `initCalibrationReading()` reads sequentially:
1. Battery level (AA battery reading, from `00002A19`)
2. Battery level again (Lipo/solar battery, second read on same characteristic)
3. Firmware version (`00002A26`) — after 500ms delay
4. Model number (`00002A24`) — after 1000ms delay

### Gear Added Command

After firmware version is read:
- Calls `sendAddedCommand()` → writes `[0x00 0x00] [nodeAddr] [0x0A 0xDD 0xED 0x00]` to CONTROL_CHARACTERISTIC
- This notifies the device that it has been added to the app
- `SetupCompleted()` is called → "Begin" button is enabled in the UI

### Setup Wizard (UI steps)

The user taps "Begin". The wizard flow depends on `setupMode`:

| Mode | Value | UI Fragments | When |
|------|-------|-------------|------|
| `INITIAL_MODE` | 0 | Location → Blind Type → Mount → Power → Open Pos → Close Pos | New, uncalibrated device |
| `INITIAL_CALIBRATED_MODE` | 1 | No Calibration (single "done" screen) | Already calibrated by another phone |
| `CALIBRATION_ONLY_MODE` | 2 | Open Pos → Close Pos | Needs recalibration only |
| `ONDEVICE_CALIBRATED_MODE` | 3 | Location → Blind Type → Mount | Named but not fully set up |

For modes 0 and 2, the naming fragments (Location, Blind Type, Mount, Power) collect metadata. The power source selection (inside mount / outside mount) triggers `sendCalibratedModeCommand()` which enters calibration mode on the device.

### Calibration Procedure (modes 0 and 2)

#### Enter calibration mode
Before the calibration UI pages, the app sends:
```
[0x00 0x00] [nodeAddr] [0xCA 0xCA 0xCA 0x00]
```
to CONTROL_CHARACTERISTIC. This puts the device into calibration mode.

#### Set open position (`SetupOpenPos`)

The user holds up/down arrow buttons in the app. Touch events map to:

| Touch Event | BLE Command | `deviceCalibration()` code |
|-------------|-------------|--------------------------|
| Press Up | `calibrationUp` → `[0x00 0x00] [nodeAddr] [0x00 0x00 0x04 0x06]` | code `1` |
| Press Down | `calibrationDown` → `[0x00 0x00] [nodeAddr] [0x00 0x00 0x04 0x05]` | code `2` |
| Release (from Up) | `calibrationStopTop` → `[0x00 0x00] [nodeAddr] [0x00 0x01 0x04 0x06]` | code `9` |
| Release (from any) | stops motor, marks position | |

The shade moves while the button is held (touch-down = start moving, touch-up = stop and mark position). A **15-second safety timeout** automatically stops the motor if the user doesn't release.

If the motor direction is wrong, the user can tap a "reverse" checkbox → sends `calibrationReverse` (`[0x00 0x00] [nodeAddr] [0x01 0x00 0x00 0x00]`), code `6`.

The app also shows: *"If your Gear continues to move even after untapping the arrows, press [X button] on your Gear to stop."* — referring to the physical button on the device.

When the user taps "Next", the app checks `getCalibrationState()`. If the user hasn't actually moved the shade, an error dialog is shown.

#### Set closed position (`SetupClosePos`)

Same UI as open position but maps to different stop commands:

| Touch Event | BLE Command | `deviceCalibration()` code |
|-------------|-------------|--------------------------|
| Press Up | `calibrationUp` → `[0x00 0x00] [nodeAddr] [0x00 0x00 0x04 0x06]` | code `1` |
| Press Down | `calibrationDown` → `[0x00 0x00] [nodeAddr] [0x00 0x00 0x04 0x05]` | code `2` |
| Release | `calibrationStopBottom` → `[0x00 0x00] [nodeAddr] [0x00 0x01 0x04 0x05]` | code `5` |

#### Finalize calibration

When the user taps "Done" on the close position screen:

**For firmware ≥ 5.x (normal path):**
1. `deviceCalibration(addr, 4)` → sends finalize: `[0x00 0x00] [nodeAddr] [0x00 0x00 0x01 0x01]` to CONTROL_CHAR
2. Sets MAC-derived node address on the `AxisGearDevice` object
3. After 1000ms delay, disconnects and returns to main screen

**For firmware `03.28.17` or `04.04.17` (older path):**
1. Sets MAC-derived node address on device object first
2. `calibrationFinish(addr, 2)` → writes MAC-derived node address to ADDRESS_CHARACTERISTIC (MESH_SERVICE)
3. On write callback: sends calibration finalize command to CONTROL_CHAR
4. On second write callback: writes MAC-derived node address to ADDRESS_CHAR again (sets the permanent node address)
5. After 1000ms delay, disconnects

### Recalibration Flow

If recalibrating an already-configured device (from `RecalibrationActivity` or `DeviceControlActivity`):
1. `sendRecalibrateCommand()` → writes `[0x00 0x00] [nodeAddr] [0x00 0x00 0xFF 0xFF]` to CONTROL_CHAR
2. `sendCalibratedModeCommand()` → writes `[0x00 0x00] [nodeAddr] [0xCA 0xCA 0xCA 0x00]` to CONTROL_CHAR
3. Same calibration steps as above (open pos → close pos → finalize)

### Device Reset Flow

`BluetoothCalibrationHelper.resetAll()` performs these in sequence:
1. Write CTS: `hexStringToByteArray("991230000000")` to CTS_CHARACTERISTIC — sets a fixed time
2. After 300ms: write `hexStringToByteArray("AA010000FF0000BB")` to SCHEDULE_CHARACTERISTIC — clears all schedules
3. After 800ms: write `[0x00]` to FEEDBACK_CHARACTERISTIC — resets master assignment
4. After 1500ms: write `[0x00, 0x00]` to SMART_HOME_FEEDBACK_CHARACTERISTIC — resets group node address

Note: Software reset via the app clears app-side state. **Full factory reset requires pressing and holding SET + PAIR + GROUP on the physical device for 10 seconds.**

## GATT Service Map

### Custom Services (from app decompilation)

| Name | Service UUID | Characteristic UUID | Properties (app) |
|------|-------------|---------------------|------------|
| **Control** | `0003C2BB-0001-0008-0000-0805F9B01310` | `0003C2B1-0000-1000-8000-00805F9B0131` | Write (no response) |
| | | `0003C2B2-0001-0008-0000-0805F9B01310` | ? |
| **Mesh** | `0003CCB0-0000-1000-8000-00805F9B0131` | `0003CCB1-...0131` (address) | Read/Write |
| | | `0003CCB2-...0131` (name) | Read/Write |
| **Feedback** | `0004FA00-0020-3000-7000-00904F5A0134` | `0005CC10-0201-1100-0439-41F5A01AC001` | Notify |
| | | `0004FA01-0023-3001-7001-00944F5A0124` (smart home) | Notify |
| **Schedule** | `0003CAC0-0000-1000-8000-00805F9BF135` | `0003CAC1-...F135` | Write |
| **CTS** | `0003CAB0-0000-1000-8000-00805F9BF125` | `0003CAB1-...F125` | Write |
| **Unknown** | `0005ED00-0030-4000-8000-00543C3E0431` | `0005ED01-0031-4001-8000-00643C3E0631` | ? |
| | | `0005ED02-0032-4002-8002-00643C3E0632` | ? |
| **OTA** | `00060000-f8ce-11e4-abf4-0002a5d5c51b` | `00060001-...c51b` | Write (no response) — see [ota.md](ota.md) |

### Verified Properties (live device, firmware 5.3.5.1125)

Actual GATT properties observed differ from what the app code implies:

| Characteristic | Observed Properties | Notes |
|---|---|---|
| `0003C2B1` (control) | read, write | **Not** write-without-response. Write-with-response returns `Insufficient Resource` error; write-without-response silently succeeds but may be ignored. |
| `0003C2B2` (control2) | read, write-without-response | Reads as `00`. Purpose unknown. |
| `0005CC10` (feedback) | read, write | **No notify support.** App claims notify but device doesn't expose it. Reads as `00 00 00 00 00 00`. |
| `0004FA01` (smart home fb) | read | **No notify support.** Read-only. Reads as `00 00`. |
| `0003CCB1` (address) | read, write | Reads as `00 00` (factory default). |
| `0003CCB2` (mesh name) | read, write | Reads as `AxisGear` + 12 null bytes (20 bytes total). |
| `0003CAB1` (CTS) | read, write | Reads as `00 00 00 00 00 00`. |
| `0003CAC1` (schedule) | write | Write-only. |
| `0005ED01` (unknown) | read | Reads as `00 00 00 00`. |
| `0005ED02` (unknown) | read | Reads as `00`. |
| `00060001` (OTA) | write, notify | Only custom characteristic with notify. |
| `00002A19` (battery) | read, notify | 1 byte. Observed value: `0x1F` (31%). |

### Standard Services

| Name | Service UUID | Characteristic UUID | Properties |
|------|-------------|---------------------|------------|
| Generic Access | `00001800-0000-1000-8000-00805f9b34fb` | `00002A00-...` (device name) | Read |
| | | `00002A01-...` (appearance) | Read |
| | | `00002A04-...` (connection params) | Read |
| Battery | `0000180f-0000-1000-8000-00805f9b34fb` | `00002a19-...` (level) | Read, Notify |
| Device Info | `0000180a-0000-1000-8000-00805f9b34fb` | `00002A26-...` (FW version) | Read |
| | | `00002A24-...` (model number) | Read |
| | | `00002A25-...` (serial number) | Read |
| | | `00002A27-...` (HW revision) | Read |
| | | `00002A28-...` (SW revision) | Read |
| | | `00002A29-...` (manufacturer name) | Read |

### Observed Device Info Values

| Characteristic | Hex | ASCII |
|---|---|---|
| Device Name | `47 45 41 52 20 55 50 21 20...` | `GEAR UP!` (padded to 20 bytes) |
| Manufacturer | `41 58 49 53 20 4C 61 62 73 20 49 6E 63 2E` | `AXIS Labs Inc.` |
| Model | `47 52 2D 5A 42 30 31` | `GR-ZB01` |
| Serial | `30 33 2E 34 36 2E 33 38 2E 39 38 3A 30 30 2E 31 62 2E 30 61 2E 31 33` | `03.46.38.98:00.1b.0a.13` |
| Firmware Rev | `35 2E 33 2E 35 2E 31 31 32 35` | `5.3.5.1125` |
| Hardware Rev | `31 30 2E 30 30 2E 30 32` | `10.00.02` |
| Software Rev | `50 6F 6C 61 72 69 73` | `Polaris` |
| Connection Params | `06 00 28 00 00 00 E8 03` | min=6, max=40, latency=0, timeout=1000 (×1.25ms/×10ms) |

CCCD descriptor for notifications: `00002902-0000-1000-8000-00805f9b34fb`

## Node Addressing

The device has a **2-byte node address** derived from the last two octets of its BLE MAC address. The app computes the address from the MAC string, but **does not use the MAC-derived address for commands during initial calibration** — it uses the placeholder `[0x11, 0x11]` instead (see below).

### Node address derivation (from `AxisGearDevice.getNodeAddress(String btAddress)`)

```
MAC string:   "00:04:09:30:32:93"
               0123456789012345678
substring(12, 14) → "32" → 0x32
substring(15)      → "93" → 0x93
node address = [0x32, 0x93]
```

For MAC `00:04:09:88:45:0D`, node address = `[0x45, 0x0D]`.

**Live testing confirmed:** ADDRESS_CHARACTERISTIC reads as `[0x00, 0x00]` on an unconfigured device — this is not the operational node address.

**Command address during initial calibration:** When the device is unconfigured (`[0x00, 0x00]` or `[0xFF, 0xFF]`) or has the placeholder address (`[0x11, 0x11]`), the app sets `helper.node_address` to `[0x11, 0x11]` via `calibrationSetRandomMeshAddress()` → `setNodeAddress(new byte[]{17, 17})` (`BluetoothGeneralHelper.java:1610`). **All commands during initial setup — including "gear added", "enter calibration mode", and jog commands — use `[0x11, 0x11]` as the node address, not the MAC-derived address.** The MAC-derived address is only written to ADDRESS_CHARACTERISTIC at finalization (`SetupClosePos.java:80`). For already-configured devices (address ≠ `[0x00, 0x00]`/`[0xFF, 0xFF]`/`[0x11, 0x11]`), the MAC-derived address is used for commands.

### Address buffers

There are two pre-allocated address buffers (both `BleUtil` and `BluetoothControlHelper` maintain their own copies):
- `controlWithNodeAddress` — 5 bytes: used for position commands
- `controlWithNodeAddress2` — 4 bytes: used for smart home mode **and** bootloader mode

`BleUtil` also declares a `controlWithNodeAddress3` (4 bytes) used in `EnableBootLoaderMode()`, but it is **never populated** — it stays as `[0x00, 0x00, 0x00, 0x00]`. In contrast, `BluetoothControlHelper.EnableBootLoaderMode()` correctly uses `controlWithNodeAddress2`.

### Buffer layout for `controlWithNodeAddress` (5 bytes)

```
[controlStart(2)] [nodeAddress(2)] [controlCenter(1)]
= [0x00 0x00] [addr_hi addr_lo] [0x00]
```

Set by `setControlWithNodeAddress(nodeAddr, mode)`:
- mode=0 (single): `controlStart` = `[0x00, 0x00]`
- mode=1 (group): `groupControlStart` = `[0x00, 0x01]`

Note: the code has a quirk — after the mode branch, it unconditionally overwrites the first 2 bytes with `controlStart` again, effectively ignoring group mode. This bug exists in both `BleUtil.setControlWithNodeAddress()` (line 163–164) and `BluetoothControlHelper.setControlWithNodeAddress()` (line 686–688). This is almost certainly a bug in the app — see gap #2.

## Shade Control Commands

Written to CONTROL_CHARACTERISTIC (`0003C2B1`). App uses write-with-response (`WRITE_TYPE_DEFAULT=2`). On Linux/bluez, write-with-response fails with `Insufficient Resource` (GATT 0x11) — write-without-response was used as a workaround but the root cause is unresolved.

### Position Command (only control command used by the app)

**The app does not send separate open/close/stop commands for normal shade control.** The `up` (`00 03 00 00`), `down` (`00 02 00 00`), and `stop` (`00 01 00 00`) byte arrays exist in `BleUtil` but are only used in calibration context (`calibrationDirUp`, `calibrationDirDown`, `calibrationStop`). For normal operation, the app exclusively uses position commands — presumably setting 0 or 100 for fully open/closed.

### Position Command

`ComputePositionCommand(position)` builds:

```
[controlWithNodeAddress(5)] [position(1)] [controlEnd(2)]
= [0x00 0x00] [addr_hi addr_lo] [0x00] [pos] [0x00 0x00]
```

Total: **8 bytes**. Position is 0–100 (0x00–0x64), directly cast to byte.

## Position Feedback

Read from FEEDBACK_CHARACTERISTIC (`0005CC10`). Despite the app code's broadcast receiver pattern suggesting notifications, the characteristic does not support notify (confirmed by live testing — see gap #3). The app polls via reads.

### Payload format

```
[0xAA] [0x01] [position] ...
```

Parsing:
1. Convert payload to hex string
2. Check starts with `"AA01"` (signature)
3. Extract `hexString.substring(4, 6)` → position as hex (e.g., `"32"` = 50%)

## Calibration Commands

Written to CONTROL_CHARACTERISTIC (`0003C2B1`) via `BluetoothGeneralHelper.deviceCalibration()`. The app code maps calibration to `Calibmode` → ADDRESS_CHAR for data type purposes, but the actual write goes through the same write queue targeting CONTROL_CHAR.

All calibration commands are prefixed with `getCalibrateNodeAddress()`:
```
[calibStart(2)] [nodeAddress(2)]
= [0x00 0x00] [addr_hi addr_lo]
```

Where `addr_hi`/`addr_lo` are derived from the MAC address (see Node Addressing). Then the command bytes are appended:

| Method | Suffix bytes | Meaning |
|--------|-------------|---------|
| `calibrationUp` | `00 00 04 06` | Move to top / mark top |
| `calibrationDown` | `00 00 04 05` | Move to bottom / mark bottom |
| `calibrationStop` | `00 01 00 00` | Stop motor |
| `calibrationStopTop` | `00 01 04 06` | Stop + confirm top position |
| `calibrationStopBottom` | `00 01 04 05` | Stop + confirm bottom position |
| `calibrationAbort` | `00 00 0F 0F` | Abort calibration |
| `calibrationReverse` | `01 00 00 00` | Reverse motor direction |
| `calibrationDirUp` | `00 03 00 00` | Jog up (same as open) |
| `calibrationDirDown` | `00 02 00 00` | Jog down (same as close) |
| `calibrationFinalize` | `00 00 01 01` | Finalize calibration |
| `calibrationEmpty` | `00 00 00 00 00` | Clear/reset (5 bytes) |

### Special Mode Commands

These use a different format: `[calibStart] [nodeAddress] [command]`

| Method | Command bytes | Meaning |
|--------|--------------|---------|
| `sendCalibratedModeCommand` | `CA CA CA 00` | Enter calibration mode |
| `sendRecalibrateCommand` | `00 00 FF FF` | Recalibrate |
| `sendAddedCommand` | `0A DD ED 00` | Gear added notification |
| `advertisePublicly` | `AD AD AD 00` | Enable public BLE advertising |

### Smart Home & Bootloader Modes

Written to CONTROL_CHARACTERISTIC (`0003C2B1`):

| Method | Format | Command bytes |
|--------|--------|--------------|
| `EnableSmartHomeMode` | `[nodeAddr2(4)] + cmd` | `BE DE AF 00` |
| `EnableBootLoaderMode` | `[nodeAddr2(4)] + cmd` | `0A 0B 0C 00` |

Note: Both use `controlWithNodeAddress2` (4 bytes). `BleUtil.EnableBootLoaderMode()` incorrectly uses the never-populated `controlWithNodeAddress3` — see address buffers section above.

## Schedule Commands

Written to SCHEDULE_CHARACTERISTIC (`0003CAC1`).

### Packet format

`ComputeScheduleCommand(hexString, position)` builds:

```
[0xAA] [position] [scheduleData...] [0xBB]
```

- Byte 0: `0xAA` (start marker)
- Byte 1: position (0–100, cast to byte)
- Bytes 2..N-1: schedule data from `hexStringToScheduleByteArray(hexString)`, excluding the last byte of the parsed array
- Byte N: `0xBB` (end marker)

### Schedule hex string special cases

When parsing the schedule hex string, `"FF"` bytes are replaced with `0x64` (100), **unless** the full string is `"0000FF0000"` or `"AA010000FF0000BB"` — those are left as-is.

### Known schedule strings

- `"0000FF0000"` — likely a "delete schedule" or "clear" command
- `"AA010000FF0000BB"` — appears to be a full clear/reset packet

## Current Time Service (CTS)

Written to CTS_CHARACTERISTIC (`0003CAB1`) before schedule operations.

### Time format

A **6-byte packed BCD-like date/time**:

| Byte | Content | Example |
|------|---------|---------|
| 0 | Year (last 2 digits) | `0x26` for 2026 |
| 1 | Month (01-12) | `0x04` for April |
| 2 | Day (01-31) | `0x12` for 12th |
| 3 | Hour (00-23) | `0x0E` for 14:00 |
| 4 | Minute (00-59) | `0x1E` for :30 |
| 5 | Second (00-59) | `0x19` for :25 |

Constructed via `Util.getCurrentTime()` → 12-char hex string → `hexStringToByteArray()`.

Two methods use it:
- `setCTS()` — set time before scheduling
- `setCTSforDelete()` — set time before deleting a schedule

## Checksums

### Non-OTA Checksum: Two's Complement Sum

Used when `calculateCheckSum2(mode=0, ...)` is called (e.g., for schedule commands):

```
sum = 0
for i from (length-1) down to 0:
    sum += data[i] & 0xFF
checksum = (~sum + 1) & 0xFF
```

## Timing & Delays

| Context | Delay |
|---------|-------|
| Control reconnection | 500ms |
| Schedule reconnection | 5000ms |
| BLE scan timeout | 20000ms |

## Data Types Enum

The app classifies characteristic data into types (from `BleUtil$dataType`):

| Type | Characteristic |
|------|---------------|
| `nodeaddress` | `0003CCB1` (ADDRESS) |
| `schedule` | `0003CAC1` (SCHEDULE) |
| `feedback` | `0005CC10` (FEEDBACK) |
| `firmwareVersion` | `00002A26` |
| `modelVersion` | `00002A24` |
| `serialID` | `00002A25` |
| `batterydata` | `00002a19` |
| `shfeedback` | `0004FA01` (SMART_HOME_FEEDBACK) |
| `gearName` | `00002A00` (DEVICE_NAME) |
| `name` | `0003CCB2` (NAME) |
| `control_sh_bootloader` | `0003C2B1` (CONTROL) — for writes |
| `Calibmode` | `0003CCB1` (ADDRESS) — for writes |
| `calibfeedback` | (from `BleUtil` enum, usage unclear) |
| `position` | (from `BleUtil` enum, usage unclear) |
| `time` | `0003CAB1` (CTS) — for writes |
