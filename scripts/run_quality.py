#!/usr/bin/env python3
"""Score recent armband signal quality (CPU heuristic).

Usage:
  python scripts/run_quality.py
  python scripts/run_quality.py --minutes 3

Exit codes: 0 ok · 1 no data · 2 DB/config failure
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
from armband_ai.quality import score_from_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Armband signal quality score")
    parser.add_argument("--minutes", type=int, default=5, help="Lookback minutes")
    parser.add_argument("--json", action="store_true", help="Print full JSON")
    args = parser.parse_args()

    if args.minutes <= 0:
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
        result = score_from_db(db_path, minutes=args.minutes)
    except DatabaseError as e:
        print(f"ERROR: database failure: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: quality scoring failed: {e}", file=sys.stderr)
        return 2

    if result is None:
        print("No data in window.")
        return 1

    print(f"Quality: {result.score:.0f}/100  ({result.label})")
    for r in result.reasons:
        print(f"  - {r}")

    if args.json:
        print()
        print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
