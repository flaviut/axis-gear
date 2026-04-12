# OTA Firmware Update

The AXIS Gear uses Cypress (Infineon) PSoC 4 BLE bootloader protocol for over-the-air firmware updates. Firmware images use the `.cyacd` file format and are transferred over BLE.

## Firmware Source

Firmware is downloaded from **AWS S3**:

| Field | Value |
|-------|-------|
| **S3 Bucket** | `axisota` |
| **Region** | `us-east-1` |
| **Auth** | Cognito Identity Pool `us-east-1:7588162b-7715-4238-b1f4-5ac8406435c9` (unauthenticated access likely) |
| **BT firmware key** | `gr_bt01_3.3.5.1081.cyacd` |
| **Zigbee firmware key** | `gr_zb01_3.3.5.1081.cyacd` |

The S3 key is the filename directly (no path prefix).

Staging Cognito pool: `us-east-1:5141f7dd-e97d-4441-bb7e-dcfdf086da48`.

**AWS infrastructure** (from `otaServer/Constants.smali`):
- DynamoDB tables: `DeviceInventoryTable`, `AxisCollectionDataTable`, `FirmwareHistoryLog`, `InventoryLog`
- Cognito User Pool: `us-east-1_DYVcll786` (app client `1hc16fnsmqquv74bht5i82rce1`)

**Hardware identifiers** (from `update/OTAConstants.smali`):
- BT external: `0001`, BT internal: `0004`
- Zigbee external: `0002`, Zigbee internal: `0005`
- Z-Wave external: `0003`, Z-Wave internal: `0006` (Z-Wave support exists in constants but may be unused)

## File Naming Convention

- `gr_bt01_` = Gear Bluetooth variant
- `gr_zb01_` = Gear Zigbee variant
- Version: `major.minor.patch.build` (e.g., `5.3.5.1125`)
- `_cyble214015` suffix = alternate BLE module variant (CYBLE-214015 vs the standard CYBLE-214009)
- `_DEBUG_ENABLED` = debug builds with logging

## `.cyacd` File Format

A text file with Windows-style line endings (`\r\n`). Line 1 is the header; lines 2–N are flash data rows.

### Header (line 1)

A 12-character hex string. The bytes are stored in **little-endian** order and must be reversed in pairs (`getMSB`) before parsing:

| Field | Chars (after byte-swap) | Description |
|-------|------------------------|-------------|
| Checksum type | [0:2] | `00` = two's complement sum, `01` = CRC-16 |
| Silicon revision | [2:4] | Silicon revision ID |
| Silicon ID | [4:12] | Target chip identifier (e.g., `1350-11A3`) |

Example header: `135011A30000` → byte-swap → `0000A3115013` → checksum type `00`, silicon rev `00`, silicon ID `A3115013`.

### Data Rows (lines 2–N)

Each line starts with `:` followed by hex-encoded fields:

```
:AA RRRR LLLL DDDD...DDDD CC
```

| Field | Hex chars | Description |
|-------|-----------|-------------|
| `:` | 1 char | Line prefix (stripped before parsing) |
| Array ID | 2 | Flash array (e.g., `00` = user flash, `01` = config/NVL) |
| Row Number | 4 | Flash row address, **little-endian** (byte-swapped via `getMSB`) |
| Data Length | 4 | Number of data bytes as hex (e.g., `0100` = 256 bytes) |
| Data | length * 2 | Raw flash row content |
| Checksum | 2 | 1-byte row checksum |

Example row:
```
:00 0036 0100 <512 hex chars> 77
```
- Array ID `0x00` (user flash)
- Row number `0x3600` = row 54 (after byte-swap of `0036`)
- 256 bytes of data
- Checksum `0x77`

Total line length: `1 + 2 + 4 + 4 + (dataLength * 2) + 2` characters.

### Flash Row Model

Each parsed data row becomes a `FlashRowModel` with:
- `mArrayId` — flash array ID (int)
- `mRowNo` — row number as hex string (after byte-swap)
- `mDataLength` — number of data bytes (int)
- `mData` — raw data byte array
- `mRowCheckSum` — 1-byte checksum (int)

## BLE OTA Protocol

OTA commands are written to the **OTA characteristic**:

| Field | Value |
|-------|-------|
| Service UUID | `00060000-f8ce-11e4-abf4-0002a5d5c51b` |
| Characteristic UUID | `00060001-f8ce-11e4-abf4-0002a5d5c51b` |
| Write type | No response |
| Max retries | 20 |
| Retry delay | 1000ms |
| MTU | 517 |
| Connection priority | HIGH (requested alongside MTU) |

