"""SQLite persistence for armband PPG + 940 nm readings, calibration, and inference.

Invalid-value convention
------------------------
Firmware uses non-positive values to mean \"invalid / not computed\":

  * spo2 < 0  (typically -1)
  * bpm  <= 0 (no finger, or no beat accumulated yet this wake)
  * temp <= 0 (temperature not sampled yet this wake)

spo2 sentinels are stored as-is and filtered at feature time. bpm/temp
sentinels are stored as NULL by insert_reading(). Neither is ever clamped
into a plausible-looking range.

source_id (optional)
--------------------
iOS companion sends a UUID per reading. When present, inserts use
INSERT OR IGNORE against a UNIQUE index so re-sent batches are idempotent.

session_id (optional, Fix Pack 3)
---------------------------------
Per-reading session UUID from the iOS companion. Authoritative over any
batch-level session_id. Stored as a first-class column so S001/S002 and
re-seat protocol attribution is queryable without parsing raw_json.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("armband_ai.db")


class DatabaseError(RuntimeError):
    """Raised when a DB operation fails in a way callers should surface."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS ppg_readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at     TEXT    NOT NULL,
    bpm             INTEGER,
    spo2            INTEGER,
    temp            REAL,
    motion          REAL,
    moving          INTEGER,
    raw940          INTEGER,
    filt940         REAL,
    batt            REAL,
    trans           TEXT,
    conn_ms         INTEGER,
    boot            INTEGER,
    source_id       TEXT,
    session_id      TEXT,
    raw_json        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ppg_received_at ON ppg_readings(received_at);
CREATE INDEX IF NOT EXISTS idx_ppg_boot       ON ppg_readings(boot);
CREATE INDEX IF NOT EXISTS idx_ppg_moving     ON ppg_readings(moving);
CREATE INDEX IF NOT EXISTS idx_ppg_session_id ON ppg_readings(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ppg_source_id ON ppg_readings(source_id) WHERE source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS libre_readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at     TEXT    NOT NULL,
    glucose_mgdl    REAL    NOT NULL,
    source          TEXT    DEFAULT 'libre',
    notes           TEXT,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_libre_recorded_at ON libre_readings(recorded_at);

CREATE TABLE IF NOT EXISTS inference_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at         TEXT    NOT NULL,
    window_minutes      REAL    NOT NULL,
    n_samples           INTEGER,
    quality_score       REAL,
    quality_label       TEXT,
    quality_reasons     TEXT,
    filt940_mean        REAL,
    still_fraction      REAL,
    bpm_mean            REAL,
    glucose_estimate    REAL,
    baseline_r2         REAL,
    model_path          TEXT,
    feature_json        TEXT,
    source              TEXT    DEFAULT 'cpu_quality'
);

CREATE INDEX IF NOT EXISTS idx_inference_computed_at ON inference_results(computed_at);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise DatabaseError(f"Cannot create DB directory {path.parent}: {e}") from e

    try:
        conn = sqlite3.connect(str(path), timeout=30.0)
    except sqlite3.Error as e:
        raise DatabaseError(f"Cannot open database {path}: {e}") from e

    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
    except sqlite3.Error as e:
        conn.close()
        raise DatabaseError(f"PRAGMA failed on {path}: {e}") from e
    return conn


def init_db(db_path: str | Path) -> None:
    """Create tables/indexes if missing. Migrates source_id and session_id on older DBs."""
    try:
        with get_connection(db_path) as conn:
            conn.executescript(SCHEMA)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(ppg_readings)").fetchall()]
            if "source_id" not in cols:
                conn.execute("ALTER TABLE ppg_readings ADD COLUMN source_id TEXT")
                log.info("Migrated ppg_readings: added source_id")
            if "session_id" not in cols:
                conn.execute("ALTER TABLE ppg_readings ADD COLUMN session_id TEXT")
                log.info("Migrated ppg_readings: added session_id")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ppg_source_id "
                "ON ppg_readings(source_id) WHERE source_id IS NOT NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ppg_session_id ON ppg_readings(session_id)"
            )
            conn.commit()
    except DatabaseError:
        raise
    except sqlite3.Error as e:
        log.exception("init_db failed for %s", db_path)
        raise DatabaseError(f"init_db failed for {db_path}: {e}") from e


