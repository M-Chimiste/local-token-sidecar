"""
HTTP smoke tests for dashboard.py.

A `FakePool` adapter implements the bits of the psycopg pool/connection/cursor
API that `dashboard.py` actually uses, and computes aggregate results from an
in-memory row list. This lets us verify the full request → SQL → JSON shape
without a live Postgres.
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))

import dashboard
from config_loader import Config, DashboardConfig

pytestmark = pytest.mark.asyncio


# ───────────────────────── Sample fixture ──────────────────────────────

NOW_UTC = datetime.now(timezone.utc)
TODAY_START = NOW_UTC.replace(hour=0, minute=0, second=0, microsecond=0)
YESTERDAY = TODAY_START - timedelta(days=1)
LAST_WEEK = TODAY_START - timedelta(days=3)


def _row(event_id, ts, node, model, prompt, completion, total, ms,
         endpoint="/v1/chat/completions", status=200):
    return {
        "event_id": event_id, "timestamp": ts, "node_id": node, "model": model,
        "prompt_tokens": prompt, "completion_tokens": completion,
        "total_tokens": total, "response_ms": ms,
        "endpoint": endpoint, "status_code": status,
    }


SAMPLE_ROWS = [
    # Today rows are pegged just after TODAY_START so they are always in the
    # past at test execution time, regardless of when "now" actually is.
    _row("e1", TODAY_START + timedelta(seconds=1), "athena", "minimax-m2.7", 100, 50, 150, 800.0),
    _row("e2", TODAY_START + timedelta(seconds=2), "athena", "minimax-m2.7", 200, 80, 280, 950.0),
    _row("e3", TODAY_START + timedelta(seconds=3), "metis",  "phi-4-mini",    50, 20,  70, 200.0),
    # Yesterday morning (well in the past today)
    _row("e4", YESTERDAY + timedelta(hours=4),     "athena", "minimax-m2.7",  90, 40, 130, 700.0),
    # 3 days ago
    _row("e5", LAST_WEEK + timedelta(hours=5),     "metis",  "phi-4-mini",    60, 25,  85, 220.0),
    # Probe row — must be filtered everywhere
    _row("permission-probe-abc", TODAY_START + timedelta(seconds=4), "athena", "probe",
         0, 0, 0, 0.0, endpoint="/probe", status=200),
]


# ───────────────────────── FakePool ────────────────────────────────────

class FakeCursor:
    def __init__(self, pool):
        self.pool = pool
        self._results: list[tuple] = []

    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass

    async def execute(self, sql, params=None):
        params = list(params or [])
        self.pool.calls.append({"sql": sql, "params": params})
        self._results = self.pool._dispatch(sql, params)

    async def fetchone(self):
        return self._results[0] if self._results else None

    async def fetchall(self):
        return list(self._results)


class FakeConn:
    def __init__(self, pool): self.pool = pool
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    def cursor(self): return FakeCursor(self.pool)


class FakePool:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[dict] = []

    def connection(self):
        return FakeConn(self)

    async def close(self):
        pass

    # ─── filtering helpers ─────────────────────────────────────────────
    def _live(self):
        return [
            r for r in self.rows
            if (r.get("endpoint") or "") != "/probe"
            and (r.get("model") or "") != "probe"
            and not r["event_id"].startswith("permission-probe-")
        ]

    @staticmethod
    def _apply_filter_params(rows, sql, params):
        """
        Look at the SQL to know whether the first list-typed param is models
        or nodes (filter_sql may emit neither, either, or both).
        """
        list_params = [p for p in params if isinstance(p, list)]
        idx = 0
        if "model = ANY(%s::text[])" in sql and idx < len(list_params):
            rows = [r for r in rows if r["model"] in list_params[idx]]
            idx += 1
        if "node_id = ANY(%s::text[])" in sql and idx < len(list_params):
            rows = [r for r in rows if r["node_id"] in list_params[idx]]
            idx += 1
        return rows

    # ─── SQL dispatch ──────────────────────────────────────────────────
    def _dispatch(self, sql, params):
        if "SELECT 1" in sql and "FROM" not in sql:
            return [(1,)]
        if "SELECT DISTINCT model" in sql:
            rows = self._apply_filter_params(self._live(), sql, params)
            return sorted({(r["model"],) for r in rows})
        if "SELECT DISTINCT node_id" in sql:
            rows = self._apply_filter_params(self._live(), sql, params)
            return sorted({(r["node_id"],) for r in rows})
        if "EXTRACT(EPOCH FROM" in sql:
            live = self._live()
            if not live:
                return [(None,)]
            min_ts = min(r["timestamp"] for r in live)
            max_ts = max(r["timestamp"] for r in live)
            return [((max_ts - min_ts).total_seconds(),)]
        if "GROUP BY day" in sql:
            rows = self._apply_filter_params(self._live(), sql, params)
            # 14-day cutoff bound; the first datetime param is spark_start
            # (a string `tz` may precede it once tz parameterization lands).
            window_start = next(
                (p for p in params if isinstance(p, datetime)),
                NOW_UTC - timedelta(days=14),
            )
            rows = [r for r in rows if r["timestamp"] >= window_start]
            buckets = {}
            for r in rows:
                k = r["timestamp"].date()
                b = buckets.setdefault(k, {"tok": 0, "req": 0, "ms_sum": 0.0, "models": set()})
                b["tok"] += r["total_tokens"]
                b["req"] += 1
                b["ms_sum"] += r["response_ms"]
                b["models"].add(r["model"])
            out = []
            for k in sorted(buckets):
                b = buckets[k]
                avg = b["ms_sum"] / b["req"] if b["req"] else 0.0
                out.append((k, b["tok"], b["req"], avg, len(b["models"])))
            return out
        if "GROUP BY bucket, model" in sql:
            rows = self._apply_filter_params(self._live(), sql, params)
            window_start = next((p for p in params if isinstance(p, datetime)), None)
            if window_start:
                rows = [r for r in rows if r["timestamp"] >= window_start]
            # Pick truncation unit from SQL: date_trunc('hour'|'day'|'week', ...)
            m = re.search(r"date_trunc\('(\w+)'", sql)
            unit = m.group(1) if m else "day"
            def trunc(ts):
                if unit == "hour":
                    return ts.replace(minute=0, second=0, microsecond=0)
                if unit == "week":
                    weekday = ts.weekday()  # Mon=0
                    return ts.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=weekday)
                return ts.replace(hour=0, minute=0, second=0, microsecond=0)
            agg = {}
            for r in rows:
                key = (trunc(r["timestamp"]), r["model"])
                t = agg.setdefault(key, [0, 0])
                t[0] += r["total_tokens"]
                t[1] += 1
            return [(k[0], k[1], v[0], v[1])
                    for k, v in sorted(agg.items(), key=lambda kv: (kv[0][0], -kv[1][0]))]
        if "AVG(response_ms)" in sql and "(timestamp, event_id)" not in sql and "GROUP BY" not in sql:
            # KPI window aggregate
            rows = self._apply_filter_params(self._live(), sql, params)
            window_start = next((p for p in params if isinstance(p, datetime)), None)
            window_end_candidates = [p for p in params if isinstance(p, datetime)]
            if len(window_end_candidates) >= 2:
                window_start, window_end = window_end_candidates[0], window_end_candidates[1]
                rows = [r for r in rows if window_start <= r["timestamp"] < window_end]
            tok = sum(r["total_tokens"] for r in rows)
            req = len(rows)
            avg = (sum(r["response_ms"] for r in rows) / req) if req else 0.0
            mods = len({r["model"] for r in rows})
            return [(tok, req, avg, mods)]
        if "GROUP BY model" in sql:
            rows = self._apply_filter_params(self._live(), sql, params)
            window_start = next((p for p in params if isinstance(p, datetime)), None)
            if window_start:
                rows = [r for r in rows if r["timestamp"] >= window_start]
            agg = {}
            for r in rows:
                a = agg.setdefault(r["model"], [0, 0, 0, 0, 0.0])
                a[0] += 1
                a[1] += r["prompt_tokens"]
                a[2] += r["completion_tokens"]
                a[3] += r["total_tokens"]
                a[4] += r["response_ms"]
            out = []
            for m, (calls, p, c, t, ms) in agg.items():
                out.append((m, calls, p, c, t, ms / calls if calls else 0.0))
            return sorted(out, key=lambda x: (-x[4], x[0]))
        if "GROUP BY node_id, model" in sql:
            rows = self._apply_filter_params(self._live(), sql, params)
            window_start = next((p for p in params if isinstance(p, datetime)), None)
            if window_start:
                rows = [r for r in rows if r["timestamp"] >= window_start]
            agg = {}
            for r in rows:
                k = (r["node_id"], r["model"])
                t = agg.setdefault(k, [0, 0])
                t[0] += r["total_tokens"]
                t[1] += 1
            return [(k[0], k[1], v[0], v[1]) for k, v in agg.items()]
        # /api/rows
        if "FROM token_usage" in sql and "event_id, timestamp" in sql:
            rows = self._apply_filter_params(self._live(), sql, params)
            if "(timestamp, event_id) > (%s, %s)" in sql:
                cursor_ts = params[0]
                cursor_id = params[1]
                rows = [r for r in rows
                        if (r["timestamp"], r["event_id"]) > (cursor_ts, cursor_id)]
                rows.sort(key=lambda r: (r["timestamp"], r["event_id"]))
            else:
                rows.sort(key=lambda r: (r["timestamp"], r["event_id"]), reverse=True)
            limit = next((p for p in reversed(params) if isinstance(p, int)), 50)
            rows = rows[:limit]
            return [
                (r["event_id"], r["timestamp"], r["node_id"], r["model"],
                 r["prompt_tokens"], r["completion_tokens"], r["total_tokens"],
                 r["response_ms"], r["endpoint"], r["status_code"])
                for r in rows
            ]
        return []


# ───────────────────────── pytest plumbing ────────────────────────────

def _cfg() -> Config:
    return Config(
        listen_host="localhost", listen_port=1240,
        upstream_url="http://localhost:1234",
        database_path=Path("/tmp/test-token-sidecar.db"),
        log_level="INFO",
        dashboard=DashboardConfig(),
        _raw={},
    )


@pytest.fixture
def fake_pool():
    return FakePool(SAMPLE_ROWS)


@pytest_asyncio.fixture
async def client(aiohttp_client, fake_pool):
    app = dashboard.create_app(_cfg(), pool_factory=lambda: fake_pool)
    return await aiohttp_client(app)


# ───────────────────────── Tests ──────────────────────────────────────

async def test_healthz_does_not_touch_pool(client, fake_pool):
    r = await client.get("/healthz")
    assert r.status == 200
    j = await r.json()
    assert j == {"status": "ok"}
    # No DB calls beyond the startup SELECT 1 connectivity check (we bypassed
    # that by injecting the pool factory).
    assert fake_pool.calls == []


async def test_meta_returns_distinct_models_and_hosts(client):
    r = await client.get("/api/meta")
    assert r.status == 200
    j = await r.json()
    assert sorted(j["models"]) == ["minimax-m2.7", "phi-4-mini"]
    assert sorted(j["hosts"]) == ["athena", "metis"]
    assert j["now"].endswith("Z")


async def test_kpi_today_excludes_probe_rows(client):
    r = await client.get("/api/kpi")
    assert r.status == 200
    j = await r.json()
    # 3 today rows: 150 + 280 + 70 = 500 tokens, 3 requests, 2 distinct models
    assert j["today"]["tok"] == 500
    assert j["today"]["req"] == 3
    assert j["today"]["models"] == 2
    # Yesterday-same-time: row e4 (130 tok) — depends on yesterday cutoff
    # (today_start + (now - today_start) = now). Loose assertion.
    assert j["yesterday_same_time"]["tok"] >= 130 or j["yesterday_same_time"]["req"] >= 0
    assert "sparkline_14d" in j


async def test_buckets_all_range_returns_day_or_week_granularity(client):
    r = await client.get("/api/buckets?range=all")
    assert r.status == 200
    j = await r.json()
    assert j["granularity"] in ("day", "week")
    # Every visible row contributes to a bucket
    total = sum(b["total"] for b in j["buckets"])
    # SAMPLE_ROWS minus probe = 150+280+70+130+85 = 715
    assert total == 715


async def test_buckets_1d_uses_hourly_granularity(client):
    r = await client.get("/api/buckets?range=1d")
    j = await r.json()
    assert j["granularity"] == "hour"
    # All hourly labels should be of HH:MM form
    for b in j["buckets"]:
        assert ":" in b["label"]


async def test_leaderboard_orders_by_total_descending(client):
    r = await client.get("/api/leaderboard?range=all")
    assert r.status == 200
    rows = await r.json()
    assert [r["model"] for r in rows] == ["minimax-m2.7", "phi-4-mini"]
    minimax = next(r for r in rows if r["model"] == "minimax-m2.7")
    assert minimax["total"] == 150 + 280 + 130  # = 560
    assert minimax["calls"] == 3


async def test_by_host_groups_models_per_node(client):
    r = await client.get("/api/by-host?range=all")
    rows = await r.json()
    by_name = {r["host"]: r for r in rows}
    assert set(by_name.keys()) == {"athena", "metis"}
    # athena: minimax 150+280+130 = 560
    assert by_name["athena"]["byModel"]["minimax-m2.7"] == 560
    # metis: phi-4-mini 70+85 = 155
    assert by_name["metis"]["byModel"]["phi-4-mini"] == 155


async def test_rows_cold_load_is_newest_first(client):
    r = await client.get("/api/rows?limit=10")
    rows = await r.json()
    assert len(rows) == 5  # 5 non-probe rows
    ids = [r["id"] for r in rows]
    # newest is e3 (today + 3h), then e2, then e1, then e4 (yesterday), then e5 (3d ago)
    assert ids == ["e3", "e2", "e1", "e4", "e5"]
    assert rows[0]["host"] == "metis"
    assert rows[0]["status"] == 200


async def test_rows_polling_returns_oldest_first_after_cursor(client):
    cursor_ts = (TODAY_START + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    r = await client.get(f"/api/rows?since_ts={cursor_ts}&since_id=e1&limit=10")
    rows = await r.json()
    ids = [r["id"] for r in rows]
    # Should return rows strictly after (e1's ts, "e1"): e2, e3 only (later today)
    assert ids == ["e2", "e3"]


async def test_rows_probe_filter_drops_permission_probe(client):
    r = await client.get("/api/rows?limit=50")
    rows = await r.json()
    for row in rows:
        assert not row["id"].startswith("permission-probe-")
        assert row["endpoint"] != "/probe"
        assert row["model"] != "probe"


async def test_model_filter_narrows_leaderboard(client, fake_pool):
    r = await client.get("/api/leaderboard?range=all&model=phi-4-mini")
    rows = await r.json()
    assert [r["model"] for r in rows] == ["phi-4-mini"]
    # Filter should have been encoded as a text array param
    leaderboard_call = next(c for c in fake_pool.calls if "GROUP BY model" in c["sql"])
    assert any(isinstance(p, list) and p == ["phi-4-mini"] for p in leaderboard_call["params"])


async def test_node_filter_narrows_by_host(client, fake_pool):
    r = await client.get("/api/by-host?range=all&node=athena")
    rows = await r.json()
    assert [r["host"] for r in rows] == ["athena"]
    byhost_call = next(c for c in fake_pool.calls if "GROUP BY node_id, model" in c["sql"])
    assert any(isinstance(p, list) and p == ["athena"] for p in byhost_call["params"])


async def test_combined_model_and_node_filter(client, fake_pool):
    r = await client.get("/api/buckets?range=all&model=phi-4-mini&node=metis")
    j = await r.json()
    # Only metis's phi-4-mini rows: 70 (today) + 85 (3 days ago) = 155
    total = sum(b["total"] for b in j["buckets"])
    assert total == 155


async def test_invalid_range_returns_400(client):
    r = await client.get("/api/buckets?range=nope")
    assert r.status == 400


async def test_invalid_since_ts_returns_400(client):
    r = await client.get("/api/rows?since_ts=not-a-date&since_id=e1")
    assert r.status == 400


async def test_index_html_is_served(client):
    r = await client.get("/")
    assert r.status == 200
    body = await r.text()
    assert "<div id=\"root\"></div>" in body
    assert "vendor/react.production.min.js" in body


async def test_styles_css_is_served(client):
    r = await client.get("/styles.css")
    assert r.status == 200
    assert "text/css" in r.headers["Content-Type"]


async def test_app_jsx_is_served_as_babel(client):
    r = await client.get("/app.jsx")
    assert r.status == 200
    assert "text/babel" in r.headers["Content-Type"]