**Bonding:** Firmware versions `03.28.17` and `04.04.17` require BLE bonding (`createBond()`) before OTA. `BluetoothOTAHelper` triggers this automatically.

### Entering Bootloader Mode

Before OTA commands can be sent, the device must be switched to bootloader mode by writing to the **Control characteristic** (`0003C2B1`):

```
[controlWithNodeAddress2(4)] [0x0A 0x0B 0x0C 0x00]
```

Note: `BluetoothControlHelper.EnableBootLoaderMode()` correctly uses `controlWithNodeAddress2`. `BleUtil.EnableBootLoaderMode()` incorrectly uses the never-populated `controlWithNodeAddress3` — likely a bug.

### Command Packet Format

All OTA commands share this structure:

```
[0x01] [opcode] [size_lo] [size_hi] [data...] [crc16_lo] [crc16_hi] [0x17]
```

| Byte | Name | Description |
|------|------|-------------|
| 0 | Start | Always `0x01` |
| 1 | Opcode | Command type |
| 2–3 | Data size | 16-bit little-endian payload length |
| 4..N | Data | Command-specific payload |
| N+1..N+2 | Checksum | CRC-16 of bytes 0 through N |
| last | End | Always `0x17` |

### Opcodes

| Opcode | Name | Payload | Purpose |
|--------|------|---------|---------|
| `0x38` | ENTER_BOOTLOADER | (none) | Enter OTA mode |
| `0x32` | GET_FLASH_SIZE | array ID byte(s) | Query valid row range for an array |
| `0x37` | SEND_DATA | data chunk | Send firmware data in pieces (max 133 bytes per BLE packet) |
| `0x39` | PROGRAM_ROW | array ID (1) + row number (1) + row data offset (1) + remaining data | Commit buffered data + payload to flash |
| `0x3A` | VERIFY_ROW | array ID (1) + row lo (1) + row hi (1) | Verify a written row's integrity |
| `0x31` | VERIFY_CHECK_SUM | (none) | Verify entire firmware image checksum |
| `0x3B` | EXIT_BOOTLOADER | (none) | Reboot into new firmware |

### Update Sequence

1. **Enter bootloader** (`0x38`) — silicon ID from the `.cyacd` header is used for the checksum
2. **Get flash size** (`0x32`) — for each array ID, query the valid row range
3. For each data row in the `.cyacd` file:
   a. **Send data** (`0x37`) — send data in chunks up to 133 bytes (`MAX_DATA_SIZE = 0x85`). Large rows require multiple SEND_DATA packets.
   b. **Program row** (`0x39`) — write array ID, row number, and any remaining data to flash
   c. **Verify row** (`0x3A`) — confirm the row was written correctly
4. **Verify checksum** (`0x31`) — verify the entire firmware image
5. **Exit bootloader** (`0x3B`) — device reboots into the new firmware

### CRC-16 Checksum

OTA packets use CRC-16/ARC (polynomial `0xA001`):

```
crc = 0x0000
for each byte b in data:
    crc = (crc >> 8) XOR table[(crc XOR b) & 0xFF]
```

Uses the standard 256-entry CRC-16 lookup table (implemented in `BootLoaderUtils.computeCrc16`).

## Security: No Code Signing

**Arbitrary firmware can be flashed.** There is no cryptographic signature verification anywhere in the chain:

- The app performs zero crypto checks — no signature, HMAC, SHA, or secure boot references exist in the update/bluetooth code
- The bootloader's only checks are:
  - Silicon ID must match the target chip
  - CRC-16 integrity on each row (`VERIFY_ROW`) and the full image (`VERIFY_CHECK_SUM`)
- These are integrity checks (detecting corruption), not authenticity checks (proving origin)

The array `01` config rows contain Cypress bootloader metadata (e.g., `0xC0FFEEDE` marker, `"eltb"` BLE config block) that is static across firmware versions — not per-build signatures.

## Array `01` — Configuration Flash

All firmware files contain two flash arrays:

| Array ID | Content | Rows |
|----------|---------|------|
| `00` | User flash (application code) | Rows `0x0036`–`0x00EE` (BT), varies by variant |
| `01` | Config/NVL (non-volatile latches, BLE config, bootloader metadata) | Row `0x01FF` only |

Row `01:FF` is mostly zeroes with a small metadata block near the end, containing values like the flash entry point and bootloader configuration.
