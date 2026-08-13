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
min_quality gate was effectively checking \"is this window clean once you
throw away everything that wasn't,\" not \"was this window clean.\" quality is
now computed from `raw_feats` (the same unfiltered window used for
still_fraction/clean_streak) via score_window(), before prefer_still is
applied. The prefer_still filter still runs afterward, but only to decide
which rows are averaged into filt940_mean etc. — it no longer affects what
gets scored or gated.

--- FIX APPLIED (multi-feature columns) ---
build_calibration_pairs previously only emitted a subset of WindowFeatures
fields into the pairs DataFrame (filt940_mean/std, raw940_mean, motion_mean,
still_fraction). models.DEFAULT_FEATURE_KEYS expects 10 features including
filt940_slope, bpm_mean, bpm_std, moving_transitions, and temp_mean. The
missing columns caused fit_multifeature to silently train on half the
intended feature set. All DEFAULT_FEATURE_KEYS fields are now written from
the already-computed raw_feats WindowFeatures object so multi-feature
training sees the full signal.

--- SUBJECT_ID / PER-SUBJECT (2026-08-11) ---
A reading with no session_id is not calibration-eligible. Pairs lacking
subject_id are dropped when a subject_map is supplied. Fitters partition
by subject_id and never pool. See design note from Claude 2026-08-11.

--- HOMOGENEITY (2026-08-11 / 12) ---
Session attribution uses homogeneity, not mode(). If a window contains >1
distinct non-null session_id values it is dropped and counted as
dropped_mixed_session. This is the discontinuity the rule exists to catch
(including re-seat straddles once re-seat = new session is in force).

--- HARD INVALIDATION (ASK 12 / 16, 2026-08-13) ---
score_window hard-fails (hard_invalid=True, score=0) when the window has
zero valid bpm samples, zero valid spo2 samples, or no motion data.
Those windows are counted as dropped_hard_invalid and never become pairs.
The rate is diagnostic of band fit during S001. Drop counts live on
the returned DataFrame as .attrs["drop_counts"] (in-process) AND as a
sibling JSON written by write_pairs() (ASK 24). .attrs alone does not
survive CSV.

--- FIT_BASELINE (ASK 20 / 21, 2026-08-13) ---
fit_baseline is the only inference path that will execute at S001
(Hailo disabled, multi-feature n<=p gated). It therefore:
  - REFUSES a pairs frame with >1 distinct non-null subject_id
    (ValueError, structural — locked decision 3). No "take the first."
  - REFUSES n < MIN_BASELINE_PAIRS (10). p=2; 5×p=10; 8 residual DoF.
  - REFUSES glucose range < 40 mg/dL AND occupancy of fewer than 3
    equal-width terciles of [min, max]. Range alone is gameable by
    two clusters. Distribution is required.
  Fits with n < PILOT_GRADE_BELOW_PAIRS (30) are marked grade="pilot"
  in the artifact. Plumbing, not evidence.

"""

from __future__ import annotations

import json
import logging
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

log = logging.getLogger("armband_ai.calibration")

# ASK 21 — LOCKED 2026-08-13 (amended).
# p = 2 (slope + intercept). n = 2 interpolates: R² = 1, MAE = 0.
# 5×p = 10 leaves 8 residual degrees of freedom. That is the justification.
MIN_BASELINE_PAIRS = 10
# Libre noise ~15–20 mg/dL. 40 mg/dL span ≈ 1.9 SNR on the slope.
# Achievable on purpose (eat a meal). Range alone is not enough.
MIN_GLUCOSE_RANGE_MGDL = 40.0
# Equal-width terciles of [min, max]; all three must be occupied.
# Two clusters 40 mg/dL apart occupy only the end bins — refuse.
MIN_GLUCOSE_TERCILES = 3
# n below this is a plumbing artifact, not evidence about the premise.
PILOT_GRADE_BELOW_PAIRS = 30

DROP_COUNT_KEYS = (
    "dropped_no_session",
    "dropped_mixed_session",
    "dropped_unmapped",
    "dropped_hard_invalid",
)

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
    subject_id: str | None = None
    grade: str = "pilot"  # "pilot" if n < 30; else "provisional"
    note: str = ""

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
            subject_id=d.get("subject_id"),
            grade=str(d.get("grade", "pilot")),
            note=str(d.get("note", "")),
        )


def _empty_drop_counts() -> dict[str, int]:
    return {k: 0 for k in DROP_COUNT_KEYS}


def pairs_drops_path(pairs_path: str | Path) -> Path:
    """Sibling JSON: exports/pairs.csv → exports/pairs.csv.drops.json."""
    p = Path(pairs_path)
    return p.with_name(p.name + ".drops.json")


def write_pairs(df: pd.DataFrame, path: str | Path) -> Path:
    """Write pairs CSV plus a drop-count sidecar that survives serialisation.

    ASK 24: pandas .attrs die on to_csv. The sidecar is the durable copy.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    drops = dict(df.attrs.get("drop_counts") or _empty_drop_counts())
    sidecar = pairs_drops_path(path)
    payload = {
        "pairs_file": path.name,
        "n_pairs": int(len(df)),
        "drop_counts": drops,
    }
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return sidecar


