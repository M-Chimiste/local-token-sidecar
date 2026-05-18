# Phase 1 — Database Layer: Detailed Implementation Plan

**Project:** token-sidecar  
**Phase:** 1 of 7  
**Parent plan:** `../implementation_plan.md`  
**Goal:** SQLite schema, read/write helpers, and unit tests for the token usage database.

---

## Context

The sidecar proxy (Phase 2+) will write every LLM API response's token usage to a SQLite database. This phase establishes the database layer only — no network calls, no proxy logic yet.

**Key design decisions:**
- No ORM or migration framework — raw `sqlite3` stdlib + hand-written SQL
- Schema created with `CREATE TABLE IF NOT EXISTS` (idempotent, no destructive migrations)
- All timestamps stored as UTC ISO 8601 strings (`YYYY-MM-DDTHH:MM:SS`)
- In-memory SQLite (`:memory:`) for all tests — fast and isolated

---

## Step 1 — Create the Database Module (`db.py`)

Create `~/Documents/hermes_projects/token_sidecar/db.py`.

### Imports and Constants

```python
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
```

### Schema SQL

Define these as module-level strings for use in both init and tests:

**`token_usage` table:**
```sql
CREATE TABLE IF NOT EXISTS token_usage (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,          -- ISO 8601 UTC
    model            TEXT    NOT NULL,          -- model name from request body
    prompt_tokens    INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens     INTEGER NOT NULL DEFAULT 0,
    response_ms      REAL    NOT NULL           -- LM Studio round-trip time in ms
);
```

**Index for time-range queries:**
```sql
CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_token_usage_model     ON token_usage(model);
```

### Function: `init_db(db_path: str) -> None`

- Expand `~` in `db_path` using `Path.expanduser()`
- Create parent directory (`~/.token_sidecar/`) if it doesn't exist
- Connect and execute the schema SQL (tables + indexes)
- Close connection

```python
def init_db(db_path: str) -> None:
    db_path = Path(db_path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
```

---

## Step 2 — Function: `log_token_usage(...)`

**Signature:**
```python
def log_token_usage(
    db_path:       str,
    model:         str,
    prompt_tokens:     int,
    completion_tokens: int,
    total_tokens:      int,
    response_ms:   float,
) -> int:
```

- Connect to `db_path`, INSERT a new row into `token_usage`
- `timestamp` = current UTC time as ISO 8601 string (`datetime.now(timezone.utc).isoformat()`)
- Return the `INTEGER PRIMARY KEY` of the inserted row (new row ID)

```python
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
        return cur.lastrowid
    finally:
        conn.close()
```

---

## Step 3 — Function: `get_daily_summary(...)`

**Signature:**
```python
def get_daily_summary(db_path: str, date: Optional[str] = None) -> list[dict]:
```

- If `date` is `None`, use today's UTC date (`datetime.now(timezone.utc).strftime("%Y-%m-%d")`)
- Query pattern:
  ```sql
  SELECT
      model,
      COUNT(*)        AS request_count,
      SUM(prompt_tokens)     AS total_prompt_tokens,
      SUM(completion_tokens) AS total_completion_tokens,
      SUM(total_tokens)      AS total_tokens,
      AVG(response_ms)       AS avg_response_ms
  FROM token_usage
  WHERE timestamp LIKE '2026-05-17%'   -- date prefix match on ISO string works for UTC dates
  GROUP BY model
  ORDER BY total_tokens DESC;
  ```
- Return a list of dicts: one per model, keys = column names

---

## Step 4 — Function: `get_hourly_summary(...)`

**Signature:**
```python
def get_hourly_summary(db_path: str, date: Optional[str] = None) -> list[dict]:
```

- If `date` is `None`, use today's UTC date
- Extracts hour from the timestamp string for grouping:
  ```sql
  SELECT
      model,
      CAST(strftime('%H', timestamp) AS INTEGER) AS hour_utc,
      COUNT(*)        AS request_count,
      SUM(prompt_tokens)     AS total_prompt_tokens,
      SUM(completion_tokens) AS total_completion_tokens,
      SUM(total_tokens)      AS total_tokens
  FROM token_usage
  WHERE timestamp LIKE '2026-05-17%'
  GROUP BY model, hour_utc
  ORDER BY hour_utc, total_tokens DESC;
  ```
- Return a list of dicts: one per (model, hour) pair

---

## Step 5 — Helper: `close_db` (Optional)

Not strictly needed since each function opens/closes its own connection. Skip unless a future need for connection pooling emerges.

