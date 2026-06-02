"""
Token Sidecar Dashboard — read-only HTTP service.

A small aiohttp app that serves the dashboard UI and a JSON API backed by the
central Postgres `token_usage` table. Intended to run as a long-lived process
on the postgres box.

Connection:
    DSN is read from the TOKEN_SIDECAR_QUERY_DSN environment variable. If
    missing, the process prints a clear error and exits 0 (so launchd's
    KeepAlive `{SuccessfulExit: false}` policy does NOT respawn it into a
    crash loop).

Usage:
    uv run python dashboard.py
    uv run python dashboard.py --config /path/to/config.yaml

Routes are documented inline below and in project_docs/dashboard.md.
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web

from config_loader import Config, load_config, parse_cli_args
from pg_common import (
    PROBE_FILTER_SQL,
    iso_utc,
    json_response,
    validate_timezone,
)


logger = logging.getLogger("dashboard")

# ---------------------------------------------------------------------------
# Static layout
# ---------------------------------------------------------------------------

_HERE = pathlib.Path(__file__).resolve().parent
STATIC_DIR = _HERE / "dashboard_static"

# Top-level static files served at their basename; everything else is rejected
# (no directory listing, no path traversal — aiohttp.add_static handles vendor/
# safely with the same constraint).
_TOP_LEVEL_STATIC = {
    "styles.css":       "text/css; charset=utf-8",
    "app.jsx":          "text/babel; charset=utf-8",
    "components.jsx":   "text/babel; charset=utf-8",
    "tweaks-panel.jsx": "text/babel; charset=utf-8",
}

# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

VALID_RANGES = ("1d", "7d", "30d", "all")
VALID_GRAN = ("hour", "day", "week")


def range_bound_sql(range_key: str, tz: str = "UTC") -> tuple[str, list[Any]]:
    """
    Return a SQL fragment (starting with ' AND ...') and its params that
    restricts `timestamp` to the requested range, anchored in the given
    IANA timezone (default UTC).

    `(timestamp AT TIME ZONE %s)::date` is compared against a
    similarly-anchored `now()` date so the result does not depend on the
    Postgres session timezone and follows the caller's local "today".
    """
    if range_key == "1d":
        return (
            " AND (timestamp AT TIME ZONE %s)::date"
            " = (now() AT TIME ZONE %s)::date",
            [tz, tz],
        )
    if range_key == "7d":
        return (
            " AND (timestamp AT TIME ZONE %s)::date"
            " >= (now() AT TIME ZONE %s)::date - INTERVAL '6 days'",
            [tz, tz],
        )
    if range_key == "30d":
        return (
            " AND (timestamp AT TIME ZONE %s)::date"
            " >= (now() AT TIME ZONE %s)::date - INTERVAL '29 days'",
            [tz, tz],
        )
    if range_key == "all":
        return ("", [])
    raise ValueError(f"invalid range: {range_key!r}")


def filter_sql(models: list[str], nodes: list[str]) -> tuple[str, list[Any]]:
    """
    Optional model / node_id filters. Empty list means "no filter on this
    dimension" (matches the design's "deselect all = show all" behavior).
    """
    sql, params = "", []
    if models:
        sql += " AND model = ANY(%s::text[])"
        params.append(models)
    if nodes:
        sql += " AND node_id = ANY(%s::text[])"
        params.append(nodes)
    return sql, params


def pick_granularity(range_key: str, span_seconds: float | None) -> str:
    """
    Granularity rule:
        1d  -> 'hour'
        7d  -> 'day'
        30d -> 'day'
        all -> 'day' if data span <= 90 days else 'week'
    """
    if range_key == "1d":
        return "hour"
    if range_key in ("7d", "30d"):
        return "day"
    if range_key == "all":
        if span_seconds is None:
            return "day"
        return "day" if span_seconds <= 90 * 86400 else "week"
    raise ValueError(f"invalid range: {range_key!r}")


def bucket_label(key: datetime, gran: str) -> str:
    """Server-side label so the client never has to know about granularity."""
    if gran == "hour":
        return key.strftime("%H:%M")
    if gran == "day":
        return key.strftime("%b %-d")
    if gran == "week":
        return "Wk " + key.strftime("%b %-d")
    return key.isoformat()


def bucket_key(key: datetime, gran: str) -> str:
    """Stable key used by the chart React component for React keys / sort."""
    if gran == "hour":
        return key.strftime("%Y-%m-%dT%H")
    return key.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def _parse_range(request: web.Request) -> str:
    r = request.query.get("range", "7d")
    if r not in VALID_RANGES:
        raise web.HTTPBadRequest(reason=f"range must be one of {VALID_RANGES}")
    return r


def _parse_filters(request: web.Request) -> tuple[list[str], list[str]]:
    models = [m for m in request.query.getall("model", []) if m]
    nodes  = [n for n in request.query.getall("node",  []) if n]
    return models, nodes


def _parse_tz(request: web.Request) -> str:
    """
    Parse and validate the `tz` query parameter against the IANA timezone
    whitelist. Defaults to 'UTC' when missing. Rejects unknown values so
    arbitrary strings never reach Postgres `AT TIME ZONE`.
    """
    return validate_timezone(request.query.get("tz") or "UTC")


def _iso(ts: datetime) -> str:
    """Render a UTC timestamptz as ISO with trailing 'Z'."""
    return iso_utc(ts)


def _json(payload: Any, status: int = 200) -> web.Response:
    return json_response(payload, status)


# ---------------------------------------------------------------------------
# Handlers — static
# ---------------------------------------------------------------------------

async def handle_healthz(_: web.Request) -> web.Response:
    """Liveness probe — no DB touch, kept cheap for launchd respawn checks."""
    return _json({"status": "ok"})


async def handle_index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


def _make_static_handler(filename: str, content_type: str):
    """Closure that serves a single pinned filename from STATIC_DIR."""
    path = STATIC_DIR / filename

    async def handler(_: web.Request) -> web.FileResponse:
        return web.FileResponse(path, headers={"Content-Type": content_type})

    return handler


# ---------------------------------------------------------------------------
# Handlers — API
# ---------------------------------------------------------------------------

async def handle_meta(request: web.Request) -> web.Response:
    pool = request.app["pool"]
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT DISTINCT model FROM token_usage WHERE {PROBE_FILTER_SQL} ORDER BY model"
            )
            models = [r[0] for r in await cur.fetchall()]
            await cur.execute(
                f"SELECT DISTINCT node_id FROM token_usage WHERE {PROBE_FILTER_SQL} ORDER BY node_id"
            )
            hosts = [r[0] for r in await cur.fetchall()]

    return _json({
        "now":    _iso(datetime.now(timezone.utc)),
        "models": models,
        "hosts":  hosts,
    })


async def handle_kpi(request: web.Request) -> web.Response:
    pool = request.app["pool"]
    tz = _parse_tz(request)
    models, nodes = _parse_filters(request)
    fsql, fparams = filter_sql(models, nodes)

    # All day boundaries are computed in the caller's local tz so that
    # "today" / "yesterday" match the viewer's wall clock, not the server's
    # UTC offset. The resulting aware datetimes pass through psycopg as
    # timestamptz and compare correctly against the stored UTC timestamps.
    zi = ZoneInfo(tz)
    now_local = datetime.now(zi)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    yesterday_cutoff = yesterday_start + (now_local - today_start)
    spark_start = (today_start - timedelta(days=13))

    kpi_sql = (
        "SELECT"
        "  COALESCE(SUM(total_tokens), 0)::bigint AS tok,"
        "  COUNT(*)                                AS req,"
        "  COALESCE(AVG(response_ms), 0)           AS avg_ms,"
        "  COUNT(DISTINCT model)                   AS models"
        " FROM token_usage"
        f" WHERE {PROBE_FILTER_SQL}"
        " AND timestamp >= %s AND timestamp < %s"
        + fsql
    )

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(kpi_sql, [today_start, now_local, *fparams])
            tok, req, avg_ms, models_ct = (await cur.fetchone()) or (0, 0, 0.0, 0)
            today = {"tok": int(tok), "req": int(req), "avgMs": float(avg_ms), "models": int(models_ct)}

            await cur.execute(kpi_sql, [yesterday_start, yesterday_cutoff, *fparams])
            tok, req, avg_ms, models_ct = (await cur.fetchone()) or (0, 0, 0.0, 0)
            yest = {"tok": int(tok), "req": int(req), "avgMs": float(avg_ms), "models": int(models_ct)}

            await cur.execute(
                "SELECT"
                "  (timestamp AT TIME ZONE %s)::date AS day,"
                "  COALESCE(SUM(total_tokens), 0)::bigint AS tok,"
                "  COUNT(*) AS req,"
                "  COALESCE(AVG(response_ms), 0) AS ms,"
                "  COUNT(DISTINCT model) AS models"
                " FROM token_usage"
                f" WHERE {PROBE_FILTER_SQL}"
                "   AND timestamp >= %s"
                + fsql +
                " GROUP BY day ORDER BY day ASC",
                [tz, spark_start, *fparams],
            )
            rows = await cur.fetchall()

    spark = [
        {"day": str(row[0]), "tok": int(row[1]), "req": int(row[2]),
         "ms": float(row[3]), "models": int(row[4])}
        for row in rows
    ]

    return _json({
        "today": today,
        "yesterday_same_time": yest,
        "sparkline_14d": spark,
    })


async def handle_buckets(request: web.Request) -> web.Response:
    pool = request.app["pool"]
    tz = _parse_tz(request)
    range_key = _parse_range(request)
    models, nodes = _parse_filters(request)
    fsql, fparams = filter_sql(models, nodes)
    rsql, rparams = range_bound_sql(range_key, tz)

    span_seconds: float | None = None
    if range_key == "all":
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp)))"
                    " FROM token_usage"
                    f" WHERE {PROBE_FILTER_SQL}" + fsql,
                    fparams,
                )
                row = await cur.fetchone()
                span_seconds = float(row[0]) if row and row[0] is not None else 0.0

    gran = pick_granularity(range_key, span_seconds)
    if gran not in VALID_GRAN:
        raise web.HTTPInternalServerError(reason="bad granularity")

    sql = (
        f"SELECT date_trunc('{gran}', timestamp AT TIME ZONE %s) AS bucket,"
        "       model,"
        "       COALESCE(SUM(total_tokens), 0)::bigint AS tok,"
        "       COUNT(*)::bigint                       AS calls"
        " FROM token_usage"
        f" WHERE {PROBE_FILTER_SQL}" + rsql + fsql +
        " GROUP BY bucket, model"
        " ORDER BY bucket ASC, tok DESC"
    )

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, [tz, *rparams, *fparams])
            rows = await cur.fetchall()

    buckets: dict[str, dict[str, Any]] = {}
    for bucket_dt, model, tok, calls in rows:
        # bucket_dt comes back as a naive datetime — wall-clock midnight in
        # the requested tz, returned by `date_trunc(... AT TIME ZONE tz)`.
        # Label/key formatting via strftime ignores tzinfo, so the attached
        # UTC tag is purely cosmetic.
        if bucket_dt.tzinfo is None:
            bucket_dt = bucket_dt.replace(tzinfo=timezone.utc)
        k = bucket_key(bucket_dt, gran)
        b = buckets.get(k)
        if b is None:
            b = {
                "key":     k,
                "label":   bucket_label(bucket_dt, gran),
                "byModel": {},
                "total":   0,
                "calls":   0,
            }
            buckets[k] = b
        b["byModel"][model] = int(tok)
        b["total"] += int(tok)
        b["calls"] += int(calls)

    out = sorted(buckets.values(), key=lambda b: b["key"])
    return _json({"granularity": gran, "buckets": out})


async def handle_leaderboard(request: web.Request) -> web.Response:
    pool = request.app["pool"]
    tz = _parse_tz(request)
    range_key = _parse_range(request)
    models, nodes = _parse_filters(request)
    fsql, fparams = filter_sql(models, nodes)
    rsql, rparams = range_bound_sql(range_key, tz)

    sql = (
        "SELECT model,"
        "       COUNT(*)::bigint                       AS calls,"
        "       COALESCE(SUM(prompt_tokens), 0)::bigint     AS prompt,"
        "       COALESCE(SUM(completion_tokens), 0)::bigint AS completion,"
        "       COALESCE(SUM(total_tokens), 0)::bigint      AS total,"
        "       COALESCE(AVG(response_ms), 0)               AS avg_ms"
        " FROM token_usage"
        f" WHERE {PROBE_FILTER_SQL}" + rsql + fsql +
        " GROUP BY model"
        " ORDER BY total DESC, model ASC"
    )

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, [*rparams, *fparams])
            rows = await cur.fetchall()

    return _json([
        {"model": m, "calls": int(c), "prompt": int(p), "completion": int(co),
         "total": int(t), "avgMs": float(a)}
        for (m, c, p, co, t, a) in rows
    ])


async def handle_by_host(request: web.Request) -> web.Response:
    pool = request.app["pool"]
    tz = _parse_tz(request)
    range_key = _parse_range(request)
    models, nodes = _parse_filters(request)
    fsql, fparams = filter_sql(models, nodes)
    rsql, rparams = range_bound_sql(range_key, tz)

    sql = (
        "SELECT node_id, model,"
        "       COALESCE(SUM(total_tokens), 0)::bigint AS tok,"
        "       COUNT(*)::bigint                       AS calls"
        " FROM token_usage"
        f" WHERE {PROBE_FILTER_SQL}" + rsql + fsql +
        " GROUP BY node_id, model"
    )

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, [*rparams, *fparams])
            rows = await cur.fetchall()

    hosts: dict[str, dict[str, Any]] = {}
    for node_id, model, tok, calls in rows:
        h = hosts.get(node_id)
        if h is None:
            h = {"host": node_id, "calls": 0, "total": 0, "byModel": {}}
            hosts[node_id] = h
        h["byModel"][model] = int(tok)
        h["total"] += int(tok)
        h["calls"] += int(calls)

    return _json(sorted(hosts.values(), key=lambda h: (-h["total"], h["host"])))


async def handle_rows(request: web.Request) -> web.Response:
    pool = request.app["pool"]
    models, nodes = _parse_filters(request)
    fsql, fparams = filter_sql(models, nodes)

    since_ts = request.query.get("since_ts")
    since_id = request.query.get("since_id")
    try:
        limit = max(1, min(500, int(request.query.get("limit", "50"))))
    except ValueError:
        raise web.HTTPBadRequest(reason="limit must be an integer")

    cols = (
        "event_id, timestamp, node_id, model,"
        " prompt_tokens, completion_tokens, total_tokens,"
        " response_ms, endpoint, status_code"
    )

    if since_ts is not None and since_id is not None:
        # Polling path — oldest-first so wire order is stable across batch sizes.
        try:
            cursor_ts = datetime.fromisoformat(since_ts.replace("Z", "+00:00"))
        except ValueError:
            raise web.HTTPBadRequest(reason="since_ts must be ISO 8601")
        sql = (
            f"SELECT {cols} FROM token_usage"
            f" WHERE {PROBE_FILTER_SQL}"
            "   AND (timestamp, event_id) > (%s, %s)"
            + fsql +
            " ORDER BY timestamp ASC, event_id ASC LIMIT %s"
        )
        params = [cursor_ts, since_id, *fparams, limit]
    else:
        # Cold load — newest-first for direct display.
        sql = (
            f"SELECT {cols} FROM token_usage"
            f" WHERE {PROBE_FILTER_SQL}" + fsql +
            " ORDER BY timestamp DESC, event_id DESC LIMIT %s"
        )
        params = [*fparams, limit]

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

    out = [_row_to_json(r) for r in rows]
    return _json(out)


def _row_to_json(row: tuple) -> dict[str, Any]:
    (event_id, ts, node_id, model, prompt, completion, total,
     response_ms, endpoint, status_code) = row
    return {
        "id":         event_id,
        "ts":         _iso(ts),
        "host":       node_id,
        "model":      model,
        "prompt":     int(prompt or 0),
        "completion": int(completion or 0),
        "total":      int(total or 0),
        "ms":         float(response_ms or 0.0),
        "endpoint":   endpoint,
        "status":     int(status_code or 0),
    }


# ---------------------------------------------------------------------------
# App factory + lifecycle
# ---------------------------------------------------------------------------

def create_app(cfg: Config, pool_factory=None) -> web.Application:
    """
    Build the aiohttp app. `pool_factory` lets tests inject a fake pool;
    when None, a real psycopg AsyncConnectionPool is created in cleanup_ctx.
    """
    app = web.Application()
    app["config"] = cfg

    async def lifecycle(app: web.Application):
        if pool_factory is not None:
            pool = pool_factory()
            app["pool"] = pool
            try:
                yield
            finally:
                close = getattr(pool, "close", None)
                if close is not None:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
            return

        # main() already enforced TOKEN_SIDECAR_QUERY_DSN; assert here to keep
        # this code path self-checked under tests that call create_app() directly.
        dsn = os.environ["TOKEN_SIDECAR_QUERY_DSN"].strip()

        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(conninfo=dsn, min_size=1, max_size=4, open=False)
        await pool.open()
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    await cur.fetchone()
        except Exception:
            await pool.close()
            raise  # surface as non-zero exit so launchd KeepAlive respawns

        app["pool"] = pool
        try:
            yield
        finally:
            await pool.close()

    app.cleanup_ctx.append(lifecycle)

    # ── routes ────────────────────────────────────────────────────────────
    app.router.add_get("/healthz", handle_healthz)

    app.router.add_get("/",        handle_index)
    app.router.add_get("/index.html", handle_index)
    for name, ctype in _TOP_LEVEL_STATIC.items():
        app.router.add_get(f"/{name}", _make_static_handler(name, ctype))

    app.router.add_static("/vendor/", path=str(STATIC_DIR / "vendor"),
                          show_index=False, follow_symlinks=False)

    app.router.add_get("/api/meta",        handle_meta)
    app.router.add_get("/api/kpi",         handle_kpi)
    app.router.add_get("/api/buckets",     handle_buckets)
    app.router.add_get("/api/leaderboard", handle_leaderboard)
    app.router.add_get("/api/by-host",     handle_by_host)
    app.router.add_get("/api/rows",        handle_rows)

    return app


def main() -> None:
    cli_args = parse_cli_args()
    cfg = load_config(cli_args.config)

    numeric_level = getattr(logging, cfg.log_level, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    dsn = os.environ.get("TOKEN_SIDECAR_QUERY_DSN", "").strip()
    if not dsn:
        sys.stderr.write(
            "ERROR: TOKEN_SIDECAR_QUERY_DSN is not set.\n"
            "       The dashboard cannot start without a read-side Postgres DSN.\n"
            "       Add it to ~/.token_sidecar/env.sh (or your shell init) and try again.\n"
        )
        sys.exit(0)

    logger.info(
        "Starting dashboard on %s:%d (UTC time semantics; postgres reader DSN host=%s)",
        cfg.dashboard.listen_host, cfg.dashboard.listen_port,
        _safe_host_from_dsn(dsn),
    )
    app = create_app(cfg)
    web.run_app(
        app,
        host=cfg.dashboard.listen_host,
        port=cfg.dashboard.listen_port,
        print=None,
    )


def _safe_host_from_dsn(dsn: str) -> str:
    """Best-effort host extraction for the startup log without leaking secrets."""
    try:
        from urllib.parse import urlparse
        return urlparse(dsn).hostname or "?"
    except Exception:
        return "?"


if __name__ == "__main__":
    main()
