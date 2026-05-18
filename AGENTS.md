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
- `db.py` owns SQLite schema creation, inserts, and daily/hourly summary
  queries, plus the local outbox/cache used by central sync.
- `postgres_store.py` owns central Postgres schema, batch inserts, and central
  summary queries.
- `central_sync.py` uploads queued SQLite outbox rows to Postgres and deletes
  local rows only after acknowledgement.
- `config_loader.py` loads `config.yaml`, validates required keys, and exposes
  the immutable `Config` dataclass.
- `setup_launchd.py` installs, unloads, removes, and checks the macOS
  LaunchAgent `com.athena.token-sidecar`.
- `queries/summary.py` is the Click CLI for daily, hourly, and all-time
  by-model summaries.
- `tests/` contains unit and integration tests for DB, proxy, launchd, query
  CLI, and end-to-end behavior.
- `config.yaml` is the default runtime config. Treat committed defaults as
  documentation of the local development setup.

## Core Behavioral Contracts

- Preserve OpenAI-compatible request bodies and upstream response bodies as much
  as possible. The proxy is an observer, not a mutator.
- Only log usage when the upstream response is JSON and contains a dict-shaped
  `usage` object.
- Store timestamps in UTC ISO 8601 format.
- Keep SQLite as the hot-path persistence layer. Do not put Postgres calls in
  the request path.
- When central sync is enabled, delete local rows only after Postgres
  acknowledges the batch.
- Keep `config.yaml` as the main configuration surface; support `--config` where
  existing CLIs already do.
- Keep Postgres DSNs in environment variables, not committed config.
- Keep launchd support macOS user-scoped. Do not make LaunchAgent commands run
  as root.
- Treat `localhost:1240` as the sidecar default and `http://localhost:1234` as
  the LM Studio default unless a task explicitly changes ports.
- `/health` should remain cheap and independent of LM Studio availability.

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

## Testing Expectations

For docs-only changes, a readback or diff check is enough.

For Python behavior changes, run the narrowest relevant tests first, then the
full suite when the change touches shared behavior. Integration tests may depend
on local LM Studio state and can take longer; if they cannot run in the current
environment, say so in the handoff.

For launchd changes, prefer unit tests that mock `launchctl` and path behavior.
Only run live `launchctl` commands when the task specifically calls for local
LaunchAgent validation.

For proxy changes, verify both halves of the contract: token rows are written
correctly and callers still receive the expected upstream status/body.

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
  `click`, `httpx`, `psycopg`, `pytest`, `pytest-aiohttp`, `pytest-asyncio`,
  `pyyaml`, and `tabulate`.
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
