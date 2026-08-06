#!/usr/bin/env python3
"""Train multi-feature OLS model from quality-gated calibration pairs.

Usage:
  python scripts/train_multifeature.py
  python scripts/train_multifeature.py --min-quality 60 --save models/multifeature.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.calibration import build_calibration_pairs
from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.models import DEFAULT_FEATURE_KEYS, fit_multifeature
from armband_ai.queries import count_libre, count_readings


def main() -> None:
    cfg = load_config()
    cal = cfg.get("calibration") or {}

    parser = argparse.ArgumentParser(description="Train multi-feature glucose model")
    parser.add_argument("--window", type=int, default=int(cal.get("window_seconds", 180)))
    parser.add_argument("--min-quality", type=float, default=float(cal.get("min_quality", 50)))
    parser.add_argument("--min-still", type=float, default=float(cal.get("min_still_fraction", 0.6)))
    parser.add_argument("--no-prefer-still", action="store_true")
    parser.add_argument(
        "--save",
        type=str,
        default=str(PROJECT_ROOT / "models" / "multifeature.json"),
    )
    parser.add_argument("--export-pairs", type=str, default=None)
    args = parser.parse_args()

    db_path = cfg["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(PROJECT_ROOT / db_path)

    prefer_still = not args.no_prefer_still
    print(f"PPG={count_readings(db_path)}  Libre={count_libre(db_path)}")
    print(f"Gates: quality>={args.min_quality} still>={args.min_still} window=±{args.window}s")

    pairs = build_calibration_pairs(
        db_path,
        window_seconds=args.window,
        prefer_still=prefer_still,
        min_quality=args.min_quality,
        min_still_fraction=args.min_still,
    )
    print(f"Pairs: {len(pairs)}")
    if pairs.empty:
        print("No pairs. Collect still Libre readings first.")
        sys.exit(1)

    if args.export_pairs:
        Path(args.export_pairs).parent.mkdir(parents=True, exist_ok=True)
        pairs.to_csv(args.export_pairs, index=False)
        print(f"Pairs → {args.export_pairs}")

    model = fit_multifeature(
        pairs,
        DEFAULT_FEATURE_KEYS,
        min_quality=args.min_quality,
        min_still_fraction=args.min_still,
        window_seconds=args.window,
    )
    if model is None:
        print(f"Need at least {len(DEFAULT_FEATURE_KEYS) + 1} pairs for full feature set.")
        sys.exit(1)

    print(f"R²={model.r2:.4f}  MAE={model.mae:.2f}  RMSE={model.rmse:.2f}  n={model.n_pairs}")
    print(f"intercept={model.intercept:.4f}")
    for k, c in zip(model.feature_keys, model.coefficients):
        print(f"  {k:20s}  {c:+.6f}")

    model.save(args.save)
    print(f"Saved → {args.save}")


if __name__ == "__main__":
    main()
