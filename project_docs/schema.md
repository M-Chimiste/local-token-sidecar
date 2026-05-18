# Token Sidecar — Database Schema

**Default local location:** `~/.token_sidecar/tokens.db`

---

## Overview

SQLite stores the local hot-path outbox/cache. When central Postgres sync is disabled, it behaves like the historical local reporting database. When central sync is enabled, rows are uploaded to Postgres in the background and deleted locally after acknowledgement.

Postgres stores the centralized reporting copy across machines. Timestamps are stored and queried in UTC.

---

## Table: `token_usage`

Primary table. One row is inserted for every `/v1/chat/completions` or `/v1/completions` request that passes through the sidecar.

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| `id` | INTEGER | No | Local auto-increment primary key |
| `event_id` | TEXT | Yes | Globally unique event id used for idempotent Postgres uploads |
| `timestamp` | TEXT | No | ISO 8601 UTC datetime, e.g. `2026-05-17T14:32:01.123456` |
| `node_id` | TEXT | Yes | Stable machine name, e.g. `athena` or `metis` |
| `model` | TEXT | No | Model name as sent in the request body — stored as-is, not normalised |
| `prompt_tokens` | INTEGER | No | Input token count from LM Studio's `usage.prompt_tokens` (default 0) |
| `completion_tokens` | INTEGER | No | Output token count from LM Studio's `usage.completion_tokens` (default 0) |
| `total_tokens` | INTEGER | No | Sum of prompt + completion tokens |
| `response_ms` | REAL | No | Elapsed milliseconds for the upstream call |
| `endpoint` | TEXT | Yes | Proxied endpoint that produced the usage object |
| `status_code` | INTEGER | Yes | Upstream HTTP status code |
| `sync_attempts` | INTEGER | No | Failed central upload attempts for this local row |
| `last_attempt_at` | TEXT | Yes | UTC timestamp of the last failed central upload attempt |
| `last_sync_error` | TEXT | Yes | Last central upload error, truncated for local diagnostics |

### Indexes

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_token_usage_event_id
    ON token_usage(event_id)
    WHERE event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_token_usage_model     ON token_usage(model);
CREATE INDEX IF NOT EXISTS idx_token_usage_node      ON token_usage(node_id);
```

These indexes exist to speed up the daily and hourly summary queries.

Existing SQLite databases are migrated in place on startup. Rows from older
schema versions receive generated `event_id` values, the configured `node_id`,
`/v1/unknown` as endpoint, and `200` as status code.

---

## Central Postgres Table

Postgres uses the same logical fact table, keyed by `event_id` for idempotent
batch uploads:

| Column | Type | Description |
|--------|------|-------------|
| `event_id` | TEXT PRIMARY KEY | Unique event id generated on the sidecar |
| `timestamp` | TIMESTAMPTZ | UTC event timestamp |
| `node_id` | TEXT | Stable machine name |
| `model` | TEXT | Request model name as sent by the client |
| `prompt_tokens` | INTEGER | Prompt token count |
| `completion_tokens` | INTEGER | Completion token count |
| `total_tokens` | INTEGER | Total token count |
| `response_ms` | DOUBLE PRECISION | Upstream response time in ms |
| `endpoint` | TEXT | Proxied endpoint |
| `status_code` | INTEGER | Upstream HTTP status code |
| `ingested_at` | TIMESTAMPTZ | Postgres insertion timestamp |

Dashboard views created by `scripts/init_postgres.py`:

- `token_usage_daily`
- `token_usage_hourly`
- `token_usage_by_model`
- `token_usage_by_node`

---

## Direct SQL Examples

All examples assume `~/.token_sidecar/tokens.db` — adjust the path as needed for your setup.

### Today's total tokens across all models

```sql
SELECT SUM(total_tokens) AS total
FROM token_usage
WHERE date(timestamp) = date('now', 'utc');
```

### Top 10 busiest days by request count

```sql
SELECT
    date(timestamp)        AS day,
    COUNT(*)               AS requests,
    SUM(total_tokens)      AS tokens_total
FROM token_usage
GROUP BY day
ORDER BY requests DESC
LIMIT 10;
```

### Per-model all-time totals (sorted heaviest first)

```sql
SELECT
    model,
    COUNT(*)                   AS calls,
    SUM(prompt_tokens)         AS prompt_total,
    SUM(completion_tokens)     AS completion_total,
    SUM(total_tokens)          AS tokens_total
FROM token_usage
GROUP BY model
ORDER BY tokens_total DESC;
```

### Average response time per model (all-time)

```sql
SELECT
    model,
    COUNT(*)           AS calls,
    AVG(response_ms)   AS avg_response_ms,
    MIN(response_ms)   AS min_response_ms,
    MAX(response_ms)   AS max_response_ms
FROM token_usage
GROUP BY model
ORDER BY avg_response_ms DESC;
```

### Hourly breakdown for a specific date (UTC)

```sql
SELECT
    strftime('%H', timestamp)  AS hour_utc,
    model,
    COUNT(*)                   AS calls,
    SUM(total_tokens)          AS tokens_total
FROM token_usage
WHERE timestamp LIKE '2026-05-17%'
GROUP BY hour_utc, model
ORDER BY hour_utc, tokens_total DESC;
```

### Central Postgres daily totals by node

```sql
SELECT *
FROM token_usage_daily
WHERE date_utc = DATE '2026-05-17'
  AND node_id = 'athena'
ORDER BY total_tokens DESC;
```

---

## Notes

- **Timestamps are UTC.** All SQLite and Postgres query helpers operate in UTC.
- **`model` is not validated or normalised** — whatever string the client sends as the `model` field in the request body is stored verbatim. Different clients may use different names for the same model.
- **`response_ms` may be 0** if the upstream call fails before timing completes (e.g., immediate 502 Bad Gateway).
- **Schema migrations:** The sidecar uses additive SQLite migrations, so running it against an existing local database is safe. New columns and indexes are added automatically on startup.
- **Central sync:** Postgres is never contacted in the request path. Background sync inserts with `ON CONFLICT DO NOTHING`, then deletes acknowledged local rows.
- **Database directory:** Install tooling creates `~/.token_sidecar/` with mode `0o700`.

---

## Schema Diagram

```
┌─────────────────── token_usage ────────────────────┐
│                                                        │
│  id                INTEGER PRIMARY KEY AUTOINCREMENT   │
│  event_id          TEXT                                │
│  timestamp         TEXT    NOT NULL                   │
│  node_id           TEXT                                │
│  model             TEXT    NOT NULL                   │
│  prompt_tokens     INTEGER NOT NULL DEFAULT 0          │
│  completion_tokens INTEGER NOT NULL DEFAULT 0          │
│  total_tokens      INTEGER NOT NULL DEFAULT 0          │
│  response_ms       REAL    NOT NULL                   │
│  endpoint/status + sync retry metadata                 │
│                                                        │
└───────────────────────────────────────────────────────┘
         │                                           ▲
         │ idx_token_usage_event_id (event_id)       │
         │ idx_token_usage_timestamp/model/node      │
         ▼                                           │
  Speed up daily / hourly aggregation queries        │
```

---

*Last updated: 2026-05-18*
