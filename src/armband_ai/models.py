"""CPU models beyond the single-variable baseline.

MultiFeatureModel: OLS on a stable subset of WindowFeatures keys.
Can be trained from quality-gated calibration pairs and used until a Hailo HEF exists.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

# Stable feature order for multi-feature glucose model (must match training)
DEFAULT_FEATURE_KEYS = [
    "filt940_mean",
    "filt940_std",
    "filt940_slope",
    "raw940_mean",
    "bpm_mean",
    "bpm_std",
    "motion_mean",
    "still_fraction",
    "moving_transitions",
    "temp_mean",
]


@dataclass
class MultiFeatureModel:
    """glucose ≈ intercept + sum(coef_i * feature_i)"""

    feature_keys: list[str]
    coefficients: list[float]  # same length as feature_keys
    intercept: float
    r2: float
    mae: float
    rmse: float
    n_pairs: int
    min_quality: float = 0.0
    min_still_fraction: float = 0.0
    window_seconds: int = 180

    def predict_row(self, row: dict | pd.Series) -> float:
        x = np.array([float(row.get(k, 0.0) or 0.0) for k in self.feature_keys], dtype=float)
        return float(self.intercept + np.dot(self.coefficients, x))

    def predict_matrix(self, df: pd.DataFrame) -> np.ndarray:
        X = df.reindex(columns=self.feature_keys).fillna(0.0).to_numpy(dtype=float)
        return self.intercept + X @ np.array(self.coefficients, dtype=float)

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "MultiFeatureModel":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return cls(
            feature_keys=list(d["feature_keys"]),
            coefficients=[float(c) for c in d["coefficients"]],
            intercept=float(d["intercept"]),
            r2=float(d["r2"]),
            mae=float(d["mae"]),
            rmse=float(d["rmse"]),
            n_pairs=int(d["n_pairs"]),
            min_quality=float(d.get("min_quality", 0.0)),
            min_still_fraction=float(d.get("min_still_fraction", 0.0)),
            window_seconds=int(d.get("window_seconds", 180)),
        )


def fit_multifeature(
    pairs: pd.DataFrame,
    feature_keys: Sequence[str] | None = None,
    *,
    min_quality: float = 0.0,
    min_still_fraction: float = 0.0,
    window_seconds: int = 180,
) -> Optional[MultiFeatureModel]:
    """Ordinary least squares with intercept on selected columns.

    Requires columns: glucose_mgdl + each feature key.
    """
    keys = list(feature_keys or DEFAULT_FEATURE_KEYS)
    if pairs is None or len(pairs) < len(keys) + 1:
        return None

    missing = [k for k in keys if k not in pairs.columns]
    if missing:
        # Allow training on calibration pairs that only have a subset
        keys = [k for k in keys if k in pairs.columns]
        if len(keys) < 1 or len(pairs) < len(keys) + 1:
            return None

    X = pairs.reindex(columns=keys).fillna(0.0).to_numpy(dtype=float)
    y = pairs["glucose_mgdl"].to_numpy(dtype=float)

    # Design matrix with intercept column
    ones = np.ones((X.shape[0], 1), dtype=float)
    A = np.hstack([ones, X])
    try:
        beta, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
    except np.linalg.LinAlgError:
        return None

    intercept = float(beta[0])
    coefs = [float(c) for c in beta[1:]]
    y_hat = A @ beta

    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    mae = float(np.mean(np.abs(y - y_hat)))
    rmse = float(np.sqrt(np.mean((y - y_hat) ** 2)))

    return MultiFeatureModel(
        feature_keys=keys,
        coefficients=coefs,
        intercept=intercept,
        r2=r2,
        mae=mae,
        rmse=rmse,
        n_pairs=len(pairs),
        min_quality=float(min_quality),
        min_still_fraction=float(min_still_fraction),
        window_seconds=int(window_seconds),
    )
