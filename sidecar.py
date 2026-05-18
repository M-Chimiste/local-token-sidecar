"""
Token Counter Sidecar — HTTP Proxy

A lightweight aiohttp-based proxy that:
  - Accepts LLM API calls on localhost:1240
  - Forwards them verbatim to LM Studio at localhost:1234
  - Intercepts the response, extracts token usage (usage object)
  - Queues a local SQLite outbox row via db.log_token_usage()
  - Optionally flushes queued rows to central Postgres in the background
  - Returns the original upstream response unchanged

Usage:
    uv run python sidecar.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

import aiohttp
from aiohttp import web

import central_sync
import db as _db
from config_loader import load_config, parse_cli_args, Config


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("sidecar")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_model(body: dict[str, Any]) -> str:
    """
    Return the model name from a chat/completions request body.

    Stores whatever is in ``model`` as-is; no normalisation.
    Returns "unknown" if the field is absent or empty.
    """
    return body.get("model") or "unknown"


# ---------------------------------------------------------------------------
# Core proxy logic
# ---------------------------------------------------------------------------

async def forward_and_intercept(
    request: web.Request,
    upstream_url: str,
    db_path: str,
    node_id: str,
) -> web.Response:
    """
    Forward an LLM API request to the upstream LM Studio instance,
    intercept its response, log token usage to SQLite, and return
    the original response verbatim to the caller.

    Parameters
    ----------
    request : web.Request
        The incoming client request.
    upstream_url : str
        Base URL of the upstream (e.g. "http://localhost:1234").
    db_path : str
        Path to the SQLite database file.

    Returns
    -------
    web.Response
        The original upstream response, byte-for-byte identical,
        with hop-by-hop headers stripped.
    """
    body_bytes = await request.read()
    start_ms = time.perf_counter()

    # Build clean request headers (strip hop-by-hop / connection-level headers)
    skip_headers = {"host", "connection", "transfer-encoding", "content-encoding"}
    upstream_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in skip_headers
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method=request.method,
                url=f"{upstream_url}{request.path}",
                headers=upstream_headers,
                data=body_bytes,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as upstream_resp:
                response_body = await upstream_resp.read()
                elapsed_ms = (time.perf_counter() - start_ms) * 1000

                # ── Intercept: extract usage and log to SQLite ─────────────────
                try:
                    resp_json = json.loads(response_body)
                    raw_usage = resp_json.get("usage")

                    if isinstance(raw_usage, dict):
                        prompt_tokens     = raw_usage.get("prompt_tokens", 0) or 0
                        completion_tokens = raw_usage.get("completion_tokens", 0) or 0
                        total_tokens      = raw_usage.get("total_tokens", 0) or 0

                        # Extract model name from request body
                        model_name = "unknown"
                        try:
                            req_json = json.loads(body_bytes)
                            model_name = extract_model(req_json)
                        except (json.JSONDecodeError, TypeError):
                            pass

                        try:
                            _db.log_token_usage(
                                db_path=db_path,
                                node_id=node_id,
                                endpoint=request.path,
                                status_code=upstream_resp.status,
                                model=model_name,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=total_tokens,
                                response_ms=elapsed_ms,
                            )
                            logger.info(
                                "tokens queued  node=%s  model=%-30s  prompt=%5d  "
                                "completion=%5d  total=%6d  (%.1fms)",
                                node_id, model_name, prompt_tokens,
                                completion_tokens, total_tokens, elapsed_ms,
                            )
                        except Exception:
                            logger.exception(
                                "Failed to queue token usage locally; returning "
                                "upstream response unchanged"
                            )

                except json.JSONDecodeError:
                    # Non-JSON response — can happen on some error paths; ignore usage.
                    pass

                # Strip hop-by-hop headers; pass response body + status only.
                # Do NOT re-set Content-Type here — upstream already set it;
                # aiohttp forbids passing both the header and content_type= kwarg.
                return web.Response(
                    body=response_body,
                    status=upstream_resp.status,
                    content_type="application/json",
                )

    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.error("Upstream error: %s", exc)
        return web.Response(
            body=json.dumps({
                "error": "Bad Gateway",
                "detail": str(exc),
            }).encode(),
            status=502,
            content_type="application/json",
        )


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------

async def handle_chat_completions(request: web.Request) -> web.Response:
    """
    Handler for POST /v1/chat/completions.

    Forwards the request verbatim to LM Studio and logs token usage
    from the upstream response body before returning it unchanged.
    """
    cfg: Config = request.app["config"]
    return await forward_and_intercept(
        request,
        str(cfg.upstream_url),
        str(cfg.database_path),
        cfg.node_id,
    )


async def handle_completions(request: web.Request) -> web.Response:
    """
    Handler for POST /v1/completions.

    Forwards the request verbatim to LM Studio and logs token usage
    from the upstream response body before returning it unchanged.
    """
    cfg: Config = request.app["config"]
    return await forward_and_intercept(
        request,
        str(cfg.upstream_url),
        str(cfg.database_path),
        cfg.node_id,
    )


async def handle_proxy(request: web.Request) -> web.Response:
    """
    Generic proxy for any path not explicitly registered.
    
    Forwards the request verbatim to LM Studio. If the upstream response
    contains an OpenAI-compatible usage object, it is queued like the primary
    completion endpoints.
    """
    cfg: Config = request.app["config"]
    return await forward_and_intercept(
        request,
        str(cfg.upstream_url),
        str(cfg.database_path),
        cfg.node_id,
    )


async def handle_404(request: web.Request) -> web.Response:
    """Fallback 404 — should rarely be reached now."""
    return web.Response(
        body=json.dumps({
            "error": "Not found",
            "path": request.path,
        }).encode(),
        status=404,
        content_type="application/json",
    )


async def handle_health(request: web.Request) -> web.Response:
    """Liveness probe — returns 200 OK when the server is running."""
    return web.Response(
        body=json.dumps({"status": "ok"}).encode(),
        status=200,
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# App factory + main
# ---------------------------------------------------------------------------

def create_app(cfg: Config) -> web.Application:
    """
    Build and return the aiohttp application with all routes registered.

    Routes:
        POST /v1/chat/completions  — forward to upstream, log usage
        POST /v1/completions       — forward to upstream, log usage
        GET  /health               — liveness probe (200 OK)

    Other GET/POST paths are proxied to upstream without special handling.
    """
    app = web.Application()
    app["config"] = cfg
    app.cleanup_ctx.append(central_sync_context)

    # Explicit routes for the two LLM endpoints we care about
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_post("/v1/completions",      handle_completions)

    # Health check (GET) — used by integration tests and launchd respawn checks
    app.router.add_get("/health", handle_health)

    # Catch-all: proxy any unmatched path to upstream LM Studio
    app.router.add_route("GET",  "/{tail:.*}", handle_proxy)
    app.router.add_route("POST", "/{tail:.*}", handle_proxy)

    return app


async def central_sync_context(app: web.Application):
    """Run optional central Postgres flushing for the app lifetime."""
    cfg: Config = app["config"]
    if not cfg.central.enabled:
        yield
        return

    task = asyncio.create_task(_central_sync_loop(cfg))
    app["central_sync_task"] = task
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _central_sync_loop(cfg: Config) -> None:
    """Flush queued local rows to Postgres with bounded backoff."""
    failures = 0
    assert cfg.central.dsn is not None

    while True:
        delay = cfg.central.flush_interval_seconds
        try:
            flushed = await asyncio.to_thread(
                central_sync.flush_once,
                db_path=str(cfg.database_path),
                postgres_dsn=cfg.central.dsn,
                batch_size=cfg.central.batch_size,
                node_id=cfg.node_id,
            )
            if flushed:
                logger.info("flushed %d token usage row(s) to Postgres", flushed)
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            delay = min(delay * (2 ** min(failures, 4)), 60.0)
            logger.warning(
                "central Postgres flush failed; queued rows will retry in %.1fs: %s",
                delay,
                exc,
            )

        await asyncio.sleep(delay)


def main() -> None:
    cli_args = parse_cli_args()
    cfg = load_config(cli_args.config)

    # Apply log level from config.yaml to the root logger
    numeric_level = getattr(logging, cfg.log_level, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Ensure the database schema exists before handling any traffic
    _db.init_db(str(cfg.database_path), node_id=cfg.node_id)

    logger.info("Starting sidecar on %s:%d → %s", cfg.listen_host, cfg.listen_port, cfg.upstream_url)
    app = create_app(cfg)
    web.run_app(app, host=cfg.listen_host, port=cfg.listen_port, print=None)


if __name__ == "__main__":
    main()
