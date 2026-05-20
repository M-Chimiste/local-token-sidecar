# Token Sidecar Dashboard — implementation plan (rev 2)

## Context

The token-sidecar fleet writes LLM usage rows from multiple Macs into a central
Postgres on `nyx:5432/token_sidecar`. The user wants the Claude-Design mockup
(`Token Sidecar Dashboard.html` + `styles.css` + `app.jsx` + `components.jsx`
+ `tweaks-panel.jsx`) shipped as a real, persistent dashboard service running
on that postgres box, serving live data over the LAN at `0.0.0.0:8080`, with
auto-restart via launchd.

What I confirmed by introspection:

- Central table `public.token_usage` exists with columns
  `event_id (PK), timestamp (timestamptz), node_id, model, prompt_tokens,
  completion_tokens, total_tokens, response_ms, endpoint, status_code,
  ingested_at`, indexed on `(model, timestamp)`, `(node_id, timestamp)`, and
  `timestamp`. Real rows from `athena` and `metis` are flowing; also
  `permission-probe-*` rows on `/probe` which must be filtered out of
  user-facing aggregates.
- Reader DSN is `TOKEN_SIDECAR_QUERY_DSN` (already named in `AGENTS.md`).
- This repo is the *pre-central-sync* scaffold; the Postgres-aware install
  lives at `/Users/c/Documents/hermes_projects/token_sidecar/` (TCC-protected,
  untouched). This work adds only a *read-only* dashboard service in this
  repo.

This rev addresses review feedback: server-rendered bootstrap dropped in
favor of fetch-on-load (HTML-injection-safe); polling uses a stable
`(timestamp, event_id)` cursor with oldest-first ordering and client-side
dedupe; chart/KPI/leaderboard/by-host queries are bucketed in SQL so
**"All time" is just another range**, not a row-shipping nightmare; React
and Babel-standalone are vendored locally (LAN service survives WAN
outages); launchd path fails closed instead of crash-looping when DSN is
missing.

## Time semantics — UTC everywhere

Match the existing project contract (`project_docs/schema.md`, `db.py`,
`queries/summary.py` all use UTC ISO 8601). The dashboard does the same:

- All SQL day boundaries use `(timestamp AT TIME ZONE 'UTC')::date`, never
  `current_date` or `now()::date` (which depend on the Postgres session TZ).
- Client `app.jsx` already buckets via `setUTCHours(0, 0, 0, 0)` and
  `toISOString().slice(0,10)`. Stays.
- Hover labels keep their existing `... UTC` suffix where shown.

## API surface (read-only, all under `/api`)

| Path                     | Query params                                      | Returns                                                                                                |
| ------------------------ | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `GET /healthz`           | —                                                 | `200 {"status":"ok"}` — does NOT touch Postgres (probe stays cheap, `AGENTS.md` §Core Behavioral Contracts)         |
| `GET /api/meta`          | —                                                 | `{ now: <iso>, models: [...], hosts: [...] }` — distinct model names + node_ids                        |
| `GET /api/kpi`           | —                                                 | `{ today: {...}, yesterday_same_time: {...}, sparkline_14d: [{day,tok,req,ms,models}, ...] }`          |
| `GET /api/buckets`       | `range=1d\|7d\|30d\|all`                          | `{ granularity:"hour"\|"day", buckets:[{key,label,byModel:{name:tok},total,calls}] }` — server-bucketed |
| `GET /api/leaderboard`   | `range=1d\|7d\|30d\|all`                          | `[{ model, calls, prompt, completion, total, avgMs }]` sorted by `total` DESC                          |
| `GET /api/by-host`       | `range=1d\|7d\|30d\|all`                          | `[{ host, calls, total, byModel:{name:tok} }]` sorted by `total` DESC                                  |
| `GET /api/rows`          | `since_ts=<iso>&since_id=<event_id>&limit=<n>`    | newest activity-feed rows; see "Polling cursor" below                                                  |

**Filtering rule applied uniformly in every query** (handles NULLs safely):

```sql
WHERE COALESCE(endpoint, '') <> '/probe'
  AND COALESCE(model, '')    <> 'probe'
  AND event_id NOT LIKE 'permission-probe-%'
```

**Granularity rule for `/api/buckets`:**
- `1d` → `'hour'`, generate buckets for the 24 hours of the UTC day
- `7d`, `30d` → `'day'`
- `all` → `'day'` if (max(ts) - min(ts)) ≤ 90 days, else `'week'` (date_trunc).
  The client bucket-label code already handles `'day'`; the `'week'` branch
  gets a tiny label helper added (`Wk of MMM D`).

**Polling cursor** (`/api/rows`):

- Initial load fetches the most recent `N` rows (default 50) with no cursor:
  `ORDER BY timestamp DESC, event_id DESC LIMIT N`, returned newest-first.
