# Token Counter Sidecar — Implementation Plan

**Project:** token-sidecar  
**Location:** `~/Documents/hermes_projects/token_sidecar/`  
**Requirements:** `./project_docs/requirements.md`

---

## Phase 0 — Scaffold

**Goal:** Create the project structure and set up development environment.

### Tasks

- [ ] Create directory tree:
  ```
  token_sidecar/
  ├── sidecar.py
  ├── config.yaml
  ├── setup_launchd.py
  ├── queries/
  │   └── summary.py
  ├── tests/
  │   ├── test_proxy.py
  │   └── test_db.py
  ├── project_docs/
  │   ├── requirements.md
  │   └── implementation_plan.md    ← (this file)
  └── README.md
  ```
- [ ] Create `config.yaml` with defaults matching the spec
- [ ] Set up a virtual environment: `python3 -m venv .venv && source .venv/bin/activate`
- [ ] Pin dependencies in `requirements.txt`:
  ```
  aiohttp>=3.9.0
  pyyaml>=6.0
  pytest>=7.0
  pytest-asyncio>=0.23.0
  httpx>=0.26.0   # for test client
  ```
- [ ] Add `.gitignore` (venv, `*.db`, `*.log`, `__pycache__/`)
- [ ] Verify LM Studio is running and its API responds at `localhost:8080`

---

## Phase 1 — Database Layer

**Goal:** Get the SQLite schema and basic read/write working.

### Tasks

- [ ] Create `db.py` with:
  - `init_db(db_path)` → creates tables if they don't exist
  - `log_token_usage(db_path, model, prompt_tokens, completion_tokens, total_tokens, response_ms)` → INSERT into `token_usage`
  - `get_daily_summary(db_path, date=None)` → SELECT + aggregate by model for a given date (default: today UTC)
  - `get_hourly_summary(db_path, date=None)` → same but hourly buckets
- [ ] Add DB migration logic (create tables only if not exists — no alembic needed at this scale)
- [ ] Write `tests/test_db.py`:
  - Test `init_db()` creates the right schema
  - Test `log_token_usage()` round-trips correctly
  - Test `get_daily_summary()` aggregates accurately
  - Use an in-memory SQLite DB for tests (`:memory:`)

### Exit Criteria
```
$ python -m pytest tests/test_db.py -v
# all pass
```

---

## Phase 2 — HTTP Proxy Core

**Goal:** Get the proxy forwarding requests and intercepting responses.

### Tasks

- [ ] Create `sidecar.py` with an `HTTPServer` subclass or `aiohttp` web app:
  - Listen on `localhost:1234`
  - Forward POST `/v1/chat/completions` to upstream
  - Forward POST `/v1/completions` to upstream
  - Pass all other paths through with 404 (or 200 + echo)
- [ ] Intercept the upstream response **before returning to client**:
  - Parse JSON body
  - Extract `usage` object if present
  - Call `log_token_usage(...)`
