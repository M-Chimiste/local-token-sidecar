#!/usr/bin/env python3
"""
Token Oracle metrics API — read-only LAN service.

Serves compact token-only metrics for the ESP32 Token Oracle display. This is
a separate process from both the sidecar and the dashboard; it reads central
Postgres only and never writes usage rows.
"""

from __future__ import annotations

import logging
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from aiohttp import web


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import Config, OracleConfig, load_config, parse_cli_args
from pg_common import PROBE_FILTER_SQL, iso_utc, json_response, resolve_env_value


logger = logging.getLogger("token_oracle")

LOCAL_ENV_FILE = PROJECT_ROOT / ".env"
USER_ENV_FILE = pathlib.Path.home() / ".token_sidecar" / "env.sh"
POSTGRES_ENV_FILE = pathlib.Path.home() / ".token_sidecar" / "postgres.env"


def _dsn_env_files() -> tuple[pathlib.Path, ...]:
    return (LOCAL_ENV_FILE, USER_ENV_FILE, POSTGRES_ENV_FILE)


def _resolve_oracle_dsn(cfg: Config) -> str | None:
    return resolve_env_value(cfg.oracle.dsn_env, _dsn_env_files())


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_app(
    cfg: Config,
    pool_factory=None,
    now_factory: Callable[[], datetime] | None = None,
) -> web.Application:
    """
    Build the Token Oracle aiohttp app.

    `pool_factory` and `now_factory` let tests inject a fake Postgres pool and
    stable clock. The production lifecycle mirrors dashboard.py.
    """
    app = web.Application()
    app["config"] = cfg
    app["now_factory"] = now_factory or _now_utc

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

        dsn = _resolve_oracle_dsn(cfg)
        if not dsn:
            raise RuntimeError(f"{cfg.oracle.dsn_env} is not set.")

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
            raise

        app["pool"] = pool
        try:
            yield
        finally:
            await pool.close()

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/metrics", handle_metrics)
    return app


async def handle_health(_: web.Request) -> web.Response:
    """Cheap liveness probe; intentionally does not touch Postgres."""
    return json_response({"status": "ok"})


async def handle_metrics(request: web.Request) -> web.Response:
    cfg: Config = request.app["config"]
    now_factory: Callable[[], datetime] = request.app["now_factory"]
    now = _ensure_aware_utc(now_factory())

    try:
        payload = await build_metrics(request.app["pool"], cfg.oracle, now)
    except Exception:
        logger.exception("failed to build Token Oracle metrics")
        payload = default_metrics(cfg.oracle, now, ok=False)

    return json_response(payload)


