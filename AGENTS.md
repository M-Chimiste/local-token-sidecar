# AGENTS.md

This file is the operating guide for coding agents working in this repository.
It is agent-neutral and should stay aligned with the repo's actual code,
`README.md`, and `project_status.md`.

## Project Summary

`token-sidecar` is a lightweight Python 3.11+ HTTP proxy for local LM Studio
usage tracking on macOS.

The sidecar listens on `localhost:1240`, forwards OpenAI-compatible requests to
LM Studio at `http://localhost:1234`, extracts token usage from upstream
responses, writes one local SQLite outbox row per request, and returns the
upstream response to the caller unchanged.

The service is intentionally small. SQLite remains the local hot-path cache, and
optional central Postgres sync can upload queued rows for cross-machine
reporting.

## Architecture At A Glance

Request path (must stay fast and side-effect-light):

```
client → sidecar.forward_and_intercept → upstream LM Studio → response to client
                                       ↓
                                 db.log_token_usage (SQLite outbox)
```

Background path (only when `database.central.enabled: true`, started in
`sidecar.create_app` via `cleanup_ctx`):

```
central_sync._central_sync_loop
  → central_sync.flush_once
  → db.get_unsynced_token_usage → postgres_store.insert_token_usage_batch
  → db.mark_token_usage_synced          (delete acknowledged rows)
    | db.mark_token_usage_sync_failed   (record error + backoff)
```

`postgres_store` and `central_sync` are imported only by the background loop
and the query CLI. They must never appear in the request handlers.

## Source Of Truth

Read these first when changing behavior:

1. `README.md` for current user-facing setup, CLI, configuration, and
   troubleshooting.
2. `project_status.md` for completed phases, historical decisions, and known
   verification state.
3. `project_docs/requirements.md` for the original product intent.
4. `project_docs/schema.md` for the SQLite schema and query contract.

If the docs disagree with live code, inspect the tests and implementation before
editing. Update docs when a behavior change intentionally changes user-facing
commands, ports, schema, or launchd behavior.

## Repository Shape

- `sidecar.py` builds the aiohttp app, forwards `/v1/chat/completions` and
  `/v1/completions`, handles `/health`, logs token usage, and returns JSON 404s.
- `dashboard.py` is a separate aiohttp service for the postgres box. It is
  read-only: serves the React dashboard UI from `dashboard_static/` and JSON
  aggregates from the central Postgres via psycopg. It does NOT touch SQLite,
  does NOT write to Postgres, and is independent of the sidecar process.
- `dashboard_static/` holds the dashboard's HTML/CSS/JSX assets plus vendored
  React + Babel-standalone (no CDN dependency at runtime).
- `db.py` owns SQLite schema creation, inserts, and daily/hourly summary
  queries, plus the local outbox/cache used by central sync.
- `postgres_store.py` owns central Postgres schema, batch inserts, and central
  summary queries.
- `central_sync.py` uploads queued SQLite outbox rows to Postgres and deletes
  local rows only after acknowledgement.
- `config_loader.py` loads `config.yaml`, validates required keys, and exposes
  the immutable `Config` (+ `DashboardConfig`) dataclasses. Resolution order:
  `--config` CLI arg → `TOKEN_SIDECAR_CONFIG` env var → `<repo>/config.yaml`.
- `setup_launchd.py` installs, unloads, removes, and checks the macOS
  LaunchAgents `com.athena.token-sidecar` (default) and
  `com.athena.token-sidecar-dashboard` (via `--service dashboard`).
- `queries/summary.py` is the Click CLI for daily, hourly, and all-time
  by-model summaries.
- `tests/` contains unit and integration tests for DB, proxy, launchd, query
  CLI, dashboard API, and end-to-end behavior.
- `config.yaml` is the default runtime config. Treat committed defaults as
  documentation of the local development setup.

## Core Behavioral Contracts

- Preserve OpenAI-compatible request bodies and upstream response bodies as much
  as possible. The proxy is an observer, not a mutator.
- Only log usage when the upstream response is JSON and contains a dict-shaped
  `usage` object.
- Store timestamps in UTC ISO 8601 format (SQLite `TEXT`, Postgres `TIMESTAMPTZ`).
- Keep SQLite as the hot-path persistence layer. Do not put Postgres calls in
  the request path.
- When central sync is enabled, delete local rows only after Postgres
  acknowledges the batch. SQLite is the outbox of record until then.
- Keep schema migrations additive (`db._migrate_schema` +
  `_backfill_outbox_fields`). Existing local DBs are migrated in place on
  startup.
- Keep `config.yaml` as the main configuration surface; support `--config` where
  existing CLIs already do.
- Keep Postgres DSNs in environment variables, not committed config
  (`TOKEN_SIDECAR_POSTGRES_DSN` for the writer, `TOKEN_SIDECAR_QUERY_DSN` for
  read).