def read_pairs(path: str | Path) -> pd.DataFrame:
    """Read a pairs CSV and restore drop_counts from the sibling JSON if present."""
    path = Path(path)
    df = pd.read_csv(path)
    sidecar = pairs_drops_path(path)
    if sidecar.exists():
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        df.attrs["drop_counts"] = payload.get("drop_counts", _empty_drop_counts())
    return df


def _tercile_occupancy(y: np.ndarray) -> int:
    """How many equal-width terciles of [min, max] contain at least one point."""
    if y.size == 0:
        return 0
    lo = float(np.min(y))
    hi = float(np.max(y))
    span = hi - lo
    if span <= 0:
        return 1
    edges = (lo, lo + span / 3.0, lo + 2.0 * span / 3.0, hi)
    occupied = 0
    for i in range(3):
        if i < 2:
            hit = np.any((y >= edges[i]) & (y < edges[i + 1]))
        else:
            hit = np.any((y >= edges[i]) & (y <= edges[i + 1]))
        if hit:
            occupied += 1
    return occupied


def _baseline_grade(n_pairs: int) -> tuple[str, str]:
    if n_pairs < PILOT_GRADE_BELOW_PAIRS:
        return (
            "pilot",
            "PILOT-GRADE. n<30. This fit tests plumbing and workflow, "
            "not whether 940nm tracks glucose. Do not treat R² as evidence.",
        )
    return (
        "provisional",
        "n>=30. Still not a claim that 940nm tracks glucose.",
    )


