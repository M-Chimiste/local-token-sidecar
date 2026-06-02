# Token Sidecar — Dashboard

A read-only single-page React dashboard backed by the central Postgres
`token_usage` table. Runs as a separate aiohttp service on the postgres box
(not on inference hosts) and exposes a small JSON API plus the static UI on
`0.0.0.0:8080`.

This document is a quick reference. The full architectural plan lives in the
plan file at `/Users/c/.claude/plans/encapsulated-gliding-riddle.md`.

---

## How it fits together

```
                 ┌──────────────────────────────────────────────┐
                 │  postgres box (e.g. "nyx")                   │
   browser ──▶   │                                              │
                 │   dashboard.py  ◀── psycopg ──▶  Postgres    │
                 │   (port 8080)                  token_usage   │
                 │       │                                      │
                 │       └──▶ dashboard_static/ (HTML/CSS/JSX)  │
                 └──────────────────────────────────────────────┘
                                      ▲
                                      │ central_sync.py uploads outbox rows
                                      │
        ┌─────────────────────────────┴─────────────────────────────┐
        ▼                             ▼                             ▼
   athena · sidecar          metis · sidecar              … other nodes
   (SQLite outbox)           (SQLite outbox)
```

The dashboard *only* reads from Postgres. It does not touch SQLite, does not
write anything anywhere, and is intentionally independent of the sidecar
process.

## Endpoints

All under `/api`. Every analytics query filters out probe rows
(`endpoint = '/probe'`, `model = 'probe'`, or `event_id LIKE 'permission-probe-%'`).
Repeated `model=` / `node=` query params narrow results (HTTP-canonical,
not comma-splitting).

| Path                                       | Notes                                                            |
| ------------------------------------------ | ---------------------------------------------------------------- |
| `GET /api/meta`                            | `{ now, models, hosts }` for the controls bar                    |
| `GET /api/kpi`                             | today vs. yesterday-same-time + 14-day sparkline series          |
| `GET /api/buckets?range=1d\|7d\|30d\|all`  | server-bucketed `(bucket, model)` totals; granularity auto-picks |
| `GET /api/leaderboard?range=…`             | per-model aggregates for the leaderboard table                   |
| `GET /api/by-host?range=…`                 | per-node aggregates for the "By machine" card                    |
| `GET /api/rows?since_ts=…&since_id=…`      | activity feed; cursored on `(timestamp, event_id)`               |
| `GET /healthz`                             | liveness probe — no Postgres call                                |

### Granularity rule for `/api/buckets`

- `1d` → `hour` (24 hourly buckets of the current UTC day)
- `7d`, `30d` → `day`
- `all` → `day` if the dataset spans ≤ 90 days, else `week`
  (computed from `MAX(timestamp) - MIN(timestamp)`)

### Polling cursor for `/api/rows`

- **Cold load:** no cursor params → newest-first, `LIMIT n` (default 50)
- **Poll:** send `since_ts` + `since_id` → rows strictly after
  `(timestamp, event_id)`, returned oldest-first
- Client merges by reversing the batch and prepending, de-duping on `id`,
  trimming the feed to `feed_initial_rows` after merge

## Configuration

```yaml
dashboard:
  enabled: false                 # gate for setup_launchd.py install --service dashboard
  listen_host: "0.0.0.0"
  listen_port: 8080
  feed_initial_rows: 50
  poll_interval_ms: 2200
```

`enabled` does **not** stop `python dashboard.py` from serving; it is only
checked by the launchd install path.

The Postgres DSN is **not** in `config.yaml`. The dashboard reads
`TOKEN_SIDECAR_QUERY_DSN` from the environment. If missing, `dashboard.py`
exits with code 0 (clean exit) so the launchd KeepAlive policy
(`{SuccessfulExit: false}`) does not respawn a crash loop.

## Launchd

Label: `com.athena.token-sidecar-dashboard`. The plist uses a `/bin/sh`
wrapper that conditionally sources `~/.token_sidecar/env.sh` (`if [ -f … ]`)
and then always exec's python — so a missing or broken env file does not
short-circuit before python runs.

Install pre-flight refuses to write the plist unless all four pass:

1. `dashboard.enabled` is `true` in `config.yaml`
2. `TOKEN_SIDECAR_QUERY_DSN` is set in the current shell or
   `~/.token_sidecar/env.sh` exports it
3. `psycopg` and `psycopg_pool` import successfully in the active venv
4. A `SELECT 1` round-trip against the DSN succeeds

```bash
mkdir -p ~/.token_sidecar
echo 'export TOKEN_SIDECAR_QUERY_DSN=postgresql://...' > ~/.token_sidecar/env.sh
chmod 600 ~/.token_sidecar/env.sh
# flip dashboard.enabled: true in config.yaml
uv run python setup_launchd.py install --service dashboard
launchctl kickstart -kp gui/$(id -u)/com.athena.token-sidecar-dashboard
```

## UI notes

- Frontend is the original Claude Design HTML + CSS + JSX shipped as static
  files. React 18 + Babel-standalone are vendored under
  `dashboard_static/vendor/` so the UI works on an air-gapped LAN.
- Google Fonts (Instrument Sans, JetBrains Mono) stay on CDN; the
  `styles.css` fallback chain (`system-ui`, `SF Mono`, `Menlo`) degrades
  gracefully if the CDN is unreachable.
- The Quiet (light) theme is default; Terminal (dark, monospace) is in the
  Tweaks panel.
