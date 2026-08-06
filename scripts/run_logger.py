#!/usr/bin/env python3
"""Convenience entry point: python scripts/run_logger.py

Exit codes: 0 clean stop · 2 startup/config failure · 130 interrupted
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    try:
        from armband_ai.logger import main as logger_main
    except ImportError as e:
        print(f"ERROR: cannot import armband_ai.logger: {e}", file=sys.stderr)
        print("Hint: run from repo root with src on PYTHONPATH / venv active.", file=sys.stderr)
        return 2

    try:
        logger_main()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"ERROR: logger failed: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
