"""
Shared helpers for Postgres-backed read-only services.

Dashboard and Token Oracle both read the central `token_usage` table. Keep
probe filtering, JSON responses, UTC ISO rendering, and timezone validation in
one place so the services do not drift.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone
from typing import Any
from zoneinfo import available_timezones

from aiohttp import web


# Applied to every analytics query. NULL-safe so older rows without an
# `endpoint` value still pass through.
PROBE_FILTER_SQL = (
    " COALESCE(endpoint, '') <> '/probe'"
    " AND COALESCE(model, '')    <> 'probe'"
    " AND event_id NOT LIKE 'permission-probe-%%'"
)

_VALID_TIMEZONES = available_timezones()


def validate_timezone(tz: str) -> str:
    """Return a valid IANA timezone name or raise HTTP 400 for callers."""
    tz = (tz or "UTC").strip() or "UTC"
    if tz not in _VALID_TIMEZONES:
        raise web.HTTPBadRequest(reason=f"unknown tz: {tz!r}")
    return tz


def is_valid_timezone(tz: str) -> bool:
    """Boolean timezone validator for config loading paths."""
    return (tz or "").strip() in _VALID_TIMEZONES


def resolve_env_value(name: str, env_files: list[pathlib.Path] | tuple[pathlib.Path, ...] = ()) -> str | None:
    """
    Resolve an environment value from the live process, then simple env files.

    Env files may contain either `KEY=value` or `export KEY=value` lines. Values
    may be quoted. This intentionally avoids a dotenv dependency.
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value

    for path in env_files:
        found = read_env_file_value(path, name)
        if found:
            return found
    return None


def read_env_file_value(path: pathlib.Path, name: str) -> str | None:
    """Read one key from a simple shell-style env file without echoing secrets."""
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError:
        return None

    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.strip() or None
    return None


def iso_utc(ts: datetime) -> str:
    """Render a UTC timestamptz as ISO with trailing 'Z'."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def json_response(payload: Any, status: int = 200) -> web.Response:
    """Return a compact JSON response with datetime support."""
    return web.Response(
        body=json.dumps(payload, default=_json_default).encode(),
        status=status,
        content_type="application/json",
    )


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return iso_utc(obj)
    raise TypeError(f"not serializable: {type(obj).__name__}")
