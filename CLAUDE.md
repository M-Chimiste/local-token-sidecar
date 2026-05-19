# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` is the canonical, agent-neutral operating guide and stays in sync with the code. This file is the Claude-Code-specific layer on top of it — read both. When the two disagree, prefer the live code and tests; update both docs.

## What this project is

`token-sidecar` is a Python 3.11+ aiohttp HTTP proxy for macOS that sits in front of LM Studio (`localhost:1240` → `http://localhost:1234`), intercepts OpenAI-compatible `/v1/chat/completions` and `/v1/completions` responses, writes one local SQLite outbox row per request, and optionally flushes those rows to a central Postgres database for cross-machine reporting.

It is intentionally small. The codebase is flat — one Python module per concern at the repo root, plus `queries/`, `scripts/`, and `tests/`.

## Architecture

Request path (must stay fast and side-effect-light):

```
client → sidecar.forward_and_intercept → upstream LM Studio → response back to client
                                       ↓
                                 db.log_token_usage (SQLite outbox)
```

Background path (only when `database.central.enabled: true`):

```
central_sync._central_sync_loop (in sidecar app cleanup_ctx)
  → central_sync.flush_once
  → db.get_unsynced_token_usage → postgres_store.insert_token_usage_batch
  → db.mark_token_usage_synced (delete acknowledged rows) | mark_token_usage_sync_failed (record + backoff)
```

Hard rules that hold the design together (mirror them in any change):

- **Proxy is an observer**, not a mutator. Preserve request and upstream response bodies byte-for-byte. Only strip hop-by-hop headers.
- **SQLite is the hot path.** Never put Postgres in the request path — `postgres_store` and `central_sync` are imported only by the background loop and CLI.
- **Only log usage** when the upstream response is JSON and `usage` is a dict.
- **Timestamps are UTC ISO 8601** in both SQLite (`TEXT`) and Postgres (`TIMESTAMPTZ`).
- **Central rows are deleted locally only after Postgres ack** — the SQLite table is the outbox of record until then.
- **DSNs come from env vars** (`TOKEN_SIDECAR_POSTGRES_DSN` for the writer, `TOKEN_SIDECAR_QUERY_DSN` for read), never committed to `config.yaml`.
- **`/health` must not depend on LM Studio.** It is the launchd respawn probe.
- **Schema migrations are additive** (`db._migrate_schema` + `_backfill_outbox_fields`). Existing local DBs are migrated in place on startup.
- **LaunchAgent is user-scoped.** Never run `launchctl` as root.

## Configuration

All runtime settings live in `config.yaml` at the repo root. `config_loader.load_config` resolves in this order:

1. `--config <path>` CLI arg (handled by `parse_cli_args` in any entry point that exposes it)
2. `TOKEN_SIDECAR_CONFIG` env var
3. `<repo>/config.yaml`

`Config` is a frozen dataclass; `CentralDatabaseConfig.dsn` is populated from the env var named by `dsn_env` at load time. When `central.enabled` is true, missing `node.id` or missing DSN env var raise `ValueError` before the server starts.

## Development commands

```bash
# Dependencies (uv-managed)
uv sync

# Run the sidecar manually (default config.yaml)
uv run python sidecar.py
uv run python sidecar.py --config /path/to/config.yaml

# Tests
uv run python -m pytest tests/ -v
uv run python -m pytest tests/test_db.py -v
uv run python -m pytest tests/test_proxy.py::test_name -v   # single test

# Query CLI (sqlite by default; auto-switches to Postgres if a query/writer DSN is in env)
uv run python -m queries.summary daily
uv run python -m queries.summary hourly --date YYYY-MM-DD
uv run python -m queries.summary by-model
uv run python -m queries.summary daily --backend postgres --node athena

# LaunchAgent lifecycle (macOS, user scope)
uv run python setup_launchd.py install
uv run python setup_launchd.py status
uv run python setup_launchd.py unload
uv run python setup_launchd.py remove
```

`./install.sh` / `./uninstall.sh` wrap the full setup/teardown including `uv sync`, DB init, plist generation, and optional launchd load.

## Testing notes

- `tests/test_integration.py` and `tests/test_postgres_integration.py` may need a live LM Studio or Postgres; if they cannot run in the current environment, say so in the handoff rather than skipping silently.
- `tests/test_launchd.py` mocks `launchctl`. Do not invoke real `launchctl` from tests.
- For proxy changes, assert both halves of the contract: a row is written to SQLite **and** the caller still receives the unmodified upstream status/body.

## Things to be careful with

- `~/.token_sidecar/` and `~/Library/LaunchAgents/com.athena.token-sidecar.plist` affect the user's machine, not just the repo. Do not delete, rewrite, or unload them unless the user asked.
- Don't commit secrets, DSNs, local model lists, generated logs, or anything from `~/.token_sidecar/`.
- Runtime deps are deliberately narrow: `aiohttp`, `click`, `httpx`, `psycopg[binary]`, `pyyaml`, `tabulate` (plus pytest stack). Don't add new ones without a clear reason.
- Don't rename CLI commands, config keys, env var names, or the plist label (`com.athena.token-sidecar`) without an explicit ask — they're documented in `README.md` and baked into installed user environments.

## Source-of-truth reading order

1. `README.md` — current user-facing setup, CLI, troubleshooting
2. `AGENTS.md` — agent-neutral operating guide (full contracts and safety notes)
3. `project_status.md` — completed phases and historical decisions
4. `project_docs/schema.md` — SQLite + Postgres schema and query contract
5. `config.yaml`, `sidecar.py`, `db.py`, then relevant `tests/`
