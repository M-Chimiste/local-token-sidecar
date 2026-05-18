"""
Postgres storage helpers for centralized token-sidecar reporting.

The sidecar request path never imports or calls this module directly. Background
sync and query commands use it to insert queued SQLite outbox rows and read
central summaries.
"""

from __future__ import annotations

from typing import Iterable

try:
    import psycopg
except ImportError:  # pragma: no cover - exercised only in un-synced envs
    psycopg = None  # type: ignore[assignment]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS token_usage (
    event_id          TEXT PRIMARY KEY,
    timestamp         TIMESTAMPTZ NOT NULL,
    node_id           TEXT        NOT NULL,
    model             TEXT        NOT NULL,
    prompt_tokens     INTEGER     NOT NULL DEFAULT 0,
    completion_tokens INTEGER     NOT NULL DEFAULT 0,
    total_tokens      INTEGER     NOT NULL DEFAULT 0,
    response_ms       DOUBLE PRECISION NOT NULL,
    endpoint          TEXT        NOT NULL DEFAULT '/v1/unknown',
    status_code       INTEGER     NOT NULL DEFAULT 200,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp
    ON token_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_token_usage_node_timestamp
    ON token_usage(node_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_token_usage_model_timestamp
    ON token_usage(model, timestamp);

CREATE OR REPLACE VIEW token_usage_daily AS
SELECT
    (timestamp AT TIME ZONE 'UTC')::date AS date_utc,
    node_id,
    model,
    COUNT(*) AS request_count,
    SUM(prompt_tokens) AS total_prompt_tokens,
    SUM(completion_tokens) AS total_completion_tokens,
    SUM(total_tokens) AS total_tokens,
    AVG(response_ms) AS avg_response_ms
FROM token_usage
GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW token_usage_hourly AS
SELECT
    (timestamp AT TIME ZONE 'UTC')::date AS date_utc,
    EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::integer AS hour_utc,
    node_id,
    model,
    COUNT(*) AS request_count,
    SUM(prompt_tokens) AS total_prompt_tokens,
    SUM(completion_tokens) AS total_completion_tokens,
    SUM(total_tokens) AS total_tokens,
    AVG(response_ms) AS avg_response_ms
FROM token_usage
GROUP BY 1, 2, 3, 4;

CREATE OR REPLACE VIEW token_usage_by_model AS
SELECT
    model,
    COUNT(*) AS request_count,
    SUM(prompt_tokens) AS total_prompt_tokens,
    SUM(completion_tokens) AS total_completion_tokens,
    SUM(total_tokens) AS total_tokens,
    AVG(response_ms) AS avg_response_ms
FROM token_usage
GROUP BY model;

CREATE OR REPLACE VIEW token_usage_by_node AS
SELECT
    node_id,
    COUNT(*) AS request_count,
    SUM(prompt_tokens) AS total_prompt_tokens,
    SUM(completion_tokens) AS total_completion_tokens,
    SUM(total_tokens) AS total_tokens,
    AVG(response_ms) AS avg_response_ms
FROM token_usage
GROUP BY node_id;
"""


def _connect(dsn: str):
    if psycopg is None:
        raise RuntimeError(
            "psycopg is not installed. Run `uv sync` after updating dependencies."
        )
    return psycopg.connect(dsn)


def init_schema(dsn: str) -> None:
    """Create the central reporting table, indexes, and dashboard views."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            for statement in SCHEMA_SQL.split(";"):
                if statement.strip():
                    cur.execute(statement)


def insert_token_usage_batch(dsn: str, rows: Iterable[dict]) -> list[str]:
    """
    Insert a batch of queued local rows into Postgres.

    Returns every attempted event_id if the transaction commits. `ON CONFLICT`
    makes duplicate uploads idempotent, so conflicts are acknowledged too.
    """
    batch = list(rows)
    if not batch:
        return []

    sql = """
        INSERT INTO token_usage (
            event_id, timestamp, node_id, model, prompt_tokens,
            completion_tokens, total_tokens, response_ms, endpoint, status_code
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (event_id) DO NOTHING
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            for row in batch:
                cur.execute(
                    sql,
                    (
                        row["event_id"],
                        row["timestamp"],
                        row["node_id"],
                        row["model"],
                        row["prompt_tokens"],
                        row["completion_tokens"],
                        row["total_tokens"],
                        row["response_ms"],
                        row.get("endpoint") or "/v1/unknown",
                        row.get("status_code") or 200,
                    ),
                )

    return [str(row["event_id"]) for row in batch]


def get_daily_summary(
    dsn: str,
    date: str,
    node_id: str | None = None,
) -> list[dict]:
    """Aggregate central token usage by model for a UTC date."""
    where = ["(timestamp AT TIME ZONE 'UTC')::date = %s::date"]
    params: list[object] = [date]
    if node_id:
        where.append("node_id = %s")
        params.append(node_id)
    return _fetch_all(
        dsn,
        f"""SELECT
                model,
                COUNT(*) AS request_count,
                SUM(prompt_tokens) AS total_prompt_tokens,
                SUM(completion_tokens) AS total_completion_tokens,
                SUM(total_tokens) AS total_tokens,
                AVG(response_ms) AS avg_response_ms
            FROM token_usage
            WHERE {' AND '.join(where)}
            GROUP BY model
            ORDER BY total_tokens DESC""",
        params,
    )


def get_hourly_summary(
    dsn: str,
    date: str,
    node_id: str | None = None,
) -> list[dict]:
    """Aggregate central token usage by model and UTC hour for a date."""
    where = ["(timestamp AT TIME ZONE 'UTC')::date = %s::date"]
    params: list[object] = [date]
    if node_id:
        where.append("node_id = %s")
        params.append(node_id)
    return _fetch_all(
        dsn,
        f"""SELECT
                model,
                EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::integer AS hour_utc,
                COUNT(*) AS request_count,
                SUM(prompt_tokens) AS total_prompt_tokens,
                SUM(completion_tokens) AS total_completion_tokens,
                SUM(total_tokens) AS total_tokens
            FROM token_usage
            WHERE {' AND '.join(where)}
            GROUP BY model, hour_utc
            ORDER BY hour_utc, total_tokens DESC""",
        params,
    )


def get_by_model_summary(dsn: str, node_id: str | None = None) -> list[dict]:
    """Aggregate central token usage by model across all time."""
    where = []
    params: list[object] = []
    if node_id:
        where.append("node_id = %s")
        params.append(node_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    return _fetch_all(
        dsn,
        f"""SELECT
                model,
                COUNT(*) AS request_count,
                SUM(prompt_tokens) AS total_prompt_tokens,
                SUM(completion_tokens) AS total_completion_tokens,
                SUM(total_tokens) AS total_tokens
            FROM token_usage
            {where_sql}
            GROUP BY model
            ORDER BY total_tokens DESC""",
        params,
    )


def _fetch_all(dsn: str, sql: str, params: list[object]) -> list[dict]:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [desc[0] for desc in cur.description or []]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
