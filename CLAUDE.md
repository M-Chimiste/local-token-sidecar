# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Canonical agent guide

[AGENTS.md](AGENTS.md) is the operating guide for coding agents in this repo and is kept in sync with the code. Read it first — this file only highlights the load-bearing parts and adds a few Claude-specific notes. Also worth scanning before non-trivial changes: [README.md](README.md), [project_status.md](project_status.md), [project_docs/requirements.md](project_docs/requirements.md), and [project_docs/schema.md](project_docs/schema.md).

## What this project is

`token-sidecar` is a Python 3.11+ aiohttp proxy that sits in front of LM Studio. It accepts OpenAI-compatible requests on `localhost:1240`, forwards them verbatim to LM Studio at `http://localhost:1234`, intercepts the response to extract `usage.{prompt,completion,total}_tokens`, queues one row in a local SQLite outbox at `~/.token_sidecar/tokens.db`, and returns the upstream response unchanged. When `database.central.enabled` is true, a background task flushes queued rows to a central Postgres and only deletes local rows after Postgres acknowledges the batch. macOS-only because it ships a LaunchAgent for auto-start.

## Architecture — the load-bearing shape

The proxy is an **observer, not a mutator**. Three contracts must hold:

1. **Request/response bodies are forwarded byte-for-byte.** [sidecar.py:58-169](sidecar.py#L58-L169) strips hop-by-hop headers, reads the upstream body, parses JSON only to extract `usage`, and returns the original bytes. Don't add transformations.
2. **The request path never talks to Postgres.** SQLite is the hot-path persistence layer. Postgres only appears in [central_sync.py](central_sync.py) (background task) and [queries/summary.py](queries/summary.py) (CLI). The `central_sync_context` cleanup_ctx in [sidecar.py:279-325](sidecar.py#L279-L325) runs the flush loop with exponential backoff (capped at 60s).
3. **Local rows are deleted only after central acknowledgement.** [central_sync.py:11-39](central_sync.py#L11-L39) calls `mark_token_usage_synced` (DELETE) only on the event_ids Postgres returned; on failure it calls `mark_token_usage_sync_failed` (UPDATE attempts/error) and re-raises so the caller can back off. The Postgres `INSERT ... ON CONFLICT (event_id) DO NOTHING` makes duplicate uploads idempotent.

Other invariants worth knowing:

- **`event_id` is the cross-store key.** UUID hex generated on local insert in [db.py:122-186](db.py#L122-L186), used as the Postgres primary key in [postgres_store.py:19-32](postgres_store.py#L19-L32). [db.py:74-119](db.py#L74-L119) migrates older databases by backfilling `event_id`, `node_id`, `endpoint`, `status_code`, `sync_attempts`.
- **Timestamps are UTC ISO 8601 strings** in SQLite, `TIMESTAMPTZ` in Postgres. All `date` filters compare `(timestamp AT TIME ZONE 'UTC')::date` on the Postgres side.
- **Config validation lives in [config_loader.py](config_loader.py)** and `Config` is `frozen=True`. When `central.enabled` is true it also requires a non-default `node.id` and a DSN present in `os.environ[central.dsn_env]` — fail loudly at startup, not at flush time.
- **LaunchAgent label is `com.athena.token-sidecar`** and the plist is regenerated from `config.yaml` by [setup_launchd.py](setup_launchd.py). When central sync is enabled, the install step copies the configured DSN env var from the current shell into the plist (mode `0600`); plain-cfg installs use mode `0644`.
- **The catch-all proxy route** ([sidecar.py:273-274](sidecar.py#L273-L274)) forwards any unmatched GET/POST to upstream and will still extract `usage` if the response happens to be an OpenAI-shaped JSON dict — useful for tracking endpoints we haven't explicitly listed.

## Development commands

```bash
uv sync                                    # install deps
uv run python sidecar.py                   # run proxy in foreground
uv run python sidecar.py --config PATH     # override config.yaml location

# Tests
uv run python -m pytest tests/ -v
uv run python -m pytest tests/test_db.py -v                 # single file
uv run python -m pytest tests/test_proxy.py::test_name -v   # single test

# Query CLI (auto picks Postgres when DSN is present, else SQLite)
uv run python -m queries.summary daily [--date YYYY-MM-DD] [--model NAME]
uv run python -m queries.summary hourly --date YYYY-MM-DD
uv run python -m queries.summary by-model
# Force backend / scope:
uv run python -m queries.summary daily --backend postgres --node athena --format json

# LaunchAgent (macOS user scope only — never run as root)
uv run python setup_launchd.py install | status | unload | remove

# One-step installer (handles deps, db init, plist, optional launchd load)
./install.sh [--force]
./uninstall.sh [--purge] [--force]
```

Postgres-backed query tests are gated on `TOKEN_SIDECAR_PG_TEST_DSN` being set; without it they skip.

## Tests that depend on external state

- `tests/test_integration.py` may rely on a running LM Studio. If it can't run in your environment, say so — don't suppress the failure.
- `tests/test_postgres_integration.py` and `tests/test_bootstrap_postgres.py` need a reachable Postgres (DSN via env). Skip rather than mock if you can't reach one.
- `tests/test_launchd.py` mocks `launchctl`; prefer that over live `launchctl` calls.

## Safety — touches the user's machine

Several actions in this repo affect the user's local machine beyond the repo:

- `~/.token_sidecar/` holds the SQLite outbox and logs — **never delete or rewrite** without an explicit ask.
- `~/Library/LaunchAgents/com.athena.token-sidecar.plist` — don't load/unload the live LaunchAgent unless the user asked for runtime validation.
- Don't start a long-running `sidecar.py` in the background as a side effect of a task.
- Don't commit the Postgres DSN. It lives in `~/.token_sidecar/postgres.env` (sourced into the shell) or in the LaunchAgent plist that `setup_launchd.py install` writes from the live env.

## Dependency policy

Runtime stack is intentionally narrow: `aiohttp`, `click`, `httpx`, `psycopg[binary]`, `pyyaml`, `tabulate`, plus `pytest` / `pytest-aiohttp` / `pytest-asyncio` for tests. Don't add new top-level deps for one-off needs.
