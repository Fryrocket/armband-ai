#!/usr/bin/env python3
"""Background inference service: quality + models (Hailo → CPU) → SQLite.

Usage:
  python scripts/run_inference.py              # loop
  python scripts/run_inference.py --once       # single snapshot

Exit codes (--once): 0 ok · 1 no data · 2 failure
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.config import load_config
from armband_ai.db import DatabaseError
from armband_ai.inference_service import (
    resolve_db_path,
    resolve_hef_path,
    resolve_model_path,
    resolve_multifeature_path,
    resolve_norm_path,
    run_loop,
    run_once,
)
from armband_ai.logger import setup_logging

log = logging.getLogger("armband_ai.inference.cli")


def main() -> int:
    parser = argparse.ArgumentParser(description="Armband inference / quality service")
    parser.add_argument("--once", action="store_true", help="Run a single iteration and exit")
    parser.add_argument("--interval", type=float, default=None, help="Override interval seconds")
    parser.add_argument("--window", type=float, default=None, help="Override window minutes")
    args = parser.parse_args()

    try:
        cfg = load_config()
    except Exception as e:
        print(f"ERROR: failed to load config: {e}", file=sys.stderr)
        return 2

    try:
        setup_logging(
            level=cfg["logging"]["level"],
            log_file=cfg["logging"].get("file"),
        )
    except OSError as e:
        print(f"ERROR: cannot set up logging: {e}", file=sys.stderr)
        return 2

    if args.interval is not None:
        if args.interval <= 0:
            print("ERROR: --interval must be positive", file=sys.stderr)
            return 1
        cfg.setdefault("inference", {})["interval_seconds"] = args.interval
    if args.window is not None:
        if args.window <= 0:
            print("ERROR: --window must be positive", file=sys.stderr)
            return 1
        cfg.setdefault("inference", {})["window_minutes"] = args.window

    if args.once:
        try:
            db_path = resolve_db_path(cfg)
            window = float((cfg.get("inference") or {}).get("window_minutes", 5))
            result = run_once(
                db_path,
                window_minutes=window,
                model_path=resolve_model_path(cfg),
                multifeature_path=resolve_multifeature_path(cfg),
                hef_path=resolve_hef_path(cfg),
                norm_path=resolve_norm_path(cfg),
            )
        except DatabaseError as e:
            print(f"ERROR: database failure: {e}", file=sys.stderr)
            return 2
        except Exception as e:
            log.exception("run_once failed")
            print(f"ERROR: inference failed: {e}", file=sys.stderr)
            return 2

        if result is None:
            print("No data.")
            return 1
        print(result)
        return 0

    try:
        run_loop(cfg)
        return 0
    except DatabaseError as e:
        print(f"ERROR: database failure: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        log.exception("inference loop failed")
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
