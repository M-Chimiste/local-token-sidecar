"""HTTP and aggregation tests for api/token_oracle_api.py."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import token_oracle_api
from config_loader import CentralDatabaseConfig, Config, OracleConfig

pytestmark = pytest.mark.asyncio


NOW = datetime(2026, 6, 2, 14, 31, tzinfo=timezone.utc)
NY = ZoneInfo("America/New_York")


def _row(event_id, ts, node, model, prompt, completion, total,
         endpoint="/v1/chat/completions"):
    return {
        "event_id": event_id,
        "timestamp": ts,
        "node_id": node,
        "model": model,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "endpoint": endpoint,
    }


SAMPLE_ROWS = [
    _row("e1", datetime(2026, 6, 2, 10, 15, tzinfo=timezone.utc), "nyx", "probe-keeper", 70, 30, 100),
    _row("e2", datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc), "athena", "gemma", 140, 60, 200),
    _row("e3", datetime(2026, 6, 2, 14, 20, tzinfo=timezone.utc), "mnemosyne", "minimax", 300, 200, 500),
    _row("e4", datetime(2026, 6, 2, 14, 30, tzinfo=timezone.utc), "mnemosyne", "qwen", 200, 100, 300),
    _row("permission-probe-abc", datetime(2026, 6, 2, 14, 30, tzinfo=timezone.utc), "athena", "probe", 0, 0, 999, "/probe"),
    _row("e5", datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc), "athena", "gemma", 400, 300, 700),
    _row("e6", datetime(2026, 5, 31, 15, 0, tzinfo=timezone.utc), "metis", "glm", 350, 250, 600),
]


class FakeCursor:
    def __init__(self, pool):
        self.pool = pool
        self._results = []

    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass

    async def execute(self, sql, params=None):
        self.pool.calls.append({"sql": sql, "params": list(params or [])})
        self._results = self.pool.dispatch(sql, list(params or []))

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
        self.calls = []

    def connection(self):
        return FakeConn(self)

    async def close(self):
        pass

    def live_rows(self):
        return [
            row for row in self.rows
            if row["endpoint"] != "/probe"
            and row["model"] != "probe"
            and not row["event_id"].startswith("permission-probe-")
        ]

    def in_range(self, start, end):
        return [
            row for row in self.live_rows()
            if start <= row["timestamp"] < end
        ]

    def dispatch(self, sql, params):
        if "oracle:today_totals" in sql:
            rows = self.in_range(params[0], params[1])
            return [(
                sum(row["prompt_tokens"] for row in rows),
                sum(row["completion_tokens"] for row in rows),
                sum(row["total_tokens"] for row in rows),
                len(rows),
            )]
        if "oracle:node_totals" in sql:
            rows = self.in_range(params[0], params[1])
            totals = {}
            for row in rows:
                totals[row["node_id"]] = totals.get(row["node_id"], 0) + row["total_tokens"]
            return sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        if "oracle:hourly" in sql:
            tz = ZoneInfo(params[0])
            rows = self.in_range(params[1], params[2])
            totals = {}
            for row in rows:
                hour = row["timestamp"].astimezone(tz).hour
                totals[hour] = totals.get(hour, 0) + row["total_tokens"]
            return sorted(totals.items())
        if "oracle:span" in sql:
            tz = ZoneInfo(params[0])
            rows = self.in_range(params[2], params[3])
            if not rows:
                return [(None, None)]
            stamps = [row["timestamp"].astimezone(tz).replace(tzinfo=None) for row in rows]
            return [(min(stamps), max(stamps))]
        if "oracle:recent" in sql:
            start, end = params
            rows = [
                row for row in self.live_rows()
                if start <= row["timestamp"] <= end
            ]
            grouped = {}
            for row in rows:
                total, last_ts = grouped.get(row["node_id"], (0, row["timestamp"]))
                grouped[row["node_id"]] = (
                    total + row["total_tokens"],
                    max(last_ts, row["timestamp"]),
                )
            return [
                (node, total, last_ts)
                for node, (total, last_ts) in sorted(
                    grouped.items(),
                    key=lambda item: (-item[1][1].timestamp(), item[0]),
                )
            ]
        if "oracle:models" in sql:
            start, end, node = params
            rows = [
                row for row in self.in_range(start, end)
                if row["node_id"] == node
            ]
            totals = {}
            for row in rows:
                totals[row["model"]] = totals.get(row["model"], 0) + row["total_tokens"]
            return sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        if "oracle:daily_totals" in sql:
            tz = ZoneInfo(params[0])
            totals = {}
            for row in self.live_rows():
                day = row["timestamp"].astimezone(tz).date()
                totals[day] = totals.get(day, 0) + row["total_tokens"]
            return sorted(totals.items())
        return []


class ErrorPool(FakePool):
    def dispatch(self, sql, params):
        raise RuntimeError("simulated postgres failure")


def _cfg() -> Config:
    return Config(
        listen_host="localhost",
        listen_port=1240,
        upstream_url="http://localhost:1234",
        database_path=Path("/tmp/test-token-sidecar.db"),
        log_level="INFO",
        node_id="test",
        central=CentralDatabaseConfig(),
        oracle=OracleConfig(
            timezone="America/New_York",
            ascendant_window_seconds=120,
            nodes=("nyx", "mnemosyne", "athena", "metis"),
        ),
        _raw={},
    )


@pytest.fixture
def fake_pool():
    return FakePool(SAMPLE_ROWS)


@pytest_asyncio.fixture
async def client(aiohttp_client, fake_pool):
    app = token_oracle_api.create_app(
        _cfg(),
        pool_factory=lambda: fake_pool,
        now_factory=lambda: NOW,
    )
    return await aiohttp_client(app)


async def test_health_does_not_touch_pool(client, fake_pool):
    r = await client.get("/health")
    assert r.status == 200
    assert await r.json() == {"status": "ok"}
    assert fake_pool.calls == []


async def test_metrics_returns_expected_shape_and_values(client):
    r = await client.get("/metrics")
    assert r.status == 200
    j = await r.json()

    assert set(j) == {
        "ok", "ts", "today", "rate_per_min", "ascendant",
        "zenith", "span", "models", "nodes", "hourly", "trend",
        "high_water", "streak_days",
    }
    assert j["ok"] is True
    assert j["ts"] == "2026-06-02T14:31:00Z"
    assert j["today"] == {"total": 1100, "prompt": 710, "completion": 390, "requests": 4}
    assert j["rate_per_min"] == 150
    assert j["ascendant"] == "mnemosyne"
    assert j["zenith"] == {"hour": 10, "tokens": 800}
    assert j["span"] == {"first": "06:15", "last": "10:30"}
    assert j["models"] == [
        {"name": "minimax", "total": 500},
        {"name": "qwen", "total": 300},
    ]
    assert j["hourly"][6] == 100
    assert j["hourly"][9] == 200
    assert j["hourly"][10] == 800
    assert len(j["hourly"]) == 24


async def test_metrics_stable_node_order_and_live_flag(client):
    r = await client.get("/metrics")
    nodes = (await r.json())["nodes"]
    assert [node["name"] for node in nodes] == ["nyx", "mnemosyne", "athena", "metis"]
    by_name = {node["name"]: node for node in nodes}
    assert by_name["nyx"] == {"name": "nyx", "total": 100, "live": False}
    assert by_name["mnemosyne"] == {"name": "mnemosyne", "total": 800, "live": True}
    assert by_name["athena"] == {"name": "athena", "total": 200, "live": False}
    assert by_name["metis"] == {"name": "metis", "total": 0, "live": False}


async def test_node_totals_orders_by_aggregate_alias(client, fake_pool):
    await client.get("/metrics")
    sql = next(call["sql"] for call in fake_pool.calls if "oracle:node_totals" in call["sql"])
    assert "AS tokens" in sql
    assert "ORDER BY tokens DESC, node_id ASC" in sql
    assert "ORDER BY total_tokens" not in sql


async def test_metrics_history_fields(client):
    r = await client.get("/metrics")
    j = await r.json()
    assert j["trend"] == {"mean": 186, "delta_pct": 492, "phase": 1.0}
    assert j["high_water"] == 1100
    assert j["streak_days"] == 3


async def test_metrics_idle_when_no_recent_activity(aiohttp_client):
    pool = FakePool([
        _row("old", NOW - timedelta(hours=2), "athena", "gemma", 10, 5, 15),
    ])
    app = token_oracle_api.create_app(
        _cfg(),
        pool_factory=lambda: pool,
        now_factory=lambda: NOW,
    )
    client = await aiohttp_client(app)
    r = await client.get("/metrics")
    j = await r.json()
    assert j["ascendant"] is None
    assert j["models"] == []
    assert all(node["live"] is False for node in j["nodes"])


async def test_metrics_empty_db_returns_safe_defaults(aiohttp_client):
    app = token_oracle_api.create_app(
        _cfg(),
        pool_factory=lambda: FakePool([]),
        now_factory=lambda: NOW,
    )
    client = await aiohttp_client(app)
    r = await client.get("/metrics")
    j = await r.json()
    assert j["ok"] is True
    assert j["today"]["total"] == 0
    assert j["hourly"] == [0] * 24
    assert j["zenith"] == {"hour": None, "tokens": 0}
    assert j["span"] == {"first": None, "last": None}
    assert j["trend"] == {"mean": 0, "delta_pct": 0, "phase": 0.5}


async def test_metrics_db_error_soft_fails_with_parseable_defaults(aiohttp_client):
    app = token_oracle_api.create_app(
        _cfg(),
        pool_factory=lambda: ErrorPool([]),
        now_factory=lambda: NOW,
    )
    client = await aiohttp_client(app)
    r = await client.get("/metrics")
    j = await r.json()
    assert r.status == 200
    assert j["ok"] is False
    assert j["today"] == {"total": 0, "prompt": 0, "completion": 0, "requests": 0}
    assert j["ascendant"] is None
