# Token Oracle — Project Status

> Memory Bank · `project_status.md`
> Update this file at the end of every coding session that changes Token
> Oracle code, config, tests, deployment behavior, or docs.

---

## Current Recommendation

**Deploy Phase 0 on `nyx` before progressing to Phase 1.**

Reason: Phase 1 board bring-up depends on a stable LAN `/metrics` endpoint.
Validate the read-only API on the Postgres box first, then move to ESPHome
hardware bring-up and a throwaway `today.total` label.

Recommended deployment gate:

1. Copy/sync this repo state to `nyx`.
2. Ensure `TOKEN_SIDECAR_QUERY_DSN` is available in the live environment,
   repo `.env`, `~/.token_sidecar/env.sh`, or `~/.token_sidecar/postgres.env`.
3. Set `oracle.enabled: true` on `nyx` only.
4. Run manually first:
   `uv run python api/token_oracle_api.py`
5. From a LAN client, verify:
   `curl -s http://<nyx-lan-ip>:8090/health`
   and `curl -s http://<nyx-lan-ip>:8090/metrics | jq`.
6. Install launchd after the manual smoke test:
   `uv run python setup_launchd.py install --service oracle`.

---

## Phase Status

| Phase | Name | Status | Notes |
|---|---|---|---|
| 0 | Data plumbing | Complete locally; ready for nyx smoke deploy | aiohttp `/metrics` API, on-the-fly rollups, launchd support, full tests green |
| 1 | Board bring-up | Not started | Start only after `/metrics` is stable over LAN |
| 2 | Face framework | Not started | Depends on firmware polling and JSON globals pattern |
| 3 | Three faces | Not started | Prototype Night Sky render path first |
| 4 | Interaction | Not started | Touch first; QMI8658 can follow |
| 5 | Polish | Not started | Real-panel color/timing tuning |

---

## Implemented In Phase 0

- `api/token_oracle_api.py`: separate read-only aiohttp service with `/health`
  and `/metrics`.
- `pg_common.py`: shared probe filtering, JSON response, UTC ISO formatting,
  timezone validation, and simple env-file DSN lookup.
- `config_loader.py`: `OracleConfig` with defaults:
  `0.0.0.0:8090`, `America/New_York`, `budget=2000000`,
  `ascendant_window_seconds=120`, `dsn_env=TOKEN_SIDECAR_QUERY_DSN`, and stable
  nodes `nyx`, `mnemosyne`, `athena`, `metis`.
- `setup_launchd.py --service oracle`: label `com.athena.token-oracle-api`,
  env-file sourcing, preflight checks, and logs under `~/.token_sidecar/`.
- `/metrics` behavior:
  - Exact implementation-plan §4 key shape.
  - Local-day aggregation from UTC timestamps.
  - Probe rows excluded.
  - `ascendant: null`, empty `models`, and all `nodes[].live=false` when idle.
  - Runtime DB/query failures return HTTP 200 with `ok:false` and parseable
    zero/default fields.
  - History fields are computed on the fly; no `daily_totals` view yet.

Verification from local development:

```bash
uv run python -m py_compile pg_common.py config_loader.py dashboard.py setup_launchd.py api/token_oracle_api.py
uv run python -m pytest tests/ -v
# 157 passed, 2 skipped
```

Skipped tests were expected: live launchd respawn and optional live Postgres
integration.

---

## Open Risks / Watch Items

- Live `nyx` deployment has not been performed from this machine.
- Confirm LAN reachability to `http://<nyx-lan-ip>:8090/metrics` before any
  firmware work.
- Confirm the reader role on `nyx` has sufficient `SELECT` access to
  `token_usage`.
- Validate real data shape: unknown `node_id` values append after configured
  gods; check whether that is acceptable before firmware layout is locked.
- Phase 0 intentionally omits ill-omen and today-vs-yesterday fields because
  they are not in the §4 `/metrics` wire contract.

---

## Session Log

### 2026-06-02 — Phase 0 Local Implementation

- Implemented Token Oracle data plumbing in the current repo.
- Chose aiohttp + psycopg_pool instead of FastAPI/uvicorn to avoid new runtime
  dependencies and mirror `dashboard.py`.
- Added launchd support for `com.athena.token-oracle-api`.
- Added config, API, shared-helper, dashboard, and launchd tests.
- Updated README, AGENTS/CLAUDE, implementation plan, requirements, and root
  project status.
- Full test suite passed: **157 passed, 2 skipped**.

Next session should start with `nyx` manual deployment validation, not firmware
implementation.
