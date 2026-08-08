# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Fryrocket

"""Load configuration from config.yaml + environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Project root = two levels up from this file (src/armband_ai/config.py)
ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ROOT / ".env")


def _deep_get(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
    return d


def load_config(path: str | Path | None = None) -> dict:
    """Load YAML config, then override with environment variables."""
    cfg_path = Path(path) if path else ROOT / "config.yaml"

    config: dict = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    # --- MQTT overrides ---
    mqtt = config.setdefault("mqtt", {})
    mqtt["broker"] = os.getenv("MQTT_BROKER", mqtt.get("broker", "localhost"))
    mqtt["port"] = int(os.getenv("MQTT_PORT", mqtt.get("port", 1883)))
    mqtt["topic"] = os.getenv("MQTT_TOPIC", mqtt.get("topic", "armband/ppg"))
    mqtt["client_id"] = os.getenv("MQTT_CLIENT_ID", mqtt.get("client_id", "armband_ai_logger"))
    mqtt["username"] = os.getenv("MQTT_USERNAME", mqtt.get("username", ""))
    mqtt["password"] = os.getenv("MQTT_PASSWORD", mqtt.get("password", ""))
    mqtt["keepalive"] = int(os.getenv("MQTT_KEEPALIVE", mqtt.get("keepalive", 60)))

    # --- Database ---
    db = config.setdefault("database", {})
    db["path"] = os.getenv("DB_PATH", db.get("path", "data/armband_data.db"))

    # --- Logging ---
    log = config.setdefault("logging", {})
    log["level"] = os.getenv("LOG_LEVEL", log.get("level", "INFO")).upper()
    log["file"] = os.getenv("LOG_FILE", log.get("file", "logs/mqtt_logger.log"))

    # --- Calibration quality gates (recommended build defaults) ---
    cal = config.setdefault("calibration", {})
    cal.setdefault("window_seconds", 180)
    cal.setdefault("prefer_still", True)
    cal.setdefault("min_quality", 60)
    cal.setdefault("min_still_fraction", 0.7)
    cal.setdefault("min_clean_streak", 10)
    if os.getenv("CAL_MIN_QUALITY") is not None:
        cal["min_quality"] = float(os.getenv("CAL_MIN_QUALITY"))
    if os.getenv("CAL_MIN_STILL") is not None:
        cal["min_still_fraction"] = float(os.getenv("CAL_MIN_STILL"))
    if os.getenv("CAL_MIN_CLEAN_STREAK") is not None:
        cal["min_clean_streak"] = int(os.getenv("CAL_MIN_CLEAN_STREAK"))

    # --- Inference service ---
    inf = config.setdefault("inference", {})
    inf.setdefault("interval_seconds", 30)
    inf.setdefault("window_minutes", 5)
    inf.setdefault("model_path", "models/baseline.json")
    inf.setdefault("multifeature_path", "models/multifeature.json")
    if os.getenv("INFERENCE_INTERVAL") is not None:
        inf["interval_seconds"] = float(os.getenv("INFERENCE_INTERVAL"))
    if os.getenv("INFERENCE_WINDOW_MIN") is not None:
        inf["window_minutes"] = float(os.getenv("INFERENCE_WINDOW_MIN"))

    # --- Hailo ---
    hailo = config.setdefault("hailo", {})
    hailo.setdefault("device_json", "models/hailo_device.json")
    hailo.setdefault("hef_path", "")
    hailo.setdefault("norm_path", "")
    hailo.setdefault("feature_window_minutes", 5)
    if os.getenv("HAILO_HEF_PATH") is not None:
        hailo["hef_path"] = os.getenv("HAILO_HEF_PATH")
    if os.getenv("HAILO_NORM_PATH") is not None:
        hailo["norm_path"] = os.getenv("HAILO_NORM_PATH")

    return config
