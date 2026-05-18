# Phase 6 — Integration Testing: Detailed Implementation Plan

**Project:** token-sidecar  
**Phase:** 6 of 7 (final implementation phase before Documentation)  
**Parent plan:** `../implementation_plan.md`  
**Requirements:** `../requirements.md`  

---

## Context

Phases 0–5 delivered all individual components working in isolation:
- **db.py** — SQLite layer with schema and CRUD helpers (11 unit tests)
- **sidecar.py** — aiohttp HTTP proxy that intercepts usage from LM Studio responses (11 unit/integration tests)
- **config_loader.py** — typed Config dataclass loaded from YAML (5 tests, exercised via test_proxy.py)
- **setup_launchd.py** — LaunchAgent plist generator and lifecycle manager (17 unit tests)
- **queries/summary.py** — click-based CLI for daily/hourly/by-model reports (17 integration tests)

Phase 6 is the convergence point: run all components together against real infrastructure, verify acceptance criteria AC1–AC7 hold under realistic conditions, and exercise failure/recovery paths that unit tests cannot cover.

---

## Scope

This phase does **not** write new production code. All features are implemented. The work is:
1. Write an integration test suite (`tests/test_integration.py`) that exercises the full stack
2. Run a manual stress test with 20–50 real requests across multiple models
3. Verify graceful degradation and recovery (LM Studio stop/start)
4. Test launchd respawn behaviour
5. Confirm all acceptance criteria from `requirements.md` still hold

---

## Step-by-Step Plan

### Step 1 — Design integration test structure

**File:** `tests/test_integration.py`

Use a **subprocess pattern** — tests run the actual sidecar binary and query it via HTTP, never importing sidecar internals. This mirrors how the system behaves in production.

**Test DB fixture** (`conftest.py` or inline):
- Each test gets its own temporary SQLite database
- Tests pass `TOKEN_SIDECAR_DB` env var so queries hit the fixture DB, not production

**Key scenarios to cover:**

| ID | Scenario | What to verify |
|----|----------|---------------|
| INT-01 | Start sidecar, send 1 chat completions request | Response returns; SQLite row inserted with correct tokens |
| INT-02 | Send 10 requests in sequence (mixed models) | Each appears as separate row; query CLI aggregates correctly |
| INT-03 | Query `daily` for today after real traffic | Matches manual count from DB |
| INT-04 | Query `hourly --date <today>` after real traffic | Returns non-empty rows with correct hour_utc values |
| INT-05 | Query `by-model` after multi-model traffic | Models sorted descending by total_tokens; no date field |
| INT-06 | LM Studio offline → request returns 502 | Sidecar does not crash; error logged |
| INT-07 | LM Studio comes back online → next request succeeds | Recovery works, row inserted post-recovery |
| INT-08 | launchd respawn after SIGKILL of sidecar process | Process restarts and resumes accepting requests |

**Test isolation strategy:**
- Sidecar runs on a random free port per test (avoid binding conflicts)
- Each test uses `TOKEN_SIDECAR_DB` pointing at its own temp DB
- Tests wait for sidecar to be ready before sending requests (health poll loop with timeout)

### Step 2 — Write `tests/test_integration.py`

**Test environment setup:**
```python
# Each test:
1. Allocates a random free port via socket (/tmp/sidecar_test_<n>.sock or threading)
2. Creates temp dir with:
   - temp_config.yaml (port: <free_port>, upstream_url: http://localhost:1234, db_path: <temp_db>)
   - Starts sidecar as subprocess: uv run python sidecar.py --config <temp_config>
3. Waits for health endpoint to respond (poll every 100ms, max 5s)
4. Sends real HTTP requests via httpx.Client against http://localhost:<free_port>
5. Queries TOKEN_SIDECAR_DB with queries/summary CLI subprocess
6. Asserts results match expected values from the direct DB query
7. Tears down sidecar subprocess and temp files
```

**Tests to write:**

```
test_integration_single_chat_request          # INT-01
test_integration_multiple_requests_aggregation # INT-02
test_integration_daily_query_matches_db       # INT-03
test_integration_hourly_query_returns_data    # INT-04
test_integration_by_model_sorted_descending   # INT-05
test_integration_lm_studio_offline_502        # INT-06 (mock upstream at wrong port or no server)
test_integration_lm_studio_recovery           # INT-07
```

