"""
Token Counter Sidecar — SQLite Outbox Layer

SQLite schema, local outbox/cache helpers, and local summaries for token usage
tracking.
All timestamps stored as UTC ISO 8601 strings.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS token_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT,
    timestamp         TEXT    NOT NULL,
    node_id           TEXT,
    model             TEXT    NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    response_ms       REAL    NOT NULL,
    endpoint          TEXT,
    status_code       INTEGER,
    sync_attempts     INTEGER NOT NULL DEFAULT 0,
    last_attempt_at   TEXT,
    last_sync_error   TEXT
);
"""

_OUTBOX_COLUMNS: dict[str, str] = {
    "event_id": "TEXT",
    "node_id": "TEXT",
    "endpoint": "TEXT",
    "status_code": "INTEGER",
    "sync_attempts": "INTEGER NOT NULL DEFAULT 0",
    "last_attempt_at": "TEXT",
    "last_sync_error": "TEXT",
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(db_path: str, node_id: str | None = None) -> None:
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
        _migrate_schema(conn)
        _backfill_outbox_fields(conn, node_id or "local")
        conn.commit()
    finally:
        conn.close()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add outbox columns to databases created by older sidecar versions."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(token_usage)")
    existing = {row[1] for row in cur.fetchall()}
    for column, ddl in _OUTBOX_COLUMNS.items():
        if column not in existing:
            cur.execute(f"ALTER TABLE token_usage ADD COLUMN {column} {ddl}")

    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_token_usage_event_id
            ON token_usage(event_id)
            WHERE event_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);
        CREATE INDEX IF NOT EXISTS idx_token_usage_model     ON token_usage(model);
        CREATE INDEX IF NOT EXISTS idx_token_usage_node      ON token_usage(node_id);
        """
    )


def _backfill_outbox_fields(conn: sqlite3.Connection, node_id: str) -> None:
    """Populate central-sync fields for existing local rows."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM token_usage WHERE event_id IS NULL OR event_id = ''")
    for row_id, in cur.fetchall():
        cur.execute(
            "UPDATE token_usage SET event_id = ? WHERE id = ?",
            (uuid.uuid4().hex, row_id),
        )

    cur.execute(
        "UPDATE token_usage SET node_id = ? WHERE node_id IS NULL OR node_id = ''",
        (node_id,),
    )
    cur.execute(
        "UPDATE token_usage SET endpoint = ? WHERE endpoint IS NULL OR endpoint = ''",
        ("/v1/unknown",),
    )
    cur.execute(
        "UPDATE token_usage SET status_code = ? WHERE status_code IS NULL",
        (200,),
    )
    cur.execute(
        "UPDATE token_usage SET sync_attempts = 0 WHERE sync_attempts IS NULL"
    )


def log_token_usage(
    db_path:           str,
    model:             str,
    prompt_tokens:     int,
    completion_tokens: int,
    total_tokens:      int,
    response_ms:       float,
    *,
    node_id:            str = "local",
    endpoint:           str = "/v1/chat/completions",
    status_code:        int = 200,
    event_id:           str | None = None,
    timestamp:          str | None = None,
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
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    event_id = event_id or uuid.uuid4().hex
    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO token_usage
               (event_id, timestamp, node_id, model, prompt_tokens,
                completion_tokens, total_tokens, response_ms, endpoint,
                status_code)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                timestamp,
                node_id,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                response_ms,
                endpoint,
                status_code,
            ),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]
    finally:
        conn.close()


def get_daily_summary(
    db_path: str,
    date: Optional[str] = None,
    node_id: str | None = None,
) -> list[dict]:
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
        where = ["timestamp LIKE ? || '%'"]
        params: list[object] = [date]
        if node_id:
            where.append("node_id = ?")
            params.append(node_id)
        cur.execute(
            f"""SELECT
                    model,
                    COUNT(*)         AS request_count,
                    SUM(prompt_tokens)     AS total_prompt_tokens,
                    SUM(completion_tokens) AS total_completion_tokens,
                    SUM(total_tokens)      AS total_tokens,
                    AVG(response_ms)       AS avg_response_ms
               FROM token_usage
              WHERE {' AND '.join(where)}
              GROUP BY model
              ORDER BY total_tokens DESC""",
            params,
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_hourly_summary(
    db_path: str,
    date: Optional[str] = None,
    node_id: str | None = None,
) -> list[dict]:
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
        where = ["timestamp LIKE ? || '%'"]
        params: list[object] = [date]
        if node_id:
            where.append("node_id = ?")
            params.append(node_id)
        cur.execute(
            f"""SELECT
                    model,
                    CAST(strftime('%H', timestamp) AS INTEGER) AS hour_utc,
                    COUNT(*)         AS request_count,
                    SUM(prompt_tokens)     AS total_prompt_tokens,
                    SUM(completion_tokens) AS total_completion_tokens,
                    SUM(total_tokens)      AS total_tokens
               FROM token_usage
              WHERE {' AND '.join(where)}
              GROUP BY model, hour_utc
              ORDER BY hour_utc, total_tokens DESC""",
            params,
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_by_model_summary(db_path: str, node_id: str | None = None) -> list[dict]:
    """Aggregate all local token usage rows by model."""
    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    try:
        cur = conn.cursor()
        where = []
        params: list[object] = []
        if node_id:
            where.append("node_id = ?")
            params.append(node_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        cur.execute(
            f"""SELECT
                    model,
                    COUNT(*)         AS request_count,
                    SUM(prompt_tokens)     AS total_prompt_tokens,
                    SUM(completion_tokens) AS total_completion_tokens,
                    SUM(total_tokens)      AS total_tokens
                 FROM token_usage
                 {where_sql}
             GROUP BY model
             ORDER BY total_tokens DESC""",
            params,
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_unsynced_token_usage(
    db_path: str,
    limit: int,
    node_id: str,
) -> list[dict]:
    """Return queued local usage rows ready to upload to central Postgres."""
    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    conn.row_factory = sqlite3.Row
    try:
        _backfill_outbox_fields(conn, node_id)
        conn.commit()
        cur = conn.cursor()
        cur.execute(
            """SELECT
                    event_id, timestamp, node_id, model, prompt_tokens,
                    completion_tokens, total_tokens, response_ms, endpoint,
                    status_code
               FROM token_usage
              WHERE event_id IS NOT NULL
              ORDER BY id
              LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def mark_token_usage_synced(db_path: str, event_ids: Sequence[str]) -> int:
    """Delete locally queued rows that have been acknowledged by Postgres."""
    ids = [event_id for event_id in event_ids if event_id]
    if not ids:
        return 0

    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    try:
        placeholders = ",".join("?" for _ in ids)
        cur = conn.cursor()
        cur.execute(
            f"DELETE FROM token_usage WHERE event_id IN ({placeholders})",
            ids,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_token_usage_sync_failed(
    db_path: str,
    event_ids: Sequence[str],
    error: str,
) -> int:
    """Record a failed central-upload attempt without removing queued rows."""
    ids = [event_id for event_id in event_ids if event_id]
    if not ids:
        return 0

    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    try:
        placeholders = ",".join("?" for _ in ids)
        cur = conn.cursor()
        cur.execute(
            f"""UPDATE token_usage
                   SET sync_attempts = COALESCE(sync_attempts, 0) + 1,
                       last_attempt_at = ?,
                       last_sync_error = ?
                 WHERE event_id IN ({placeholders})""",
            [datetime.now(timezone.utc).isoformat(), error[:500], *ids],
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