- Subsequent polls send `since_ts`+`since_id` from the latest row already
  rendered. SQL becomes
  `WHERE (timestamp, event_id) > (:since_ts, :since_id) ORDER BY timestamp ASC, event_id ASC LIMIT 500`,
  returned **oldest-first** so the client can append in order.
- Client de-dupes by `event_id` regardless (an event_id set is already used
  for the flash-in animation), so a single row can never appear twice.

## Backend (`dashboard.py`)

Single new file. Routes:

| Path                    | Handler                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| `GET /`                 | Serve `dashboard_static/index.html` as a *static file* — **no template injection**       |
| `GET /styles.css`       | Static                                                                                   |
| `GET /app.jsx`          | Static                                                                                   |
| `GET /components.jsx`   | Static                                                                                   |
| `GET /tweaks-panel.jsx` | Static                                                                                   |
| `GET /vendor/<path>`    | Static (vendored React + Babel; see "Dependencies")                                      |
| `GET /healthz`          | Liveness — no Postgres call                                                              |
| `GET /api/*`            | The endpoints above                                                                      |

Connection management:

- One `psycopg_pool.AsyncConnectionPool` (min=1, max=4) created in
  `cleanup_ctx`; closed on shutdown. Reuses the existing `aiohttp`
  cleanup-context pattern.
- All queries are parametrized (`%s` placeholders, never f-string).
- Each handler acquires a connection per request from the pool; releases on
  exit.

`bucket_sql()` helper builds the right `date_trunc` per granularity:

```python
{
  "hour": "date_trunc('hour', timestamp AT TIME ZONE 'UTC')",
  "day":  "date_trunc('day',  timestamp AT TIME ZONE 'UTC')",
  "week": "date_trunc('week', timestamp AT TIME ZONE 'UTC')",
}
```

## Frontend

Static files copied verbatim into `dashboard_static/` from
`/tmp/design-bundle/token-dashboard/project/`:
`styles.css`, `tweaks-panel.jsx`, `components.jsx`.

`Token Sidecar Dashboard.html` is renamed `index.html` and modified:

- Drop `<script src="data.js"></script>` (no `data.js` is shipped).
- Replace the three `https://unpkg.com/...` script tags with three
  `<script src="/vendor/...">` tags (see "Dependencies").
- No server-side injection. The page is a plain static file.

`app.jsx` — refactored, **not** "two surgical edits" (honest scope: this
is the biggest single file change):

1. Drop the `window.MOCK` reads.
2. Drop client-side aggregation: `buildBuckets`, `aggByModel`, `aggByHost`,
   `spark` are deleted. The new app calls `/api/*` endpoints whenever
   `range` or `selectedModels` changes, and stores results in React state:
   `meta`, `kpi`, `buckets`, `leaderboard`, `byHost`, `feed`.
3. Live tick (when `liveOn`) polls `/api/rows?since_ts=...&since_id=...`
   every `poll_interval_ms` (default 2200 ms; configurable per below).
   Returned rows are de-duped by `event_id` and prepended to `feed`
   (newest at top). Every 10th tick (~22 s) it refetches the active range's
   `/api/buckets`, `/api/leaderboard`, `/api/by-host`, `/api/kpi` so the
   chart/KPIs catch up without thrashing the DB.
4. Model multiselect filter sends `?models=a,b,c` to each endpoint when it
   changes (server-side filter; faster than fetching everything and
   filtering on the client).
5. The "planned host column" footnote in [dashboard_static/app.jsx](dashboard_static/app.jsx)
   §378-381 is removed (column is real).

`components.jsx` — no changes. The existing `TimeSeriesChart`, `KPI`,
`ModelTable`, `HostBars`, `ActivityFeed`, `Controls`, `Sparkline`, `fmt`
all take the same shapes as the new server payloads.

`tweaks-panel.jsx` — no changes.

## Files added

- [dashboard.py](dashboard.py)
- [dashboard_static/index.html](dashboard_static/index.html)
- [dashboard_static/styles.css](dashboard_static/styles.css)
- [dashboard_static/app.jsx](dashboard_static/app.jsx)
- [dashboard_static/components.jsx](dashboard_static/components.jsx)
- [dashboard_static/tweaks-panel.jsx](dashboard_static/tweaks-panel.jsx)
- [dashboard_static/vendor/react.production.min.js](dashboard_static/vendor/react.production.min.js)
- [dashboard_static/vendor/react-dom.production.min.js](dashboard_static/vendor/react-dom.production.min.js)
- [dashboard_static/vendor/babel.min.js](dashboard_static/vendor/babel.min.js)
- [dashboard_static/vendor/LICENSES.md](dashboard_static/vendor/LICENSES.md) — version + license text for the three vendored libs
- [tests/test_dashboard.py](tests/test_dashboard.py)
- [tests/test_dashboard_queries.py](tests/test_dashboard_queries.py)
- [project_docs/dashboard.md](project_docs/dashboard.md) — short user-facing doc

