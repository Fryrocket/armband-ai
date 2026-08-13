# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Fryrocket

"""Rule-based signal quality score for armband windows.

This is the CPU stand-in until a Hailo HEF quality / artifact model exists.
Score is 0..100. Higher = better for calibration and glucose estimation.

Hard invalidation (ASK 12 / 16, 2026-08-13)
-------------------------------------------
A window with *no* valid bpm samples (bpm > 0), *no* valid spo2 samples
(spo2 > 0), or *no* motion data (missing/empty `moving` column) is refused
at the gate: score=0, label=poor, reasons include `no_valid_bpm`,
`no_valid_spo2`, and/or `no_motion_data`. Feature extraction still invents
0.0 for the fixed-width vector; only this gate can refuse the window so
the invented number never reaches a model as a "measurement".

These refusals are counted by callers (build_calibration_pairs logs them)
because the rate during S001 is diagnostic of band fit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import pandas as pd

from .features import WindowFeatures, extract_window_features, features_from_db


@dataclass
class QualityResult:
    score: float                 # 0..100
    label: str                   # poor | fair | good | excellent
    reasons: list[str]
    features: dict
    hard_invalid: bool = False   # True when gate refused (no_valid_* / no_motion_data)

    def to_dict(self) -> dict:
        return asdict(self)


def _label(score: float) -> str:
    if score >= 80:
        return "excellent"
    if score >= 60:
        return "good"
    if score >= 40:
        return "fair"
    return "poor"


def score_window(feats: WindowFeatures) -> QualityResult:
    """Heuristic quality from one feature window.

    Hard-fails (score=0, hard_invalid=True) when the window has zero valid
    bpm samples, zero valid spo2 samples, or no motion data. Those windows
    never produce a usable calibration pair or inference estimate.
    """
    reasons: list[str] = []
    n_valid_bpm = int(getattr(feats, "n_valid_bpm", -1))
    n_valid_spo2 = int(getattr(feats, "n_valid_spo2", -1))
    n_valid_motion = int(getattr(feats, "n_valid_motion", -1))

    # ---- ASK 12 / 16 / 18 hard invalidation --------------------------------
    # Prefer the explicit counts from features.py. Fall back to the mean
    # only if an older WindowFeatures without the new fields is passed.
    # Validity is bpm > 0 and spo2 > 0 (same definition in both paths).
    if n_valid_bpm < 0:
        n_valid_bpm = 1 if feats.bpm_mean > 0 else 0
    if n_valid_spo2 < 0:
        n_valid_spo2 = 1 if feats.spo2_mean > 0 else 0
    # Motion fallback: cannot distinguish "genuinely still" from "no
    # accelerometer" on an old object. Skip the motion hard-fail unless
    # the field is present (n_valid_motion >= 0).

    hard = False
    if n_valid_bpm == 0:
        reasons.append("no_valid_bpm")
        hard = True
    if n_valid_spo2 == 0:
        reasons.append("no_valid_spo2")
        hard = True
    if n_valid_motion == 0:
        reasons.append("no_motion_data")
        hard = True

    if hard:
        return QualityResult(
            score=0.0,
            label="poor",
            reasons=reasons,
            features=feats.to_dict(),
            hard_invalid=True,
        )

    # ---- Soft heuristics --------------------------------------------------
    score = 100.0

    # Motion dominates PPG / 940 nm quality
    if feats.still_fraction < 0.5:
        pen = 35.0 * (1.0 - feats.still_fraction)
        score -= pen
        reasons.append(f"high motion (still={feats.still_fraction:.0%}, -{pen:.0f})")
    elif feats.still_fraction < 0.8:
        pen = 15.0 * (0.8 - feats.still_fraction) / 0.3
        score -= pen
        reasons.append(f"some motion (still={feats.still_fraction:.0%}, -{pen:.0f})")

    if feats.motion_mean > 12.0:
        pen = min(20.0, (feats.motion_mean - 12.0) * 2.0)
        score -= pen
        reasons.append(f"elevated motion_mean={feats.motion_mean:.1f} (-{pen:.0f})")

    if feats.moving_transitions >= 3:
        pen = min(15.0, feats.moving_transitions * 3.0)
        score -= pen
        reasons.append(f"many motion transitions ({feats.moving_transitions}, -{pen:.0f})")

    # Consecutive-clean streak (still + optically stable).
    # Prefer sustained clean runs; short streaks inside noisy windows are weak.
    streak = getattr(feats, "max_clean_streak", 0) or 0
    clean_frac = getattr(feats, "clean_fraction", 0.0) or 0.0
    if streak < 5:
        pen = 18.0
        score -= pen
        reasons.append(f"short clean streak ({streak}, -{pen:.0f})")
    elif streak < 10:
        pen = 10.0
        score -= pen
        reasons.append(f"modest clean streak ({streak}, -{pen:.0f})")
    elif streak < 15 and clean_frac < 0.5:
        pen = 6.0
        score -= pen
        reasons.append(f"clean streak={streak} but clean_frac={clean_frac:.0%} (-{pen:.0f})")

    # Sample count / duration
    if feats.n_samples < 5:
        score -= 25.0
        reasons.append(f"few samples ({feats.n_samples}, -25)")
    elif feats.n_samples < 15:
        score -= 10.0
        reasons.append(f"short window ({feats.n_samples} samples, -10)")

    # 940 nm stability (whole-window CV)
    if feats.filt940_mean > 0 and feats.filt940_std > 0:
        cv = feats.filt940_std / max(feats.filt940_mean, 1.0)
        if cv > 0.08:
            pen = min(20.0, (cv - 0.08) * 200.0)
            score -= pen
            reasons.append(f"filt940 unstable (cv={cv:.3f}, -{pen:.0f})")
        elif cv > 0.045:
            # Milder band — tighter optical gate recommended in hardening notes
            pen = min(8.0, (cv - 0.045) * 150.0)
            score -= pen
            reasons.append(f"filt940 mild CV={cv:.3f} (-{pen:.0f})")

    # Extreme slope often means contact change or motion
    if abs(feats.filt940_slope) > 5.0:
        pen = min(15.0, abs(feats.filt940_slope) * 1.5)
        score -= pen
        reasons.append(f"steep filt940 slope={feats.filt940_slope:.2f} (-{pen:.0f})")
    elif abs(feats.filt940_slope) > 2.5:
        pen = min(8.0, abs(feats.filt940_slope) * 1.2)
        score -= pen
        reasons.append(f"elevated filt940 slope={feats.filt940_slope:.2f} (-{pen:.0f})")

    # BPM sanity (optional soft penalty) — only when we have valid samples
    if feats.bpm_mean > 0:
        if feats.bpm_mean < 40 or feats.bpm_mean > 200:
            score -= 10.0
            reasons.append(f"bpm out of range ({feats.bpm_mean:.0f}, -10)")
        elif feats.bpm_std > 25:
            score -= 8.0
            reasons.append(f"bpm noisy (std={feats.bpm_std:.1f}, -8)")

    score = float(max(0.0, min(100.0, score)))
    if not reasons:
        reasons.append("stable still window")

    return QualityResult(
        score=score,
        label=_label(score),
        reasons=reasons,
        features=feats.to_dict(),
        hard_invalid=False,
    )


def score_dataframe(df: pd.DataFrame) -> Optional[QualityResult]:
    """Score an arbitrary PPG DataFrame (e.g. calibration candidate window)."""
    feats = extract_window_features(df)
    if feats is None:
        return None
    return score_window(feats)


def score_from_db(db_path: str, minutes: int = 5) -> Optional[QualityResult]:
    feats = features_from_db(db_path, minutes=minutes)
    if feats is None:
        return None
    return score_window(feats)