---

## Step 6 — Write Unit Tests (`tests/test_db.py`)

Replace the placeholder with full test coverage using `pytest` and an in-memory database via a shared pytest fixture.

### Fixtures

```python
import pytest, db as _db, tempfile, pathlib

@pytest.fixture
def mem_db(tmp_path):
    """Provide a path to a temporary SQLite DB backed by :memory: (via URI)."""
    # Use file-backed temp DB so init_db logic runs; cleaned up automatically
    db_path = str(tmp_path / "test.db")
    _db.init_db(db_path)
    return db_path

@pytest.fixture
def populated_db(mem_db):
    """A database with 3 sample rows across two models."""
    import datetime as dt
    for row in SAMPLE_ROWS:
        _db.log_token_usage(mem_db, **row)
    return mem_db
```

### Sample Data Constant

```python
from datetime import datetime, timezone, timedelta

BASE = datetime(2026, 5, 17, 10, 0, 0, tzinfo=timezone.utc)

SAMPLE_ROWS = [
    dict(model="llama-3.1-8b",   prompt_tokens=120, completion_tokens=80,
         total_tokens=200,        response_ms=450.2),
    dict(model="llama-3.1-8b",   prompt_tokens=95,  completion_tokens=65,
         total_tokens=160,        response_ms=380.5),
    dict(model="mixtral-8x7b",   prompt_tokens=200, completion_tokens=150,
         total_tokens=350,        response_ms=920.1),
]
```

### Tests

| Test | What it verifies |
|------|------------------|
| `test_init_db_creates_tables` | After calling `init_db`, both tables exist and have the right columns |
| `test_init_db_is_idempotent` | Calling `init_db` twice does not raise an error |
| `test_log_token_usage_returns_row_id` | Returns a positive integer (the inserted row ID) |
| `test_log_token_usage_roundtrip` | Inserted values are retrievable by querying back the last row ID |
| `test_get_daily_summary_empty` | Empty DB returns `[]` for any date |
| `test_get_daily_summary_one_model` | Correct aggregation on a single model across multiple rows |
| `test_get_daily_summary_multiple_models` | Each model gets its own aggregated row; totals are correct |
| `test_get_hourly_summary_breakdown` | Hour grouping correctly separates 10:00 from 11:00 data |
| `test_get_daily_summary_filters_by_date` | Query for wrong date returns empty list |

### Run Tests

```bash
cd ~/Documents/hermes_projects/token_sidecar
source .venv/bin/activate
python -m pytest tests/test_db.py -v --tb=short
```

Expected: **9 passed** (all green).

---

## Step 7 — Verify Schema with Real DB File

Manual smoke test against a real file path:

```bash
cd ~/Documents/hermes_projects/token_sidecar
source .venv/bin/activate

python3 - <<'EOF'
from db import init_db, log_token_usage, get_daily_summary
import tempfile, pathlib

db = str(tempfile.mktemp(suffix=".db"))
init_db(db)

# Insert a row
rid = log_token_usage(db, model="test-model", prompt_tokens=10,
                      completion_tokens=5, total_tokens=15, response_ms=100.0)
print(f"Inserted row ID: {rid}")

# Query it back
summary = get_daily_summary(db)  # today UTC
print(f"Daily summary: {summary}")

pathlib.Path(db).unlink()
print("Smoke test passed.")
EOF
```

---

## Step 8 — Commit

```bash
cd ~/Documents/hermes_projects/token_sidecar
git add db.py tests/test_db.py
git status   # review
git commit -m "Phase 1: SQLite database layer with token_usage schema and CRUD helpers"
```

---

## Exit Criteria Checklist

- [ ] `db.py` created with all four functions (`init_db`, `log_token_usage`, `get_daily_summary`, `get_hourly_summary`)
- [ ] Schema uses `CREATE TABLE IF NOT EXISTS` — no destructive migrations
- [ ] Timestamps stored as UTC ISO 8601 strings
- [ ] All tests pass: `python -m pytest tests/test_db.py -v --tb=short`
- [ ] Manual smoke test runs without errors (Step 7)
- [ ] Git committed with clean message "Phase 1: SQLite database layer..."
- [ ] `project_status.md` updated

---

## File Changes After Phase 1

```
token_sidecar/
├── db.py                ← NEW
├── tests/
│   └── test_db.py       ← REPLACED placeholder with full tests
```

All other files unchanged.

---

*Last updated: 2026-05-17*