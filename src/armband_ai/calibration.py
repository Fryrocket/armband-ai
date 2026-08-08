"""Calibration: pair Libre readings with nearest armband samples + baseline model.

--- FIX APPLIED (still_fraction) ---
build_calibration_pairs() previously computed `still_fraction` AFTER already
filtering `candidates` down to moving==0 rows (when prefer_still=True and any
still sample existed). That made still_fraction trivially 1.0 for almost any
window, silently defeating the min_still_fraction quality gate. still_fraction
is now computed on the *original* unfiltered window before the prefer_still
filter is applied, so the gate actually reflects how still the raw window was.

--- CONSECUTIVE-CLEAN ---
Additionally gate on max_clean_streak (still + optically stable run length)
computed on the raw window via WindowFeatures. Prefer-still alone can still
cherry-pick short clean snippets; requiring a sustained clean streak rejects
those. Default min_clean_streak=0 keeps old behaviour; recommend 10-15.

--- FIX APPLIED (quality_score) ---
score_dataframe() was previously called AFTER the prefer_still filter had
already dropped every moving sample from `candidates`. quality.score_window
penalizes almost entirely on motion (still_fraction, motion_mean,
moving_transitions), so scoring a motion-free subset silently inflated
quality_score in exactly the same way the still_fraction bug did — the
min_quality gate was effectively checking "is this window clean once you
throw away everything that wasn't," not "was this window clean." quality is
now computed from `raw_feats` (the same unfiltered window used for
still_fraction/clean_streak) via score_window(), before prefer_still is
applied. The prefer_still filter still runs afterward, but only to decide
which rows are averaged into filt940_mean etc. — it no longer affects what
gets scored or gated.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .db import get_connection, init_db
from .features import extract_window_features
from .quality import score_window
from .queries import load_libre


@dataclass
class BaselineModel:
    """Simple linear model: glucose ≈ slope * filt940 + intercept"""

    slope: float
    intercept: float
    r2: float
    mae: float
    rmse: float
    n_pairs: int
    window_seconds: int
    prefer_still: bool
    min_quality: float = 0.0
    min_still_fraction: float = 0.0
    min_clean_streak: int = 0

    def predict(self, filt940: float | np.ndarray) -> float | np.ndarray:
        return self.slope * filt940 + self.intercept

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "BaselineModel":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return cls(
            slope=float(d["slope"]),
            intercept=float(d["intercept"]),
            r2=float(d["r2"]),
            mae=float(d["mae"]),
            rmse=float(d["rmse"]),
            n_pairs=int(d["n_pairs"]),
            window_seconds=int(d.get("window_seconds", 180)),
            prefer_still=bool(d.get("prefer_still", True)),
            min_quality=float(d.get("min_quality", 0.0)),
            min_still_fraction=float(d.get("min_still_fraction", 0.0)),
            min_clean_streak=int(d.get("min_clean_streak", 0)),
        )


def build_calibration_pairs(
    db_path: str | Path,
    window_seconds: int = 180,
    prefer_still: bool = True,
    min_samples: int = 1,
    min_quality: float = 0.0,
    min_still_fraction: float = 0.0,
    min_clean_streak: int = 0,
) -> pd.DataFrame:
    """Match each Libre reading to nearby armband samples.

    Strategy
    --------
    For each Libre timestamp T:
      - Find PPG rows with received_at in [T - window, T + window]  (raw window)
      - Compute still_fraction, max_clean_streak, and quality_score on that
        *raw* window (before any filtering) via a single WindowFeatures pass
      - Gate on min_still_fraction, min_clean_streak, and min_quality first —
        all three reflect the window as actually recorded
      - THEN, for aggregation only, prefer non-moving samples if prefer_still
        and any exist
      - Aggregate: mean filt940, mean raw940, mean motion, count (over the
        aggregation set)

    Returns a DataFrame with one row per successful pair.
    """
    init_db(db_path)
    libre = load_libre(db_path)
    if libre.empty:
        return pd.DataFrame()

    with get_connection(db_path) as conn:
        ppg = pd.read_sql_query(
            "SELECT id, received_at, filt940, raw940, motion, moving, bpm, temp, spo2, batt "
            "FROM ppg_readings ORDER BY received_at ASC",
            conn,
        )

    if ppg.empty:
        return pd.DataFrame()

    ppg["received_at"] = pd.to_datetime(ppg["received_at"], utc=True)

    pairs = []
    half = timedelta(seconds=window_seconds)

    for _, row in libre.iterrows():
        t = row["recorded_at"]
        mask = (ppg["received_at"] >= t - half) & (ppg["received_at"] <= t + half)
        candidates = ppg.loc[mask].copy()

        if candidates.empty:
            continue

        # still_fraction, consecutive-clean, and quality — all computed on the
        # *raw* unfiltered window, in one pass, before any prefer_still filter.
        raw_feats = extract_window_features(candidates)
        if raw_feats is None:
            continue

        still_fraction = float(raw_feats.still_fraction)
        max_clean_streak = int(raw_feats.max_clean_streak)
        clean_fraction = float(raw_feats.clean_fraction)

        if still_fraction < min_still_fraction:
            continue
        if max_clean_streak < min_clean_streak:
            continue

        q = score_window(raw_feats)
        quality_score = float(q.score)
        quality_label = q.label
        if quality_score < min_quality:
            continue

        # Aggregation set: prefer still samples, but this no longer affects
        # what was scored or gated above.
        agg = candidates
        if prefer_still and (candidates["moving"] == 0).any():
            agg = candidates[candidates["moving"] == 0]

        if len(agg) < min_samples:
            continue

        median_t = agg["received_at"].median()
        offset_s = (median_t - t).total_seconds()

        pairs.append(
            {
                "libre_id": int(row["id"]),
                "recorded_at": t,
                "glucose_mgdl": float(row["glucose_mgdl"]),
                "source": row.get("source", "libre"),
                "notes": row.get("notes"),
                "n_samples": len(agg),
                "filt940_mean": float(agg["filt940"].mean()),
                "filt940_std": float(agg["filt940"].std()) if len(agg) > 1 else 0.0,
                "raw940_mean": float(agg["raw940"].mean()),
                "motion_mean": float(agg["motion"].mean()),
                "still_fraction": still_fraction,
                "max_clean_streak": max_clean_streak,
                "clean_fraction": clean_fraction,
                "quality_score": quality_score,
                "quality_label": quality_label,
                "time_offset_s": offset_s,
            }
        )

    if not pairs:
        return pd.DataFrame()

    return pd.DataFrame(pairs)


def fit_baseline(
    pairs: pd.DataFrame,
    window_seconds: int = 180,
    prefer_still: bool = True,
    min_quality: float = 0.0,
    min_still_fraction: float = 0.0,
    min_clean_streak: int = 0,
) -> Optional[BaselineModel]:
    """Fit glucose = slope * filt940 + intercept using ordinary least squares."""
    if pairs is None or len(pairs) < 2:
        return None

    x = pairs["filt940_mean"].to_numpy(dtype=float)
    y = pairs["glucose_mgdl"].to_numpy(dtype=float)

    slope, intercept = np.polyfit(x, y, 1)
    y_hat = slope * x + intercept

    ss_res = np.sum((y - y_hat) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    mae = float(np.mean(np.abs(y - y_hat)))
    rmse = float(np.sqrt(np.mean((y - y_hat) ** 2)))

    return BaselineModel(
        slope=float(slope),
        intercept=float(intercept),
        r2=float(r2),
        mae=mae,
        rmse=rmse,
        n_pairs=len(pairs),
        window_seconds=window_seconds,
        prefer_still=prefer_still,
        min_quality=float(min_quality),
        min_still_fraction=float(min_still_fraction),
        min_clean_streak=int(min_clean_streak),
    )
