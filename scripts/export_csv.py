#!/usr/bin/env python3
"""Export recent (or all) readings to CSV.

Usage:
  python scripts/export_csv.py                  # last 24 h → exports/armband_YYYYMMDD_HHMM.csv
  python scripts/export_csv.py --minutes 60
  python scripts/export_csv.py --all -o mydata.csv
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.queries import load_recent
from armband_ai.db import get_connection
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Export armband readings to CSV")
    parser.add_argument("--minutes", type=int, default=1440, help="Look-back window in minutes (default 24 h)")
    parser.add_argument("--all", action="store_true", help="Export entire database")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output path")
    args = parser.parse_args()

    cfg = load_config()
    db_path = cfg["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(PROJECT_ROOT / db_path)

    if args.all:
        with get_connection(db_path) as conn:
            df = pd.read_sql_query("SELECT * FROM ppg_readings ORDER BY id ASC", conn)
        if not df.empty:
            df["received_at"] = pd.to_datetime(df["received_at"], utc=True)
    else:
        df = load_recent(db_path, minutes=args.minutes)

    if df.empty:
        print("No data to export.")
        return

    out_dir = PROJECT_ROOT / "exports"
    out_dir.mkdir(exist_ok=True)

    if args.output:
        out_path = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        out_path = out_dir / f"armband_{stamp}.csv"

    df.to_csv(out_path, index=False)
    print(f"Exported {len(df)} rows → {out_path}")


if __name__ == "__main__":
    main()