def load_subject_map_from_csv(path: str | Path) -> dict[str, str]:
    """Load session_id → Subject_ID mapping from a two-column CSV.

    Expected columns (header required): session_id, subject_id
    Extra columns ignored. Empty/blank rows skipped.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"subject map CSV not found: {path}")
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    if "session_id" not in cols or "subject_id" not in cols:
        raise ValueError(
            "subject map CSV must have columns session_id and subject_id "
            f"(got {list(df.columns)})"
        )
    sid_col = cols["session_id"]
    subj_col = cols["subject_id"]
    mapping: dict[str, str] = {}
    for _, row in df.iterrows():
        sid = row[sid_col]
        subj = row[subj_col]
        if pd.isna(sid) or pd.isna(subj):
            continue
        mapping[str(sid).strip()] = str(subj).strip()
    return mapping


def build_calibration_pairs(
    db_path: str | Path,
    window_seconds: int = 180,
    prefer_still: bool = True,
    min_samples: int = 1,
    min_quality: float = 0.0,
    min_still_fraction: float = 0.0,
    min_clean_streak: int = 0,
    subject_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Match each Libre reading to nearby armband samples.

    Strategy
    --------
    For each Libre timestamp T:
      - Find PPG rows with received_at in [T - window, T + window]  (raw window)
      - Require homogeneity of session_id in the window (>1 distinct non-null
        values → drop as mixed_session)
      - Compute still_fraction, max_clean_streak, and quality_score on that
        *raw* window (before any filtering) via a single WindowFeatures pass
      - Hard-invalid windows (no valid bpm / spo2 / motion) are refused by
        score_window and counted as dropped_hard_invalid (ASK 12 / 16)
      - Gate on min_still_fraction, min_clean_streak, and min_quality first —
        all three reflect the window as actually recorded
      - THEN, for aggregation only, prefer non-moving samples if prefer_still
        and any exist
      - Aggregate: mean filt940, mean raw940, mean motion, count (over the
        aggregation set)
      - Also emit the full set of DEFAULT_FEATURE_KEYS fields from raw_feats
        so multi-feature training receives every intended column.

    Returns a DataFrame with one row per successful pair.

    subject_map
    -----------
    Optional dict mapping session_id → Subject_ID (e.g. {\"S001\": \"SUBJ_A\"}).
    When provided, pairs whose session is missing or unmapped are dropped
    (never defaulted) and logged. When omitted, subject_id column is still
    emitted as None; pairs without session_id are still dropped. A reading
    with no session_id is never calibration-eligible.
    """
    init_db(db_path)
    libre = load_libre(db_path)
    empty_counts = _empty_drop_counts()
    if libre.empty:
        out = pd.DataFrame()
        out.attrs["drop_counts"] = dict(empty_counts)
        return out

    with get_connection(db_path) as conn:
        ppg = pd.read_sql_query(
            "SELECT id, received_at, filt940, raw940, motion, moving, bpm, temp, spo2, batt, session_id "
            "FROM ppg_readings ORDER BY received_at ASC",
            conn,
        )

    if ppg.empty:
        out = pd.DataFrame()
        out.attrs["drop_counts"] = dict(empty_counts)
        return out

    ppg["received_at"] = pd.to_datetime(ppg["received_at"], utc=True)

    pairs = []
    dropped_no_session = 0
    dropped_mixed_session = 0
    dropped_unmapped = 0
    dropped_hard_invalid = 0
    half = timedelta(seconds=window_seconds)

    for _, row in libre.iterrows():
        t = row["recorded_at"]
        mask = (ppg["received_at"] >= t - half) & (ppg["received_at"] <= t + half)
        candidates = ppg.loc[mask].copy()

        if candidates.empty:
            continue

        # --- Subject / session attribution (required for calibration) ---
        # Homogeneity: >1 distinct non-null session_id → drop (mixed).
        # No non-null session_id → drop (no_session).
        sess_series = candidates["session_id"].dropna()
        if sess_series.empty:
            dropped_no_session += 1
            continue
        unique_sessions = sess_series.astype(str).unique()
        if len(unique_sessions) > 1:
            dropped_mixed_session += 1
            continue
        session_id = str(unique_sessions[0])
        if not session_id or session_id.lower() in ("none", "nan", ""):
            dropped_no_session += 1
            continue

        subject_id = None
        if subject_map is not None:
            subject_id = subject_map.get(session_id)
            if subject_id is None:
                dropped_unmapped += 1
                continue
        # When no subject_map, still emit session_id; subject_id stays None
        # (caller / fit must decide whether to accept; fitters skip None).

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
        # ASK 12 / 16: hard invalidation (no valid bpm / spo2 / motion).
        if getattr(q, "hard_invalid", False):
            dropped_hard_invalid += 1
            continue

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

        # Provenance of this row (ASK 22) — two sample sets, one pair:
        #   From `agg` (prefer-still filtered, when any still sample exists):
        #     filt940_mean, filt940_std, raw940_mean, motion_mean, n_samples.
        #     These are the optical/motion means that become baseline X.
        #   From `raw_feats` (unfiltered window — the same object the gate
        #     scored): filt940_slope, bpm_mean, bpm_std, temp_mean,
        #     moving_transitions, still_fraction, max_clean_streak,
        #     clean_fraction.
        # Deliberate: baseline X is the still-preferred optical mean;
        # the extras describe the window the gate actually judged. They
        # are not the same samples. Do not "fix" this by scoring agg.
        pairs.append(
            {
                "libre_id": int(row["id"]),
                "recorded_at": t,
                "glucose_mgdl": float(row["glucose_mgdl"]),
                "source": row.get("source", "libre"),
                "notes": row.get("notes"),
                "session_id": session_id,
                "subject_id": subject_id,
                "n_samples": len(agg),
                "filt940_mean": float(agg["filt940"].mean()),
                "filt940_std": float(agg["filt940"].std()) if len(agg) > 1 else 0.0,
                "filt940_slope": float(raw_feats.filt940_slope),
                "raw940_mean": float(agg["raw940"].mean()),
                "bpm_mean": float(raw_feats.bpm_mean),
                "bpm_std": float(raw_feats.bpm_std),
                "motion_mean": float(agg["motion"].mean()),
                "still_fraction": still_fraction,
                "moving_transitions": int(raw_feats.moving_transitions),
                "temp_mean": float(raw_feats.temp_mean),
                "max_clean_streak": max_clean_streak,
                "clean_fraction": clean_fraction,
                "quality_score": quality_score,
                "quality_label": quality_label,
                "time_offset_s": offset_s,
            }
        )

    drop_counts = {
        "dropped_no_session": dropped_no_session,
        "dropped_mixed_session": dropped_mixed_session,
        "dropped_unmapped": dropped_unmapped,
        "dropped_hard_invalid": dropped_hard_invalid,
    }
    if any(drop_counts.values()):
        log.info(
            "build_calibration_pairs: dropped %d (no session_id) + %d (mixed session) "
            "+ %d (unmapped subject) + %d (hard invalid: no_valid_bpm/spo2/no_motion_data)",
            dropped_no_session,
            dropped_mixed_session,
            dropped_unmapped,
            dropped_hard_invalid,
        )

    out = pd.DataFrame(pairs) if pairs else pd.DataFrame()
    # ASK 23: diagnostic must survive past the log line.
    out.attrs["drop_counts"] = drop_counts
    return out


