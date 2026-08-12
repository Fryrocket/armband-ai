"""Multi-feature linear models and per-subject fitting for calibration pairs.

Design rules (locked 2026-08-11 / 12, structural bar 2026-08-12)
----------------------------------------------------------------
- Fitters partition by subject_id and never pool across subjects.
- Rows with subject_id=None are skipped and logged (not silently dropped).
- MIN_PAIRS_PER_SUBJECT is a named floor; below it the subject is refused
  with an explicit log line.
- Structural n <= p bar: multi-feature OLS is refused when n_pairs <= number
  of features (underdetermined / rank-deficient at pilot scale). Explicit log.
- Models carry subject_id; artifacts are subject-keyed when possible.
- Structural cross-subject refusal: do not train a single model on mixed
  subject_id values.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("armband_ai.models")

# Named floor — refuse a subject with fewer pairs than this.
MIN_PAIRS_PER_SUBJECT = 20

# Feature keys used by the multi-feature linear path (subset of WindowFeatures
# that are stable and available on every calibration pair row).
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
    """Ordinary least-squares multi-feature model: glucose ≈ X @ coef + intercept."""

    feature_keys: list[str]
    coef: list[float]
    intercept: float
    r2: float
    mae: float
    rmse: float
    n_pairs: int
    subject_id: str | None = None
    window_seconds: int = 180
    prefer_still: bool = True
    min_quality: float = 0.0
    min_still_fraction: float = 0.0
    min_clean_streak: int = 0

    def predict(self, X: np.ndarray | pd.DataFrame) -> np.ndarray:
        if isinstance(X, pd.DataFrame):
            X = X.reindex(columns=self.feature_keys).fillna(0.0).to_numpy(dtype=float)
        else:
            X = np.asarray(X, dtype=float)
        return X @ np.asarray(self.coef, dtype=float) + self.intercept

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
            coef=[float(c) for c in d["coef"]],
            intercept=float(d["intercept"]),
            r2=float(d["r2"]),
            mae=float(d["mae"]),
            rmse=float(d["rmse"]),
            n_pairs=int(d["n_pairs"]),
            subject_id=d.get("subject_id"),
            window_seconds=int(d.get("window_seconds", 180)),
            prefer_still=bool(d.get("prefer_still", True)),
            min_quality=float(d.get("min_quality", 0.0)),
            min_still_fraction=float(d.get("min_still_fraction", 0.0)),
            min_clean_streak=int(d.get("min_clean_streak", 0)),
        )


def _fit_ols(
    X: np.ndarray,
    y: np.ndarray,
) -> tuple[np.ndarray, float, float, float, float]:
    """Return (coef, intercept, r2, mae, rmse)."""
    # Add intercept column
    ones = np.ones((X.shape[0], 1), dtype=float)
    A = np.hstack([X, ones])
    # Least squares
    beta, residuals, rank, s = np.linalg.lstsq(A, y, rcond=None)
    coef = beta[:-1]
    intercept = float(beta[-1])
    y_hat = A @ beta
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    mae = float(np.mean(np.abs(y - y_hat)))
    rmse = float(np.sqrt(np.mean((y - y_hat) ** 2)))
    return coef, intercept, r2, mae, rmse


def fit_multifeature(
    pairs: pd.DataFrame,
    feature_keys: list[str] | None = None,
    *,
    window_seconds: int = 180,
    prefer_still: bool = True,
    min_quality: float = 0.0,
    min_still_fraction: float = 0.0,
    min_clean_streak: int = 0,
    min_pairs: int = MIN_PAIRS_PER_SUBJECT,
) -> list[MultiFeatureModel]:
    """Fit one MultiFeatureModel per subject_id. Never pools across subjects.

    Rows with subject_id=None are skipped and logged.
    Subjects with fewer than min_pairs rows are refused with an explicit log.
    Subjects with n_pairs <= number of features are refused (structural n <= p
    bar — underdetermined OLS at pilot scale).
    Returns a list of fitted models (may be empty).
    """
    if pairs is None or pairs.empty:
        log.info("fit_multifeature: no pairs supplied")
        return []

    keys = feature_keys or DEFAULT_FEATURE_KEYS
    missing = [k for k in keys if k not in pairs.columns]
    if missing:
        log.warning("fit_multifeature: missing feature columns %s — filling 0.0", missing)
        for k in missing:
            pairs = pairs.copy()
            pairs[k] = 0.0

    if "subject_id" not in pairs.columns:
        log.warning(
            "fit_multifeature: no subject_id column — treating entire set as one "
            "anonymous subject (not recommended for production)"
        )
        pairs = pairs.copy()
        pairs["subject_id"] = "ANON"

    # Skip + log None subject_id rows
    none_mask = pairs["subject_id"].isna() | (pairs["subject_id"].astype(str).str.lower() == "none")
    n_none = int(none_mask.sum())
    if n_none:
        log.info(
            "fit_multifeature: skipping %d rows with subject_id=None (and logging)",
            n_none,
        )
        pairs = pairs.loc[~none_mask].copy()

    if pairs.empty:
        log.info("fit_multifeature: no pairs left after subject_id filter")
        return []

    models: list[MultiFeatureModel] = []
    for subject_id, group in pairs.groupby("subject_id", sort=False):
        sid = str(subject_id)
        n = len(group)
        if n < min_pairs:
            log.info(
                "fit_multifeature: refuse subject %s — only %d pairs "
                "(MIN_PAIRS_PER_SUBJECT=%d)",
                sid,
                n,
                min_pairs,
            )
            continue

        # Structural bar: OLS is underdetermined / rank-deficient when n <= p.
        # p = number of features (intercept is free). Refuse rather than emit
        # a singular or near-singular model that looks fitted at pilot scale.
        p = len(keys)
        if n <= p:
            log.info(
                "fit_multifeature: refuse subject %s — structural n <= p "
                "(n=%d pairs, p=%d features). Underdetermined; raise n or "
                "reduce feature set.",
                sid,
                n,
                p,
            )
            continue

        X = group.reindex(columns=keys).fillna(0.0).to_numpy(dtype=float)
        y = group["glucose_mgdl"].to_numpy(dtype=float)
        if not np.isfinite(X).all() or not np.isfinite(y).all():
            log.warning("fit_multifeature: non-finite values for subject %s — skip", sid)
            continue

        coef, intercept, r2, mae, rmse = _fit_ols(X, y)
        model = MultiFeatureModel(
            feature_keys=list(keys),
            coef=coef.tolist(),
            intercept=intercept,
            r2=r2,
            mae=mae,
            rmse=rmse,
            n_pairs=n,
            subject_id=sid,
            window_seconds=window_seconds,
            prefer_still=prefer_still,
            min_quality=min_quality,
            min_still_fraction=min_still_fraction,
            min_clean_streak=min_clean_streak,
        )
        models.append(model)
        log.info(
            "fit_multifeature: subject %s  n=%d  R²=%.3f  MAE=%.1f  RMSE=%.1f",
            sid,
            n,
            r2,
            mae,
            rmse,
        )

    return models


def fit_baseline_per_subject(
    pairs: pd.DataFrame,
    *,
    window_seconds: int = 180,
    prefer_still: bool = True,
    min_quality: float = 0.0,
    min_still_fraction: float = 0.0,
    min_clean_streak: int = 0,
    min_pairs: int = MIN_PAIRS_PER_SUBJECT,
) -> list:
    """Convenience: fit BaselineModel (from calibration) once per subject.

    Same skip/log + MIN_PAIRS rules as fit_multifeature.
    """
    from .calibration import BaselineModel, fit_baseline

    if pairs is None or pairs.empty:
        return []

    if "subject_id" not in pairs.columns:
        pairs = pairs.copy()
        pairs["subject_id"] = "ANON"

    none_mask = pairs["subject_id"].isna() | (pairs["subject_id"].astype(str).str.lower() == "none")
    n_none = int(none_mask.sum())
    if n_none:
        log.info(
            "fit_baseline_per_subject: skipping %d rows with subject_id=None",
            n_none,
        )
        pairs = pairs.loc[~none_mask].copy()

    models = []
    for subject_id, group in pairs.groupby("subject_id", sort=False):
        sid = str(subject_id)
        n = len(group)
        if n < min_pairs:
            log.info(
                "fit_baseline_per_subject: refuse subject %s — only %d pairs "
                "(MIN_PAIRS_PER_SUBJECT=%d)",
                sid,
                n,
                min_pairs,
            )
            continue
        m = fit_baseline(
            group,
            window_seconds=window_seconds,
            prefer_still=prefer_still,
            min_quality=min_quality,
            min_still_fraction=min_still_fraction,
            min_clean_streak=min_clean_streak,
        )
        if m is not None:
            m.subject_id = sid
            models.append(m)
    return models
