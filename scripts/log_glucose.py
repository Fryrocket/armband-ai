#!/usr/bin/env python3
"""Log a FreeStyle Libre / reference glucose reading.

Examples:
  python scripts/log_glucose.py 142
  python scripts/log_glucose.py 142 --notes "post-meal 45 min"
  python scripts/log_glucose.py 118 --source fingerstick
  python scripts/log_glucose.py 135 --at "2026-08-06T14:30:00"
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.db import init_db, insert_libre


def parse_at(value: str) -> str:
    """Accept ISO-ish strings; return UTC ISO."""
    # Try a few common formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            # Assume local if no tz; store as UTC-naive ISO with Z-ish form
            # For simplicity treat as UTC if no offset given
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    # Last resort: fromisoformat
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Log a reference glucose reading")
    parser.add_argument("glucose", type=float, help="Glucose in mg/dL")
    parser.add_argument("--source", default="libre", help="libre | fingerstick | other")
    parser.add_argument("--notes", default=None, help="Optional note")
    parser.add_argument(
        "--at",
        default=None,
        help="Timestamp of the reading (ISO). Default = now (UTC).",
    )
    args = parser.parse_args()

    if args.glucose <= 0 or args.glucose > 600:
        print(f"Warning: {args.glucose} mg/dL looks unusual – continuing anyway.")

    cfg = load_config()
    db_path = cfg["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(PROJECT_ROOT / db_path)

    init_db(db_path)

    recorded_at = parse_at(args.at) if args.at else None
    row_id = insert_libre(
        db_path,
        glucose_mgdl=args.glucose,
        recorded_at=recorded_at,
        source=args.source,
        notes=args.notes,
    )

    when = recorded_at or "now (UTC)"
    print(f"Logged glucose {args.glucose} mg/dL  id={row_id}  at={when}  source={args.source}")


if __name__ == "__main__":
    main()
