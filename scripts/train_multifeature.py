#!/usr/bin/env python3
"""Train multi-feature OLS model from quality-gated calibration pairs.

Usage:
  python scripts/train_multifeature.py
  python scripts/train_multifeature.py --min-quality 60 --save models/multifeature.json

Exit codes: 0 ok · 1 insufficient data · 2 DB/IO failure
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.calibration import build_calibration_pairs
from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.db import DatabaseError
from armband_ai.models import DEFAULT_FEATURE_KEYS, fit_multifeature
from armband_ai.queries import count_libre, count_readings


def main() -> int:
    try:
        cfg = load_config()
    except Exception as e:
        print(f"ERROR: failed to load config: {e}", file=sys.stderr)
        return 2

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

    if args.window <= 0:
        print("ERROR: --window must be positive", file=sys.stderr)
        return 1

    db_path = cfg["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(PROJECT_ROOT / db_path)

    prefer_still = not args.no_prefer_still

    try:
        print(f"PPG={count_readings(db_path)}  Libre={count_libre(db_path)}")
    except (DatabaseError, OSError) as e:
        print(f"ERROR: database failure: {e}", file=sys.stderr)
        return 2

    print(
        f"Gates: quality>={args.min_quality} still>={args.min_still} "
        f"window=±{args.window}s"
    )

    try:
        pairs = build_calibration_pairs(
            db_path,
            window_seconds=args.window,
            prefer_still=prefer_still,
            min_quality=args.min_quality,
            min_still_fraction=args.min_still,
        )
    except (DatabaseError, OSError) as e:
        print(f"ERROR: pairing failed: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: pairing failed: {e}", file=sys.stderr)
        return 2

    print(f"Pairs: {len(pairs)}")
    if pairs.empty:
        print("No pairs. Collect still Libre readings first.")
        return 1

    if args.export_pairs:
        try:
            Path(args.export_pairs).parent.mkdir(parents=True, exist_ok=True)
            pairs.to_csv(args.export_pairs, index=False)
            print(f"Pairs → {args.export_pairs}")
        except OSError as e:
            print(f"ERROR: cannot write {args.export_pairs}: {e}", file=sys.stderr)
            return 2

    model = fit_multifeature(
        pairs,
        DEFAULT_FEATURE_KEYS,
        min_quality=args.min_quality,
        min_still_fraction=args.min_still,
        window_seconds=args.window,
    )
    if model is None:
        print(
            f"Need at least {len(DEFAULT_FEATURE_KEYS) + 1} pairs "
            "for full feature set (or enough columns present)."
        )
        return 1

    print(f"R²={model.r2:.4f}  MAE={model.mae:.2f}  RMSE={model.rmse:.2f}  n={model.n_pairs}")
    print(f"intercept={model.intercept:.4f}")
    for k, c in zip(model.feature_keys, model.coefficients):
        print(f"  {k:20s}  {c:+.6f}")

    try:
        model.save(args.save)
        print(f"Saved → {args.save}")
    except OSError as e:
        print(f"ERROR: cannot save model to {args.save}: {e}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
