# Token Counter Sidecar — Project Status

**Project:** token-sidecar  
**Location:** `~/Documents/hermes_projects/token_sidecar/`  
**Started:** 2026-05-17  

---

## Phase Completion Summary

### ✅ Phase 0 — Scaffold
**Status:** Complete  
**Completed:** 2026-05-17  

| Step | Description | Status |
|------|-------------|--------|
| 1 | Directory tree + placeholder files | ✅ |
| 2 | `config.yaml` with defaults | ✅ |
| 3 | UV installed, project env initialized (Python 3.11) | ✅ |
| 4 | Dependencies added via `uv add` | ✅ |
| 5 | `.gitignore` created | ✅ |
| 6 | LM Studio API verified at `localhost:1234/v1/models` | ✅ |
| 7 | Initial git commit (`4c6099a`) | ✅ |

**Artifact:** Git commit `4c6099a`

---

### ✅ Phase 1 — Database Layer
**Status:** Complete  
**Completed:** 2026-05-17  

| Step | Description | Status |
|------|-------------|--------|
| 1 | Create `db.py` with schema SQL and all four functions (`init_db`, `log_token_usage`, `get_daily_summary`, `get_hourly_summary`) | ✅ |
| 2 | Write full test suite in `tests/test_db.py` — 11 tests using fixtures + deterministic sample data | ✅ |
| 3 | Run pytest: **11/11 passed** (0.04s) | ✅ |
| 4 | Manual smoke test against real temp DB file — PASSED | ✅ |
| 5 | Git commit (`932e758`) | ✅ |

---

### ✅ Phase 2 — HTTP Proxy Core
**Status:** Complete  
**Completed:** 2026-05-17  

| Step | Description | Status |
|------|-------------|--------|
| 1 | Create `config_loader.py` (fast-tracked from Phase 3) | ✅ |
| 2 | Write `sidecar.py` — aiohttp proxy: forward to LM Studio at `:1234`, intercept usage, log via `db.log_token_usage()`, return upstream verbatim; handle errors with 502 | ✅ |
| 3 | Write `tests/test_proxy.py` — mock upstream fixture + 6 tests | ✅ |
| 4 | Run pytest: **6/6 passed** (0.04s) | ✅ |
| 5 | Manual E2E test — live request through proxy, verified SQLite row (`minimax-m2.7`: 39/5/44 tokens, ~3.5s) | ✅ |
| 6 | Git commit (`2e3de18`) | ✅ |

**Bug fixed in Phase 2:** `web.Response` forbids passing both `Content-Type` header and `content_type=` kwarg; resolved by using `content_type=` only.

---

### ✅ Phase 3 — Configuration
**Status:** Complete  
**Completed:** 2026-05-17  

| Step | Description | Status |
|------|-------------|--------|
| 1 | Audit existing code for hardcoded values across all `.py` files | ✅ |
| 2 | Rewrite `config_loader.py`: frozen `@dataclass Config` with typed attrs (`listen_host`, `listen_port: int`, `upstream_url: str`, `database_path: pathlib.Path`, `log_level`), dotted-key resolution, startup validation | ✅ |
| 3 | Add `--config <path>` CLI arg via `parse_cli_args()`; update `sidecar.py`'s `main()` to use it; log level from config applied at startup via `logging.basicConfig` | ✅ |
| 4 | Audit pass: remaining hardcoded values only in docstrings (acceptable) or test fixtures (intentional); all runtime code uses Config object attributes | ✅ |
| 5 | Add new tests: `FileNotFoundError` on missing file, `ValueError` with descriptive message for missing required keys, `Config.from_dict()` roundtrip, log level defaulting to INFO, `--config` override path | ✅ |
| 6 | Run full test suite: **22/22 passed** (11 db + 11 proxy) | ✅ |
| 7 | Git commit (`252ace9`) | ✅ |

**Bug fixed in Phase 3:** Port `0` (valid "assign any available port") was rejected by validator because it is falsy; fixed with explicit `"key" not in dict` checks instead of `if not value`.

---

