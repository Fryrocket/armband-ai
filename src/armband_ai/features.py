"""Sliding-window feature extraction from armband PPG / 940 nm readings.

These features are the bridge between the MQTT logger and any future model
(CPU baseline or Hailo-8 HEF). Keep them deterministic and easy to export.

The default 17-float vector order is frozen for Hailo MLP / HEF training.
Extra diagnostic fields (max_clean_streak, clean_fraction, n_valid_bpm,
n_valid_spo2, n_valid_motion) live on WindowFeatures but are not part of
that vector unless callers request them.

Sentinel policy (2026-08-12 / 13)
---------------------------------
Firmware emits bpm=0 (or NULL) and spo2<0 when the finger/contact is absent.
Feature extraction *filters* those sentinels so averages are not pulled to
zero, but a fixed-width vector still needs a number in every slot. When a
window has *no* valid bpm samples (bpm > 0) or *no* valid spo2 samples
(spo2 > 0) the invented 0.0 is a lie. A missing `moving` column (or zero
valid motion samples) inventing still_fraction=1.0 is the same lie.
Only the quality gate can refuse the window (see quality.py hard
invalidation). extract_window_features still returns the numbers for
diagnostics; score_window hard-fails those cases.
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
    spo2_mean: float              # ignores invalid (<=0) values
    # bpm_mean / bpm_std ignore non-positive (no-finger / not-yet) samples
    n_valid_bpm: int              # count of bpm > 0 in the window (gate uses this)
    n_valid_spo2: int             # count of spo2 > 0 in the window (gate uses this)
    n_valid_motion: int           # count of non-null `moving` samples (gate uses this)
    temp_mean: float

    # Motion
    motion_mean: float
    motion_max: float
    still_fraction: float         # fraction of samples with moving==0
    moving_transitions: int       # count of still↔moving edges

    # Consecutive-clean (still + optically stable)
    max_clean_streak: int         # longest consecutive clean sample run
    clean_fraction: float         # fraction of samples that are clean

    # Battery (diagnostic)
    batt_mean: float

    def to_dict(self) -> dict:
        return asdict(self)

    def to_vector(self, keys: list[str] | None = None) -> np.ndarray:
        """Flat float vector for model input. Default uses a stable key order."""
        d = self.to_dict()
        if keys is None:
            # Frozen 17-vector for Hailo / MLP path — do not reorder.
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


def _safe_std(series: pd.Series, ddof: int = 1) -> float:
    """Sample std (ddof=1) by default — features feed a fit."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) <= ddof:
        return 0.0
    return float(s.std(ddof=ddof))


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


def _clean_streak_metrics(
    moving: pd.Series,
    filt940: pd.Series,
    *,
    roll: int = 5,
    rel_thresh: float = 0.06,
    range_frac: float = 0.12,
) -> tuple[int, float]:
    """Longest consecutive clean run + clean fraction.

    A sample is *clean* when:
      - still (moving == 0), and
      - optically stable vs a short centered rolling median of filt940
        (|x - med| / max(med, 1) <= rel_thresh) and local relative range
        is not extreme (range / max(med, 1) <= range_frac).

    When the rolling window has <2 valid points, optical checks are skipped
    (still alone is enough). Empty input → (0, 0.0).
    """
    n = len(moving)
    if n == 0:
        return 0, 0.0

    mov = pd.to_numeric(moving, errors="coerce").fillna(1).astype(int).to_numpy()
    filt = pd.to_numeric(filt940, errors="coerce").to_numpy(dtype=float)

    # Rolling median / range on filt940 (centered; min_periods=1)
    s = pd.Series(filt)
    med = s.rolling(window=roll, center=True, min_periods=1).median().to_numpy()
    lo = s.rolling(window=roll, center=True, min_periods=1).min().to_numpy()
    hi = s.rolling(window=roll, center=True, min_periods=1).max().to_numpy()

    clean = np.zeros(n, dtype=bool)
    for i in range(n):
        if mov[i] != 0:
            continue
        m = med[i]
        if not np.isfinite(m) or not np.isfinite(filt[i]):
            # still but unusable optics — treat as not clean
            continue
        denom = max(abs(m), 1.0)
        rel_dev = abs(filt[i] - m) / denom
        rel_range = (hi[i] - lo[i]) / denom if np.isfinite(hi[i]) and np.isfinite(lo[i]) else 0.0
        if rel_dev <= rel_thresh and rel_range <= range_frac:
            clean[i] = True

    # Longest consecutive True run
    max_streak = 0
    cur = 0
    for c in clean:
        if c:
            cur += 1
            if cur > max_streak:
                max_streak = cur
        else:
            cur = 0

    clean_fraction = float(clean.mean()) if n else 0.0
    return int(max_streak), clean_fraction