async def build_metrics(pool, cfg: OracleConfig, now: datetime) -> dict[str, Any]:
    """Query Postgres and build the compact Token Oracle /metrics payload."""
    now = _ensure_aware_utc(now)
    local_now = now.astimezone(ZoneInfo(cfg.timezone))
    today_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start_local = today_start_local + timedelta(days=1)
    today_start = today_start_local.astimezone(timezone.utc)
    tomorrow_start = tomorrow_start_local.astimezone(timezone.utc)
    recent_start = now - timedelta(seconds=cfg.ascendant_window_seconds)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                /* oracle:today_totals */
                SELECT
                    COALESCE(SUM(prompt_tokens), 0)::bigint,
                    COALESCE(SUM(completion_tokens), 0)::bigint,
                    COALESCE(SUM(total_tokens), 0)::bigint,
                    COUNT(*)::bigint
                FROM token_usage
                WHERE {PROBE_FILTER_SQL}
                  AND timestamp >= %s
                  AND timestamp < %s
                """,
                [today_start, tomorrow_start],
            )
            prompt, completion, total, requests = (await cur.fetchone()) or (0, 0, 0, 0)

            await cur.execute(
                f"""
                /* oracle:node_totals */
                SELECT node_id, COALESCE(SUM(total_tokens), 0)::bigint AS tokens
                FROM token_usage
                WHERE {PROBE_FILTER_SQL}
                  AND timestamp >= %s
                  AND timestamp < %s
                GROUP BY node_id
                ORDER BY tokens DESC, node_id ASC
                """,
                [today_start, tomorrow_start],
            )
            node_rows = await cur.fetchall()

            await cur.execute(
                f"""
                /* oracle:hourly */
                SELECT
                    EXTRACT(HOUR FROM timestamp AT TIME ZONE %s)::integer AS hour_local,
                    COALESCE(SUM(total_tokens), 0)::bigint
                FROM token_usage
                WHERE {PROBE_FILTER_SQL}
                  AND timestamp >= %s
                  AND timestamp < %s
                GROUP BY hour_local
                ORDER BY hour_local ASC
                """,
                [cfg.timezone, today_start, tomorrow_start],
            )
            hourly_rows = await cur.fetchall()

            await cur.execute(
                f"""
                /* oracle:span */
                SELECT
                    MIN(timestamp AT TIME ZONE %s),
                    MAX(timestamp AT TIME ZONE %s)
                FROM token_usage
                WHERE {PROBE_FILTER_SQL}
                  AND timestamp >= %s
                  AND timestamp < %s
                """,
                [cfg.timezone, cfg.timezone, today_start, tomorrow_start],
            )
            span_row = await cur.fetchone()

            await cur.execute(
                f"""
                /* oracle:recent */
                SELECT
                    node_id,
                    COALESCE(SUM(total_tokens), 0)::bigint AS tokens,
                    MAX(timestamp) AS last_ts
                FROM token_usage
                WHERE {PROBE_FILTER_SQL}
                  AND timestamp >= %s
                  AND timestamp <= %s
                GROUP BY node_id
                ORDER BY last_ts DESC, node_id ASC
                """,
                [recent_start, now],
            )
            recent_rows = await cur.fetchall()

            ascendant = str(recent_rows[0][0]) if recent_rows else None
            if ascendant is not None:
                await cur.execute(
                    f"""
                    /* oracle:models */
                    SELECT model, COALESCE(SUM(total_tokens), 0)::bigint
                    FROM token_usage
                    WHERE {PROBE_FILTER_SQL}
                      AND timestamp >= %s
                      AND timestamp < %s
                      AND node_id = %s
                    GROUP BY model
                    ORDER BY COALESCE(SUM(total_tokens), 0) DESC, model ASC
                    """,
                    [today_start, tomorrow_start, ascendant],
                )
                model_rows = await cur.fetchall()
            else:
                model_rows = []

            await cur.execute(
                f"""
                /* oracle:daily_totals */
                SELECT
                    (timestamp AT TIME ZONE %s)::date AS day,
                    COALESCE(SUM(total_tokens), 0)::bigint
                FROM token_usage
                WHERE {PROBE_FILTER_SQL}
                GROUP BY day
                ORDER BY day ASC
                """,
                [cfg.timezone],
            )
            daily_rows = await cur.fetchall()

    hourly = _hourly(hourly_rows)
    zenith_hour, zenith_tokens = _zenith(hourly)
    first, last = _span(span_row)
    live_nodes = {str(row[0]) for row in recent_rows}
    recent_total = sum(int(row[1] or 0) for row in recent_rows)
    rate_per_min = int(round(recent_total / (cfg.ascendant_window_seconds / 60)))
    today_total = int(total or 0)
    daily_totals = _daily_totals(daily_rows)
    trend, high_water, streak_days = _history(daily_totals, local_now.date(), today_total)

    return {
        "ok": True,
        "ts": iso_utc(now),
        "today": {
            "total": today_total,
            "prompt": int(prompt or 0),
            "completion": int(completion or 0),
            "requests": int(requests or 0),
        },
        "rate_per_min": rate_per_min,
        "ascendant": ascendant,
        "zenith": {"hour": zenith_hour, "tokens": zenith_tokens},
        "span": {"first": first, "last": last},
        "models": [
            {"name": str(model), "total": int(tokens or 0)}
            for model, tokens in model_rows
        ],
        "nodes": _nodes(cfg.nodes, node_rows, live_nodes),
        "hourly": hourly,
        "trend": trend,
        "high_water": high_water,
        "streak_days": streak_days,
    }


def default_metrics(cfg: OracleConfig, now: datetime, ok: bool) -> dict[str, Any]:
    """Safe zero/default metric payload for empty data and soft failures."""
    now = _ensure_aware_utc(now)
    return {
        "ok": ok,
        "ts": iso_utc(now),
        "today": {"total": 0, "prompt": 0, "completion": 0, "requests": 0},
        "rate_per_min": 0,
        "ascendant": None,
        "zenith": {"hour": None, "tokens": 0},
        "span": {"first": None, "last": None},
        "models": [],
        "nodes": [{"name": node, "total": 0, "live": False} for node in cfg.nodes],
        "hourly": [0] * 24,
        "trend": {"mean": 0, "delta_pct": 0, "phase": 0.5},
        "high_water": 0,
        "streak_days": 0,
    }


def _ensure_aware_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _hourly(rows: list[tuple]) -> list[int]:
    hourly = [0] * 24
    for hour, tokens in rows:
        h = int(hour)
        if 0 <= h < 24:
            hourly[h] = int(tokens or 0)
    return hourly


def _zenith(hourly: list[int]) -> tuple[int | None, int]:
    tokens = max(hourly) if hourly else 0
    if tokens <= 0:
        return None, 0
    return hourly.index(tokens), tokens


def _span(row: tuple | None) -> tuple[str | None, str | None]:
    if not row:
        return None, None
    first, last = row
    if first is None or last is None:
        return None, None
    return _time_label(first), _time_label(last)


def _time_label(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    return str(value)[:5]


def _nodes(configured: tuple[str, ...], rows: list[tuple], live_nodes: set[str]) -> list[dict[str, Any]]:
    totals = {str(node): int(tokens or 0) for node, tokens in rows}
    names: list[str] = list(configured)
    for name in sorted(set(totals) | live_nodes):
        if name not in names:
            names.append(name)
    return [
        {"name": name, "total": totals.get(name, 0), "live": name in live_nodes}
        for name in names
    ]


def _daily_totals(rows: list[tuple]) -> dict[date, int]:
    totals: dict[date, int] = {}
    for day, tokens in rows:
        if isinstance(day, datetime):
            key = day.date()
        elif isinstance(day, date):
            key = day
        else:
            key = date.fromisoformat(str(day))
        totals[key] = int(tokens or 0)
    return totals


def _history(
    daily_totals: dict[date, int],
    today: date,
    today_total: int,
) -> tuple[dict[str, float | int], int, int]:
    totals = dict(daily_totals)
    totals[today] = today_total

    previous = [
        totals.get(today - timedelta(days=offset), 0)
        for offset in range(1, 8)
    ]
    mean = sum(previous) / 7
    if mean > 0:
        delta_pct = int(round(((today_total - mean) / mean) * 100))
        phase = round(max(0.0, min(1.0, today_total / (2 * mean))), 4)
    else:
        delta_pct = 0
        phase = 0.5

    high_water = max(totals.values(), default=0)
    streak = 0
    day = today
    while totals.get(day, 0) > 0:
        streak += 1
        day -= timedelta(days=1)

    return {"mean": int(round(mean)), "delta_pct": delta_pct, "phase": phase}, high_water, streak


def main() -> None:
    cli_args = parse_cli_args()
    cfg = load_config(cli_args.config)

    numeric_level = getattr(logging, cfg.log_level, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    dsn = _resolve_oracle_dsn(cfg)
    if not dsn:
        sys.stderr.write(
            f"ERROR: {cfg.oracle.dsn_env} is not set.\n"
            "       The Token Oracle API cannot start without a read-side Postgres DSN.\n"
            "       Set it in the environment, .env, ~/.token_sidecar/env.sh, or "
            "~/.token_sidecar/postgres.env and try again.\n"
        )
        sys.exit(0)

    logger.info(
        "Starting Token Oracle API on %s:%d (timezone=%s)",
        cfg.oracle.listen_host,
        cfg.oracle.listen_port,
        cfg.oracle.timezone,
    )
    app = create_app(cfg)
    web.run_app(
        app,
        host=cfg.oracle.listen_host,
        port=cfg.oracle.listen_port,
        print=None,
    )


if __name__ == "__main__":
    main()
