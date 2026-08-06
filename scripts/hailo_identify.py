#!/usr/bin/env python3
"""Discover the Hailo device on this Pi and optionally save identity JSON.

Usage:
  python scripts/hailo_identify.py
  python scripts/hailo_identify.py --extended
  python scripts/hailo_identify.py --save models/hailo_device.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.hailo import identify, identify_extended, try_import_hailort


def main() -> None:
    parser = argparse.ArgumentParser(description="Identify Hailo device on this host")
    parser.add_argument(
        "--extended",
        action="store_true",
        help="Prefer hailortcli --extended output when available",
    )
    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="Path to write device JSON (default: models/hailo_device.json)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON to stdout",
    )
    args = parser.parse_args()

    info = identify_extended() if args.extended else identify()
    bindings_ok, bindings_msg = try_import_hailort()

    print("=== Hailo device ===")
    if not info.available:
        print(f"  NOT AVAILABLE: {info.error}")
    else:
        print(f"  Board name     : {info.board_name or '—'}")
        print(f"  Architecture   : {info.device_architecture or '—'}")
        print(f"  Serial number  : {info.serial_number or '—'}")
        print(f"  Part number    : {info.part_number or '—'}")
        print(f"  Product name   : {info.product_name or '—'}")
        print(f"  Firmware       : {info.firmware_version or '—'}")
        print(f"  is_hailo8      : {info.is_hailo8}")
        print(f"  is_hailo8l     : {info.is_hailo8l}")
        if info.extra:
            print("  Extra fields:")
            for k, v in info.extra.items():
                print(f"    {k}: {v}")

    print()
    print("=== Python bindings ===")
    print(f"  {bindings_msg}")

    print()
    print("=== Expected silicon (from board photos) ===")
    print("  Marking : HAILO / HNC18B1 118H / PHH808.00 / 19DR12 / 2322")
    print("  Part    : HNC18BI11BH (industrial Hailo-8, 26 TOPS)")
    print("  Note    : Some 13 TOPS (8L) HATs ship with the same marking;")
    print("            trust the live Architecture field above.")

    save_path = Path(args.save) if args.save else (ROOT / "models" / "hailo_device.json")
    if info.available or args.save:
        payload = info.to_dict()
        payload["bindings_ok"] = bindings_ok
        payload["bindings_msg"] = bindings_msg
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\nSaved → {save_path}")

    if args.json:
        print()
        print(json.dumps(info.to_dict(), indent=2))

    if not info.available:
        sys.exit(1)


if __name__ == "__main__":
    main()
