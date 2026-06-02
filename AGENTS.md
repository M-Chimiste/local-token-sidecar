# AGENTS.md

This file is the operating guide for coding agents working in this repository.
It is agent-neutral and should stay aligned with the repo's actual code,
`README.md`, and `project_status.md`.

## Project Summary

`token-sidecar` is a lightweight Python 3.11+ HTTP proxy for local LM Studio
usage tracking on macOS, with optional central Postgres reporting and a
read-only Postgres-backed dashboard.

The sidecar listens on `localhost:1240`, forwards OpenAI-compatible requests to
LM Studio at `http://localhost:1234`, extracts token usage from upstream
responses, writes one local SQLite outbox row per request, and returns the
upstream response to the caller unchanged.

The service is intentionally small. SQLite remains the local hot-path cache, and
optional central Postgres sync can upload queued rows for cross-machine
reporting.

The central Postgres data is also the base for **Token Oracle**, a planned
LAN-only ESP32/LVGL ambient display. Token Oracle is an enhancement layer:
sidecars write usage, `nyx` keeps the central record, a read-only metrics API
aggregates compact token-only JSON, and the device renders the Pantheon/Night
Sky/Ephemeris faces from `project_docs/design.md`.

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

Dashboard path (read-only reporting process, usually on the Postgres box):

```
dashboard.py
  → central Postgres token_usage
  → /api/meta | /api/kpi | /api/buckets | /api/leaderboard
    | /api/by-host | /api/rows
  → dashboard_static/ React UI
```

Token Oracle target path (from `project_docs/implementation-plan.md`):

```
sidecar(s) → central Postgres on nyx → read-only metrics API on nyx
                                      → ESP32 over LAN GET /metrics
```

All Token Oracle aggregation belongs on `nyx`; the microcontroller should parse
one small JSON document, not perform heavy SQL-style or history math.

## Source Of Truth

Read these first when changing behavior:

1. `README.md` for current user-facing setup, CLI, configuration, and
   troubleshooting.
2. `project_status.md` for completed phases, historical decisions, and known
   verification state.
3. `project_docs/schema.md` for the SQLite schema and query contract.

For the **Token Oracle** hardware-dashboard initiative (see its own section
below), the source of truth is the `project_docs/` memory bank:

- `project_docs/requirements.md` — Token Oracle product requirements (vision,
  hardware, faces, data source, security). Note: this file now describes the
  Oracle, not the sidecar's original intent.
- `project_docs/design.md` — visual/interaction spec (palette, typography,
  the three faces, data→visual bindings).
- `project_docs/implementation-plan.md` — architecture, the `/metrics` API
  contract, Postgres rollups, phased build order, hardware pin map.
- `project_docs/oracle_final.html` — the **locked visual mockup**; the
  canonical look, proportions, palette, and motion. Match it.

If the docs disagree with live code, inspect the tests and implementation before
editing. Update docs when a behavior change intentionally changes user-facing
commands, ports, schema, or launchd behavior.

## Repository Shape

- `sidecar.py` builds the aiohttp app, forwards `/v1/chat/completions` and
  `/v1/completions`, proxies unmatched GET/POST paths, handles `/health`, logs
  token usage from JSON responses and SSE usage chunks, and returns upstream
  responses as unchanged as possible.
- `dashboard.py` is a separate aiohttp service for the postgres box. It is
  read-only: serves the React dashboard UI from `dashboard_static/` and JSON
  aggregates from the central Postgres via `psycopg_pool`. It does NOT touch
  SQLite, does NOT write to Postgres, and is independent of the sidecar process.
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
  CLI, dashboard API/query helpers, and end-to-end behavior.
- `config.yaml` is the default runtime config. Treat committed defaults as
  documentation of the local development setup.

## Core Behavioral Contracts

- Preserve OpenAI-compatible request bodies and upstream response bodies as much
  as possible. The proxy is an observer, not a mutator. For SSE responses, tee
  only the small rolling tail needed to find the final usage chunk.
