# Token Counter Sidecar

**Python:** 3.11+ · **Tests:** 64 passing (63 + 1 skipped) · **macOS only**

A lightweight HTTP proxy that sits in front of LM Studio, intercepts every LLM API response, and writes token usage to a local SQLite database — no Docker, no containers, just Python.

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
                     ┌─────────────────┐
                     │  SQLite         │
                     │  ~/.token_side  │
                     │  car/tokens.db  │
                     └─────────────────┘
```

- Accepts OpenAI-compatible API calls (`/v1/chat/completions`, `/v1/completions`) on `localhost:1240`
- Forwards them verbatim to LM Studio at `localhost:1234`
- Intercepts the response, extracts `usage.prompt_tokens / completion_tokens / total_tokens`
- Logs one row per request to SQLite with timestamps
- Returns the original upstream response unchanged — zero behaviour change for clients

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **LM Studio** | Running with API server enabled (default: `localhost:1234`) |
| **Python** | 3.11 or newer |
| **`uv`** | Package manager — [installation guide](https://github.com/astral-sh/uv) |
| **macOS** | Required for the LaunchAgent auto-start feature |

---

## Quick start

```bash
# 1. Clone / cd into the project
cd ~/Documents/hermes_projects/token_sidecar

# 2. Sync dependencies (creates .venv, installs packages)
uv sync

# 3. Configure — defaults work out of the box; edit if you changed ports
#    sidecar listen:   localhost:1240
#    upstream target:  http://localhost:1234
#    database:         ~/.token_sidecar/tokens.db
vim config.yaml

# 4a. Run manually (dev)
uv run python sidecar.py

# 4b. Install as a LaunchAgent (starts immediately and on every login)
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
| `database.path` | string/path | `"~/.token_sidecar/tokens.db"` | SQLite database file path (`~` expanded) |
| `logging.level` | string | `"INFO"` | Log level — one of `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Override the config path at runtime:

```bash
uv run python sidecar.py --config /path/to/custom-config.yaml
```

---

## Query CLI

Three commands to inspect collected data. All accept `--format json` or `--format table`.

```bash
# Today's token summary grouped by model (default: today UTC)
uv run python -m queries.summary daily

# Specific date
uv run python -m queries.summary daily --date 2026-05-17

# Filter to a single model
uv run python -m queries.summary daily --model minimax-m2.7

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

---

## LaunchAgent management

```bash
# Install — generates plist and prints load instructions
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
| `db.py` | SQLite schema + CRUD helpers (`init_db`, `log_token_usage`, `get_daily_summary`, `get_hourly_summary`) |
| `config_loader.py` | YAML config loader with typed `Config` dataclass and `--config` CLI override |
| `setup_launchd.py` | LaunchAgent plist generator + CLI: install / unload / remove / status |
| `queries/summary.py` | Click-based query CLI with `daily`, `hourly`, `by-model` subcommands |
| `config.yaml` | Configuration file — all runtime settings |

---

## Development

```bash
# Run the full test suite (64 tests)
uv run python -m pytest tests/ -v

# Run a specific test file
uv run python -m pytest tests/test_db.py -v

# Manual load test (not committed to git — for local profiling only)
python scripts/stress_test.py --requests 20
```

---

*Last updated: 2026-05-17*