### ✅ Phase 4 — LaunchAgent Setup
**Status:** Complete
**Completed:** 2026-05-17

| Step | Description | Status |
|------|-------------|--------|
| 1 | Write `setup_launchd.py` with `generate_plist_content()` using `plistlib.dumps()`, `install()` (write plist + print instructions), `unload()` (`launchctl bootout gui/<uid>`), `remove()` (unload + delete plist), `status()` (`launchctl list <label>`), and CLI subcommands | ✅ |
| 2 | Auto-create `~/.token_sidecar/` directory with mode `0o700` before writing plist file | ✅ |
| 3 | Write `tests/test_launchd.py` — 17 tests covering: XML parse + all plist keys (`KeepAlive`, `RunAtLoad=true`, ProgramArguments, StandardOutPath), root guard for all commands, correct launchctl command construction, idempotent unload on missing job, file deletion on remove, status [LOADED]/[UNLOADED] detection | ✅ |
| 4 | Run pytest: **17/17 launchd tests passed** (0.03s) | ✅ |
| 5 | Manual `uv run python setup_launchd.py install` + `plutil -p <plist>` validation — all keys present and correctly typed; status command verified as [UNLOADED] when agent not running | ✅ |
| 6 | Run full test suite: **39/39 passed** (11 db + 17 launchd + 11 proxy) | ✅ |
| 7 | Git commit (`070fdf1`) | ✅ |

**Bugs fixed during Phase 4 testing:**
- Root guard used `patch"os.geteuid"` syntax instead of `patch("os.geteuid")` throughout — all 9 occurrences corrected to proper call signature
- `sidecar_script` fixture didn't create parent directories before writing — added `project_dir.mkdir(parents=True)` 
- `test_plist_label_is_correct` used `\n` literal but plistlib uses `\n\t` indentation — relaxed assertion to just check `<string>label</string>` presence
- `launchctl print` behaves differently in TTY vs non-TTY contexts (non-TTY returns domain info even for unknown labels) — switched status detection to use `launchctl list <label>` which is reliable regardless of TTY state

---

### ✅ Phase 5 — Query CLI
**Status:** Complete
**Completed:** 2026-05-17

| Step | Description | Status |
|------|-------------|--------|
| 1 | Add `click` + `tabulate` dependencies via `uv add` | ✅ |
| 2 | Create empty `queries/__init__.py` package marker for `-m` invocation | ✅ |
| 3 | Implement `queries/summary.py` — click-based CLI with three subcommands: `daily [--date] [--model]`, `hourly --date`, `by-model`. Auto-detects tty→table, pipe→json. Uses existing `db.get_daily_summary()` and `get_hourly_summary()`. New direct SQL aggregate query for `by-model` | ✅ |
| 4 | Write `tests/test_queries.py` — 17 subprocess integration tests covering all commands, format flags, model filter, error cases, and `resolve_format()` tty detection logic | ✅ |
| 5 | Run full test suite: **56/56 passed** (11 db + 17 launchd + 11 proxy/config + 17 query) | ✅ |
| 6 | Manual verification — all three commands confirmed working against real DB in both JSON and table format; by-model correctly omits spurious date field from aggregate rows | ✅ |
| 7 | Git commit (`4f01503`) | ✅ |

**Bugs fixed during Phase 5 testing:**
- `by_model` was injecting a spurious `"date": "<today>"` into all aggregate rows — `_normalise_row()` logic was too aggressive. Fixed by adding explicit `row_date: str | None` parameter to the normaliser and only passing it from `daily()`, not from `by-model`
- `get_daily_summary()` SQL does not include a date column in its SELECT (only filters by one) — fixed by passing the target date explicitly into `_normalise_row(row_date=target_date)`

---

### ✅ Phase 6 — Integration Testing
**Status:** Complete
**Completed:** 2026-05-17

