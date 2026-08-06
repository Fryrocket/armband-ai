#!/usr/bin/env python3
"""Log a FreeStyle Libre / reference glucose reading.

Examples:
  python scripts/log_glucose.py 142
  python scripts/log_glucose.py 142 --notes "post-meal 45 min"
  python scripts/log_glucose.py 118 --source fingerstick
  python scripts/log_glucose.py 135 --at "2026-08-06T14:30:00"

Exit codes: 0 ok · 1 usage/validation · 2 DB/IO failure
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.db import DatabaseError, init_db, insert_libre


def parse_at(value: str) -> str:
    """Accept ISO-ish strings; return UTC ISO. Raises ValueError on bad input."""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(
            f"Cannot parse --at timestamp {value!r}. "
            "Use ISO like 2026-08-06T14:30:00"
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def main() -> int:
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
        print(
            f"Warning: {args.glucose} mg/dL looks unusual – continuing anyway.",
            file=sys.stderr,
        )

    try:
        recorded_at = parse_at(args.at) if args.at else None
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        cfg = load_config()
        db_path = cfg["database"]["path"]
        if not Path(db_path).is_absolute():
            db_path = str(PROJECT_ROOT / db_path)

        init_db(db_path)
        row_id = insert_libre(
            db_path,
            glucose_mgdl=args.glucose,
            recorded_at=recorded_at,
            source=args.source,
            notes=args.notes,
        )
    except DatabaseError as e:
        print(f"ERROR: database failure: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"ERROR: filesystem failure: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: unexpected failure: {e}", file=sys.stderr)
        return 2

    when = recorded_at or "now (UTC)"
    print(
        f"Logged glucose {args.glucose} mg/dL  id={row_id}  "
        f"at={when}  source={args.source}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
