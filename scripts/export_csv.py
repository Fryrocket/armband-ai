#!/usr/bin/env python3
"""Export recent (or all) readings to CSV.

Usage:
  python scripts/export_csv.py                  # last 24 h
  python scripts/export_csv.py --minutes 60
  python scripts/export_csv.py --all -o mydata.csv

Exit codes: 0 ok · 1 no data · 2 DB/IO failure
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.db import DatabaseError, get_connection
from armband_ai.queries import load_recent


def main() -> int:
    parser = argparse.ArgumentParser(description="Export armband readings to CSV")
    parser.add_argument(
        "--minutes",
        type=int,
        default=1440,
        help="Look-back window in minutes (default 24 h)",
    )
    parser.add_argument("--all", action="store_true", help="Export entire database")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output path")
    args = parser.parse_args()

    if not args.all and args.minutes <= 0:
        print("ERROR: --minutes must be positive", file=sys.stderr)
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
        if args.all:
            with get_connection(db_path) as conn:
                df = pd.read_sql_query(
                    "SELECT * FROM ppg_readings ORDER BY id ASC", conn
                )
            if not df.empty:
                df["received_at"] = pd.to_datetime(df["received_at"], utc=True)
        else:
            df = load_recent(db_path, minutes=args.minutes)
    except DatabaseError as e:
        print(f"ERROR: database failure: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"ERROR: cannot open database: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: query failed: {e}", file=sys.stderr)
        return 2

    if df.empty:
        print("No data to export.")
        return 1

    try:
        out_dir = PROJECT_ROOT / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)

        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
            out_path = out_dir / f"armband_{stamp}.csv"

        df.to_csv(out_path, index=False)
        print(f"Exported {len(df)} rows → {out_path}")
        return 0
    except OSError as e:
        print(f"ERROR: cannot write CSV: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
