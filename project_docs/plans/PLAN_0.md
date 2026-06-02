# Phase 0 Plan: Token Oracle Data Plumbing

## Summary
Build Phase 0 as a separate read-only aiohttp service in the current repo. It will run on `nyx`, read central Postgres via `TOKEN_SIDECAR_QUERY_DSN`, expose `GET /metrics` and `GET /health`, and compute all Token Oracle aggregates server-side. No sidecar request-path changes, no dashboard merge, no firmware work, no FastAPI/uvicorn dependency.

## Key Changes
- Add a small shared Postgres/API helper module, `pg_common.py`:
  - Move the dashboard probe filter there as `PROBE_FILTER_SQL`; both dashboard and Oracle import it.
  - Share UTC ISO `Z` formatting and JSON response helper behavior.
  - Share IANA timezone validation using `zoneinfo.available_timezones()` cached at import, matching dashboard behavior.
- Add an `oracle` config section and frozen config dataclasses:
  - Defaults: `enabled: false`, `listen_host: "0.0.0.0"`, `listen_port: 8090`, `timezone: "America/New_York"`, `budget: 2000000`, `ascendant_window_seconds: 120`, `dsn_env: "TOKEN_SIDECAR_QUERY_DSN"`.
  - Add configured node mapping in this order: `nyx`, `mnemosyne`, `athena`, `metis`.
  - Validate timezone and require positive budget/window/port.
- Add `api/token_oracle_api.py` as an aiohttp app:
  - `create_app(cfg, pool_factory=None)` mirrors `dashboard.py` so tests can inject a fake pool.
  - `GET /health` returns `{"status":"ok"}` without touching Postgres.
  - `GET /metrics` returns exactly the implementation-plan §4 keys.
  - On DB/query error, return HTTP 200 with a parseable `{"ok": false, ...}` payload using safe zero/default metric values.
  - On startup: missing DSN exits 0; DSN present but unreachable raises so launchd can respawn.
- Implement aggregation from `token_usage` only:
  - Use configured local-day bounds from UTC timestamps.
  - Exclude probe rows via shared `PROBE_FILTER_SQL`.
  - `rate_per_min` is recent-window tokens divided by `ascendant_window_seconds / 60`.
  - `ascendant` is the most recent node inside the recency window.
  - When idle, `ascendant` is `null`, all `nodes[].live` are false, and `models` is empty.
  - Configured nodes are zero-filled in stable order; unknown today/live nodes append after configured nodes.
  - History fields are computed on the fly from grouped local-day totals; no materialized view for v1.
- Add launchd support:
  - Extend `setup_launchd.py --service oracle`.
  - Use label `com.athena.token-oracle-api`.
  - Source `~/.token_sidecar/env.sh`, write logs under `~/.token_sidecar/oracle.log` and `oracle.error.log`, and preflight `oracle.enabled`, DSN presence, imports, and `SELECT 1`.
- Update docs with aiohttp runtime, on-the-fly rollups, manual run, launchd install/status, config, and LAN validation.

## Test Plan
- Config tests: defaults, custom `oracle` config, node mapping, bad timezone, bad numeric values.
- Shared helper tests: probe filter import stability, ISO formatting, timezone validation.
- API tests with fake pool:
  - `/health` does not touch DB.
  - `/metrics` returns exact key shape, UTC `ts`, stable node order, and 24 hourly buckets.
  - Empty DB behavior returns safe defaults.
  - DB/query failure returns HTTP 200 with `ok:false` and parseable defaults.
  - Local timezone day boundaries, zenith, span, trend, high-water, and streak are correct.
  - Recency window sets `ascendant` and `nodes[].live`; idle returns `ascendant:null`.
  - Probe rows are excluded.
- Launchd tests: `--service oracle` label/path/logs, root guard, install/status/unload/remove routing, env wrapper, and preflight messages.
- Optional live validation on `nyx`: run the API with `TOKEN_SIDECAR_QUERY_DSN`, then verify `/health` and `/metrics` from a LAN client with `curl | jq`.

## Assumptions
- The existing read-only Postgres role can `SELECT` from `token_usage`.
- Phase 0 intentionally does not add ill-omen or today-vs-yesterday fields; it sticks to implementation-plan §4.
- The metrics endpoint is trusted-LAN only, read-only, and unauthenticated.
- No database schema migration is needed because rollups are computed on the fly for v1.
