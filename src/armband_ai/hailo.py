"""Hailo-8 helpers for the armband-ai Pi 5 side.

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

import numpy as np

log = logging.getLogger("armband_ai.hailo")


@dataclass
class HailoDeviceInfo:
    board_name: str = ""
    device_architecture: str = ""
    serial_number: str = ""
    part_number: str = ""
    product_name: str = ""
    firmware_version: str = ""
    control_protocol_version: str = ""
    logger_version: str = ""
    raw: str = ""
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
    cli = shutil.which("hailortcli")
    if not cli:
        return HailoDeviceInfo(
            available=False,
            error=(
                "hailortcli not found on PATH. "
                "Install HailoRT on the Pi 5 (e.g. `sudo apt install hailo-all`), then re-run."
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
    return False, "Hailo Python bindings not found. Install HailoRT on the Pi."


class HailoRunner:
    """Load a HEF when possible; otherwise report status and fall back gracefully.

    Feature vectors from features.py are the intended input once a model is
    compiled for Hailo-8. The infer() path uses hailo_platform if available.
    """

    def __init__(self, hef_path: str | Path | None = None):
        self.hef_path = Path(hef_path) if hef_path else None
        self.device = identify()
        self._bindings_ok, self._bindings_msg = try_import_hailort()
        self._network_group = None
        self._input_vstream_info = None
        self._output_vstream_info = None
        self._hef_loaded = False
        self._load_error = ""

        if self.ready_to_load:
            self._try_load_hef()

    @property
    def ready_to_load(self) -> bool:
        return (
            self.device.available
            and self._bindings_ok
            and self.hef_path is not None
            and self.hef_path.exists()
        )

    @property
    def ready(self) -> bool:
        return self.ready_to_load and self._hef_loaded

    def _try_load_hef(self) -> None:
        """Best-effort HEF configure via hailo_platform."""
        try:
            from hailo_platform import (  # type: ignore
                HEF,
                ConfigureParams,
                FormatType,
                HailoStreamInterface,
                InferVStreams,
                InputVStreamParams,
                OutputVStreamParams,
                VDevice,
            )

            hef = HEF(str(self.hef_path))
            self._vdevice = VDevice()
            configure_params = ConfigureParams.create_from_hef(
                hef, interface=HailoStreamInterface.PCIe
            )
            network_groups = self._vdevice.configure(hef, configure_params)
            self._network_group = network_groups[0]
            self._network_group_params = self._network_group.create_params()
            self._input_vstreams_params = InputVStreamParams.make(
                self._network_group, format_type=FormatType.FLOAT32
            )
            self._output_vstreams_params = OutputVStreamParams.make(
                self._network_group, format_type=FormatType.FLOAT32
            )
            self._InferVStreams = InferVStreams
            self._hef_loaded = True
            log.info("HEF loaded: %s", self.hef_path)
        except Exception as e:
            self._hef_loaded = False
            self._load_error = str(e)
            log.warning("HEF load failed: %s", e)

    def status(self) -> dict[str, Any]:
        return {
            "device": self.device.to_dict(),
            "bindings_ok": self._bindings_ok,
            "bindings_msg": self._bindings_msg,
            "hef_path": str(self.hef_path) if self.hef_path else None,
            "hef_exists": bool(self.hef_path and self.hef_path.exists()),
            "hef_loaded": self._hef_loaded,
            "load_error": self._load_error,
            "ready": self.ready,
        }

    def infer(self, feature_vector: Any) -> np.ndarray:
        """Run HEF inference on a feature vector / batch.

        feature_vector: 1-D or 2-D array-like float32. Shape must match HEF input.
        Returns model output as numpy array.
        """
        if not self.ready:
            raise RuntimeError(f"HailoRunner not ready. status={self.status()}")

        arr = np.asarray(feature_vector, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)

        input_name = list(self._input_vstreams_params.keys())[0]
        with self._network_group.activate(self._network_group_params):
            with self._InferVStreams(
                self._network_group,
                self._input_vstreams_params,
                self._output_vstreams_params,
            ) as pipeline:
                results = pipeline.infer({input_name: arr})
        # results is dict name -> ndarray
        if isinstance(results, dict):
            out = next(iter(results.values()))
            return np.asarray(out)
        return np.asarray(results)
