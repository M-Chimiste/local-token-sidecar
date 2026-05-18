# Token Sidecar — Database Schema

**Default location:** `~/.token_sidecar/tokens.db`

---

## Overview

SQLite database storing one row per LLM API call, capturing token usage extracted from LM Studio's OpenAI-compatible responses. Timestamps are stored in UTC.

---

## Table: `token_usage`

Primary table. One row is inserted for every `/v1/chat/completions` or `/v1/completions` request that passes through the sidecar.

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| `id` | INTEGER | No | Auto-increment primary key |
| `timestamp` | TEXT | No | ISO 8601 UTC datetime, e.g. `2026-05-17T14:32:01.123456` |
| `model` | TEXT | No | Model name as sent in the request body — stored as-is, not normalised |
| `prompt_tokens` | INTEGER | No | Input token count from LM Studio's `usage.prompt_tokens` (default 0) |
| `completion_tokens` | INTEGER | No | Output token count from LM Studio's `usage.completion_tokens` (default 0) |
| `total_tokens` | INTEGER | No | Sum of prompt + completion tokens |
| `response_ms` | REAL | No | Elapsed milliseconds for the upstream call |

### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_token_usage_model     ON token_usage(model);
```

These indexes exist to speed up the daily and hourly summary queries.

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

---

## Notes

- **Timestamps are UTC.** All query helpers (`get_daily_summary`, `get_hourly_summary`) operate in UTC.
- **`model` is not validated or normalised** — whatever string the client sends as the `model` field in the request body is stored verbatim. Different clients may use different names for the same model.
- **`response_ms` may be 0** if the upstream call fails before timing completes (e.g., immediate 502 Bad Gateway).
- **Schema migrations:** The sidecar uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`, so running it against an existing database is safe. New indexes are added automatically on startup.
- **Database file permissions:** Created with `0o600` (user read/write only) under the `~/.token_sidecar/` directory which itself is created with `0o700`.

---

## Schema Diagram

```
┌─────────────────── token_usage ────────────────────┐
│                                                        │
│  id                INTEGER PRIMARY KEY AUTOINCREMENT   │
│  timestamp         TEXT    NOT NULL                   │
│  model             TEXT    NOT NULL                   │
│  prompt_tokens     INTEGER NOT NULL DEFAULT 0          │
│  completion_tokens INTEGER NOT NULL DEFAULT 0          │
│  total_tokens      INTEGER NOT NULL DEFAULT 0          │
│  response_ms       REAL    NOT NULL                   │
│                                                        │
└───────────────────────────────────────────────────────┘
         │                                           ▲
         │ idx_token_usage_timestamp (timestamp)     │
         │ idx_token_usage_model (model)             │
         ▼                                           │
  Speed up daily / hourly aggregation queries        │
```

---

*Last updated: 2026-05-17*