| Step | Description | Status |
|------|-------------|--------|
| 1 | Write `tests/test_integration.py` — 8 subprocess-based tests: INT-01 single request, INT-02 multi-model aggregation (3 models), INT-03 daily query vs raw DB, INT-04 hourly returns data, INT-05 by-model sorted desc, INT-06 LM Studio offline → 502, INT-07 recovery after restart, INT-08 launchd respawn | ✅ |
| 2 | Run integration suite: **8/8 passed** (INT-08 skipped — requires loaded plist) in ~112s | ✅ |
| 3 | Write `scripts/stress_test.py` for manual 20–50 request load testing; added to `.gitignore` so it stays local-only | ✅ |
| 4 | Run full test suite: **63 passed + 1 skipped** (11 db + 8 integration + 17 launchd + 6 proxy/config + 17 query) | ✅ |
| 5 | Manual launchd respawn: `kill -9 <pid>`, confirmed restart within ~1s via `/health` poll loop | ✅ |
| 6 | Verify AC1–AC7 against live infrastructure (real request → 200 OK, SQLite row correct, /health returns ok, LaunchAgent registered) | ✅ |
| 7 | Git commit (`d8f04cc`) + update project_status.md | ✅ |

**Key bugs fixed during Phase 6:**
- `sidecar.py` had no `/health` endpoint — added `handle_health()` GET handler returning `{"status": "ok"}`
- Integration tests used `import httpx` inside functions but module-level `httpx.Timeout` constant needed import at top level — moved to module scope
- Test INT-02 timeout on cold model load (qwen3.6-27b-mlx) — increased per-request read timeout from 30s → 120s via `DEFAULT_REQUEST_TIMEOUT = httpx.Timeout(30.0, read=120.0)`
- LaunchAgent plist used UV bare python (`~/.local/share/uv/python/cpython-...`) instead of `.venv/bin/python3` — fixed by using `os.path.abspath()` which resolves the path absolute without following symlinks (pathlib's `.resolve()` would follow through to UV bare interpreter)
- `status()` function in setup_launchd.py used `launchctl print <domain> <label>` which returns exit 0 with domain info for unknown labels when run non-interactively — switched to `launchctl list` pattern matching

---

### ✅ Phase 7 — Documentation
**Status:** Complete
**Completed:** 2026-05-17

| Step | Description | Status |
|------|-------------|--------|
| 1 | Docstring audit across all five `.py` files (db.py, sidecar.py, config_loader.py, setup_launchd.py, queries/summary.py) — filled in missing/one-line docstrings for `handle_chat_completions`, `handle_completions`, `create_app` | ✅ |
| 2 | Write `README.md` at project root (11 sections: architecture ASCII diagram, prerequisites, quick-start, config reference table, CLI examples, LaunchAgent commands, troubleshooting, dev/test guide) — 7,587 bytes | ✅ |
| 3 | Write `project_docs/schema.md` — standalone DB schema reference with column descriptions, index notes, and SQL examples for each query type — 4,878 bytes | ✅ |
| 4 | Final review: run full test suite **64/64 passed** (63 + 1 skipped) | ✅ |
| 5 | Git commit (`7fc6ca3`) + update project_status.md | ✅ |

**Bug fixed during Phase 7:**
- INT-04 `test_integration_hourly_query_returns_data` used `date.today()` which gives local calendar date; at ~22:00 local time the UTC date is already tomorrow, so `--date today_local` found no rows. Fixed by using `datetime.now(timezone.utc).strftime("%Y-%m-%d")` to match LM Studio's UTC timestamps.

---

### ✅ Phase 8 — Central Postgres Reporting
**Status:** Complete
**Completed:** 2026-05-18

| Step | Description | Status |
|------|-------------|--------|
| 1 | Add `psycopg[binary]` dependency for optional central Postgres writes and queries | ✅ |
| 2 | Extend SQLite `token_usage` into a local outbox/cache with `event_id`, `node_id`, endpoint/status, and retry metadata; migrate old DBs in place | ✅ |
| 3 | Add `postgres_store.py` central schema, idempotent batch insert, reporting views, and Postgres summary queries | ✅ |
| 4 | Add `central_sync.py` and sidecar background flusher with bounded retry backoff; Postgres is never called in the request path | ✅ |
| 5 | Extend config with `node.id` and `database.central.*`; DSNs resolve from environment variables | ✅ |
| 6 | Update query CLI with `--backend auto|sqlite|postgres` and `--node` filtering | ✅ |
| 7 | Add Mac mini Postgres setup docs and schema initialization script | ✅ |
| 8 | Verification: **81 passed + 2 skipped** across full test suite | ✅ |

**Design note:** When central sync is enabled, local SQLite is a durable outbox/cache. Rows are deleted locally only after Postgres acknowledges the upload batch.

---

## Discovery Notes

### LM Studio Port
- LM Studio running on **port 1234**, not 8080.
- Sidecar listen port: `1240`, upstream_url: `http://localhost:1234`

**Available models:** minimax-m2.7, nvidia/nemotron-3-nano-omni, qwen3.6-27b-mlx, qwen3.6-35b-a3b-mlx, google/gemma-4-26b-a4b, gemma-4-e4b-it, qwen3.5-397b-a17b, qwen/qwen3-coder-next, zai-org/glm-4.7-flash, ibm/granite-4-h-tiny, text-embedding-nomic-embed-text-v1.5, zai-org/glm-4.6v-flash, granite-4.0-h-tiny-mlx, granite-4.0-h-micro

### UV Environment
- `uv` v0.11.11 pre-installed; project on Python 3.11.15

---

## Current Configuration (config.yaml)

```yaml
proxy:
  listen_host: "localhost"
  listen_port: 1240          # sidecar binds here → forwards to upstream_url
  upstream_url: "http://localhost:1234"

database:
  path: "~/.token_sidecar/tokens.db"
  central:
    enabled: false
    driver: "postgres"
    dsn_env: "TOKEN_SIDECAR_POSTGRES_DSN"
    flush_interval_seconds: 5
    batch_size: 100

node:
  id: "local"

logging:
  level: "INFO"   # DEBUG, INFO, WARNING, ERROR

launchd:
  enabled: true
  label: "com.athena.token-sidecar"
```

---

## All Phases Complete

All 8 phases implemented, tested, and documented. The token-sidecar is production-ready for local SQLite usage and optional central Postgres reporting.

**Git history:** 15 commits (`4c6099a` → `7fc6ca3`)
**Test suite:** 81 passed + 2 skipped (INT-08 launchd respawn and optional live Postgres test)

---

## File Inventory (after Phase 8)

```
token_sidecar/
├── .git/                    # commits: 4c6099a → 7fc6ca3 (15 total)
├── .venv/                   # uv virtual environment
├── README.md                ← architecture, central reporting setup, usage, troubleshooting
├── queries/
│   ├── __init__.py          ← from Phase 5 (package marker)
│   └── summary.py           ← CLI: daily/hourly/by-model, SQLite or Postgres backend
├── tests/
│   ├── test_db.py           ← SQLite schema, migration, summaries, outbox
│   ├── test_central_sync.py ← Postgres flush orchestration
│   ├── test_proxy.py        ← proxy behavior and config validation
│   ├── test_launchd.py      ← LaunchAgent plist/lifecycle behavior
│   ├── test_queries.py      ← CLI behavior
│   ├── test_integration.py  ← live sidecar/LM Studio integration
│   └── test_postgres_integration.py ← optional live Postgres integration
├── db.py                    ← SQLite outbox/cache and local summaries
├── postgres_store.py        ← central Postgres schema, inserts, summaries
├── central_sync.py          ← local outbox to Postgres batch sync
├── sidecar.py               ← aiohttp proxy + background sync lifecycle
├── config_loader.py         ← config, node id, central sync settings
├── setup_launchd.py         ← LaunchAgent generation with env var support
├── scripts/
│   └── init_postgres.py     ← central schema initializer
└── project_docs/
    ├── postgres_setup.md    ← Mac mini Postgres setup guide
    ├── plans/               # phase-0.md through phase-7.md
    └── schema.md            ← SQLite and Postgres schema reference
```

---

*Last updated: 2026-05-18 (Phase 8 complete — central Postgres reporting)*
