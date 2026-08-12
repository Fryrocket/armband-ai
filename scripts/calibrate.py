#!/usr/bin/env python3
"""Build calibration pairs and fit baseline / multi-feature models.

Usage examples:
  python scripts/calibrate.py --from-db
  python scripts/calibrate.py --from-db --subject-map maps/sessions.csv
  python scripts/calibrate.py --from-db --min-pairs 20 --out models/

Exit codes: 0 ok · 1 usage/data · 2 dependency/DB/IO failure
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate baseline / multi-feature models")
    parser.add_argument("--from-db", action="store_true", help="Build pairs from SQLite")
    parser.add_argument("--pairs", type=str, default=None, help="CSV of pre-built pairs")
    parser.add_argument(
        "--subject-map",
        type=str,
        default=None,
        help="CSV with columns session_id,subject_id (required for production fits)",
    )
    parser.add_argument("--min-quality", type=float, default=None)
    parser.add_argument("--min-still", type=float, default=None)
    parser.add_argument("--min-clean-streak", type=int, default=None)
    parser.add_argument("--window", type=int, default=None)
    parser.add_argument("--no-prefer-still", action="store_true")
    parser.add_argument(
        "--min-pairs",
        type=int,
        default=None,
        help=f"Floor per subject (default MIN_PAIRS_PER_SUBJECT from models)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Directory for model JSON artifacts (default models/)",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Fit only the single-feature baseline models",
    )
    args = parser.parse_args()

    if not args.from_db and not args.pairs:
        print("ERROR: provide --from-db or --pairs path", file=sys.stderr)
        return 1

    try:
        from armband_ai.calibration import (
            build_calibration_pairs,
            fit_baseline,
            load_subject_map_from_csv,
        )
        from armband_ai.config import ROOT as PROJECT_ROOT
        from armband_ai.config import load_config
        from armband_ai.db import DatabaseError
        from armband_ai.models import (
            MIN_PAIRS_PER_SUBJECT,
            fit_baseline_per_subject,
            fit_multifeature,
        )
    except ImportError as e:
        print(f"ERROR: import failure: {e}", file=sys.stderr)
        return 2

    cfg = load_config()
    cal = cfg.get("calibration", {})
    window = args.window if args.window is not None else int(cal.get("window_seconds", 180))
    prefer_still = not args.no_prefer_still and bool(cal.get("prefer_still", True))
    min_quality = args.min_quality if args.min_quality is not None else float(cal.get("min_quality", 60))
    min_still = args.min_still if args.min_still is not None else float(cal.get("min_still_fraction", 0.7))
    min_clean = (
        args.min_clean_streak
        if args.min_clean_streak is not None
        else int(cal.get("min_clean_streak", 10))
    )
    min_pairs = args.min_pairs if args.min_pairs is not None else MIN_PAIRS_PER_SUBJECT

    subject_map = None
    if args.subject_map:
        try:
            subject_map = load_subject_map_from_csv(args.subject_map)
            print(f"Loaded subject map: {len(subject_map)} sessions → subjects")
        except Exception as e:
            print(f"ERROR: subject map: {e}", file=sys.stderr)
            return 1

    try:
        if args.from_db:
            db_path = cfg["database"]["path"]
            if not Path(db_path).is_absolute():
                db_path = str(PROJECT_ROOT / db_path)
            pairs = build_calibration_pairs(
                db_path,
                window_seconds=window,
                prefer_still=prefer_still,
                min_quality=min_quality,
                min_still_fraction=min_still,
                min_clean_streak=min_clean,
                subject_map=subject_map,
            )
        else:
            import pandas as pd

            pairs = pd.read_csv(args.pairs)
            if subject_map is not None and "session_id" in pairs.columns:
                pairs = pairs.copy()
                pairs["subject_id"] = pairs["session_id"].map(subject_map)
    except DatabaseError as e:
        print(f"ERROR: database: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: failed to build/load pairs: {e}", file=sys.stderr)
        return 2

    if pairs is None or pairs.empty:
        print(
            "ERROR: zero calibration pairs.\n"
            "Possible causes: no Libre rows, no PPG in window, all windows mixed-session,\n"
            "quality/still/clean gates, or subject_map left everything unmapped.",
            file=sys.stderr,
        )
        return 1

    print(f"Pairs: {len(pairs)}")
    if "subject_id" in pairs.columns:
        print(pairs.groupby("subject_id", dropna=False).size().to_string())

    out_dir = Path(args.out) if args.out else (PROJECT_ROOT / "models")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Per-subject baseline
    baselines = fit_baseline_per_subject(
        pairs,
        window_seconds=window,
        prefer_still=prefer_still,
        min_quality=min_quality,
        min_still_fraction=min_still,
        min_clean_streak=min_clean,
        min_pairs=min_pairs,
    )
    for m in baselines:
        name = f"baseline_{m.subject_id or 'anon'}.json"
        path = out_dir / name
        m.save(path)
        print(f"Baseline → {path}  (n={m.n_pairs} R²={m.r2:.3f} MAE={m.mae:.1f})")

    if not args.baseline_only:
        multi = fit_multifeature(
            pairs,
            window_seconds=window,
            prefer_still=prefer_still,
            min_quality=min_quality,
            min_still_fraction=min_still,
            min_clean_streak=min_clean,
            min_pairs=min_pairs,
        )
        for m in multi:
            name = f"multifeature_{m.subject_id or 'anon'}.json"
            path = out_dir / name
            m.save(path)
            print(f"Multi    → {path}  (n={m.n_pairs} R²={m.r2:.3f} MAE={m.mae:.1f})")

    if not baselines and (args.baseline_only or True):
        # Also emit a single pooled baseline for quick sanity if no subject models
        # (still respects subject_id=None skip inside fit_baseline_per_subject)
        if not baselines:
            print("No per-subject models met MIN_PAIRS_PER_SUBJECT.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
