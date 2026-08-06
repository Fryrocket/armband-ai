#!/usr/bin/env python3
"""Build calibration pairs and fit a baseline linear model.

Usage:
  python scripts/calibrate.py
  python scripts/calibrate.py --window 120 --no-prefer-still
  python scripts/calibrate.py --save models/baseline.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.calibration import build_calibration_pairs, fit_baseline
from armband_ai.queries import count_libre, count_readings


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibration pairing + baseline fit")
    parser.add_argument(
        "--window",
        type=int,
        default=180,
        help="± seconds around each Libre reading to search for PPG samples (default 180)",
    )
    parser.add_argument(
        "--no-prefer-still",
        action="store_true",
        help="Do not prefer non-moving samples",
    )
    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="Path to save model JSON (default: models/baseline.json)",
    )
    parser.add_argument(
        "--export-pairs",
        type=str,
        default=None,
        help="Optional path to export pairs CSV",
    )
    args = parser.parse_args()

    cfg = load_config()
    db_path = cfg["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(PROJECT_ROOT / db_path)

    prefer_still = not args.no_prefer_still

    n_ppg = count_readings(db_path)
    n_libre = count_libre(db_path)
    print(f"PPG readings : {n_ppg}")
    print(f"Libre readings: {n_libre}")

    if n_libre < 2:
        print("Need at least 2 Libre readings to fit a baseline. Log more with:")
        print("  python scripts/log_glucose.py <mg/dL>")
        return

    pairs = build_calibration_pairs(
        db_path,
        window_seconds=args.window,
        prefer_still=prefer_still,
    )

    print(f"Calibration pairs found: {len(pairs)}  (window ±{args.window}s, prefer_still={prefer_still})")

    if pairs.empty:
        print("No pairs. Check that armband data exists around your Libre timestamps.")
        return

    print()
    print(pairs[["recorded_at", "glucose_mgdl", "filt940_mean", "n_samples", "still_fraction", "time_offset_s"]].to_string(index=False))
    print()

    if args.export_pairs:
        out = Path(args.export_pairs)
        pairs.to_csv(out, index=False)
        print(f"Pairs exported → {out}")

    model = fit_baseline(pairs, window_seconds=args.window, prefer_still=prefer_still)
    if model is None:
        print("Could not fit model (need ≥ 2 pairs).")
        return

    print("Baseline model:  glucose ≈ slope * filt940 + intercept")
    print(f"  slope     = {model.slope:.6f}")
    print(f"  intercept = {model.intercept:.4f}")
    print(f"  R²        = {model.r2:.4f}")
    print(f"  MAE       = {model.mae:.2f} mg/dL")
    print(f"  RMSE      = {model.rmse:.2f} mg/dL")
    print(f"  n_pairs   = {model.n_pairs}")

    save_path = args.save or str(PROJECT_ROOT / "models" / "baseline.json")
    model.save(save_path)
    print(f"\nModel saved → {save_path}")


if __name__ == "__main__":
    main()