- [ ] Extract model name from request body: `messages[0].content` or `prompt` field — store as-is for now
- [ ] Measure `response_ms` with a simple timer around the upstream call
- [ ] Return upstream response verbatim to client (don't modify it)
- [ ] On upstream error: return 502 Bad Gateway, log error, do not crash

### Exit Criteria
```
# In terminal 1:
$ python sidecar.py

# In terminal 2:
$ curl -s -X POST http://localhost:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "llama-3", "messages": [{"role":"user","content":"hi"}], "max_tokens": 10}'

# Response comes back from LM Studio
# SQLite has a new row in token_usage
```

---

## Phase 3 — Configuration

**Goal:** Make everything configurable via `config.yaml`, no hardcoded values.

### Tasks

- [ ] Create `config.py`:
  - Load `config.yaml` from the same directory as `sidecar.py`
  - Validate required keys (listen_host, listen_port, upstream_url, db_path)
  - Raise a clear error on missing/malformed config
- [ ] Update `sidecar.py` to read all settings from `Config` object
- [ ] Make log level configurable (`DEBUG`, `INFO`, etc.)
- [ ] Add `--config <path>` CLI arg to override default `config.yaml` location

### Exit Criteria
```
# Changing listen_port in config.yaml changes the port sidecar.py binds to
# No hardcoded values remain in sidecar.py
```

---

## Phase 4 — LaunchAgent Setup

**Goal:** Sidecar starts automatically on login and restarts on crash.

### Tasks

- [ ] Create `setup_launchd.py`:
  - Generate a plist at `~/Library/LaunchAgents/com.athena.token-sidecar.plist`
  - Set `RunAtLoad: true`, `KeepAlive: true`
  - Redirect stdout/stderr to `~/.token_sidecar/sidecar.log`
- [ ] Add instructions in `README.md` for:
  - Running manually (dev): `python sidecar.py`
  - Installing as LaunchAgent
  - Checking status: `launchctl list | grep token-sidecar`
  - Viewing logs: `tail -f ~/.token_sidecar/sidecar.log`

### Exit Criteria
```
$ python setup_launchd.py
# plist installed

$ launchctl load ~/Library/LaunchAgents/com.athena.token-sidecar.plist
$ launchctl list | grep token-sidecar
# shows running process
```

---

## Phase 5 — Summary Query CLI

**Goal:** Allow easy inspection of collected data from the terminal.

### Tasks

- [ ] Build `queries/summary.py` CLI:
  ```bash
  # Daily summary (default: today)
  python -m queries.summary --daily

  # Hourly breakdown for a date
  python -m queries.summary --hourly --date 2026-05-17

  # All-time totals by model
  python -m queries.summary --by-model

  # JSON output flag (for scripting)
  python -m queries.summary --daily --json
  ```
- [ ] Pretty-print tabular output when running interactively
- [ ] Write `tests/test_queries.py` with mocked DB data

### Exit Criteria
```
$ python -m queries.summary --daily
Model           Requests   Prompt Tokens   Completion Tokens   Total Tokens
─────────────────────────────────────────────────────────────────────────────
llama-3.1-8b    42         12,340          5,678               18,018
mixtral-8x7b    17         8,901            4,123               13,024
```

---

## Phase 6 — Integration Testing

**Goal:** Verify the full stack works end-to-end under realistic conditions.

### Tasks

- [ ] Run the sidecar against a real LM Studio session with multiple models
- [ ] Verify data lands correctly in SQLite after ~20–50 requests
- [ ] Test graceful degradation: stop LM Studio, confirm 502 response, restart LM Studio, confirm recovery
- [ ] Check that `launchd` respawns the process after a manual kill:
  ```bash
  launchctl kickstart -kp gui/$(id -u)/com.athena.token-sidecar
  ```
- [ ] Run full test suite:
  ```bash
  python -m pytest tests/ -v
  ```

---

## Phase 7 — Documentation

**Goal:** Make the project reproducible and understandable.

### Tasks

- [ ] Write `README.md`:
  - Prerequisites (LM Studio running, Python 3.10+)
  - Quick start (4 steps: clone, venv, config, run)
  - Architecture diagram
  - Configuration reference
  - LaunchAgent installation
  - Query CLI usage
  - Troubleshooting section
- [ ] Add inline comments to all functions in `sidecar.py`, `db.py`, `config.py`
- [ ] Document the schema in `project_docs/schema.md` (optional — can live in README)

---

## Phase Ordering Summary

| Phase | Name | Key Output |
|-------|------|------------|
| 0 | Scaffold | Project structure, venv, deps |
| 1 | Database Layer | `db.py`, tests passing |
| 2 | HTTP Proxy Core | Forwarding + interception working |
| 3 | Configuration | Everything config-driven |
| 4 | LaunchAgent Setup | Auto-start on login |
| 5 | Summary Query CLI | Readable output from DB |
| 6 | Integration Testing | Full stack verified |
| 7 | Documentation | README, inline comments |

**Recommended:** Phases 0 → 1 → 2 → 3 can run in tight succession (they build on each other). Phase 4 is independent and can be done after phase 2. Phases 5–7 are polish and can be deferred.

---

*Last updated: 2026-05-17*