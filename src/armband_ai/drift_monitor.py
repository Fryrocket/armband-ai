# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Fryrocket

"""Tracks slow baseline shift in filt940 relative to last calibration.

Uses the same 'still' definition as prefer_still (moving == 0) so the
baseline is directly comparable to what calibration actually trained on.

Advisory only — never blocks inference. Surfaces drift + is_stale for
the dashboard / status payload.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from statistics import median
from typing import Optional

from .config import ROOT

log = logging.getLogger("armband_ai.drift")

DEFAULT_BASELINE_PATH = ROOT / "models" / "drift_baseline.json"
DEFAULT_WINDOW = 300
DEFAULT_FLAG_THRESHOLD = 40.0  # |Δ median| advisory threshold


class DriftMonitor:
    """Rolling still-only median of filt940 vs snapshot at last successful cal."""

    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW,
        flag_threshold: Optional[float] = DEFAULT_FLAG_THRESHOLD,
        baseline_path: Optional[Path] = None,
    ):
        self._still_window: deque[float] = deque(maxlen=max(10, window_size))
        self.baseline_median: Optional[float] = None
        self.flag_threshold = flag_threshold  # None = never flag
        self.baseline_path = Path(baseline_path) if baseline_path else DEFAULT_BASELINE_PATH
        self._load_baseline()

    def _load_baseline(self) -> None:
        if not self.baseline_path.exists():
            return
        try:
            data = json.loads(self.baseline_path.read_text(encoding="utf-8"))
            val = data.get("baseline_median")
            if val is not None:
                self.baseline_median = float(val)
                log.debug("Loaded drift baseline median=%.2f from %s", self.baseline_median, self.baseline_path)
        except Exception:
            log.exception("Failed to load drift baseline from %s", self.baseline_path)

    def save_baseline(self) -> None:
        if self.baseline_median is None:
            return
        try:
            self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "baseline_median": self.baseline_median,
                "window_size": self._still_window.maxlen,
                "note": "still-only filt940 median at last successful calibration",
            }
            self.baseline_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            log.info("Drift baseline saved median=%.2f → %s", self.baseline_median, self.baseline_path)
        except OSError:
            log.exception("Failed to save drift baseline")

    def on_sample(self, filt940: float, is_still: bool) -> None:
        if is_still and filt940 is not None:
            try:
                self._still_window.append(float(filt940))
            except (TypeError, ValueError):
                pass

    def feed_still_values(self, values: list[float]) -> None:
        """Bulk-feed still-only filt940 values (e.g. from a DB query)."""
        for v in values:
            try:
                self._still_window.append(float(v))
            except (TypeError, ValueError):
                continue

    def snapshot_baseline(self) -> Optional[float]:
        """Call right after a successful calibration completes."""
        if len(self._still_window) == 0:
            return None
        self.baseline_median = median(self._still_window)
        self.save_baseline()
        return self.baseline_median

    @property
    def current_median(self) -> Optional[float]:
        if len(self._still_window) == 0:
            return None
        return median(self._still_window)

    @property
    def drift(self) -> Optional[float]:
        cur = self.current_median
        if cur is None or self.baseline_median is None:
            return None
        return cur - self.baseline_median

    @property
    def is_stale(self) -> bool:
        if self.flag_threshold is None or self.drift is None:
            return False
        return abs(self.drift) >= self.flag_threshold

    def status(self) -> dict:
        return {
            "baseline_median": self.baseline_median,
            "current_median": self.current_median,
            "drift": self.drift,
            "is_stale": self.is_stale,
            "still_samples": len(self._still_window),
            "flag_threshold": self.flag_threshold,
        }


def snapshot_baseline_from_db(
    db_path: str | Path,
    *,
    limit: int = DEFAULT_WINDOW,
    baseline_path: Optional[Path] = None,
    flag_threshold: Optional[float] = DEFAULT_FLAG_THRESHOLD,
) -> Optional[float]:
    """Compute still-only median from recent PPG rows and persist as baseline.

    Call after a successful calibrate / train_multifeature / train_mlp_onnx.
    """
    from .db import get_connection, init_db

    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT filt940 FROM ppg_readings
            WHERE moving = 0 AND filt940 IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    values = [float(r[0]) for r in rows if r[0] is not None]
    if not values:
        log.warning("No still filt940 samples available for drift baseline snapshot")
        return None

    mon = DriftMonitor(
        window_size=limit,
        flag_threshold=flag_threshold,
        baseline_path=baseline_path,
    )
    mon.feed_still_values(values)
    return mon.snapshot_baseline()


def compute_drift_from_db(
    db_path: str | Path,
    *,
    limit: int = DEFAULT_WINDOW,
    baseline_path: Optional[Path] = None,
    flag_threshold: Optional[float] = DEFAULT_FLAG_THRESHOLD,
) -> dict:
    """Load baseline + current still median from DB; return status dict."""
    from .db import get_connection, init_db

    mon = DriftMonitor(
        window_size=limit,
        flag_threshold=flag_threshold,
        baseline_path=baseline_path,
    )
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT filt940 FROM ppg_readings
            WHERE moving = 0 AND filt940 IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    values = [float(r[0]) for r in rows if r[0] is not None]
    mon.feed_still_values(values)
    return mon.status()
