# Phase 2 — HTTP Proxy Core: Detailed Implementation Plan

**Project:** token-sidecar  
**Phase:** 2 of 7  
**Parent plan:** `../implementation_plan.md`  
**Goal:** Get the proxy forwarding requests and intercepting responses to log token usage.

---

## Context

The sidecar is an HTTP **man-in-the-middle** that:
1. Accepts client requests on a local port (`1240`)
2. Forwards them verbatim to LM Studio (`1234`)
3. Intercepts LM Studio's response, extracts `usage`, writes to SQLite
4. Returns the original upstream response unchanged to the caller

The client (Athena/Metis) points at `localhost:1240` instead of directly at `localhost:1234`. To the client it's just another LLM API endpoint.

**Key constraints:**
- Return LM Studio's response **byte-for-byte identical** — do not modify it
- Never crash on upstream errors; always return a well-formed HTTP error to the client
- All config values come from `config.yaml` (Phase 3 will make this explicit, but use them now)
- Use `aiohttp` for the proxy server

---

## Step 1 — Create Config Loader (`config.py`)

Extract loading and validation of `config.yaml` into a reusable module ahead of Phase 3. This avoids hardcoding while keeping things simple.

**File:** `~/Documents/hermes_projects/token_sidecar/config_loader.py`

```python
"""Lightweight config loader for token-sidecar."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = str(Path(__file__).parent / "config.yaml")


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load and return the config.yaml as a plain dict."""
    path = Path(config_path or os.environ.get("SIDECAR_CONFIG", DEFAULT_CONFIG_PATH))
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        cfg = yaml.safe_load(f)

    # Resolve ~ in database path
    db_path = Path(cfg["database"]["path"]).expanduser().resolve()
    cfg["database"]["path"] = str(db_path)
    return cfg


if __name__ == "__main__":
    import pprint, json
    pprint.pprint(load_config())
```

**Verification:**
```bash
cd ~/Documents/hermes_projects/token_sidecar
uv run python config_loader.py
# Should print parsed config.yaml with expanded database path
```

---

## Step 2 — Implement the Proxy Server (`sidecar.py`)

Replace the placeholder `sidecar.py` with a full implementation.

### Architecture

```
aiohttp web app on localhost:1240
    │
    ├── POST /v1/chat/completions  →  forward_to_upstream()
    ├── POST /v1/completions       →  forward_to_upstream()
    └── *   (any other path)       →  404 { "error": "Not found" }
```

### Imports

```python
import json
import time
import logging
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

import db as _db
from config_loader import load_config
```

### Logging Setup

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("sidecar")
```

### Helper: Extract Model Name

LM Studio's API accepts `model` in the request body for `/v1/chat/completions`. Pull it out before forwarding.

```python
def extract_model(body: dict[str, Any]) -> str:
    """Return the model name from a chat or completions request body."""
    return body.get("model", "unknown")
```

### Helper: Forward and Intercept

This is the core of the proxy — one function handles both `/v1/chat/completions` and `/v1/completions`.

```python
async def forward_and_intercept(
    request: web.Request,
    upstream_url: str,
    db_path: str,
) -> web.Response:
    """
    Read the incoming request, forward it to upstream_url, intercept the
    response usage data, write to SQLite, and return the original response.
    """
    # Read request body
    body_bytes = await request.read()
    start_ms = time.perf_counter()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method=request.method,
                url=f"{upstream_url}{request.path}",
                headers={k: v for k, v in request.headers.items()
                         if k.lower() not in ("host", "connection")},
                data=body_bytes,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as upstream_response:
                response_body = await upstream_response.read()
                elapsed_ms = (time.perf_counter() - start_ms) * 1000

                # Try to extract usage from response
                try:
                    resp_json = json.loads(response_body)
                    usage = resp_json.get("usage")
                    if usage and isinstance(usage, dict):
                        model_name = "unknown"
                        try:
                            req_json = json.loads(body_bytes)
                            model_name = extract_model(req_json)
                        except Exception:
                            pass

                        _db.log_token_usage(
                            db_path=db_path,
                            model=model_name,
                            prompt_tokens=usage.get("prompt_tokens", 0),
                            completion_tokens=usage.get("completion_tokens", 0),
                            total_tokens=usage.get("total_tokens", 0),
                            response_ms=elapsed_ms,
                        )
                        logger.info(
                            "tokens logged: model=%s prompt=%d completion=%d total=%d (%.1fms)",
                            model_name,
                            usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0),
                            usage.get("total_tokens", 0),
                            elapsed_ms,
                        )
                except json.JSONDecodeError:
                    pass  # non-JSON response — normal for some error cases

                return web.Response(
                    body=response_body,
                    status=upstream_response.status,
                    headers={
                        k: v
                        for k, v in upstream_response.headers.items()
                        if k.lower() not in ("transfer-encoding", "content-encoding")
                    },
                    content_type="application/json",
                )

    except aiohttp.ClientError as exc:
        logger.error("Upstream error: %s", exc)
        return web.Response(
            body=json.dumps({"error": "Bad Gateway", "detail": str(exc)}).encode(),
            status=502,
            content_type="application/json",
        )