## Files modified

- [config.yaml](config.yaml) — add `dashboard:` block (see "Configuration")
- [config_loader.py](config_loader.py) — add `DashboardConfig` dataclass + parsing
- [pyproject.toml](pyproject.toml) — add `psycopg[binary]>=3.2`, `psycopg-pool>=3.2`
- [uv.lock](uv.lock) — regenerated by `uv sync`
- [setup_launchd.py](setup_launchd.py) — add `--service {sidecar,dashboard}` flag; install/status/unload/remove subcommands cover both labels
- [README.md](README.md) — short "Dashboard" section with install/run notes
- [AGENTS.md](AGENTS.md) — add `dashboard.py` to §Repository Shape, note the new plist label, list dashboard queries as read-side-only
- [project_docs/schema.md](project_docs/schema.md) — document the live Postgres columns (currently only documents SQLite)

## Configuration

`config.yaml` gains a top-level block:

```yaml
dashboard:
  enabled: false               # gate for setup_launchd.py install --service dashboard (NOT a runtime flag)
  listen_host: "0.0.0.0"
  listen_port: 8080
  feed_initial_rows: 50
  poll_interval_ms: 2200
```

**Clarification (was a finding):** `enabled: false` does **not** stop the
binary from serving. `dashboard.py` always serves when invoked directly
(`uv run python dashboard.py`). `dashboard.enabled` is read **only** by
`setup_launchd.py install --service dashboard`: if false, install refuses
to write the plist (a deliberate safety gate, "you have to opt in on the
postgres box"). Documented in [README.md](README.md) and in the install
command's `--help`.

DSN is **not** in `config.yaml`. The dashboard reads
`TOKEN_SIDECAR_QUERY_DSN` from the environment. If missing, the process
prints a clear error and **exits 0** (clean exit; KeepAlive will not
respawn — see "Persistent process" below).

## Dependencies

Runtime (added to [pyproject.toml](pyproject.toml)):

- `psycopg[binary]>=3.2`
- `psycopg-pool>=3.2`

Both are mentioned in `AGENTS.md` §Implementation Guidance as the expected
runtime stack; this commit gets `pyproject.toml` to actually match.

Vendored frontend (no Python deps, no Node toolchain):

- `react.production.min.js` 18.3.1 (UMD)
- `react-dom.production.min.js` 18.3.1 (UMD)
- `@babel/standalone/babel.min.js` 7.29.0

Files are downloaded once during initial implementation, committed to
`dashboard_static/vendor/`, and pinned. `LICENSES.md` in the same dir
records source, version, license, and SHA-256. No CDN dependency at
runtime; the dashboard works on an air-gapped LAN.

Google Fonts (Instrument Sans, JetBrains Mono) **stay on CDN** with the
fallback chain already present in `styles.css` (`system-ui`, `SF Mono`,
`Menlo`, etc.) — losing the fonts is a cosmetic degradation, not a
functional one, and shipping font files inflates the bundle by ~250 KB for
modest gain.

## Persistent process on the postgres box (launchd)

`setup_launchd.py` grows a `--service` option. Both subcommands
(`install`, `status`, `unload`, `remove`) accept `--service sidecar`
(existing, default) or `--service dashboard`.

Dashboard plist `com.athena.token-sidecar-dashboard`:

- `ProgramArguments`:
  ```
  ["/bin/sh", "-c",
   ". \"$HOME/.token_sidecar/env.sh\" && exec <venv>/bin/python <repo>/dashboard.py"]
  ```
  (sourcing the env file gives the process `TOKEN_SIDECAR_QUERY_DSN`
  without committing secrets; matches the existing sidecar plist layout
  but inside a shell wrapper).
- `RunAtLoad`: true
- `KeepAlive`: `{ "SuccessfulExit": false }` — **mirrors the existing
  sidecar plist** (see `~/Library/LaunchAgents/com.athena.token-sidecar.plist`).
  This is the launchd policy "restart on crash (non-zero exit), do not
  restart on intentional exit (zero)."
- `StandardOutPath` / `StandardErrorPath`:
  `~/.token_sidecar/dashboard.log` / `dashboard.error.log`
- User scope only (`launchctl bootstrap gui/<uid>`), never root.

**No crash-loop hazard**: combined with "exit 0 on missing DSN," a
misconfigured install stays down with a single line in the log instead of
hammering launchd every 10 s.

**Install command pre-flight checks** (refuses to write the plist if any fails):

1. `TOKEN_SIDECAR_QUERY_DSN` is set, OR `~/.token_sidecar/env.sh` exists
   and contains the export line.
2. `config.yaml`'s `dashboard.enabled` is `true`.
3. `psycopg` import succeeds in the target venv.
4. A quick `SELECT 1` round-trip against the DSN succeeds.

All four must pass before any state is written to `~/Library/LaunchAgents/`.

## Tests

- [tests/test_dashboard_queries.py](tests/test_dashboard_queries.py) — unit
  tests for the SQL builders (string-equality assertions on generated SQL,
  and parameter shape). No live DB needed.
- [tests/test_dashboard.py](tests/test_dashboard.py) — aiohttp
  `test_utils.AioHTTPTestCase` smoke tests. The app factory accepts an
  injected pool stub (a small `_FakePool` that returns canned rows), so all
  endpoints run without a real Postgres.
  - `/healthz` → 200 without hitting the pool
  - `/api/meta`, `/api/kpi`, `/api/buckets?range=1d|7d|30d|all`,
    `/api/leaderboard`, `/api/by-host`, `/api/rows` all return JSON of the
    documented shape
  - Probe-row filtering: stub returns one probe row, endpoint filters it
    out across every aggregate
  - Cursor: `/api/rows?since_ts=X&since_id=Y` returns oldest-first; absent
    cursor returns newest-first

Existing tests stay green (the existing modules are untouched).

## Things explicitly NOT in scope

- No auth, no TLS — LAN-only is fine per the user.
- No writes to Postgres. Strictly read-only.
- No changes to `sidecar.py`, `db.py`, SQLite schema, or the existing
  `com.athena.token-sidecar` plist.
- No materialized views — at current volume aggregate queries are
  millisecond-scale on the existing indexes. Add covering indexes later if
  the table grows past ~10 M rows.
- No Postgres password handling beyond reading the env var.

## Verification

Local dev (no plist install yet):

```bash
uv sync                                                                # picks up psycopg + psycopg-pool
export TOKEN_SIDECAR_QUERY_DSN='postgresql://token_sidecar_reader:...@nyx:5432/token_sidecar'
uv run python dashboard.py                                             # binds 0.0.0.0:8080 regardless of dashboard.enabled
curl http://localhost:8080/healthz                                     # → {"status":"ok"}, no DB touch
curl 'http://localhost:8080/api/meta' | python -m json.tool
curl 'http://localhost:8080/api/kpi' | python -m json.tool
curl 'http://localhost:8080/api/buckets?range=all' | python -m json.tool
curl 'http://localhost:8080/api/leaderboard?range=30d' | python -m json.tool
curl 'http://localhost:8080/api/by-host?range=7d' | python -m json.tool
curl 'http://localhost:8080/api/rows?limit=10' | python -m json.tool
open http://localhost:8080/                                            # interact in browser
```

Cross-check each panel against ground-truth SQL (UTC-anchored):

```sql
-- "Tokens today" (UTC)
SELECT SUM(total_tokens) FROM token_usage
 WHERE timestamp >= date_trunc('day', now() AT TIME ZONE 'UTC')
   AND timestamp <  date_trunc('day', now() AT TIME ZONE 'UTC') + interval '1 day'
   AND COALESCE(endpoint, '') <> '/probe'
   AND COALESCE(model, '')    <> 'probe'
   AND event_id NOT LIKE 'permission-probe-%';

-- "All time" leaderboard
SELECT model, COUNT(*) AS calls, SUM(total_tokens) AS total
  FROM token_usage
 WHERE COALESCE(endpoint, '') <> '/probe'
   AND COALESCE(model, '')    <> 'probe'
   AND event_id NOT LIKE 'permission-probe-%'
 GROUP BY model ORDER BY total DESC;
```

Tests:

```bash
uv run python -m pytest tests/test_dashboard.py tests/test_dashboard_queries.py -v
uv run python -m pytest tests/ -v                                      # full suite stays green
```

Production install on the postgres box:

```bash
# 1. Land DSN
echo 'export TOKEN_SIDECAR_QUERY_DSN=postgresql://...' > ~/.token_sidecar/env.sh
chmod 600 ~/.token_sidecar/env.sh

# 2. Flip the gate
yq -i '.dashboard.enabled = true' config.yaml      # or edit by hand

# 3. Install (refuses if pre-flight fails)
uv run python setup_launchd.py install --service dashboard

# 4. Confirm KeepAlive policy via a controlled restart
launchctl print gui/$(id -u)/com.athena.token-sidecar-dashboard | grep -E 'state|pid'
PID=$(pgrep -f 'python.*dashboard\.py' | head -1)
kill -TERM "$PID"                                  # respawns (non-zero exit on signal)
sleep 2 && pgrep -f 'python.*dashboard\.py'        # confirms new PID

# 5. Confirm fail-closed on bad config
unset TOKEN_SIDECAR_QUERY_DSN; uv run python dashboard.py    # exits 0 with clear stderr; no respawn under launchd

# 6. LAN reachability
curl http://<nyx-lan-ip>:8080/healthz
```
