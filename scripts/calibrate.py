#!/usr/bin/env python3
"""Build calibration pairs and fit a baseline linear model.

Usage:
  python scripts/calibrate.py
  python scripts/calibrate.py --window 120 --min-quality 60 --min-still 0.7 --min-clean-streak 12
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.calibration import build_calibration_pairs, fit_baseline, BaselineModel
from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.drift_monitor import snapshot_baseline_from_db


def main() -> int:
    cfg = load_config()
    cal = cfg.get("calibration", {}) or {}

    parser = argparse.ArgumentParser(description="Fit baseline model from Libre + PPG pairs")
    parser.add_argument("--window", type=int, default=int(cal.get("window_seconds", 180)))
    parser.add_argument(
        "--min-quality",
        type=float,
        default=float(cal.get("min_quality", 60)),
        help="Minimum quality score 0-100 to keep a pair (default 60)",
    )
    parser.add_argument(
        "--min-still",
        type=float,
        default=float(cal.get("min_still_fraction", 0.7)),
        help="Minimum still_fraction 0-1 to keep a pair (default 0.7)",
    )
    parser.add_argument(
        "--min-clean-streak",
        type=int,
        default=int(cal.get("min_clean_streak", 10)),
        help="Min consecutive still+optically-stable samples (0=off, recommend 10–15)",
    )
    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="Path to write baseline.json (default models/baseline.json)",
    )
    parser.add_argument("--no-prefer-still", action="store_true")
    args = parser.parse_args()

    if not (0 <= args.min_quality <= 100):
        print("ERROR: --min-quality must be 0–100", file=sys.stderr)
        return 1
    if not (0 <= args.min_still <= 1):
        print("ERROR: --min-still must be 0–1", file=sys.stderr)
        return 1
    if args.min_clean_streak < 0:
        print("ERROR: --min-clean-streak must be ≥ 0", file=sys.stderr)
        return 1

    db_path = cfg["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(PROJECT_ROOT / db_path)

    pairs = build_calibration_pairs(
        db_path,
        window_seconds=args.window,
        prefer_still=not args.no_prefer_still,
        min_quality=args.min_quality,
        min_still_fraction=args.min_still,
        min_clean_streak=args.min_clean_streak,
    )

    print(f"Pairs kept: {len(pairs)}")
    if pairs.empty:
        print(
            "No pairs passed the quality / still / clean-streak gates. "
            "Loosen --min-quality / --min-still / --min-clean-streak or collect more still data."
        )
        return 1

    model = fit_baseline(
        pairs,
        window_seconds=args.window,
        prefer_still=not args.no_prefer_still,
        min_quality=args.min_quality,
        min_still_fraction=args.min_still,
        min_clean_streak=args.min_clean_streak,
    )
    if model is None:
        print("Need at least 2 pairs to fit.")
        return 1

    print(f"R²={model.r2:.3f}  MAE={model.mae:.1f}  RMSE={model.rmse:.1f}  n={model.n_pairs}")
    print(f"glucose ≈ {model.slope:.6f} × filt940 + {model.intercept:.2f}")

    out = Path(args.save) if args.save else PROJECT_ROOT / "models" / "baseline.json"
    model.save(out)
    print(f"Saved → {out}")

    try:
        med = snapshot_baseline_from_db(db_path)
        if med is not None:
            print(f"Drift baseline snapshot (still filt940 median) = {med:.2f}")
    except Exception as e:
        print(f"Note: drift baseline snapshot skipped: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
