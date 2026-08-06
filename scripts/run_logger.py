#!/usr/bin/env python3
"""Convenience entry point: python scripts/run_logger.py"""

import sys
from pathlib import Path

# Allow running without installing the package
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.logger import main

if __name__ == "__main__":
    main()
