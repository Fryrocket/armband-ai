#!/usr/bin/env python3
"""Export sliding-window features from the armband SQLite DB.

Usage:
  python scripts/export_features.py
  python scripts/export_features.py --minutes 5
  python scripts/export_features.py --rolling --window 120 --step 30 -o exports/features.csv

Exit codes: 0 ok · 1 no data · 2 DB/IO failure
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.db import DatabaseError
from armband_ai.features import features_from_db, rolling_feature_frames


def main() -> int:
    parser = argparse.ArgumentParser(description="Export armband feature windows")
    parser.add_argument("--minutes", type=int, default=5, help="Single-window lookback minutes")
    parser.add_argument("--rolling", action="store_true", help="Overlapping windows")
    parser.add_argument("--window", type=int, default=120, help="Rolling window seconds")
    parser.add_argument("--step", type=int, default=30, help="Rolling step seconds")
    parser.add_argument("--lookback", type=int, default=60, help="Rolling lookback minutes")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output path")
    args = parser.parse_args()

    if args.minutes <= 0 or args.window <= 0 or args.step <= 0:
        print("ERROR: minutes/window/step must be positive", file=sys.stderr)
        return 1

    try:
        cfg = load_config()
        db_path = cfg["database"]["path"]
        if not Path(db_path).is_absolute():
            db_path = str(PROJECT_ROOT / db_path)
    except Exception as e:
        print(f"ERROR: config: {e}", file=sys.stderr)
        return 2

    try:
        if args.rolling:
            df = rolling_feature_frames(
                db_path,
                window_seconds=args.window,
                step_seconds=args.step,
                lookback_minutes=args.lookback,
            )
            if df.empty:
                print("No rolling windows produced (need more history in the DB).")
                return 1
            out = (
                Path(args.output)
                if args.output
                else (PROJECT_ROOT / "exports" / "features_rolling.csv")
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(out, index=False)
            print(f"{len(df)} windows → {out}")
            print(df.head().to_string(index=False))
            return 0

        feats = features_from_db(db_path, minutes=args.minutes)
        if feats is None:
            print("No PPG data in the requested window.")
            return 1

        print(json.dumps(feats.to_dict(), indent=2))
        print()
        print("vector:", feats.to_vector().tolist())

        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.suffix.lower() == ".json":
                out.write_text(json.dumps(feats.to_dict(), indent=2), encoding="utf-8")
            else:
                import pandas as pd

                pd.DataFrame([feats.to_dict()]).to_csv(out, index=False)
            print(f"Saved → {out}")
        return 0

    except DatabaseError as e:
        print(f"ERROR: database failure: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"ERROR: IO failure: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
