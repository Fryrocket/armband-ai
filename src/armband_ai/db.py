"""SQLite persistence for armband PPG + 940 nm readings and calibration data."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS ppg_readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at     TEXT    NOT NULL,          -- UTC ISO when Pi received it
    bpm             INTEGER,
    spo2            INTEGER,                   -- -1 means invalid from firmware
    temp            REAL,
    motion          REAL,                      -- filtered magnitude
    moving          INTEGER,                   -- 0/1
    raw940          INTEGER,
    filt940         REAL,
    batt            REAL,
    trans           TEXT,                      -- still_to_moving / moving_to_still / none
    conn_ms         INTEGER,
    boot            INTEGER,
    raw_json        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ppg_received_at ON ppg_readings(received_at);
CREATE INDEX IF NOT EXISTS idx_ppg_boot       ON ppg_readings(boot);
CREATE INDEX IF NOT EXISTS idx_ppg_moving     ON ppg_readings(moving);

CREATE TABLE IF NOT EXISTS libre_readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at     TEXT    NOT NULL,          -- UTC ISO of the glucose reading
    glucose_mgdl    REAL    NOT NULL,          -- FreeStyle Libre / fingerstick value
    source          TEXT    DEFAULT 'libre',   -- libre | fingerstick | other
    notes           TEXT,
    created_at      TEXT    NOT NULL           -- when this row was inserted
);

CREATE INDEX IF NOT EXISTS idx_libre_recorded_at ON libre_readings(recorded_at);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(db_path: str | Path) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def insert_reading(db_path: str | Path, data: dict[str, Any]) -> int:
    """Insert one PPG reading. Returns the new row id."""
    received_at = datetime.now(timezone.utc).isoformat()

    moving = data.get("moving")
    if isinstance(moving, bool):
        moving = 1 if moving else 0
    elif moving is None:
        moving = None
    else:
        moving = 1 if str(moving).lower() in ("true", "1", "yes") else 0

    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO ppg_readings (
                received_at, bpm, spo2, temp, motion, moving,
                raw940, filt940, batt, trans, conn_ms, boot, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                received_at,
                data.get("bpm"),
                data.get("spo2"),
                data.get("temp"),
                data.get("motion"),
                moving,
                data.get("raw940"),
                data.get("filt940"),
                data.get("batt"),
                data.get("trans"),
                data.get("conn_ms"),
                data.get("boot"),
                json.dumps(data, ensure_ascii=False),
            ),
        )
        conn.commit()
        return cur.lastrowid or 0


def insert_libre(
    db_path: str | Path,
    glucose_mgdl: float,
    recorded_at: Optional[str] = None,
    source: str = "libre",
    notes: Optional[str] = None,
) -> int:
    """Insert a FreeStyle Libre / reference glucose reading.

    recorded_at should be UTC ISO. If omitted, uses now.
    """
    now = datetime.now(timezone.utc).isoformat()
    if recorded_at is None:
        recorded_at = now

    with get_connection(db_path) as conn:
        # Ensure schema exists (safe to call repeatedly)
        conn.executescript(SCHEMA)
        cur = conn.execute(
            """
            INSERT INTO libre_readings (recorded_at, glucose_mgdl, source, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (recorded_at, float(glucose_mgdl), source, notes, now),
        )
        conn.commit()
        return cur.lastrowid or 0


def delete_libre(db_path: str | Path, libre_id: int) -> bool:
    with get_connection(db_path) as conn:
        cur = conn.execute("DELETE FROM libre_readings WHERE id = ?", (libre_id,))
        conn.commit()
        return cur.rowcount > 0