- Only log usage when the upstream response is JSON with a dict-shaped `usage`
  object, or an SSE stream includes a `data: {... "usage": {...}}` chunk.
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
- Dashboard `/healthz` should remain cheap and independent of Postgres
  availability. Dashboard startup validates Postgres connectivity; liveness
  probes should not.
- Do not rename the plist labels (`com.athena.token-sidecar`,
  `com.athena.token-sidecar-dashboard`), CLI commands, config keys, or env
  var names without an explicit ask — they are baked into installed user
  environments.
- The dashboard is strictly read-only. Adding write paths against the
  central Postgres from `dashboard.py` is out of scope; if a writer is
  needed, it belongs in `postgres_store.py` / `central_sync.py` and runs
  in the sidecar's background loop, not in the dashboard service.
- Dashboard analytics must keep filtering probe rows (`/probe`, `model =
  'probe'`, and `permission-probe-%` event IDs), validate IANA `tz` query
  parameters before sending them to Postgres, and keep repeated `model=` /
  `node=` filters as narrowing filters.

## Token Oracle (hardware dashboard — active initiative, pre-implementation)

The current branch (`hardware-dashboard`) adds **Token Oracle**: a round
480×480 ESP32-S3 touch display that renders the fleet's token usage as a Greek
"pantheon" of three faces (Night Sky, Pantheon, Ephemeris). It is an ambient
desk instrument, read-only, LAN-only. **None of its code exists in the repo
yet** — the `project_docs/` memory bank is the spec; treat everything in this
section as the plan to build against, not as shipped code.

How it relates to what already exists:

- It is a **second, independent read-only consumer** of the central Postgres
  `token_usage` table — a sibling to `dashboard.py`, not a replacement. The
  sidecar and its outbox/sync contracts are unchanged; the Oracle never writes.
- The device is **not** on the tailnet. It polls a new metrics API on `nyx`
  over plain HTTP via `nyx`'s LAN IP. No cloud, no auth beyond LAN trust.
- **All aggregation happens server-side on `nyx`.** The MCU only parses one
  small JSON document — never push heavy JSON or math onto the device.

Two new components to build (suggested layout in `implementation-plan.md §8`,
likely under an `api/` and `firmware/` tree):

- **Metrics API** — a FastAPI + psycopg (v3) read-only service on `nyx`,
  co-located with Postgres and run under launchd like the sidecar. Exposes
  `GET /metrics` (the aggregated JSON document in `implementation-plan.md §4`)
  and `GET /health`. Same-day fields aggregate from `token_usage` over the
  local-day UTC bounds; history fields (`trend`, `high_water`, `streak_days`)
  come from a `daily_totals` rollup (materialized view or on-the-fly while
  volumes are small — see `implementation-plan.md §5`).
- **Firmware** — ESPHome + LVGL for the Waveshare ESP32-S3-Touch-LCD-2.8C.
  Start from the verified hardware block (ST7701S 480×480 RGB panel, GT911
  touch, PCA9554 IO expander; full pin map in `implementation-plan.md §3`).
  QMI8658 IMU likely needs a custom external component.

Contracts specific to the Oracle:

- **Tokens only — never cost or currency.** This is an explicit product
  decision (`requirements.md §2`, `§7`). Do not add dollar/spend metrics.
- **`node_id` is the node column** but treat it as configurable in the API and
  degrade gracefully — guard against schema drift.
- **Verdigris (`#46c2a6`) is reserved exclusively for "ascendant / alive"** —
  the node generating right now. Gold is the default world; never use verdigris
  decoratively. This is the single most load-bearing visual rule.
- Timestamps are UTC; "today" is computed in a configured local timezone. The
  daily budget for progress framing is configurable (default 2,000,000 tokens).
- Build order is deliberate: **data plumbing → board bring-up → hardest face
  (Night Sky) first.** Validate the custom-draw render path before building all
  three faces (`implementation-plan.md §6`).

