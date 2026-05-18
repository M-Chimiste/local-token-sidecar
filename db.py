"""
Token Counter Sidecar — Database Layer

SQLite schema and CRUD helpers for token usage tracking.
All timestamps stored as UTC ISO 8601 strings.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS token_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    response_ms       REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_token_usage_model     ON token_usage(model);
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> None:
    """
    Initialise the database — creates tables and indexes if they don't exist.

    Idempotent: safe to call on an already-initialised DB.
    Parent directory of db_path is created automatically if missing.
    """
    db_path = Path(db_path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def log_token_usage(
    db_path:           str,
    model:             str,
    prompt_tokens:     int,
    completion_tokens: int,
    total_tokens:      int,
    response_ms:       float,
) -> int:
    """
    Insert a new token usage row and return its integer primary key.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.
    model : str
        Model name from the LLM request body (e.g. "llama-3.1-8b").
    prompt_tokens : int
        Number of input/prompt tokens for this call.
    completion_tokens : int
        Number of output/completion tokens for this call.
    total_tokens : int
        Sum of prompt + completion tokens.
    response_ms : float
        LM Studio round-trip time in milliseconds.

    Returns
    -------
    int
        The rowid of the newly inserted record.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO token_usage
               (timestamp, model, prompt_tokens, completion_tokens, total_tokens, response_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (timestamp, model, prompt_tokens, completion_tokens, total_tokens, response_ms),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]
    finally:
        conn.close()


def get_daily_summary(db_path: str, date: Optional[str] = None) -> list[dict]:
    """
    Aggregate token usage by model for a given UTC date.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.
    date : str or None
        ISO date string (YYYY-MM-DD). Defaults to today's UTC date.

    Returns
    -------
    list[dict]
        One dict per model, ordered by total_tokens descending. Keys:
        model, request_count, total_prompt_tokens, total_completion_tokens,
        total_tokens, avg_response_ms.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT
                    model,
                    COUNT(*)         AS request_count,
                    SUM(prompt_tokens)     AS total_prompt_tokens,
                    SUM(completion_tokens) AS total_completion_tokens,
                    SUM(total_tokens)      AS total_tokens,
                    AVG(response_ms)       AS avg_response_ms
               FROM token_usage
              WHERE timestamp LIKE ? || '%'
              GROUP BY model
              ORDER BY total_tokens DESC""",
            (date,),
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_hourly_summary(db_path: str, date: Optional[str] = None) -> list[dict]:
    """
    Aggregate token usage by (model, hour) for a given UTC date.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.
    date : str or None
        ISO date string (YYYY-MM-DD). Defaults to today's UTC date.

    Returns
    -------
    list[dict]
        One dict per (model, hour) pair, ordered by hour then total_tokens desc.
        Keys: model, hour_utc, request_count, total_prompt_tokens,
        total_completion_tokens, total_tokens.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT
                    model,
                    CAST(strftime('%H', timestamp) AS INTEGER) AS hour_utc,
                    COUNT(*)         AS request_count,
                    SUM(prompt_tokens)     AS total_prompt_tokens,
                    SUM(completion_tokens) AS total_completion_tokens,
                    SUM(total_tokens)      AS total_tokens
               FROM token_usage
              WHERE timestamp LIKE ? || '%'
              GROUP BY model, hour_utc
              ORDER BY hour_utc, total_tokens DESC""",
            (date,),
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()