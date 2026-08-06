"""Calibration: pair Libre readings with nearest armband samples + baseline model."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .db import get_connection, init_db
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
        return cls(**d)


def build_calibration_pairs(
    db_path: str | Path,
    window_seconds: int = 180,
    prefer_still: bool = True,
    min_samples: int = 1,
) -> pd.DataFrame:
    """Match each Libre reading to nearby armband samples.

    Strategy
    --------
    For each Libre timestamp T:
      - Find PPG rows with received_at in [T - window, T + window]
      - Prefer non-moving samples if prefer_still and any exist
      - Aggregate: mean filt940, mean raw940, mean motion, count
      - Keep the pair only if ≥ min_samples found

    Returns a DataFrame with one row per successful pair.
    """
    init_db(db_path)
    libre = load_libre(db_path)
    if libre.empty:
        return pd.DataFrame()

    with get_connection(db_path) as conn:
        ppg = pd.read_sql_query(
            "SELECT id, received_at, filt940, raw940, motion, moving, bpm, temp "
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

        if prefer_still and (candidates["moving"] == 0).any():
            candidates = candidates[candidates["moving"] == 0]

        if len(candidates) < min_samples:
            continue

        # Time offset of the median candidate (for diagnostics)
        median_t = candidates["received_at"].median()
        offset_s = (median_t - t).total_seconds()

        pairs.append(
            {
                "libre_id": int(row["id"]),
                "recorded_at": t,
                "glucose_mgdl": float(row["glucose_mgdl"]),
                "source": row.get("source", "libre"),
                "notes": row.get("notes"),
                "n_samples": len(candidates),
                "filt940_mean": float(candidates["filt940"].mean()),
                "filt940_std": float(candidates["filt940"].std()) if len(candidates) > 1 else 0.0,
                "raw940_mean": float(candidates["raw940"].mean()),
                "motion_mean": float(candidates["motion"].mean()),
                "still_fraction": float((candidates["moving"] == 0).mean()),
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
) -> Optional[BaselineModel]:
    """Fit glucose = slope * filt940 + intercept using ordinary least squares."""
    if pairs is None or len(pairs) < 2:
        return None

    x = pairs["filt940_mean"].to_numpy(dtype=float)
    y = pairs["glucose_mgdl"].to_numpy(dtype=float)

    # numpy polyfit degree 1
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
    )
