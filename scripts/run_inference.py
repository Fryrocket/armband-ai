#!/usr/bin/env python3
"""Background inference service: quality + models (Hailo → CPU) → SQLite.

Usage:
  python scripts/run_inference.py              # loop
  python scripts/run_inference.py --once       # single snapshot
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.config import load_config
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Armband inference / quality service")
    parser.add_argument("--once", action="store_true", help="Run a single iteration and exit")
    parser.add_argument("--interval", type=float, default=None, help="Override interval seconds")
    parser.add_argument("--window", type=float, default=None, help="Override window minutes")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(
        level=cfg["logging"]["level"],
        log_file=cfg["logging"].get("file"),
    )

    if args.interval is not None:
        cfg.setdefault("inference", {})["interval_seconds"] = args.interval
    if args.window is not None:
        cfg.setdefault("inference", {})["window_minutes"] = args.window

    if args.once:
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
        if result is None:
            print("No data.")
            sys.exit(1)
        print(result)
        return

    run_loop(cfg)


if __name__ == "__main__":
    main()