If the metrics API lands in this repo, it adds FastAPI + uvicorn to the runtime
stack — these are **not** in the current dependency set (see Implementation
Guidance below). Confirm before adding them, and keep them scoped to the API
service so the sidecar's narrow runtime is unaffected.

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
uv run python -m pytest tests/test_dashboard.py -v
uv run python -m pytest tests/test_dashboard_queries.py -v
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
uv run python dashboard.py                                # binds dashboard.listen_host:listen_port
uv run python setup_launchd.py install --service dashboard
uv run python setup_launchd.py status  --service dashboard
```

Open the Token Oracle visual source of truth:

```bash
open project_docs/oracle_final.html
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
For streaming proxy changes, verify both halves for SSE too: streamed chunks
still reach the caller while usage is logged only when an include-usage chunk is
present.

For central-sync changes, verify both halves of the outbox contract: rows are
only deleted locally after Postgres acknowledgement, and failures bump
`sync_attempts` / `last_sync_error` without dropping rows.

For dashboard changes, run `tests/test_dashboard.py` and
`tests/test_dashboard_queries.py`; keep `/healthz` no-DB and all API queries
read-only.

For Token Oracle metrics/API changes, add contract tests around `/metrics` and
`/health`: local-day timezone bounds, ascendant recency, per-node/model totals,
rollups (`trend`, `high_water`, `streak_days`), compact JSON shape, and degraded
behavior when optional history is missing.

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
  `click`, `httpx`, `psycopg[binary]`, `psycopg-pool`, `pyyaml`, and
  `tabulate` (plus `pytest`, `pytest-aiohttp`, `pytest-asyncio` for tests).
  If implementing the Token Oracle metrics API, keep any new FastAPI/firmware
  dependencies isolated from the sidecar hot path.
- Keep user-facing errors clear and local-actionable, especially for missing
  config, LM Studio downtime, and launchd state.
- Keep the Token Oracle metrics API separate from `sidecar.py` and
  `dashboard.py` unless the user explicitly asks to merge them. It should read
  central Postgres only, preferably on `nyx`, and it must not write usage rows.
- For Token Oracle visuals, `project_docs/oracle_final.html` is canonical.
  Preserve the Mythos constraints: ink ground, antique gold as the default,
  verdigris reserved only for ascendant/alive, Cinzel-style labels,
  Cormorant-style numerals, no cost/currency, and Nyx as the keeper/center.
- Build Token Oracle in the planned order: data plumbing, board bring-up, face
  framework, then prototype the Night Sky render path before implementing all
  three faces. Keep Wi-Fi secrets in ESPHome `secrets.yaml`, not in repo files.

## Safety Notes

- Never discard user changes in this repository.
- Be careful with commands that touch `~/Library/LaunchAgents` or
  `~/.token_sidecar`; these affect the user's local machine, not just the repo.
- Do not delete or rewrite the user's SQLite database unless explicitly asked.
- Do not commit secrets, local model lists, private paths beyond documented
  defaults, or generated logs.
- Avoid starting a long-running sidecar or live LaunchAgent unless the user
  requested runtime validation.
- Do not expose dashboard or Token Oracle metrics endpoints publicly. They are
  trusted-LAN read-only surfaces, not internet services.
- Do not flash ESP32 firmware, run OTA, or touch real hardware unless the user
  explicitly asks for hardware validation.

## Useful Reading Order

1. `README.md`
2. `project_status.md`
3. `config.yaml`
4. `sidecar.py`
5. `db.py`
6. `queries/summary.py`
7. `dashboard.py`
8. `setup_launchd.py`
9. Relevant tests in `tests/`

For Token Oracle work, read the `project_docs/` memory bank instead:

1. `project_docs/requirements.md`
2. `project_docs/design.md`
3. `project_docs/implementation-plan.md`
4. `project_docs/oracle_final.html` (open in a browser — the visual truth)
5. `postgres_store.py` and `dashboard.py` for the existing Postgres read
   patterns the metrics API can mirror.
