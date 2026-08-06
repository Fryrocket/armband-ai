"""Common database queries for the dashboard and CLI tools."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .db import get_connection, init_db


def load_recent(
    db_path: str | Path,
    limit: int = 500,
    minutes: Optional[int] = None,
) -> pd.DataFrame:
    """Return recent PPG readings as a DataFrame ordered by time ascending."""
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
    """Return the single most recent PPG reading as a dict, or None."""
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


def load_libre(db_path: str | Path) -> pd.DataFrame:
    """Return all Libre / reference glucose readings, oldest first."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM libre_readings ORDER BY recorded_at ASC",
            conn,
        )
    if not df.empty and "recorded_at" in df.columns:
        df["recorded_at"] = pd.to_datetime(df["recorded_at"], utc=True)
        if "created_at" in df.columns:
            df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    return df


def count_libre(db_path: str | Path) -> int:
    init_db(db_path)
    with get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM libre_readings").fetchone()[0]


def load_inference(
    db_path: str | Path,
    limit: int = 100,
    minutes: Optional[int] = None,
) -> pd.DataFrame:
    """Return recent inference_results rows, oldest → newest within the slice."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        if minutes is not None:
            sql = """
                SELECT *
                FROM inference_results
                WHERE computed_at >= datetime('now', ?)
                ORDER BY computed_at ASC
            """
            df = pd.read_sql_query(sql, conn, params=(f"-{minutes} minutes",))
        else:
            sql = """
                SELECT *
                FROM inference_results
                ORDER BY id DESC
                LIMIT ?
            """
            df = pd.read_sql_query(sql, conn, params=(limit,))
            df = df.iloc[::-1].reset_index(drop=True)

    if not df.empty and "computed_at" in df.columns:
        df["computed_at"] = pd.to_datetime(df["computed_at"], utc=True)
    return df


def load_latest_inference(db_path: str | Path) -> Optional[dict]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM inference_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return dict(row)
