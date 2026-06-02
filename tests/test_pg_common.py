"""Tests for shared Postgres/API helper functions."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent.parent))

import pg_common


def test_probe_filter_is_null_safe_and_probe_specific():
    assert "COALESCE(endpoint" in pg_common.PROBE_FILTER_SQL
    assert "COALESCE(model" in pg_common.PROBE_FILTER_SQL
    assert "permission-probe-%" in pg_common.PROBE_FILTER_SQL


def test_iso_utc_appends_zulu():
    ts = datetime(2026, 6, 2, 14, 31, tzinfo=timezone.utc)
    assert pg_common.iso_utc(ts) == "2026-06-02T14:31:00Z"


def test_validate_timezone_accepts_iana_name():
    assert pg_common.validate_timezone("America/New_York") == "America/New_York"


def test_validate_timezone_rejects_unknown_name():
    with pytest.raises(web.HTTPBadRequest):
        pg_common.validate_timezone("Not/AZone")


def test_resolve_env_value_reads_simple_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKEN_SIDECAR_QUERY_DSN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OTHER=value\nTOKEN_SIDECAR_QUERY_DSN='postgresql://reader@example/db'\n",
        encoding="utf-8",
    )
    assert (
        pg_common.resolve_env_value("TOKEN_SIDECAR_QUERY_DSN", (env_file,))
        == "postgresql://reader@example/db"
    )