def extract_window_features(df: pd.DataFrame) -> Optional[WindowFeatures]:
    """Build WindowFeatures from a DataFrame of PPG rows (oldest → newest).

    Always returns numbers (including 0.0 for empty valid sets). Callers that
    need to *refuse* a window with no valid bpm, spo2, or motion data must
    use the quality gate (score_window / score_dataframe), which hard-fails
    those cases.
    """
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

    # SpO2: ignore invalid (<= 0). Firmware sentinel is -1; 0% is not a
    # measurement (symmetric with bpm > 0). When n_valid_spo2 == 0 the
    # gate must refuse the window.
    spo2 = pd.to_numeric(work.get("spo2", pd.Series(dtype=float)), errors="coerce")
    spo2_valid = spo2[spo2 > 0]
    n_valid_spo2 = int(len(spo2_valid))
    spo2_mean = float(spo2_valid.mean()) if n_valid_spo2 else 0.0

    # BPM: ignore non-positive (firmware 0 / insert NULL for no-finger).
    # Without this filter a loose band produces bpm_mean near 0 and the model
    # treats it as signal. When n_valid_bpm == 0 the gate must refuse the window.
    bpm = pd.to_numeric(work.get("bpm", pd.Series(dtype=float)), errors="coerce")
    bpm_valid = bpm[bpm > 0]
    n_valid_bpm = int(len(bpm_valid))
    bpm_mean = float(bpm_valid.mean()) if n_valid_bpm else 0.0
    # Sample std (ddof=1). Single valid sample → 0.0 (gate may still accept
    # on other grounds, but a one-sample "perfectly steady" pulse is weak).
    bpm_std = float(bpm_valid.std(ddof=1)) if n_valid_bpm > 1 else 0.0

    # Motion: missing column (or zero valid samples) is NOT still_fraction=1.0.
    # A dead/unconfigured LIS3DH must hard-fail at the gate (no_motion_data).
    # Per-sample NaN → MOVING (fillna(1)), matching _clean_streak_metrics.
    if "moving" in work.columns:
        moving_raw = pd.to_numeric(work["moving"], errors="coerce")
        n_valid_motion = int(moving_raw.notna().sum())
        moving_num = moving_raw.fillna(1).astype(int)
        still_fraction = float((moving_num == 0).mean())
        transitions = int((moving_num.diff().abs() == 1).sum())
    else:
        n_valid_motion = 0
        moving_num = pd.Series(np.ones(n, dtype=int))
        still_fraction = 0.0
        transitions = 0

    filt_series = work.get("filt940", pd.Series(np.zeros(n, dtype=float)))
    max_clean_streak, clean_fraction = _clean_streak_metrics(moving_num, filt_series)

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
        bpm_mean=bpm_mean,
        bpm_std=bpm_std,
        spo2_mean=spo2_mean,
        n_valid_bpm=n_valid_bpm,
        n_valid_spo2=n_valid_spo2,
        n_valid_motion=n_valid_motion,
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
        max_clean_streak=max_clean_streak,
        clean_fraction=clean_fraction,
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
