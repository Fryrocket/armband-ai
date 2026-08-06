"""MQTT logger that subscribes to the armband and writes to SQLite."""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from .config import load_config, ROOT
from .db import init_db, insert_reading

log = logging.getLogger("armband_ai.logger")


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    # Optional file
    if log_file:
        path = Path(log_file)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


class ArmbandLogger:
    def __init__(self, config: dict | None = None):
        self.cfg = config or load_config()
        self.mqtt_cfg = self.cfg["mqtt"]
        self.db_path = self.cfg["database"]["path"]
        if not Path(self.db_path).is_absolute():
            self.db_path = str(ROOT / self.db_path)

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.mqtt_cfg["client_id"],
            clean_session=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        if self.mqtt_cfg.get("username"):
            self.client.username_pw_set(
                self.mqtt_cfg["username"],
                self.mqtt_cfg.get("password") or None,
            )

        self._running = False
        self._msg_count = 0

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0 or str(reason_code) == "Success":
            topic = self.mqtt_cfg["topic"]
            client.subscribe(topic, qos=1)
            log.info("Connected to %s:%s – subscribed to %s",
                     self.mqtt_cfg["broker"], self.mqtt_cfg["port"], topic)
        else:
            log.error("MQTT connect failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        log.warning("Disconnected (rc=%s). Will auto-reconnect...", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8")
            data: dict[str, Any] = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            log.warning("Bad payload on %s: %s", msg.topic, e)
            return

        try:
            row_id = insert_reading(self.db_path, data)
            self._msg_count += 1

            bpm = data.get("bpm")
            filt940 = data.get("filt940")
            moving = data.get("moving")
            boot = data.get("boot")

            log.info(
                "#%d id=%d  bpm=%s  filt940=%s  moving=%s  boot=%s",
                self._msg_count, row_id, bpm, filt940, moving, boot,
            )
        except Exception:
            log.exception("Failed to store reading")

    def start(self) -> None:
        init_db(self.db_path)
        log.info("Database ready: %s", self.db_path)

        broker = self.mqtt_cfg["broker"]
        port = self.mqtt_cfg["port"]
        keepalive = self.mqtt_cfg.get("keepalive", 60)

        log.info("Connecting to MQTT %s:%s ...", broker, port)
        self.client.connect(broker, port, keepalive)

        self._running = True

        def _shutdown(signum, frame):
            log.info("Shutdown signal received")
            self.stop()

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        log.info("Logger running. Ctrl+C to stop.")
        try:
            self.client.loop_forever()
        except Exception:
            log.exception("MQTT loop error")
        finally:
            self._running = False

    def stop(self) -> None:
        if self._running:
            log.info("Stopping logger (messages stored: %d)", self._msg_count)
            self.client.disconnect()
            self.client.loop_stop()
            self._running = False


def main() -> None:
    cfg = load_config()
    setup_logging(
        level=cfg["logging"]["level"],
        log_file=cfg["logging"].get("file"),
    )
    logger = ArmbandLogger(cfg)
    logger.start()


if __name__ == "__main__":
    main()
