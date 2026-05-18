"""
Tests for sidecar.py — HTTP proxy forwarding and token usage interception.

Each test builds its own sidecar app pointing at the mock upstream it needs.
Uses pytest-aiohttp's aiohttp_client fixture to run TestClient against TestServer.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import db as _db
import sidecar
from sidecar import create_app


# ---------------------------------------------------------------------------
# Constants / shared test data
# ---------------------------------------------------------------------------

UPSTREAM_USAGE = {
    "prompt_tokens": 10,
    "completion_tokens": 5,
    "total_tokens": 15,
}

CHAT_UPSTREAM_BODY = {
    "id": "chatcmpl-test-001",
    "object": "chat.completion",
    "choices": [{
        "message": {"role": "assistant", "content": "hello"},
        "finish_reason": "stop",
        "index": 0,
    }],
    "usage": UPSTREAM_USAGE,
}

COMP_UPSTREAM_BODY = {
    "id": "cmpl-test-001",
    "object": "text_completion",
    "choices": [{"text": "hello", "finish_reason": "stop", "index": 0}],
    "usage": UPSTREAM_USAGE,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Fresh temp SQLite DB, initialised with schema."""
    p = str(tmp_path / "test.db")
    _db.init_db(p)
    return p


def make_upstream_server(path: str, response_body: dict) -> TestServer:
    """Build a mock LM Studio server for the given path and JSON response body."""
    app = web.Application()

    async def handler(request: web.Request) -> web.Response:
        await request.read()
        return web.Response(
            body=json.dumps(response_body).encode(),
            content_type="application/json",
        )

    app.router.add_post(path, handler)
    return TestServer(app)


@pytest.fixture
def upstream_chat_server():
    """Mock LM Studio server for /v1/chat/completions."""
    return make_upstream_server("/v1/chat/completions", CHAT_UPSTREAM_BODY)


@pytest.fixture
def upstream_comp_server():
    """Mock LM Studio server for /v1/completions."""
    return make_upstream_server("/v1/completions", COMP_UPSTREAM_BODY)


@pytest.fixture
def error_upstream_server():
    """Mock LM Studio that raises ConnectionError on every request."""
    app = web.Application()

    async def handler(request: web.Request) -> web.Response:
        raise ConnectionError("simulated upstream failure")

    app.router.add_post("/v1/chat/completions", handler)
    return TestServer(app)


# ---------------------------------------------------------------------------
# Helper to build sidecar app pointing at a specific upstream server
# ---------------------------------------------------------------------------

def make_sidecar_app(db_path: str, upstream_server: TestServer) -> web.Application:
    """
    Build the sidecar application with upstream_url set to the given test server.
    The client parameter is the live TestClient wrapping the mock upstream;
    we read its base URL via make_url('/').
    """
    # We need the real socket host/port of the upstream server. Since we can't
    # get that directly without making it live, we build the app with a dummy
    # and override per-test (see individual tests).
    config = {
        "proxy": {
            "listen_host": "localhost",
            "listen_port": 0,
            "upstream_url": "http://127.0.0.1:9999",   # placeholder; overridden below
        },
        "database": {"path": db_path},
        "logging": {"level": "CRITICAL"},
    }
    return create_app(config)


