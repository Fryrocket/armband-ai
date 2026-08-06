#!/usr/bin/env python3
"""Score recent armband signal quality (CPU heuristic).

Usage:
  python scripts/run_quality.py
  python scripts/run_quality.py --minutes 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.quality import score_from_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Armband signal quality score")
    parser.add_argument("--minutes", type=int, default=5, help="Lookback minutes")
    parser.add_argument("--json", action="store_true", help="Print full JSON")
    args = parser.parse_args()

    cfg = load_config()
    db_path = cfg["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(PROJECT_ROOT / db_path)

    result = score_from_db(db_path, minutes=args.minutes)
    if result is None:
        print("No data in window.")
        sys.exit(1)

    print(f"Quality: {result.score:.0f}/100  ({result.label})")
    for r in result.reasons:
        print(f"  - {r}")

    if args.json:
        print()
        print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    main()
