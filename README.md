# Token Counter Sidecar

**Python:** 3.11+ · **Tests:** 157 passing + 2 skipped · **macOS only**

A lightweight HTTP proxy that sits in front of LM Studio, intercepts every LLM API response, writes token usage to a local SQLite outbox, and can flush it to a central Postgres database for cross-machine reporting.

---

## What this does

```
┌──────────────┐     ┌─────────────────┐     ┌───────────────┐
│  Client      │ ──▶ │  token-sidecar  │ ──▶ │ LM Studio     │
│ (any app     │     │  localhost:1240 │     │ localhost:1234│
│ using the    │ ◀── │                 │ ◀── │               │
│ sidecar)     │     └─────────────────┘     └───────────────┘
└──────────────┘              │
                              ▼
                     ┌─────────────────┐       ┌──────────────────┐
                     │ SQLite outbox   │ ─ ─ ▶ │ Postgres         │
                     │ ~/.token_side   │       │ optional central │
                     │ car/tokens.db   │       │ reporting DB     │
                     └─────────────────┘       └──────────────────┘
```

- Accepts OpenAI-compatible API calls (`/v1/chat/completions`, `/v1/completions`) on `localhost:1240`
- Forwards them verbatim to LM Studio at `localhost:1234`
- Intercepts the response, extracts `usage.prompt_tokens / completion_tokens / total_tokens`
- Queues one local SQLite row per request with timestamps, node ID, endpoint, and HTTP status
- Optionally flushes queued rows to central Postgres in the background, then clears local acknowledged rows
- Returns the original upstream response unchanged — zero behaviour change for clients

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **LM Studio** | Running with API server enabled (default: `localhost:1234`) |
| **Python** | 3.11 or newer |
| **`uv`** | Package manager — [installation guide](https://github.com/astral-sh/uv) |
| **macOS** | Required for the LaunchAgent auto-start feature |
| **Postgres** | Optional; useful for multi-machine reporting over Tailscale |

---

## Quick start

```bash
# 1. Clone / cd into the project
cd ~/software_projects/local-token-sidecar

# 2. Run the install script — handles all setup in one step:
./install.sh          # interactive (confirms before loading launchd)
./install.sh --force  # fully silent, load immediately

# That's it! The sidecar is installed and running.
```

The install script will:
- Verify `uv` and Python ≥3.11 are present
- Run `uv sync` to install dependencies
- Create `~/.token_sidecar/` (mode `0700`) and initialise the SQLite schema
- Generate + validate the LaunchAgent plist from `config.yaml`
- Optionally load it via launchd (starts on every login)

### Manual alternatives

```bash
# Run manually without installing as a LaunchAgent:
uv run python sidecar.py

# Install just the LaunchAgent step manually:
uv run python setup_launchd.py install
launchctl kickstart -kp gui/$(id -u)/com.athena.token-sidecar
```

---

## Configuration reference

All settings live in `config.yaml` at the project root.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `proxy.listen_host` | string | `"localhost"` | Interface the proxy binds to |
| `proxy.listen_port` | int | `1240` | Port the sidecar listens on (must be free) |
| `proxy.upstream_url` | string | `"http://localhost:1234"` | Full URL of the LM Studio API |
| `node.id` | string | `"local"` | Stable machine name for central reports (e.g. `athena`) |
| `database.path` | string/path | `"~/.token_sidecar/tokens.db"` | SQLite database file path (`~` expanded) |
| `database.central.enabled` | bool | `false` | Enable background Postgres flushing |
| `database.central.driver` | string | `"postgres"` | Central DB driver; only `postgres` is supported |
| `database.central.dsn_env` | string | `"TOKEN_SIDECAR_POSTGRES_DSN"` | Env var containing the sidecar writer DSN |
| `database.central.flush_interval_seconds` | number | `5` | Background flush interval after successful attempts |
| `database.central.batch_size` | int | `100` | Maximum queued rows per central upload batch |
| `oracle.enabled` | bool | `false` | Gate for `setup_launchd.py install --service oracle` |
| `oracle.listen_host` | string | `"0.0.0.0"` | Interface the Token Oracle metrics API binds to |
| `oracle.listen_port` | int | `8090` | Port for the Token Oracle metrics API |
| `oracle.timezone` | string | `"America/New_York"` | IANA timezone used for local-day metrics |
| `oracle.budget` | int | `2000000` | Daily token budget surfaced in `/metrics` |
| `oracle.ascendant_window_seconds` | int | `120` | Recency window for live/ascendant detection |
| `oracle.dsn_env` | string | `"TOKEN_SIDECAR_QUERY_DSN"` | Env var containing the read-side Postgres DSN |
| `oracle.nodes` | list | `nyx,mnemosyne,athena,metis` | Stable node order for zero-filled `/metrics` output |
| `logging.level` | string | `"INFO"` | Log level — one of `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Override the config path at runtime:

```bash
uv run python sidecar.py --config /path/to/custom-config.yaml
```

---

## Central Postgres reporting

On the Mac mini, install Postgres with Homebrew, then bootstrap the database, roles, schema, grants, and local env file:

```bash
scripts/setup_nyx_postgres.sh
```

The writer role receives `INSERT` on `token_usage` plus narrow
`SELECT(event_id)` access so idempotent uploads can use
`ON CONFLICT (event_id) DO NOTHING`.

The script writes known DSNs to `~/.token_sidecar/postgres.env` on `nyx`. On each sidecar machine, copy the `TOKEN_SIDECAR_POSTGRES_DSN` export from that file, set a stable `node.id`, enable central sync in `config.yaml`, and provide the writer DSN via environment variable:

```bash
source ~/.token_sidecar/postgres.env
uv run python sidecar.py
```

When running via LaunchAgent, `setup_launchd.py install` copies `TOKEN_SIDECAR_POSTGRES_DSN` from the current environment into the user LaunchAgent plist when central sync is enabled. The plist is written with mode `0600` in that case.

See `project_docs/postgres_setup.md` for Mac mini setup notes and example SQL for writer/read-only roles.

---

## Query CLI

Three commands to inspect collected data. All accept `--format json` or `--format table`, plus `--backend auto|sqlite|postgres` and `--node NODE_ID`. `auto` uses Postgres when a query/writer DSN is present, and falls back to SQLite if central reads are unavailable.

```bash
# Today's token summary grouped by model (default: today UTC)
uv run python -m queries.summary daily

# Specific date
uv run python -m queries.summary daily --date 2026-05-17

# Filter to a single model
uv run python -m queries.summary daily --model minimax-m2.7

# Query central Postgres for one machine
TOKEN_SIDECAR_QUERY_DSN='postgresql://token_sidecar_reader:password@mac-mini.tailnet-name.ts.net:5432/token_sidecar' \
  uv run python -m queries.summary daily --backend postgres --node athena

# Hourly breakdown for a given date (--date is required)
uv run python -m queries.summary hourly --date 2026-05-17

# All-time totals by model, sorted descending by total tokens
uv run python -m queries.summary by-model

# JSON output (default when piped — safe for scripting)
uv run python -m queries.summary daily --format json
```

Sample table output:

```
Date       Model             Requests   Prompt Tokens   Completion Tokens   Total Tokens
----------  ---------------  ---------  --------------  ------------------  -------------
2026-05-17  minimax-m2.7            12           1,204                 389         1,593
2026-05-17  qwen3.6-27b-mlx          4             832                 201         1,033
```

### Querying the local outbox directly

The SQLite file lives at `~/.token_sidecar/tokens.db`. With central sync disabled, it behaves like the historical local database. With central sync enabled, it is an outbox/cache and successfully uploaded rows are deleted.

```bash
# Recent rows (last 10)
sqlite3 ~/.token_sidecar/tokens.db \
  "SELECT datetime(timestamp), model, prompt_tokens, completion_tokens, total_tokens
   FROM token_usage ORDER BY id DESC LIMIT 10;"

# All-time totals by model
sqlite3 ~/.token_sidecar/tokens.db \
  "SELECT model, COUNT(*) AS requests,
          SUM(prompt_tokens) AS prompt_toks,
          SUM(completion_tokens) AS completion_toks,
          SUM(total_tokens) AS total_toks
   FROM token_usage GROUP BY model ORDER BY total_toks DESC;"

# Today's usage
sqlite3 ~/.token_sidecar/tokens.db \
  "SELECT date(timestamp) AS day, SUM(total_tokens)
   FROM token_usage WHERE date(timestamp)=date('now') GROUP BY day;"
```

Or override the path via environment variable for scripting:

```bash
TOKEN_SIDECAR_DB=/path/to/tokens.db uv run python -m queries.summary daily
```

---

## Dashboard

A read-only web dashboard for the **central Postgres** `token_usage` table.
Intended to run on the postgres box only — not on the same machine doing
inference (it's a separate process).

### Run it

```bash
# Set the read-side DSN (or put this in ~/.token_sidecar/env.sh)
export TOKEN_SIDECAR_QUERY_DSN='postgresql://token_sidecar_reader:...@host:5432/token_sidecar'

uv run python dashboard.py            # binds 0.0.0.0:8080 by default
open http://localhost:8080/
```

If `TOKEN_SIDECAR_QUERY_DSN` is unset, `dashboard.py` prints a clear error and
exits 0 (clean exit; the launchd `KeepAlive` policy won't respawn it into a
crash loop).

### What you see

- **KPI tiles** — Tokens today, Requests today, Avg response, Active models — each with delta vs. same time yesterday and a 14-day sparkline
- **Tokens over time** — stacked-by-model time series (hourly for 1d, daily for 7d/30d, daily-or-weekly for all-time)
- **By machine** — token share per `node_id` (athena, metis, …)
- **Models leaderboard** — table of model usage for the selected range
- **Recent activity** — live-updating feed of recent requests
- **Tweaks panel** — switch between Quiet and Terminal themes, chart modes, sparkline visibility

### API

All under `/api`. Every endpoint filters out probe rows
(`/probe` endpoint, `model = 'probe'`, or `event_id` like
`permission-probe-%`). Repeated `model=`/`node=` params narrow results.

| Path                                          | Returns                                           |
| --------------------------------------------- | ------------------------------------------------- |
| `GET /api/meta`                               | `{ now, models[], hosts[] }`                      |
| `GET /api/kpi?model=...&node=...`             | today / yesterday-same-time / sparkline_14d       |
| `GET /api/buckets?range=1d\|7d\|30d\|all`     | server-bucketed totals by `(bucket, model)`       |
| `GET /api/leaderboard?range=…`                | per-model aggregates                              |
| `GET /api/by-host?range=…`                    | per-node aggregates with model breakdown          |
| `GET /api/rows?since_ts=...&since_id=...`     | activity feed rows, cursored on `(ts, event_id)`  |
| `GET /healthz`                                | liveness probe — does not touch Postgres          |

### Configuration

```yaml
dashboard:
  enabled: false                 # gate for setup_launchd.py install --service dashboard
  listen_host: "0.0.0.0"
  listen_port: 8080
  feed_initial_rows: 50
  poll_interval_ms: 2200
```

`enabled` does **not** stop the binary from serving — `python dashboard.py`
always serves. The flag is only consulted by the launchd install command
(below).

### Install as a launchd service (postgres box only)

```bash
# 1. Land the DSN
mkdir -p ~/.token_sidecar
echo 'export TOKEN_SIDECAR_QUERY_DSN=postgresql://...' > ~/.token_sidecar/env.sh
chmod 600 ~/.token_sidecar/env.sh

# 2. Flip the gate in config.yaml
#    dashboard:
#      enabled: true

# 3. Install (refuses if any pre-flight check fails — env file, config gate,
#    psycopg import, or SELECT 1 round-trip)
uv run python setup_launchd.py install --service dashboard
launchctl kickstart -kp gui/$(id -u)/com.athena.token-sidecar-dashboard

# Status / unload / remove
uv run python setup_launchd.py status --service dashboard
uv run python setup_launchd.py unload --service dashboard
uv run python setup_launchd.py remove --service dashboard
```

The plist (`com.athena.token-sidecar-dashboard`) uses a `/bin/sh` wrapper
that conditionally sources `~/.token_sidecar/env.sh` and always exec's
python — so a missing env file does not short-circuit before python runs.
Combined with `KeepAlive: {SuccessfulExit: false}` and the clean-exit
behavior on missing DSN, a misconfigured install stays down with a log
message rather than crash-looping launchd.

---

## Token Oracle Metrics API

A read-only LAN API for the Token Oracle ESP32 display. It is a separate
process from the sidecar and dashboard, reads only central Postgres, and serves
one compact token-only JSON document for the device.

### Run it

```bash
# The API checks the live env, repo .env, ~/.token_sidecar/env.sh,
# then ~/.token_sidecar/postgres.env for TOKEN_SIDECAR_QUERY_DSN.
uv run python api/token_oracle_api.py

curl -s http://localhost:8090/health
curl -s http://localhost:8090/metrics | jq
```

If the query DSN is missing, the process prints a clear error and exits 0 so
launchd does not crash-loop. If the DSN is present but Postgres is unreachable
at startup, startup raises so launchd can retry. Runtime query failures return
HTTP 200 with `ok: false` and safe zero/default metric values so firmware can
still parse the payload.

### API

| Path | Returns |
| ---- | ------- |
| `GET /health` | `{"status":"ok"}` without touching Postgres |
| `GET /metrics` | Token Oracle payload from `project_docs/implementation-plan.md` §4 |

`/metrics` computes local-day totals, per-node totals, 24 hourly buckets,
ascendant/live state, ascendant model totals, trend, high-water, and active-day
streak on the fly from `token_usage`. Probe rows are excluded using the same
rules as the dashboard. The endpoint is intended for a trusted LAN only.

### Install as a launchd service (postgres box only)

```bash
# 1. Put the reader DSN in one of the supported local env files.
mkdir -p ~/.token_sidecar
echo 'export TOKEN_SIDECAR_QUERY_DSN=postgresql://...' > ~/.token_sidecar/env.sh
chmod 600 ~/.token_sidecar/env.sh

# 2. Flip the gate in config.yaml
#    oracle:
#      enabled: true

# 3. Install and start
uv run python setup_launchd.py install --service oracle
launchctl kickstart -kp gui/$(id -u)/com.athena.token-oracle-api

# Status / unload / remove
uv run python setup_launchd.py status --service oracle
uv run python setup_launchd.py unload --service oracle
uv run python setup_launchd.py remove --service oracle
```

The Oracle plist sources repo `.env`, `~/.token_sidecar/env.sh`, and
`~/.token_sidecar/postgres.env`, then writes logs to
`~/.token_sidecar/oracle.log` and `~/.token_sidecar/oracle.error.log`.

---

## Install & Uninstall

### `./install.sh` — full setup

```bash
./install.sh          # interactive, confirms before loading launchd
./install.sh --force  # silent, load immediately
```

Does everything: dependencies, DB init, plist generation, optional launchd load.

### `./uninstall.sh` — clean removal

```bash
./uninstall.sh           # remove launchd only, keep ~/.token_sidecar/
./uninstall.sh --purge   # also delete all data and uv environment
./uninstall.sh --force   # skip confirmations
```

---

## LaunchAgent management (manual)

If you prefer to manage the plist manually without the shell scripts:

```bash
# Install — generates plist
uv run python setup_launchd.py install

# Check status (loaded / unloaded + plist path)
uv run python setup_launchd.py status

# Unload — stop the agent but keep the plist file
uv run python setup_launchd.py unload

# Remove — stop and delete the plist
uv run python setup_launchd.py remove
```

**What `install` does:**
- Reads `config.yaml` for database/log paths
- Writes `~/Library/LaunchAgents/com.athena.token-sidecar.plist`
- Writes `TOKEN_SIDECAR_CONFIG`, and when central sync is enabled, the configured Postgres DSN env var into the plist
- Sets `RunAtLoad: true` (starts on login) and `KeepAlive: {SuccessfulExit: false}` (restarts after crash, not clean exit)
- Redirects stdout → `~/.token_sidecar/sidecar.log`, stderr → `~/.token_sidecar/sidecar.error.log`

---

## Troubleshooting

### Sidecar returns 502 Bad Gateway

LM Studio is not running or its API port changed.

```bash
# Verify LM Studio is responding
curl -s http://localhost:1234/v1/models | head -c 200

# Check the upstream_url in config.yaml matches your LM Studio port
grep upstream_url config.yaml
```

### No data appearing in SQLite

```bash
# Is the sidecar actually listening?
curl -s http://localhost:1240/health
# Expected: {"status": "ok"}

# Check the log file for errors
tail ~/.token_sidecar/sidecar.error.log

# Verify LM Studio is loading a model (first request after LM Studio start
# can take 3-5 seconds while the model loads into VRAM)
```

### Central Postgres rows are not appearing

```bash
# Confirm rows are queued locally
sqlite3 ~/.token_sidecar/tokens.db \
  "SELECT COUNT(*), MAX(last_sync_error) FROM token_usage;"

# Confirm the sidecar process has the DSN env var
grep TOKEN_SIDECAR_POSTGRES_DSN ~/Library/LaunchAgents/com.athena.token-sidecar.plist

# Check central connectivity from this machine
TOKEN_SIDECAR_QUERY_DSN="$TOKEN_SIDECAR_POSTGRES_DSN" \
  uv run python -m queries.summary by-model --backend postgres --format table
```

### Port already in use

```bash
# Find what's using port 1240
lsof -i :1240

# Change the sidecar's listen port in config.yaml, then restart:
uv run python setup_launchd.py unload
uv run python setup_launchd.py install
launchctl kickstart -kp gui/$(id -u)/com.athena.token-sidecar
```

### LaunchAgent status shows [LOADED] but process is not responding

```bash
# Check for import / startup errors in the error log
tail ~/.token_sidecar/sidecar.error.log

# Common cause: wrong Python interpreter path in plist.
# Re-run install to regenerate it with the correct .venv python:
uv run python setup_launchd.py remove
uv run python setup_launchd.py install
launchctl kickstart -kp gui/$(id -u)/com.athena.token-sidecar
```

---

## File inventory

| File | Purpose |
|------|---------|
| `sidecar.py` | Main proxy — aiohttp app, intercepts responses, logs to SQLite |
| `dashboard.py` | Read-only dashboard service (postgres box) — serves the React UI and `/api/*` JSON endpoints |
| `api/token_oracle_api.py` | Read-only Token Oracle metrics API for ESP32/LAN polling |
| `pg_common.py` | Shared Postgres API helpers for probe filtering, JSON, UTC ISO, timezone validation, and env-file DSNs |
| `dashboard_static/` | HTML/CSS/JSX assets for the dashboard, plus vendored React + Babel |
| `db.py` | SQLite schema + CRUD helpers (`init_db`, `log_token_usage`, `get_daily_summary`, `get_hourly_summary`) |
| `config_loader.py` | YAML config loader with typed `Config`, `DashboardConfig`, and `OracleConfig` dataclasses |
| `setup_launchd.py` | LaunchAgent plist generator + CLI: install / unload / remove / status (`--service sidecar\|dashboard\|oracle`) |
| `queries/summary.py` | Click-based query CLI with `daily`, `hourly`, `by-model` subcommands |
| `scripts/setup_nyx_postgres.sh` | One-shot nyx setup wrapper: start Homebrew Postgres and run central bootstrap |
| `scripts/bootstrap_postgres.py` | One-shot central Postgres database, role, schema, and grant bootstrap |
| `scripts/init_postgres.py` | Create central Postgres table, indexes, and reporting views |
| `install.sh` | One-step install: dependencies, DB init, plist generation, optional launchd load |
| `uninstall.sh` | Clean removal: unload LaunchAgent, optionally purge data and uv env |
| `config.yaml` | Configuration file — all runtime settings |

---

## Development

```bash
# Run the full test suite (157 passing + 2 skipped in current verification)
uv run python -m pytest tests/ -v

# Run a specific test file
uv run python -m pytest tests/test_db.py -v
```

---

*Last updated: 2026-06-02*
