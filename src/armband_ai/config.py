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

    return config
