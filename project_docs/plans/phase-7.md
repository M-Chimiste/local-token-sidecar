# Phase 7 — Documentation: Detailed Implementation Plan

**Project:** token-sidecar
**Phase:** 7 of 7
**Parent plan:** `../implementation_plan.md`
**Goal:** Make the project fully reproducible and understandable for a future reader (including future self).

---

## Background

All six preceding phases are complete. The codebase has 64 passing tests, a live LaunchAgent, and working query CLI — but no user-facing documentation exists beyond inline code comments and plan documents internal to `project_docs/`. Phase 7 produces the README and finalises all artefacts.

**What already exists:**
- `db.py` — SQLite schema + CRUD (11 tests)
- `sidecar.py` — aiohttp HTTP proxy with `/health` endpoint (6 tests)
- `config_loader.py` — typed Config dataclass, `--config` override (5 tests)
- `setup_launchd.py` — LaunchAgent plist generator + CLI: install/unload/remove/status (17 tests)
- `queries/summary.py` — click CLI with `daily`, `hourly`, `by-model` subcommands (17 tests)
- `tests/test_integration.py` — 8 subprocess E2E integration tests (7 passed + 1 skipped)
- `scripts/stress_test.py` — manual load tester, gitignored
- `.gitignore`
- UV project (`pyproject.toml`), no `.venv` in git

**What needs to be created:**
- `README.md` — primary user-facing document
- Inline docstrings for all public functions without them (audit + fill gaps)
- `project_docs/schema.md` — standalone schema reference

---

## Step 1 — Audit Existing Docstrings

Scan every `.py` file in the project root and `queries/` for missing or inadequate docstrings on **public** functions. A function needs a docstring if it is:
- Imported elsewhere (i.e., not `_`-prefixed)
- Part of a public API (CLI commands, `init_db`, `log_token_usage`, etc.)

Target files to audit:

| File | Functions requiring review |
|------|---------------------------|
| `db.py` | `init_db`, `log_token_usage`, `get_daily_summary`, `get_hourly_summary` |
| `sidecar.py` | `create_app`, `handle_chat_completions`, `handle_completions`, `handle_health`, `main` |
| `config_loader.py` | `load_config`, `validate_config`, `parse_cli_args`, the `Config` dataclass itself |
| `setup_launchd.py` | `generate_plist_content`, `install`, `unload`, `remove`, `status`, CLI argument parsing block |
| `queries/summary.py` | All click commands (`daily`, `hourly`, `by_model`) and helpers |

For each function without a docstring, add one covering:
1. What it does (one line)
2. Arguments / parameters
3. Return value (if any)
4. Raises: any notable exceptions

*Exit criterion:* No public function in the five files above lacks a docstring.

---

## Step 2 — Write README.md

Create `README.md` at the project root (`~/Documents/hermes_projects/token_sidecar/README.md`). This is the primary onboarding document. Structure:

### Suggested sections (in order):

1. **Badge / title block** — project name, Python version, test count
2. **What this does** — one paragraph: HTTP proxy that intercepts LM Studio responses and logs token usage to SQLite; LaunchAgent auto-starts it on login
3. **Architecture diagram** — ASCII art:
   ```
   ┌──────────────┐     ┌─────────────────┐     ┌───────────────┐
   │  Client      │ ──▶ │  token-sidecar  │ ──▶ │ LM Studio     │
   │ (any app     │     │  :1240          │     │ localhost:1234│
   │ using the    │ ◀── │                 │ ◀── │               │
   │ sidecar)     │     └─────────────────┘     └───────────────┘
   └──────────────┘              │
                                 ▼
                        ┌─────────────────┐
                        │  SQLite         │
                        │  ~/.token_side  │
                        │  car/tokens.db  │
                        └─────────────────┘
   ```
4. **Prerequisites** — LM Studio running with API enabled (port 1234), Python 3.11+, `uv` installed, macOS (LaunchAgent is macOS-only)
5. **Quick start** — numbered steps:
   - Clone / cd into project
   - `uv sync`
   - Edit `config.yaml` if needed (defaults: sidecar `:1240`, upstream `:1234`)
   - Run manually: `uv run python sidecar.py`
6. **Configuration reference** — table of every config key with type, default, description:
   | Key | Type | Default | Description |
   |-----|------|---------|-------------|
   | `proxy.listen_host` | string | `"localhost"` | Interface to bind |
   | `proxy.listen_port` | int | `1240` | Sidecar listen port |
   | `proxy.upstream_url` | string | `"http://localhost:1234"` | LM Studio endpoint |
   | `database.path` | string/path | `"~/.token_sidecar/tokens.db"` | SQLite DB path |
   | `logging.level` | string | `"INFO"` | Log level (DEBUG/INFO/WARNING/ERROR) |
7. **Query CLI** — three commands with examples:
   ```bash
   # Today's summary
   uv run python -m queries.summary daily

   # Hourly breakdown for a date
   uv run python -m queries.summary hourly --date 2026-05-17

   # All-time totals by model (sorted desc by total_tokens)
   uv run python -m queries.summary by-model

   # JSON output (for scripting / piping)
   uv run python -m queries.summary daily --format json
   ```
