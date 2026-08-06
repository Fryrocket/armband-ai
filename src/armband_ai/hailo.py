"""Hailo-8 helpers for the armband-ai Pi 5 side.

Goals
-----
- Discover the live device (architecture, serial, firmware) via hailortcli
- Provide a thin, optional Python binding wrapper when hailort is installed
- Stay usable even when the Hailo runtime is not yet installed on the Pi

Confirmed silicon (2026-08-06 photos):
  HAILO / HNC18B1 118H / PHH808.00 / 19DR12 / 2322
  → industrial Hailo-8 (HNC18BI11BH), 26 TOPS
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("armband_ai.hailo")


@dataclass
class HailoDeviceInfo:
    """Parsed output from `hailortcli fw-control identify`."""

    board_name: str = ""
    device_architecture: str = ""          # HAILO8 | HAILO8L | HAILO10H | ...
    serial_number: str = ""
    part_number: str = ""
    product_name: str = ""
    firmware_version: str = ""
    control_protocol_version: str = ""
    logger_version: str = ""
    raw: str = ""                         # full CLI output
    available: bool = False
    error: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def is_hailo8(self) -> bool:
        arch = (self.device_architecture or "").upper()
        return "HAILO8" in arch and "HAILO8L" not in arch

    @property
    def is_hailo8l(self) -> bool:
        return "HAILO8L" in (self.device_architecture or "").upper()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_hailo8"] = self.is_hailo8
        d["is_hailo8l"] = self.is_hailo8l
        return d

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)


def _parse_identify_output(text: str) -> HailoDeviceInfo:
    info = HailoDeviceInfo(raw=text, available=True)
    key_map = {
        "board name": "board_name",
        "device architecture": "device_architecture",
        "serial number": "serial_number",
        "part number": "part_number",
        "product name": "product_name",
        "firmware version": "firmware_version",
        "control protocol version": "control_protocol_version",
        "logger version": "logger_version",
    }
    for line in text.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key_l = key.strip().lower()
        value = value.strip()
        attr = key_map.get(key_l)
        if attr:
            setattr(info, attr, value)
        elif key_l and value:
            info.extra[key.strip()] = value
    return info


def identify(timeout: float = 15.0) -> HailoDeviceInfo:
    """Run `hailortcli fw-control identify` and parse the result.

    Returns a HailoDeviceInfo even on failure (available=False, error set).
    """
    cli = shutil.which("hailortcli")
    if not cli:
        return HailoDeviceInfo(
            available=False,
            error=(
                "hailortcli not found on PATH. "
                "Install HailoRT on the Pi 5 (e.g. `sudo apt install hailo-all` "
                "or the packages from hailo.ai), then re-run."
            ),
        )

    try:
        proc = subprocess.run(
            [cli, "fw-control", "identify"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return HailoDeviceInfo(available=False, error="hailortcli timed out")
    except OSError as e:
        return HailoDeviceInfo(available=False, error=f"failed to run hailortcli: {e}")

    combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        return HailoDeviceInfo(
            available=False,
            error=f"hailortcli exit {proc.returncode}: {combined.strip()[:500]}",
            raw=combined,
        )

    info = _parse_identify_output(combined)
    if not info.device_architecture and not info.board_name:
        info.available = False
        info.error = "Could not parse device architecture from hailortcli output"
    return info


def identify_extended(timeout: float = 20.0) -> HailoDeviceInfo:
    """Same as identify(), but prefers the --extended flag when available."""
    cli = shutil.which("hailortcli")
    if not cli:
        return identify(timeout=timeout)

    try:
        proc = subprocess.run(
            [cli, "fw-control", "identify", "--extended"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return _parse_identify_output(
                (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            )
    except (subprocess.TimeoutExpired, OSError):
        pass

    return identify(timeout=timeout)


def try_import_hailort() -> tuple[bool, str]:
    """Attempt to import the Hailo Python bindings.

    Returns (ok, message). Does not raise.
    """
    try:
        import hailo_platform  # type: ignore  # noqa: F401

        return True, "hailo_platform importable"
    except ImportError:
        pass

    try:
        import hailort  # type: ignore  # noqa: F401

        return True, "hailort importable"
    except ImportError:
        pass

    return False, (
        "Hailo Python bindings not found. "
        "Install HailoRT + Python package on the Pi, then re-check."
    )


class HailoRunner:
    """Minimal placeholder for future HEF loading / inference.

    Until a real model (HEF) exists, this class only reports device status
    and refuses to run inference. Feature vectors from `features.py` are the
    intended input once a temporal / quality model is compiled for Hailo-8.
    """

    def __init__(self, hef_path: str | Path | None = None):
        self.hef_path = Path(hef_path) if hef_path else None
        self.device = identify()
        self._bindings_ok, self._bindings_msg = try_import_hailort()

    @property
    def ready(self) -> bool:
        return (
            self.device.available
            and self._bindings_ok
            and self.hef_path is not None
            and self.hef_path.exists()
        )

    def status(self) -> dict[str, Any]:
        return {
            "device": self.device.to_dict(),
            "bindings_ok": self._bindings_ok,
            "bindings_msg": self._bindings_msg,
            "hef_path": str(self.hef_path) if self.hef_path else None,
            "hef_exists": bool(self.hef_path and self.hef_path.exists()),
            "ready": self.ready,
        }

    def infer(self, feature_vector: Any) -> Any:
        if not self.ready:
            raise RuntimeError(
                "HailoRunner not ready. "
                f"status={self.status()}"
            )
        # Placeholder – real path will load HEF via hailort and run.
        raise NotImplementedError(
            "HEF inference not implemented yet. "
            "Compile a model for Hailo-8 and wire it here."
        )