- Keep launchd support macOS user-scoped. Do not make LaunchAgent commands run
  as root.
- Treat `localhost:1240` as the sidecar default and `http://localhost:1234` as
  the LM Studio default unless a task explicitly changes ports.
- `/health` should remain cheap and independent of LM Studio availability — it
  is the launchd respawn probe.
- Do not rename the plist labels (`com.athena.token-sidecar`,
  `com.athena.token-sidecar-dashboard`), CLI commands, config keys, or env
  var names without an explicit ask — they are baked into installed user
  environments.
- The dashboard is strictly read-only. Adding write paths against the
  central Postgres from `dashboard.py` is out of scope; if a writer is
  needed, it belongs in `postgres_store.py` / `central_sync.py` and runs
  in the sidecar's background loop, not in the dashboard service.

## Development Commands

Set up dependencies:

```bash
uv sync
```

Run the sidecar manually:

```bash
uv run python sidecar.py
```

Run with a custom config:

```bash
uv run python sidecar.py --config /path/to/config.yaml
```

Run the full test suite:

```bash
uv run python -m pytest tests/ -v
```

Run targeted tests:

```bash
uv run python -m pytest tests/test_db.py -v
uv run python -m pytest tests/test_proxy.py -v
uv run python -m pytest tests/test_queries.py -v
uv run python -m pytest tests/test_launchd.py -v
uv run python -m pytest tests/test_central_sync.py -v
uv run python -m pytest tests/test_bootstrap_postgres.py -v
uv run python -m pytest tests/test_postgres_integration.py -v
uv run python -m pytest tests/test_integration.py -v
```

Query collected usage:

```bash
uv run python -m queries.summary daily
uv run python -m queries.summary hourly --date YYYY-MM-DD
uv run python -m queries.summary by-model
uv run python -m queries.summary daily --backend postgres --node athena
```

Manage the LaunchAgent:

```bash
uv run python setup_launchd.py install
uv run python setup_launchd.py status
uv run python setup_launchd.py unload
uv run python setup_launchd.py remove
```

Run / manage the dashboard (postgres box only):

```bash
export TOKEN_SIDECAR_QUERY_DSN='postgresql://token_sidecar_reader:...@host:5432/token_sidecar'
uv run python dashboard.py                                # binds 0.0.0.0:8080
uv run python setup_launchd.py install --service dashboard
uv run python setup_launchd.py status  --service dashboard
```

## Testing Expectations

For docs-only changes, a readback or diff check is enough.

For Python behavior changes, run the narrowest relevant tests first, then the
full suite when the change touches shared behavior. `test_integration.py` and
`test_postgres_integration.py` may depend on local LM Studio state or a live
Postgres instance and can take longer; if they cannot run in the current
environment, say so in the handoff rather than skipping silently.

For launchd changes, prefer unit tests that mock `launchctl` and path behavior.
Only run live `launchctl` commands when the task specifically calls for local
LaunchAgent validation.

For proxy changes, verify both halves of the contract: token rows are written
correctly and callers still receive the expected upstream status/body.

For central-sync changes, verify both halves of the outbox contract: rows are
only deleted locally after Postgres acknowledgement, and failures bump
`sync_attempts` / `last_sync_error` without dropping rows.

## Implementation Guidance

- Keep the codebase small and direct. Prefer existing modules over new
  abstractions unless a change clearly needs a new boundary.
- Use typed dataclasses and plain standard-library types where the repo already
  does.
- Use structured parsers for YAML, JSON, plist, and SQLite instead of string
  manipulation.
- Preserve current CLI names and output formats unless the user asks for a
  breaking change.
- Keep database schema changes explicit and covered by tests. If a schema change
  is needed, update `project_docs/schema.md` and affected query CLI behavior.
- Avoid broad dependency additions. The current runtime stack is `aiohttp`,
  `click`, `httpx`, `psycopg[binary]`, `pyyaml`, and `tabulate` (plus
  `pytest`, `pytest-aiohttp`, `pytest-asyncio` for tests).
- Keep user-facing errors clear and local-actionable, especially for missing
  config, LM Studio downtime, and launchd state.

## Safety Notes

- Never discard user changes in this repository.
- Be careful with commands that touch `~/Library/LaunchAgents` or
  `~/.token_sidecar`; these affect the user's local machine, not just the repo.
- Do not delete or rewrite the user's SQLite database unless explicitly asked.
- Do not commit secrets, local model lists, private paths beyond documented
  defaults, or generated logs.
- Avoid starting a long-running sidecar or live LaunchAgent unless the user
  requested runtime validation.

## Useful Reading Order

1. `README.md`
2. `project_status.md`
3. `config.yaml`
4. `sidecar.py`
5. `db.py`
6. `queries/summary.py`
7. `setup_launchd.py`
8. Relevant tests in `tests/`