```

### Request Handlers

```python
async def handle_chat_completions(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    return await forward_and_intercept(
        request, cfg["proxy"]["upstream_url"], cfg["database"]["path"]
    )


async def handle_completions(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    return await forward_and_intercept(
        request, cfg["proxy"]["upstream_url"], cfg["database"]["path"]
    )


async def handle_404(request: web.Request) -> web.Response:
    return web.Response(
        body=json.dumps({"error": "Not found", "path": request.path}).encode(),
        status=404,
        content_type="application/json",
    )
```

### App Factory and Main

```python
def create_app(config: dict[str, Any]) -> web.Application:
    app = web.Application()
    app["config"] = config
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_post("/v1/completions",      handle_completions)
    app.router.add_route("*", "/{tail:.*}",     handle_404)
    return app


def main() -> None:
    config = load_config()
    db_path = config["database"]["path"]

    # Ensure DB schema exists
    _db.init_db(db_path)

    host = config["proxy"]["listen_host"]
    port = config["proxy"]["listen_port"]
    logger.info("Starting sidecar on %s:%d → %s", host, port,
                config["proxy"]["upstream_url"])

    app = create_app(config)
    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    main()
```

**Save as:** `~/Documents/hermes_projects/token_sidecar/sidecar.py`

---

## Step 3 — Write Unit Tests (`tests/test_proxy.py`)

Replace the placeholder with tests that mock LM Studio responses.

### Test Strategy

- Use `aiohttp.test_utils.AioHTTPTestCase` or raw `app` fixture + `aiohttp.ClientSession`
- Mock upstream LM Studio via a pytest fixture that runs an in-process echo server
- Verify: (a) request is forwarded, (b) response is returned unchanged, (c) DB row is written

### Fixtures

```python
import asyncio
from unittest.mock import patch, AsyncMock
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

# ── Upstream echo server (simulates LM Studio) ──────────────────────────────

@pytest.fixture
def upstream_server(aiohttp_client):
    """A minimal LM Studio mock that returns usage data."""
    async def handler(request):
        body = await request.read()
        return web.Response(
            body=json.dumps({
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [{
                    "message": {"role": "assistant", "content": "hi"},
                    "finish_reason": "stop",
                    "index": 0,
                }],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }).encode(),
            content_type="application/json",
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    return app
```

### Tests

| Test | What it verifies |
|------|------------------|
| `test_proxy_forwards_chat_request` | Client POST to `/v1/chat/completions` reaches upstream with correct body and headers |
| `test_proxy_returns_upstream_response_verbatim` | Response status, body content-type all match the upstream response |
| `test_proxy_extracts_usage_and_logs_to_db` | After a request, SQLite contains one row matching prompt/completion/total tokens |
| `test_proxy_handles_upstream_error_gracefully` | When upstream is down, sidecar returns 502, not a crash |
| `test_proxy_404_on_unknown_path` | Any non-chat/non-completions path gets a JSON 404 |

### Run Tests

```bash
cd ~/Documents/hermes_projects/token_sidecar
uv run python -m pytest tests/test_proxy.py -v --tb=short
```

---

## Step 4 — Manual End-to-End Test

Run the sidecar in the foreground, hit it with curl, and verify a row appears in SQLite.

**Terminal 1:**
```bash
cd ~/Documents/hermes_projects/token_sidecar
uv run python sidecar.py
# Should print: "Starting sidecar on localhost:1240 → http://localhost:1234"
```

**Terminal 2:**
```bash
curl -s -X POST http://localhost:1240/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "minimax-m2.7", "messages": [{"role":"user","content":"hi"}], "max_tokens": 10}'
```

**Verify in SQLite:**
```bash
cd ~/Documents/hermes_projects/token_sidecar
uv run python3 - <<'EOF'
from db import get_daily_summary, init_db

db = "~/.token_sidecar/tokens.db"
init_db(db)
summary = get_daily_summary(db)   # today UTC
for row in summary:
    print(row)
# Expected: one row with model="minimax-m2.7", total_tokens=15 (or similar)
EOF
```

---

## Step 5 — Git Commit

```bash
cd ~/Documents/hermes_projects/token_sidecar
git add sidecar.py config_loader.py tests/test_proxy.py
git status   # review
git commit -m "Phase 2: HTTP proxy core — forward requests, intercept usage, log to SQLite"
```

---

## Exit Criteria Checklist

- [ ] `sidecar.py` starts on `localhost:1240` without errors
- [ ] POST to `/v1/chat/completions` returns a valid response from LM Studio
- [ ] After one chat completions call, SQLite has exactly one row with the correct token counts and model name
- [ ] Upstream error (e.g. stop LM Studio) returns 502 Bad Gateway, not a crash
- [ ] Unknown path returns JSON 404
- [ ] `python -m pytest tests/test_proxy.py -v --tb=short` — all pass
- [ ] Git committed with clean message

---

## File Changes After Phase 2

```
token_sidecar/
├── sidecar.py           ← REWRITTEN (full aiohttp proxy)
├── config_loader.py     ← NEW (Phase 3 fast-tracked here for usability)
└── tests/
    └── test_proxy.py    ← REWRITTEN placeholder with full tests
```

All other files unchanged.

---

*Last updated: 2026-05-17*