def _normalize_spo2(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


BPM_MIN, BPM_MAX = 35, 220
TEMP_MIN, TEMP_MAX = 30.0, 45.0


def _soft_validate(bpm, temp):
    warnings = []

    if bpm is not None:
        try:
            bpm_f = float(bpm)
            if bpm_f <= 0:
                bpm = None
            elif not (BPM_MIN <= bpm_f <= BPM_MAX):
                warnings.append(f"bpm {bpm_f} outside [{BPM_MIN}, {BPM_MAX}]")
                bpm = max(BPM_MIN, min(BPM_MAX, bpm_f))
                bpm = int(round(bpm)) if bpm == int(bpm) else bpm
            else:
                bpm = int(round(bpm_f)) if bpm_f == int(bpm_f) else bpm_f
        except (TypeError, ValueError):
            bpm = None

    if temp is not None:
        try:
            temp_f = float(temp)
            if temp_f <= 0:
                temp = None
            elif not (TEMP_MIN <= temp_f <= TEMP_MAX):
                warnings.append(f"temp {temp_f} outside [{TEMP_MIN}, {TEMP_MAX}]")
                temp = max(TEMP_MIN, min(TEMP_MAX, temp_f))
            else:
                temp = temp_f
        except (TypeError, ValueError):
            temp = None

    if warnings:
        log.warning("insert_reading soft-validation: %s", "; ".join(warnings))

    return bpm, temp


def insert_reading(db_path: str | Path, data: dict[str, Any]) -> int:
    """Insert one PPG reading. Returns new row id, or -1 if ignored as duplicate source_id.

    When data contains 'id' or 'source_id' (iOS UUID), uses INSERT OR IGNORE so
    re-sent batches are idempotent. Callers treating \"accepted\" should count
    both positive ids and -1 as success for ACK purposes.

    session_id is stored when present (per-reading preferred over batch-level).
    """
    received_at = datetime.now(timezone.utc).isoformat()

    moving = data.get("moving")
    if isinstance(moving, bool):
        moving = 1 if moving else 0
    elif moving is None:
        moving = None
    elif isinstance(moving, (int, float)):
        moving = 1 if moving else 0
    else:
        moving = 1 if str(moving).lower() in ("true", "1", "yes") else 0

    spo2 = _normalize_spo2(data.get("spo2"))
    bpm, temp = _soft_validate(data.get("bpm"), data.get("temp"))

    source_id = data.get("source_id") or data.get("id")
    if source_id is not None:
        source_id = str(source_id)

    session_id = data.get("session_id")
    if session_id is not None:
        session_id = str(session_id)

    try:
        with get_connection(db_path) as conn:
            if source_id:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO ppg_readings (
                        received_at, bpm, spo2, temp, motion, moving,
                        raw940, filt940, batt, trans, conn_ms, boot,
                        source_id, session_id, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        received_at, bpm, spo2, temp,
                        data.get("motion"), moving,
                        data.get("raw940"), data.get("filt940"),
                        data.get("batt"), data.get("trans"),
                        data.get("conn_ms"), data.get("boot"),
                        source_id, session_id,
                        json.dumps(data, ensure_ascii=False),
                    ),
                )
                conn.commit()
                if cur.rowcount == 0:
                    return -1  # duplicate source_id ignored
                return cur.lastrowid or 0
            else:
                cur = conn.execute(
                    """
                    INSERT INTO ppg_readings (
                        received_at, bpm, spo2, temp, motion, moving,
                        raw940, filt940, batt, trans, conn_ms, boot,
                        source_id, session_id, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        received_at, bpm, spo2, temp,
                        data.get("motion"), moving,
                        data.get("raw940"), data.get("filt940"),
                        data.get("batt"), data.get("trans"),
                        data.get("conn_ms"), data.get("boot"),
                        None, session_id,
                        json.dumps(data, ensure_ascii=False),
                    ),
                )
                conn.commit()
                return cur.lastrowid or 0
    except DatabaseError:
        raise
    except sqlite3.Error as e:
        log.exception("insert_reading failed")
        raise DatabaseError(f"insert_reading failed: {e}") from e
    except OSError as e:
        log.exception("insert_reading OS error")
        raise DatabaseError(f"insert_reading OS error: {e}") from e


def insert_libre(
    db_path: str | Path,
    glucose_mgdl: float,
    recorded_at: Optional[str] = None,
    source: str = "libre",
    notes: Optional[str] = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    if recorded_at is None:
        recorded_at = now
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO libre_readings (recorded_at, glucose_mgdl, source, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (recorded_at, float(glucose_mgdl), source, notes, now),
            )
            conn.commit()
            return cur.lastrowid or 0
    except DatabaseError:
        raise
    except sqlite3.Error as e:
        log.exception("insert_libre failed")
        raise DatabaseError(f"insert_libre failed: {e}") from e
    except OSError as e:
        log.exception("insert_libre OS error")
        raise DatabaseError(f"insert_libre OS error: {e}") from e


def delete_libre(db_path: str | Path, libre_id: int) -> bool:
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute("DELETE FROM libre_readings WHERE id = ?", (libre_id,))
            conn.commit()
            return cur.rowcount > 0
    except DatabaseError:
        raise
    except sqlite3.Error as e:
        log.exception("delete_libre failed")
        raise DatabaseError(f"delete_libre failed: {e}") from e


def insert_inference(
    db_path: str | Path,
    *,
    window_minutes: float,
    n_samples: int,
    quality_score: Optional[float],
    quality_label: Optional[str],
    quality_reasons: Optional[list[str]],
    filt940_mean: Optional[float],
    still_fraction: Optional[float],
    bpm_mean: Optional[float],
    glucose_estimate: Optional[float],
    baseline_r2: Optional[float],
    model_path: Optional[str],
    feature_json: Optional[dict[str, Any]],
    source: str = "cpu_quality",
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO inference_results (
                    computed_at, window_minutes, n_samples,
                    quality_score, quality_label, quality_reasons,
                    filt940_mean, still_fraction, bpm_mean,
                    glucose_estimate, baseline_r2, model_path,
                    feature_json, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now, float(window_minutes), int(n_samples),
                    quality_score, quality_label,
                    json.dumps(quality_reasons or [], ensure_ascii=False),
                    filt940_mean, still_fraction, bpm_mean,
                    glucose_estimate, baseline_r2, model_path,
                    json.dumps(feature_json or {}, ensure_ascii=False),
                    source,
                ),
            )
            conn.commit()
            return cur.lastrowid or 0
    except DatabaseError:
        raise
    except sqlite3.Error as e:
        log.exception("insert_inference failed")
        raise DatabaseError(f"insert_inference failed: {e}") from e
    except OSError as e:
        log.exception("insert_inference OS error")
        raise DatabaseError(f"insert_inference OS error: {e}") from e


def integrity_check(db_path: str | Path) -> str:
    try:
        with get_connection(db_path) as conn:
            row = conn.execute("PRAGMA integrity_check;").fetchone()
            return str(row[0]) if row else "unknown"
    except DatabaseError as e:
        return f"error: {e}"
    except sqlite3.Error as e:
        return f"error: {e}"
