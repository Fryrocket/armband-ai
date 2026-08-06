#!/usr/bin/env python3
"""Diagnose Hailo-8 PCIe driver, runtime, and bindings on the Pi.

Usage:
  python scripts/hailo_diagnose.py
  python scripts/hailo_diagnose.py --json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.hailo import identify_extended, try_import_hailort


def _run(cmd: list[str], timeout: float = 15.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def main() -> None:
    parser = argparse.ArgumentParser(description="Hailo driver / runtime diagnostics")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report: dict = {}

    rc, uname = _run(["uname", "-r"])
    report["kernel"] = uname if rc == 0 else None

    rc, lspci = _run(["lspci"])
    hailo_pci_lines = [ln for ln in (lspci or "").splitlines() if "hailo" in ln.lower()]
    report["lspci_hailo"] = hailo_pci_lines

    rc, lsmod = _run(["lsmod"])
    report["lsmod_hailo"] = [
        ln for ln in (lsmod or "").splitlines() if "hailo" in ln.lower()
    ]

    rc, dmesg = _run(["dmesg"], timeout=10)
    # dmesg may need privileges; still try
    hailo_dmesg = [ln for ln in (dmesg or "").splitlines() if "hailo" in ln.lower()][-15:]
    report["dmesg_hailo_tail"] = hailo_dmesg

    report["hailortcli_path"] = shutil.which("hailortcli")
    rc, ver = _run(["hailortcli", "--version"]) if report["hailortcli_path"] else (127, "")
    report["hailortcli_version"] = ver if rc == 0 else None

    rc, pkgs = _run(["dpkg-query", "-W", "-f=${Package}\t${Version}\n"])
    hailo_pkgs = [
        ln for ln in (pkgs or "").splitlines()
        if "hailo" in ln.lower()
    ]
    report["apt_hailo_packages"] = hailo_pkgs

    ok, msg = try_import_hailort()
    report["python_bindings_ok"] = ok
    report["python_bindings_msg"] = msg

    info = identify_extended()
    report["identify"] = info.to_dict()

    # Simple health score
    checks = {
        "pcie_visible": bool(hailo_pci_lines),
        "module_loaded": any("hailo_pci" in ln for ln in report["lsmod_hailo"]),
        "cli_present": bool(report["hailortcli_path"]),
        "identify_ok": bool(info.available),
        "bindings_ok": ok,
    }
    report["checks"] = checks
    report["healthy"] = all(
        checks[k] for k in ("pcie_visible", "module_loaded", "cli_present", "identify_ok")
    )

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print("=== Hailo diagnostics ===")
    print(f"Kernel:     {report['kernel']}")
    print(f"PCIe:       {hailo_pci_lines or 'NO Hailo in lspci'}")
    print(f"Module:     {report['lsmod_hailo'] or 'NO hailo_* in lsmod'}")
    print(f"hailortcli: {report['hailortcli_path'] or 'NOT ON PATH'}")
    if report["hailortcli_version"]:
        print(f"  version:  {report['hailortcli_version']}")
    print(f"Bindings:   {msg}")
    print()
    print("Packages:")
    for p in hailo_pkgs or ["(none)"]:
        print(f"  {p}")
    print()
    print("identify:")
    if info.available:
        print(f"  architecture: {info.device_architecture}")
        print(f"  board:        {info.board_name}")
        print(f"  firmware:     {info.firmware_version}")
        print(f"  serial:       {info.serial_number or 'N/A'}")
        print(f"  part:         {info.part_number or 'N/A'}")
    else:
        print(f"  FAILED: {info.error}")
    print()
    print("Checks:")
    for k, v in checks.items():
        print(f"  {'OK' if v else 'FAIL':4s}  {k}")
    print(f"\nOverall: {'HEALTHY' if report['healthy'] else 'NEEDS ATTENTION'}")
    if not report["healthy"]:
        print("See docs/HAILO_DRIVER.md for install + troubleshooting.")


if __name__ == "__main__":
    main()