8. **LaunchAgent setup** — two commands:
   ```bash
   # Install (writes plist + loads it)
   uv run python setup_launchd.py install

   # Check status
   uv run python setup_launchd.py status
   ```
   Include note about `RunAtLoad: true` and `KeepAlive` restart behavior.
9. **Troubleshooting** — cover at minimum:
   - *Sidecar returns 502*: LM Studio is not running or API port has changed → check `config.yaml upstream_url`
   - *No data in SQLite*: verify sidecar is listening (`curl http://localhost:1240/health`), check log file at `~/.token_sidecar/sidecar.log`
   - *launchctl status shows [loaded] but process not responding*: check `tail ~/.token_sidecar/sidecar.error.log` for import/startup errors; common cause is wrong python path in plist — re-run `install` to regenerate
   - *Port already in use*: another process is using 1240 → change `proxy.listen_port` in config.yaml and restart
10. **File inventory** — brief table of main files and their purpose (optional, can reference project_status.md)
11. **Development** — how to run tests:
    ```bash
    uv run python -m pytest tests/ -v
    ```

*Exit criterion:* README exists at `README.md`, covers all 11 sections above, and is syntactically valid markdown.

---

## Step 3 — Write project_docs/schema.md

Create a standalone schema reference document for future readers who want to query the DB directly. Structure:

```markdown
# Token Sidecar — Database Schema

## Overview

SQLite database storing one row per LLM API call, capturing token usage extracted from LM Studio's OpenAI-compatible responses.

**Default location:** `~/.token_sidecar/tokens.db`

---

## Table: token_usage

Primary table. One row per `/v1/chat/completions` or `/v1/completions` request.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | INTEGER | No | Auto-increment primary key |
| `timestamp` | TEXT | No | ISO 8601 UTC datetime (e.g. `2026-05-17T14:32:01.123456`) |
| `model` | TEXT | No | Model name as sent in the request body (`messages[0].content` or `prompt` field) |
| `prompt_tokens` | INTEGER | No | Input token count from LM Studio's `usage.prompt_tokens` |
| `completion_tokens` | INTEGER | No | Output token count from LM Studio's `usage.completion_tokens` |
| `total_tokens` | INTEGER | No | Sum of prompt + completion tokens |
| `response_ms` | REAL | Yes | Elapsed milliseconds for the upstream call (informational) |

### Index

```sql
CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_token_usage_model ON token_usage(model);
```

These indexes exist to speed up the daily and hourly summary queries.

---

## Direct SQL Examples

### Today's total tokens across all models

```sql
SELECT SUM(total_tokens) AS total
FROM token_usage
WHERE date(timestamp) = date('now', 'utc');
```

### Top 10 busiest days (by total request count)

```sql
SELECT date(timestamp) AS day, COUNT(*) AS requests, SUM(total_tokens) AS tokens
FROM token_usage
GROUP BY day
ORDER BY requests DESC
LIMIT 10;
```

### Per-model all-time totals

```sql
SELECT model,
       COUNT(*)           AS calls,
       SUM(prompt_tokens)     AS prompt_total,
       SUM(completion_tokens) AS completion_total,
       SUM(total_tokens)      AS tokens_total
FROM token_usage
GROUP BY model
ORDER BY tokens_total DESC;
```

---

## Notes

- Timestamps are stored in UTC. All query helpers (`get_daily_summary`, `get_hourly_summary`) operate in UTC.
- The `model` column stores whatever string the client sent as the `model` field in the request body — it is **not** validated or normalised by the sidecar.
- `response_ms` may be NULL if the upstream call failed before timing was recorded (e.g., immediate 502).
```

*Exit criterion:* `project_docs/schema.md` exists with all sections above.

---

## Step 4 — Final Review

Before committing, do a quick sanity check:

1. README opens in any markdown viewer and renders the ASCII diagram correctly
2. All internal links point to existing files (`config.yaml`, `setup_launchd.py`, etc.)
3. No placeholder text like "TODO" or "fill in" remains
4. The test suite still passes after any docstring-only changes:
   ```bash
   uv run python -m pytest tests/ -q
   ```

---

## Step 5 — Git Commit

```bash
git add README.md project_docs/schema.md
# also commit any updated docstrings from Step 1
git add db.py sidecar.py config_loader.py setup_launchd.py queries/summary.py
git commit -m "Phase 7: README, schema docs, and inline docstrings"
```

---

## Exit Criteria

| # | Criterion |
|---|-----------|
| 1 | `README.md` exists at project root with ≥10 of the 11 specified sections |
| 2 | `project_docs/schema.md` exists with table definition + SQL examples |
| 3 | Every public function in `db.py`, `sidecar.py`, `config_loader.py`, `setup_launchd.py`, and `queries/summary.py` has a docstring |
| 4 | Full test suite still passes: `uv run python -m pytest tests/ -q` → all pass |
| 5 | Git commit created with Phase 7 changes |

---

## Files Modified / Created

| File | Action |
|------|--------|
| `README.md` | **Created** — main user-facing document |
| `project_docs/schema.md` | **Created** — standalone DB schema reference |
| `db.py` | Updated — add missing docstrings |
| `sidecar.py` | Updated — add missing docstrings |
| `config_loader.py` | Updated — add missing docstrings |
| `setup_launchd.py` | Updated — add missing docstrings |
| `queries/summary.py` | Updated — add missing docstrings |

---

*Last updated: 2026-05-17*