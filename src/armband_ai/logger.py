"""MQTT logger that subscribes to the armband and writes to SQLite.

Also accepts iOS companion batch dumps on armband/ios/batch and ACKs them
on armband/ios/batch/ack so the phone only marks records synced after
confirmed insert.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .config import load_config, ROOT
from .db import init_db, insert_reading

log = logging.getLogger("armband_ai.logger")

DEFAULT_LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
DEFAULT_LOG_BACKUP_COUNT = 5
DEFAULT_LOG_COMPRESSION = "zstd"


def _normalize_compression(value: Any, compress_flag: Any = True) -> str:
    """Return 'gzip' | 'zstd' | 'none'. Default zstd when compression is enabled."""
    if isinstance(compress_flag, str):
        flag = compress_flag.strip().lower() in ("1", "true", "yes", "on")
    else:
        flag = bool(compress_flag)

    if value is None:
        return DEFAULT_LOG_COMPRESSION if flag else "none"

    s = str(value).strip().lower()
    if s in ("", "none", "off", "false", "0", "no"):
        return "none"
    if s in ("zstd", "zst", "true", "1", "yes", "on"):
        return "zstd"
    if s in ("gzip", "gz"):
        return "gzip"
    return DEFAULT_LOG_COMPRESSION if flag else "none"


def _warn_compression(msg: str) -> None:
    """Best-effort warning before/during rotation (logging may be mid-setup)."""
    line = f"armband_ai.logger: {msg}"
    try:
        log.warning("%s", msg)
    except Exception:
        pass
    print(line, file=sys.stderr)


def _make_namer(suffix: str) -> Callable[[str], str]:
    def namer(name: str) -> str:
        return name + suffix

    return namer


def _gzip_rotator(source: str, dest: str) -> None:
    try:
        with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
            while True:
                chunk = f_in.read(1024 * 64)
                if not chunk:
                    break
                f_out.write(chunk)
        os.remove(source)
    except OSError as e:
        _warn_compression(f"gzip rotation failed ({e}); keeping plain archive if possible")
        try:
            os.replace(source, dest[:-3] if dest.endswith(".gz") else dest)
        except OSError as e2:
            _warn_compression(f"gzip fallback rename failed: {e2}")


def _zstd_rotator(source: str, dest: str) -> None:
    """Prefer `zstd` CLI; fall back to plain file if unavailable or compress fails."""
    zstd = shutil.which("zstd")
    plain = dest[: -len(".zst")] if dest.endswith(".zst") else dest

    if not zstd:
        _warn_compression(
            "zstd not on PATH – rotated log left uncompressed "
            f"({plain}). Install: sudo apt install zstd"
        )
        try:
            os.replace(source, plain)
        except OSError as e:
            _warn_compression(f"zstd fallback rename failed: {e}")
        return

    try:
        subprocess.run(
            [zstd, "-f", "-q", "-o", dest, source],
            check=True,
            capture_output=True,
        )
        os.remove(source)
    except (OSError, subprocess.CalledProcessError) as e:
        _warn_compression(
            f"zstd compress failed ({e}); leaving uncompressed archive if possible"
        )
        try:
            if os.path.exists(source):
                os.replace(source, plain)
        except OSError as e2:
            _warn_compression(f"zstd fallback rename failed: {e2}")


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
    compression: str = DEFAULT_LOG_COMPRESSION,
) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    ):
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    if log_file:
        path = Path(log_file)
        if not path.is_absolute():
            path = ROOT / path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(
                f"armband_ai.logger: cannot create log dir {path.parent}: {e}",
                file=sys.stderr,
            )
            raise

        for h in list(root.handlers):
            if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == str(path):
                root.removeHandler(h)
                h.close()

        method = _normalize_compression(compression, True)
        if method == "zstd" and not shutil.which("zstd"):
            _warn_compression(
                "compression=zstd but 'zstd' not found – "
                "archives will stay uncompressed until: sudo apt install zstd"
            )
        elif method == "gzip" and not shutil.which("gzip"):
            _warn_compression(
                "compression=gzip but 'gzip' not found – archives will stay uncompressed"
            )

        try:
            fh = RotatingFileHandler(
                path,
                maxBytes=max(1024, int(max_bytes)),
                backupCount=max(1, int(backup_count)),
                encoding="utf-8",
            )
        except OSError as e:
            print(
                f"armband_ai.logger: cannot open log file {path}: {e}",
                file=sys.stderr,
            )
            raise

        if method == "gzip":
            fh.namer = _make_namer(".gz")
            fh.rotator = _gzip_rotator
        elif method == "zstd":
            fh.namer = _make_namer(".zst")
            fh.rotator = _zstd_rotator
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
            batch_topic = self.mqtt_cfg.get("batch_topic") or "armband/ios/batch"
            client.subscribe(batch_topic, qos=1)
            log.info(
                "Connected to %s:%s – subscribed to %s and %s",
                self.mqtt_cfg["broker"],
                self.mqtt_cfg["port"],
                topic,
                batch_topic,
            )
        else:
            log.error("MQTT connect failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        log.warning("Disconnected (rc=%s). Will auto-reconnect...", reason_code)

    def _on_message(self, client, userdata, msg):
        batch_topic = self.mqtt_cfg.get("batch_topic") or "armband/ios/batch"
        if msg.topic == batch_topic or msg.topic.endswith("/ios/batch"):
            self._handle_ios_batch(msg)
            return

        try:
            payload = msg.payload.decode("utf-8")
            data: dict[str, Any] = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            log.warning("Bad payload on %s: %s", msg.topic, e)
            return

        try:
            row_id = insert_reading(self.db_path, data)
            self._msg_count += 1
            log.info(
                "#%d id=%d  bpm=%s  filt940=%s  moving=%s  boot=%s",
                self._msg_count,
                row_id,
                data.get("bpm"),
                data.get("filt940"),
                data.get("moving"),
                data.get("boot"),
            )
        except Exception:
            log.exception("Failed to store reading")

    def _handle_ios_batch(self, msg):
        """Accept a batch dump from armband-ios and ACK it.

        Phone only marks records synced after receiving
        {"batch_id": "...", "status": "ok", "inserted": N} on the ACK topic.
        """
        try:
            payload = msg.payload.decode("utf-8")
            data: dict[str, Any] = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            log.warning("Bad iOS batch on %s: %s", msg.topic, e)
            return

        batch_id = str(data.get("batch_id") or "")
        readings = data.get("readings") or []
        if not isinstance(readings, list):
            log.warning("iOS batch %s: readings is not a list", batch_id[:8])
            return

        inserted = 0
        for r in readings:
            if not isinstance(r, dict):
                continue
            row = {
                "bpm": r.get("bpm"),
                "spo2": r.get("spo2"),
                "temp": r.get("temp"),
                "motion": r.get("motion"),
                "moving": r.get("moving"),
                "raw940": r.get("raw940"),
                "filt940": r.get("filt940"),
                "batt": r.get("batt"),
                "trans": r.get("trans"),
                "ts": r.get("ts"),
                "source": data.get("source", "ios"),
                "batch_id": batch_id,
                "device_id": data.get("device_id"),
                "session_id": data.get("session_id"),
            }
            try:
                insert_reading(self.db_path, row)
                inserted += 1
                self._msg_count += 1
            except Exception:
                log.exception("Failed to store iOS batch reading")

        ack_topic = self.mqtt_cfg.get("batch_ack_topic") or "armband/ios/batch/ack"
        ack = json.dumps(
            {"batch_id": batch_id, "status": "ok", "inserted": inserted},
            ensure_ascii=False,
        )
        try:
            self.client.publish(ack_topic, ack, qos=1)
        except Exception:
            log.exception("Failed to publish batch ACK")
        log.info(
            "iOS batch %s: inserted %d/%d, ACK → %s",
            batch_id[:8] if batch_id else "?",
            inserted,
            len(readings),
            ack_topic,
        )

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
    log_cfg = cfg.get("logging") or {}
    method = _normalize_compression(
        log_cfg.get("compression", log_cfg.get("compress", DEFAULT_LOG_COMPRESSION)),
        log_cfg.get("compress", True),
    )
    setup_logging(
        level=log_cfg.get("level", "INFO"),
        log_file=log_cfg.get("file"),
        max_bytes=int(log_cfg.get("max_bytes", DEFAULT_LOG_MAX_BYTES)),
        backup_count=int(log_cfg.get("backup_count", DEFAULT_LOG_BACKUP_COUNT)),
        compression=method,
    )
    logger = ArmbandLogger(cfg)
    logger.start()


if __name__ == "__main__":
    main()