**Test for launchd respawn (INT-08):**
- This requires the installed LaunchAgent to already exist from Phase 4
- Use `launchctl kickstart -kp gui/$(id -u)/com.athena.token-sidecar` to trigger restart
- Verify process is back up within 10s by polling health endpoint

### Step 3 — Write a standalone stress test script (optional, not checked into tests/)

**Purpose:** Generate 20–50 real requests across multiple known models on LM Studio for manual confidence.

```python
# scripts/stress_test.py (not part of the test suite)
# Reads model list from LM Studio /v1/models endpoint
# Sends N requests per model with increasing prompt size
# Prints token totals at end
```

This script lives in `scripts/` and is `.gitignore`'d — it only runs locally when Christian wants to do a live smoke test.

### Step 4 — Run full integration suite

```bash
cd ~/Documents/hermes_projects/token_sidecar
uv run python -m pytest tests/test_integration.py -v --tb=short
```

Expected: all INT-0x tests pass. Some (INT-06, INT-07) may be skipped if LM Studio is not running at test time — document this in the skip reason.

### Step 5 — Run full test suite

```bash
uv run python -m pytest tests/ -v --tb=short
```

Must maintain **56+ passing** (existing 56 + however many integration tests added).

### Step 6 — Manual verification: launchd respawn (INT-08)

Requires LM Studio running on port 1234.

1. Ensure plist is installed:
   ```bash
   python setup_launchd.py status   # should show [LOADED]
   ```

2. Find sidecar PID and kill it:
   ```bash
   ps aux | grep token-sidecar | grep -v grep
   kill -9 <PID>
   ```

3. Wait up to 10s, then verify it's back:
   ```bash
   python setup_launchd.py status   # should show [LOADED] again within ~5s
   curl http://localhost:1240/v1/models  # should return 200
   ```

4. Confirm DB still accepts writes after respawn.

### Step 7 — Verify all acceptance criteria

From `requirements.md` Section 7:

| AC | Criterion | Verification |
|----|-----------|-------------|
| AC1 | Proxy starts on localhost:1234 (config), forwards to LM Studio | Manual curl test + INT-01 |
| AC2 | Every /v1/chat/completions or /v1/completions response with `usage` is written to SQLite | INT-02, direct DB row count check |
| AC3 | Database schema matches spec | Covered by test_db.py (Phase 1) — no changes needed |
| AC4 | LaunchAgent plist installs correctly; sidecar starts on login | INT-08 + manual verification |
| AC5 | Summary query script returns daily token totals by model from CLI | INT-03, INT-04, INT-05 |
| AC6 | No external dependencies beyond stdlib + aiohttp/httpx | Already satisfied — no new deps in Phase 6 |
| AC7 | Sidecar handles LM Studio downtime gracefully (502, no crash) | INT-06 |

### Step 8 — Git commit

```
git add -A
git commit -m "Phase 6: integration test suite covering full stack E2E scenarios"
```

---

## Dependencies & Environment Requirements

**Prerequisites for Phase 6:**
- LM Studio must be running on the upstream port (default: localhost:1234)
- LaunchAgent plist should already be installed from Phase 4
- `uv` environment with all dependencies installed (`aiohttp`, `httpx`, `click`, `tabulate`, etc.)

**New test dependencies:** `pytest-asyncio` and `pytest-aiohttp` are already in pyproject.toml.

**No new runtime dependencies** — Phase 6 only adds tests, no production code changes.

---

## Exit Criteria

1. `tests/test_integration.py` covers all INT-0x scenarios (minimum 7 tests)
2. All integration tests pass against the real LM Studio instance on port 1234
3. Full test suite runs clean: **63+ passing** (`56 existing + 7 new`)
4. Manual launchd respawn verified working
5. All acceptance criteria AC1–AC7 confirmed satisfied
6. Git committed with phase-6 plan updated in `project_docs/plans/phase-6.md`

---

## File Changes

| Action | Path |
|--------|------|
| **Create** | `tests/test_integration.py` — subprocess-based integration tests |
| **Create** (optional, .gitignore'd) | `scripts/stress_test.py` — manual multi-model load generator |
| **Update** | `project_status.md` — Phase 6 completion notes |

No existing production files are modified. No new dependencies introduced.