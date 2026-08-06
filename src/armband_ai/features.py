"""Sliding-window feature extraction from armband PPG / 940 nm readings.

These features are the bridge between the MQTT logger and any future model
(CPU baseline or Hailo-8 HEF). Keep them deterministic and easy to export.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .db import get_connection, init_db
from .queries import load_recent


@dataclass
class WindowFeatures:
    """Summary stats over one time window of armband samples."""

    n_samples: int
    duration_s: float
    start_ts: str
    end_ts: str

    # 940 nm
    filt940_mean: float
    filt940_std: float
    filt940_min: float
    filt940_max: float
    filt940_slope: float          # simple linear slope vs sample index
    raw940_mean: float

    # PPG / vitals
    bpm_mean: float
    bpm_std: float
    spo2_mean: float              # ignores invalid (<0) values
    temp_mean: float

    # Motion
    motion_mean: float
    motion_max: float
    still_fraction: float         # fraction of samples with moving==0
    moving_transitions: int       # count of still↔moving edges

    # Battery (diagnostic)
    batt_mean: float

    def to_dict(self) -> dict:
        return asdict(self)

    def to_vector(self, keys: list[str] | None = None) -> np.ndarray:
        """Flat float vector for model input. Default uses a stable key order."""
        d = self.to_dict()
        if keys is None:
            keys = [
                "filt940_mean",
                "filt940_std",
                "filt940_min",
                "filt940_max",
                "filt940_slope",
                "raw940_mean",
                "bpm_mean",
                "bpm_std",
                "spo2_mean",
                "temp_mean",
                "motion_mean",
                "motion_max",
                "still_fraction",
                "moving_transitions",
                "batt_mean",
                "n_samples",
                "duration_s",
            ]
        return np.array([float(d.get(k, 0.0) or 0.0) for k in keys], dtype=np.float32)


def _safe_mean(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.mean()) if len(s) else 0.0


def _safe_std(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.std()) if len(s) > 1 else 0.0


def _safe_minmax(series: pd.Series) -> tuple[float, float]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) == 0:
        return 0.0, 0.0
    return float(s.min()), float(s.max())


def _linear_slope(y: pd.Series) -> float:
    """Slope of y vs 0..n-1 using least squares. Returns 0 if <2 points."""
    vals = pd.to_numeric(y, errors="coerce").dropna().to_numpy(dtype=float)
    n = len(vals)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    # slope = cov(x,y) / var(x)
    x_mean = x.mean()
    y_mean = vals.mean()
    denom = np.sum((x - x_mean) ** 2)
    if denom <= 0:
        return 0.0
    return float(np.sum((x - x_mean) * (vals - y_mean)) / denom)


def extract_window_features(df: pd.DataFrame) -> Optional[WindowFeatures]:
    """Build WindowFeatures from a DataFrame of PPG rows (oldest → newest)."""
    if df is None or df.empty:
        return None

    work = df.copy()
    if "received_at" in work.columns:
        work["received_at"] = pd.to_datetime(work["received_at"], utc=True)
        work = work.sort_values("received_at")

    n = len(work)
    start_ts = str(work["received_at"].iloc[0]) if "received_at" in work.columns else ""
    end_ts = str(work["received_at"].iloc[-1]) if "received_at" in work.columns else ""
    if "received_at" in work.columns and n >= 2:
        duration_s = float(
            (work["received_at"].iloc[-1] - work["received_at"].iloc[0]).total_seconds()
        )
    else:
        duration_s = 0.0

    filt_min, filt_max = _safe_minmax(work.get("filt940", pd.Series(dtype=float)))

    # SpO2: ignore invalid (< 0) from firmware
    spo2 = pd.to_numeric(work.get("spo2", pd.Series(dtype=float)), errors="coerce")
    spo2_valid = spo2[spo2 >= 0]
    spo2_mean = float(spo2_valid.mean()) if len(spo2_valid) else 0.0

    moving = work.get("moving")
    if moving is not None:
        moving_num = pd.to_numeric(moving, errors="coerce").fillna(0).astype(int)
        still_fraction = float((moving_num == 0).mean())
        transitions = int((moving_num.diff().abs() == 1).sum())
    else:
        still_fraction = 1.0
        transitions = 0

    return WindowFeatures(
        n_samples=n,
        duration_s=duration_s,
        start_ts=start_ts,
        end_ts=end_ts,
        filt940_mean=_safe_mean(work.get("filt940", pd.Series(dtype=float))),
        filt940_std=_safe_std(work.get("filt940", pd.Series(dtype=float))),
        filt940_min=filt_min,
        filt940_max=filt_max,
        filt940_slope=_linear_slope(work.get("filt940", pd.Series(dtype=float))),
        raw940_mean=_safe_mean(work.get("raw940", pd.Series(dtype=float))),
        bpm_mean=_safe_mean(work.get("bpm", pd.Series(dtype=float))),
        bpm_std=_safe_std(work.get("bpm", pd.Series(dtype=float))),
        spo2_mean=spo2_mean,
        temp_mean=_safe_mean(work.get("temp", pd.Series(dtype=float))),
        motion_mean=_safe_mean(work.get("motion", pd.Series(dtype=float))),
        motion_max=float(
            pd.to_numeric(work.get("motion", pd.Series(dtype=float)), errors="coerce")
            .dropna()
            .max()
            if "motion" in work.columns
            and pd.to_numeric(work["motion"], errors="coerce").notna().any()
            else 0.0
        ),
        still_fraction=still_fraction,
        moving_transitions=transitions,
        batt_mean=_safe_mean(work.get("batt", pd.Series(dtype=float))),
    )


def features_from_db(
    db_path: str | Path,
    minutes: int = 5,
    limit: Optional[int] = None,
) -> Optional[WindowFeatures]:
    """Load recent rows from SQLite and compute one window of features."""
    init_db(db_path)
    if limit is not None:
        df = load_recent(db_path, limit=limit)
    else:
        df = load_recent(db_path, minutes=minutes)
    return extract_window_features(df)


def rolling_feature_frames(
    db_path: str | Path,
    window_seconds: int = 120,
    step_seconds: int = 30,
    lookback_minutes: int = 60,
) -> pd.DataFrame:
    """Compute overlapping feature windows over recent history.

    Returns a DataFrame with one row per window (useful for offline training).
    """
    init_db(db_path)
    df = load_recent(db_path, minutes=lookback_minutes)
    if df.empty or "received_at" not in df.columns:
        return pd.DataFrame()

    df = df.copy()
    df["received_at"] = pd.to_datetime(df["received_at"], utc=True)
    df = df.sort_values("received_at").reset_index(drop=True)

    t0 = df["received_at"].iloc[0]
    t1 = df["received_at"].iloc[-1]
    rows = []
    cursor = t0

    while cursor + pd.Timedelta(seconds=window_seconds) <= t1 + pd.Timedelta(seconds=1):
        end = cursor + pd.Timedelta(seconds=window_seconds)
        mask = (df["received_at"] >= cursor) & (df["received_at"] < end)
        chunk = df.loc[mask]
        feats = extract_window_features(chunk)
        if feats is not None and feats.n_samples >= 3:
            rows.append(feats.to_dict())
        cursor += pd.Timedelta(seconds=step_seconds)

    return pd.DataFrame(rows)
