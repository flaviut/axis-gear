#!/usr/bin/env python3
"""Extract and decompile Cypress PSoC CYACD firmware files.

Converts .cyacd bootloader images to flat binaries, then optionally runs
Ghidra headless analysis to produce decompiled C output.

Usage:
    ./cyacd_decompile.py firmware.cyacd                  # extract + decompile
    ./cyacd_decompile.py firmware.cyacd --extract-only    # just produce .bin
    ./cyacd_decompile.py firmware.cyacd -o output_dir     # custom output dir
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

GHIDRA_PATHS = [
    shutil.which("analyzeHeadless"),
    "/opt/ghidra/support/analyzeHeadless",
    "/usr/local/share/ghidra/support/analyzeHeadless",
]


def find_ghidra():
    for p in GHIDRA_PATHS:
        if p and os.path.isfile(p):
            return p
    return None


def parse_cyacd(input_path):
    """Parse a CYACD file into header info and per-array row data."""
    arrays = defaultdict(list)
    with open(input_path, "r") as f:
        header = f.readline().strip()
        info = {
            "silicon_id": header[:8],
            "silicon_rev": header[8:10],
            "checksum_type": header[10:12],
        }
        for line in f:
            line = line.strip()
            if not line or line[0] != ":":
                continue
            h = line[1:]
            array_id = int(h[0:2], 16)
            row_num = int(h[2:6], 16)
            data_len = int(h[6:10], 16)
            data = bytes.fromhex(h[10 : 10 + data_len * 2])
            arrays[array_id].append((row_num, data))
    return info, dict(arrays)


def extract_binary(arrays, output_prefix):
    """Write each flash array to a flat binary file. Returns list of (path, base_addr) for array 0."""
    results = []
    for array_id, rows in sorted(arrays.items()):
        rows.sort(key=lambda r: r[0])
        row_size = len(rows[0][1])
        min_row = rows[0][0]
        max_row = rows[-1][0]
        total_size = (max_row - min_row + 1) * row_size
        base_addr = min_row * row_size

        print(f"  Array {array_id}: {len(rows)} rows ({min_row}-{max_row}), "
              f"row_size={row_size}, base=0x{base_addr:08X}, size={total_size} bytes")

        binary = bytearray(total_size)
        for row_num, data in rows:
            offset = (row_num - min_row) * row_size
            binary[offset : offset + len(data)] = data

        suffix = ".bin" if array_id == 0 else f"_array{array_id}.bin"
        out_path = f"{output_prefix}{suffix}"
        with open(out_path, "wb") as f:
            f.write(binary)
        print(f"    -> {out_path}")

        if array_id == 0:
            sp = struct.unpack_from("<I", binary, 0)[0]
            reset = struct.unpack_from("<I", binary, 4)[0]
            print(f"    Vector table: SP=0x{sp:08X} Reset=0x{reset:08X}")
            results.append((out_path, base_addr))

    return results


def run_ghidra(bin_path, base_addr, decompiled_path, script_dir):
    """Run Ghidra headless analysis and decompilation."""
    ghidra = find_ghidra()
    if not ghidra:
        print("ERROR: Ghidra analyzeHeadless not found. Install Ghidra or use --extract-only.")
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="ghidra_") as project_dir:
        cmd = [
            ghidra,
            project_dir,
            "FirmwareProject",
            "-import", bin_path,
            "-processor", "ARM:LE:32:Cortex",
            "-cspec", "default",
            "-loader", "BinaryLoader",
            "-loader-baseAddr", hex(base_addr),
            "-scriptPath", script_dir,
            "-postScript", "GhidraDecompile.java", decompiled_path,
        ]
        print(f"  Running Ghidra headless analysis...")
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Check for the decompile count line in output
        for line in result.stdout.splitlines() + result.stderr.splitlines():
            if "Decompiled" in line and "functions" in line:
                print(f"  {line.strip().split('> ')[-1]}")
                break

        if result.returncode != 0:
            # Check if it actually succeeded despite return code
            if not os.path.isfile(decompiled_path):
                print(f"  Ghidra failed (exit {result.returncode}).")
                print(f"  stderr (last 10 lines):")
                for line in result.stderr.splitlines()[-10:]:
                    print(f"    {line}")
                sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Extract and decompile Cypress PSoC CYACD firmware")
    parser.add_argument("cyacd_file", help="Input .cyacd firmware file")
    parser.add_argument("-o", "--output-dir", help="Output directory (default: ./decompiled)")
    parser.add_argument("--extract-only", action="store_true", help="Only extract .bin, skip Ghidra decompilation")
    args = parser.parse_args()

    cyacd_path = Path(args.cyacd_file).resolve()
    stem = cyacd_path.stem
    output_dir = Path(args.output_dir) if args.output_dir else cyacd_path.parent / "decompiled"
    output_dir.mkdir(parents=True, exist_ok=True)

    script_dir = str(Path(__file__).resolve().parent)

    print(f"Parsing {cyacd_path.name}...")
    info, arrays = parse_cyacd(str(cyacd_path))
    print(f"  Silicon ID: 0x{info['silicon_id']}  Rev: 0x{info['silicon_rev']}")

    output_prefix = str(output_dir / stem)
    bin_results = extract_binary(arrays, output_prefix)

    if args.extract_only:
        print("\nDone (extract only).")
        return

    if not bin_results:
        print("No code flash (array 0) found, nothing to decompile.")
        return

    bin_path, base_addr = bin_results[0]
    decompiled_path = str(output_dir / f"{stem}_decompiled.c")

    run_ghidra(bin_path, base_addr, decompiled_path, script_dir)
    print(f"\nDone. Output in {output_dir}/")


if __name__ == "__main__":
    main()