def fit_baseline(
    pairs: pd.DataFrame,
    window_seconds: int = 180,
    prefer_still: bool = True,
    min_quality: float = 0.0,
    min_still_fraction: float = 0.0,
    min_clean_streak: int = 0,
) -> Optional[BaselineModel]:
    """Fit glucose = slope * filt940 + intercept using ordinary least squares.

    Refuses (ValueError), does not warn:
      - more than one distinct non-null subject_id (ASK 20, structural)
      - n < MIN_BASELINE_PAIRS (ASK 21)
      - glucose range < MIN_GLUCOSE_RANGE_MGDL (ASK 21)
      - fewer than MIN_GLUCOSE_TERCILES occupied terciles of [min, max] (ASK 21)
    Fits with n < 30 are grade="pilot" in the saved artifact.
    """
    n = 0 if pairs is None else len(pairs)
    if pairs is None or n < MIN_BASELINE_PAIRS:
        raise ValueError(
            f"fit_baseline: need >= {MIN_BASELINE_PAIRS} pairs "
            f"(p=2; 5×p={MIN_BASELINE_PAIRS}; 8 residual DoF; n=2 interpolates; got {n})"
        )

    # ASK 20: mixed subjects are a structural error, not a label to pick.
    subject_id = None
    if "subject_id" in pairs.columns:
        raw = pairs["subject_id"].dropna().astype(str)
        raw = raw[~raw.str.lower().isin(("none", "nan", ""))]
        subjects = sorted(raw.unique().tolist())
        if len(subjects) > 1:
            raise ValueError(
                f"fit_baseline: mixed subjects {subjects} — "
                "cross-subject pool is a structural error (locked decision 3). "
                "Partition first (fit_baseline_per_subject)."
            )
        if len(subjects) == 1:
            subject_id = subjects[0]

    x = pairs["filt940_mean"].to_numpy(dtype=float)
    y = pairs["glucose_mgdl"].to_numpy(dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("fit_baseline: non-finite filt940_mean or glucose_mgdl")

    g_range = float(np.max(y) - np.min(y))
    if g_range < MIN_GLUCOSE_RANGE_MGDL:
        raise ValueError(
            f"fit_baseline: glucose range {g_range:.1f} mg/dL < "
            f"{MIN_GLUCOSE_RANGE_MGDL:.0f}. "
            "A tight band teaches nothing about excursions."
        )
    n_terciles = _tercile_occupancy(y)
    if n_terciles < MIN_GLUCOSE_TERCILES:
        raise ValueError(
            f"fit_baseline: glucose occupies {n_terciles}/3 terciles of "
            f"[{float(np.min(y)):.0f}, {float(np.max(y)):.0f}]. "
            "Need all three (range plus distribution; two clusters fail)."
        )

    slope, intercept = np.polyfit(x, y, 1)
    y_hat = slope * x + intercept

    ss_res = np.sum((y - y_hat) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    mae = float(np.mean(np.abs(y - y_hat)))
    rmse = float(np.sqrt(np.mean((y - y_hat) ** 2)))

    grade, note = _baseline_grade(n)

    return BaselineModel(
        slope=float(slope),
        intercept=float(intercept),
        r2=float(r2),
        mae=mae,
        rmse=rmse,
        n_pairs=n,
        window_seconds=window_seconds,
        prefer_still=prefer_still,
        min_quality=float(min_quality),
        min_still_fraction=float(min_still_fraction),
        min_clean_streak=int(min_clean_streak),
        subject_id=subject_id,
        grade=grade,
        note=note,
    )