# ---------------------------------------------------------------------------
# Tests — Chat Completions endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_proxy_forwards_chat_request(db_path, aiohttp_client, upstream_chat_server):
    """
    A POST to /v1/chat/completions is forwarded to the upstream server and
    returns its response with correct fields.
    """
    # Create mock upstream client; we need its actual URL for sidecar's upstream_url
    mock_client = await aiohttp_client(upstream_chat_server)
    upstream_base = str(mock_client.make_url("")).rstrip("/")

    config = {
        "proxy": {"listen_host": "localhost", "listen_port": 0,
                  "upstream_url": upstream_base},
        "database": {"path": db_path},
        "logging": {"level": "CRITICAL"},
    }
    sidecar_app = create_app(Config.from_dict(config))

    async with TestClient(TestServer(sidecar_app)) as sc:
        resp = await sc.post(
            "/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["id"] == "chatcmpl-test-001"
        assert data["choices"][0]["message"]["content"] == "hello"


@pytest.mark.asyncio
async def test_proxy_returns_upstream_response_verbatim(db_path, aiohttp_client, upstream_chat_server):
    """The proxy returns the exact upstream body including the usage object."""
    mock_client = await aiohttp_client(upstream_chat_server)
    upstream_base = str(mock_client.make_url("")).rstrip("/")

    config = {
        "proxy": {"listen_host": "localhost", "listen_port": 0,
                  "upstream_url": upstream_base},
        "database": {"path": db_path},
        "logging": {"level": "CRITICAL"},
    }
    sidecar_app = create_app(Config.from_dict(config))

    async with TestClient(TestServer(sidecar_app)) as sc:
        resp = await sc.post(
            "/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 200
        body = await resp.json()
        # usage must be returned verbatim — proxy must not modify it
        assert body["usage"] == UPSTREAM_USAGE


@pytest.mark.asyncio
async def test_proxy_extracts_usage_and_logs_to_db(db_path, aiohttp_client, upstream_chat_server):
    """
    After one successful /v1/chat/completions call the SQLite DB contains exactly
    one row with correct model name, token counts and response_ms.
    """
    mock_client = await aiohttp_client(upstream_chat_server)
    upstream_base = str(mock_client.make_url("")).rstrip("/")

    config = {
        "proxy": {"listen_host": "localhost", "listen_port": 0,
                  "upstream_url": upstream_base},
        "database": {"path": db_path},
        "logging": {"level": "CRITICAL"},
    }
    sidecar_app = create_app(Config.from_dict(config))

    async with TestClient(TestServer(sidecar_app)) as sc:
        await sc.post(
            "/v1/chat/completions",
            json={"model": "my-test-model", "messages": [{"role": "user", "content": "hi"}]},
        )

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT model, prompt_tokens, completion_tokens, total_tokens "
            "FROM token_usage ORDER BY id",
        )
        rows = list(cur.fetchall())
    finally:
        conn.close()

    assert len(rows) == 1, f"Expected exactly 1 DB row; got {len(rows)}"
    model, pt, ct, tt = rows[0]
    assert model == "my-test-model"
    assert (pt, ct, tt) == (
        UPSTREAM_USAGE["prompt_tokens"],
        UPSTREAM_USAGE["completion_tokens"],
        UPSTREAM_USAGE["total_tokens"],
    )


@pytest.mark.asyncio
async def test_proxy_logs_outbox_metadata(db_path, aiohttp_client, upstream_chat_server):
    """A logged usage row includes node, endpoint, status, and event id."""
    mock_client = await aiohttp_client(upstream_chat_server)
    upstream_base = str(mock_client.make_url("")).rstrip("/")

    config = {
        "node": {"id": "athena"},
        "proxy": {"listen_host": "localhost", "listen_port": 0,
                  "upstream_url": upstream_base},
        "database": {"path": db_path},
        "logging": {"level": "CRITICAL"},
    }
    sidecar_app = create_app(Config.from_dict(config))

    async with TestClient(TestServer(sidecar_app)) as sc:
        resp = await sc.post(
            "/v1/chat/completions",
            json={"model": "my-test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 200

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT event_id, node_id, endpoint, status_code "
            "FROM token_usage ORDER BY id",
        )
        rows = list(cur.fetchall())
    finally:
        conn.close()

    assert len(rows) == 1
    event_id, node_id, endpoint, status_code = rows[0]
    assert event_id
    assert node_id == "athena"
    assert endpoint == "/v1/chat/completions"
    assert status_code == 200


@pytest.mark.asyncio
async def test_proxy_handles_upstream_error_gracefully(db_path, aiohttp_client):
    """
    When the upstream is unreachable (port 1 has nothing listening),
    the proxy returns HTTP 502 Bad Gateway and does not crash.
    """
    config = {
        "proxy": {"listen_host": "localhost", "listen_port": 0,
                  "upstream_url": "http://127.0.0.1:1"},   # nothing on port 1
        "database": {"path": db_path},
        "logging": {"level": "CRITICAL"},
    }
    sidecar_app = create_app(Config.from_dict(config))

    async with TestClient(TestServer(sidecar_app)) as sc:
        resp = await sc.post(
            "/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 502
        body = await resp.json()
        # error or detail field should mention the problem
        err_msg = (body.get("error") or "").lower() + (body.get("detail") or "").lower()
        assert any(w in err_msg for w in ("bad gateway", "connection", "refused"))


@pytest.mark.asyncio
async def test_proxy_unknown_path_returns_upstream_error(db_path, aiohttp_client):
    """Unmatched paths are proxied; unreachable upstream returns an error."""
    config = {
        "proxy": {"listen_host": "localhost", "listen_port": 0,
                  "upstream_url": "http://127.0.0.1:9999"},
        "database": {"path": db_path},
        "logging": {"level": "CRITICAL"},
    }
    sidecar_app = create_app(Config.from_dict(config))

    async with TestClient(TestServer(sidecar_app)) as sc:
        resp = await sc.get("/v1/models")
        assert resp.status == 502
        body = await resp.json()
        assert "error" in body


@pytest.mark.asyncio
async def test_proxy_returns_upstream_response_when_local_log_fails(
    db_path, aiohttp_client, upstream_chat_server, monkeypatch
):
    """SQLite/outbox failures must not change the response returned to clients."""
    mock_client = await aiohttp_client(upstream_chat_server)
    upstream_base = str(mock_client.make_url("")).rstrip("/")

    def fail_log(*args, **kwargs):
        raise sqlite3.OperationalError("disk is angry")

    monkeypatch.setattr(sidecar._db, "log_token_usage", fail_log)

    config = {
        "proxy": {"listen_host": "localhost", "listen_port": 0,
                  "upstream_url": upstream_base},
        "database": {"path": db_path},
        "logging": {"level": "CRITICAL"},
    }
    sidecar_app = create_app(Config.from_dict(config))

    async with TestClient(TestServer(sidecar_app)) as sc:
        resp = await sc.post(
            "/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 200
        assert await resp.json() == CHAT_UPSTREAM_BODY


@pytest.mark.asyncio
async def test_proxy_logs_completions_endpoint(db_path, aiohttp_client, upstream_comp_server):
    """The /v1/completions endpoint is forwarded and usage is logged to DB."""
    mock_client = await aiohttp_client(upstream_comp_server)
    upstream_base = str(mock_client.make_url("")).rstrip("/")

    config = {
        "proxy": {"listen_host": "localhost", "listen_port": 0,
                  "upstream_url": upstream_base},
        "database": {"path": db_path},
        "logging": {"level": "CRITICAL"},
    }
    sidecar_app = create_app(Config.from_dict(config))

    async with TestClient(TestServer(sidecar_app)) as sc:
        resp = await sc.post(
            "/v1/completions",
            json={"model": "completion-model", "prompt": "say hello"},
        )
        assert resp.status == 200

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT model, total_tokens FROM token_usage ORDER BY id")
        rows = list(cur.fetchall())
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "completion-model"
    assert rows[0][1] == UPSTREAM_USAGE["total_tokens"]


# ---------------------------------------------------------------------------
# Tests — Config validation (sync; no pytest-aiohttp needed)
# ---------------------------------------------------------------------------

from config_loader import load_config, parse_cli_args, Config
import tempfile
import os


def test_load_config_raises_on_missing_file():
    """Missing config file raises FileNotFoundError with a helpful message."""
    with pytest.raises(FileNotFoundError) as exc_info:
        load_config("/nonexistent/config.yaml")
    assert "Config file not found" in str(exc_info.value)
    assert "/nonexistent/config.yaml" in str(exc_info.value)


def test_load_config_raises_on_missing_required_keys(tmp_path):
    """Malformed config missing required keys raises ValueError listing them."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("proxy: {}\ndatabase: {}")
    with pytest.raises(ValueError) as exc_info:
        load_config(bad)
    msg = str(exc_info.value)
    assert "Missing required config keys" in msg
    # All four required keys should be listed (they all have no default)
    assert "proxy.listen_host" in msg


def test_load_config_returns_valid_Config_object():
    """Default config.yaml produces a properly typed Config object."""
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert isinstance(cfg.listen_port, int)
    assert isinstance(cfg.upstream_url, str)
    assert str(cfg.database_path).endswith(".db")


def test_config_log_level_defaults_to_info():
    """log_level is optional and defaults to INFO when absent from config."""
    cfg = load_config()
    assert cfg.log_level == "INFO"


def test_config_central_defaults_disabled():
    """Central reporting is opt-in and does not require a DSN by default."""
    cfg = Config.from_dict({
        "proxy": {
            "listen_host": "localhost",
            "listen_port": 0,
            "upstream_url": "http://127.0.0.1:1234",
        },
        "database": {"path": "/tmp/tokens.db"},
    })
    assert cfg.node_id == "local"
    assert cfg.central.enabled is False
    assert cfg.central.dsn is None


def test_config_central_requires_node_id(monkeypatch):
    """A stable node id is required when central Postgres sync is enabled."""
    monkeypatch.setenv("TOKEN_SIDECAR_POSTGRES_DSN", "postgresql://example")
    with pytest.raises(ValueError, match="node.id"):
        Config.from_dict({
            "proxy": {
                "listen_host": "localhost",
                "listen_port": 0,
                "upstream_url": "http://127.0.0.1:1234",
            },
            "database": {
                "path": "/tmp/tokens.db",
                "central": {"enabled": True},
            },
        })


def test_config_central_reads_dsn_from_env(monkeypatch):
    """Central sync resolves the configured DSN environment variable."""
    monkeypatch.setenv("TOKEN_SIDECAR_POSTGRES_DSN", "postgresql://example")
    cfg = Config.from_dict({
        "node": {"id": "athena"},
        "proxy": {
            "listen_host": "localhost",
            "listen_port": 0,
            "upstream_url": "http://127.0.0.1:1234",
        },
        "database": {
            "path": "/tmp/tokens.db",
            "central": {
                "enabled": True,
                "flush_interval_seconds": 1,
                "batch_size": 5,
            },
        },
    })
    assert cfg.node_id == "athena"
    assert cfg.central.enabled is True
    assert cfg.central.dsn == "postgresql://example"
    assert cfg.central.batch_size == 5


@pytest.mark.asyncio
async def test_sidecar_starts_with_custom_config_file(
    db_path, aiohttp_client, upstream_chat_server
):
    """Passing --config <path> uses the specified file instead of default."""
    # Build a custom config pointing at our mock upstream server
    mock_client = await aiohttp_client(upstream_chat_server)
    upstream_base = str(mock_client.make_url("")).rstrip("/")

    import yaml, pathlib as _p

    custom_dir = tempfile.mkdtemp()
    custom_config_path = os.path.join(custom_dir, "custom.yaml")
    custom_db_path = os.path.join(custom_dir, "test.db")

    with open(custom_config_path, "w") as f:
        yaml.dump({
            "proxy": {
                "listen_host": "localhost",
                "listen_port": 0,
                "upstream_url": upstream_base,
            },
            "database": {"path": custom_db_path},
            "logging": {"level": "CRITICAL"},
        }, f)

    # Initialise schema at the custom DB path
    _db.init_db(custom_db_path)

    # Load via custom path — should resolve to our mock server
    cfg = load_config(_p.Path(custom_config_path))
    assert cfg.upstream_url == upstream_base

    sidecar_app = create_app(cfg)
    async with TestClient(TestServer(sidecar_app)) as sc:
        resp = await sc.post(
            "/v1/chat/completions",
            json={"model": "custom-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 200

    # Cleanup
    os.unlink(custom_config_path)
    os.unlink(custom_db_path)
    os.rmdir(custom_dir)
