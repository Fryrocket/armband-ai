"""Common database queries for the dashboard and CLI tools."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .db import get_connection


def load_recent(
    db_path: str | Path,
    limit: int = 500,
    minutes: Optional[int] = None,
) -> pd.DataFrame:
    """Return recent readings as a DataFrame ordered by time ascending."""
    with get_connection(db_path) as conn:
        if minutes is not None:
            sql = """
                SELECT *
                FROM ppg_readings
                WHERE received_at >= datetime('now', ?)
                ORDER BY received_at ASC
            """
            df = pd.read_sql_query(sql, conn, params=(f"-{minutes} minutes",))
        else:
            sql = """
                SELECT *
                FROM ppg_readings
                ORDER BY id DESC
                LIMIT ?
            """
            df = pd.read_sql_query(sql, conn, params=(limit,))
            df = df.iloc[::-1].reset_index(drop=True)  # oldest → newest

    if not df.empty and "received_at" in df.columns:
        df["received_at"] = pd.to_datetime(df["received_at"], utc=True)

    return df


def load_latest(db_path: str | Path) -> Optional[dict]:
    """Return the single most recent reading as a dict, or None."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM ppg_readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def count_readings(db_path: str | Path) -> int:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM ppg_readings").fetchone()[0]
