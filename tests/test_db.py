"""
Tests for db.py — SQLite token usage database layer.
Uses pytest with in-memory (URI) and temp-file backed DBs via fixtures.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone

import pytest

import db as _db


# ---------------------------------------------------------------------------
# Constants / shared sample data
# ---------------------------------------------------------------------------

BASE = datetime(2026, 5, 17, 10, 0, 0, tzinfo=timezone.utc)

SAMPLE_ROWS = [
    dict(model="llama-3.1-8b",   prompt_tokens=120, completion_tokens=80,
         total_tokens=200,        response_ms=450.2),
    dict(model="llama-3.1-8b",   prompt_tokens=95,  completion_tokens=65,
         total_tokens=160,        response_ms=380.5),
    dict(model="mixtral-8x7b",   prompt_tokens=200, completion_tokens=150,
         total_tokens=350,        response_ms=920.1),
]

# Timestamps for SAMPLE_ROWS (matching BASE through BASE+2 hours)
SAMPLE_TIMESTAMPS = [
    "2026-05-17T10:00:00+00:00",
    "2026-05-17T11:00:00+00:00",
    "2026-05-17T12:00:00+00:00",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_db(tmp_path):
    """
    Provide a path to a temporary SQLite DB initialised with the schema.
    Uses a file-backed temp db so init_db logic runs end-to-end.
    """
    db_path = str(tmp_path / "test.db")
    _db.init_db(db_path)
    return db_path


@pytest.fixture
def populated_db(mem_db):
    """A database with 3 sample rows across two models."""
    # Patch timestamps using direct SQL inserts so they are deterministic
    conn = sqlite3.connect(mem_db)
    try:
        cur = conn.cursor()
        for row, ts in zip(SAMPLE_ROWS, SAMPLE_TIMESTAMPS):
            cur.execute(
                """INSERT INTO token_usage
                   (timestamp, model, prompt_tokens, completion_tokens,
                    total_tokens, response_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ts, row["model"], row["prompt_tokens"],
                 row["completion_tokens"], row["total_tokens"], row["response_ms"]),
            )
        conn.commit()
    finally:
        conn.close()
    return mem_db


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_init_db_creates_tables(mem_db):
    """init_db creates token_usage table and indexes without error."""
    conn = sqlite3.connect(mem_db)
    try:
        cur = conn.cursor()
        # Tables exist
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='token_usage'"
        )
        assert cur.fetchone()[0] == "token_usage"
        # Columns match schema
        cur.execute("PRAGMA table_info(token_usage)")
        cols = {row[1] for row in cur.fetchall()}
        expected_cols = {
            "id", "timestamp", "model",
            "prompt_tokens", "completion_tokens",
            "total_tokens", "response_ms",
        }
        assert cols == expected_cols
    finally:
        conn.close()


def test_init_db_is_idempotent(mem_db):
    """Calling init_db twice on the same DB does not raise."""
    _db.init_db(mem_db)  # should not raise


# ---------------------------------------------------------------------------
# log_token_usage tests
# ---------------------------------------------------------------------------

def test_log_token_usage_returns_row_id(mem_db):
    """log_token_usage returns a positive integer rowid."""
    rid = _db.log_token_usage(
        mem_db,
        model="test-model",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        response_ms=100.0,
    )
    assert isinstance(rid, int)
    assert rid > 0


def test_log_token_usage_roundtrip(mem_db):
    """Values inserted are retrievable by querying back the last rowid."""
    rid = _db.log_token_usage(
        mem_db,
        model="roundtrip-model",
        prompt_tokens=33,
        completion_tokens=22,
        total_tokens=55,
        response_ms=77.7,
    )

    conn = sqlite3.connect(mem_db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM token_usage WHERE id = ?", (rid,))
        row = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    finally:
        conn.close()

    assert row["model"] == "roundtrip-model"
    assert row["prompt_tokens"] == 33
    assert row["completion_tokens"] == 22
    assert row["total_tokens"] == 55
    assert row["response_ms"] == 77.7


# ---------------------------------------------------------------------------
# get_daily_summary tests
# ---------------------------------------------------------------------------

def test_get_daily_summary_empty(mem_db):
    """Empty DB returns empty list regardless of date."""
    assert _db.get_daily_summary(mem_db, "2026-05-17") == []
    assert _db.get_daily_summary(mem_db) == []          # today's UTC date


def test_get_daily_summary_one_model(populated_db):
    """
    A single model with two rows aggregates correctly:
    - llama-3.1-8b: (120+95)=215 prompt, (80+65)=145 completion, 2 requests
    """
    summary = _db.get_daily_summary(populated_db, "2026-05-17")
    llama_rows = [r for r in summary if r["model"] == "llama-3.1-8b"]
    assert len(llama_rows) == 1
    r = llama_rows[0]
    assert r["request_count"] == 2
    assert r["total_prompt_tokens"] == 215
    assert r["total_completion_tokens"] == 145
    assert r["total_tokens"] == 360          # 200+160


def test_get_daily_summary_multiple_models(populated_db):
    """Each model gets its own aggregated row; mixtral totals are correct."""
    summary = _db.get_daily_summary(populated_db, "2026-05-17")
    by_model = {r["model"]: r for r in summary}

    # llama-3.1-8b (2 rows: 120+95 prompt, 80+65 completion)
    llm = by_model["llama-3.1-8b"]
    assert llm["request_count"] == 2
    assert llm["total_prompt_tokens"] == 215
    assert llm["total_completion_tokens"] == 145
    assert llm["total_tokens"] == 360

    # mixtral-8x7b (1 row)
    mx = by_model["mixtral-8x7b"]
    assert mx["request_count"] == 1
    assert mx["total_prompt_tokens"] == 200
    assert mx["total_completion_tokens"] == 150
    assert mx["total_tokens"] == 350


def test_get_daily_summary_filters_by_date(populated_db):
    """Querying a date with no data returns an empty list."""
    summary = _db.get_daily_summary(populated_db, "2025-01-01")
    assert summary == []


# ---------------------------------------------------------------------------
# get_hourly_summary tests
# ---------------------------------------------------------------------------

def test_get_hourly_summary_breakdown(populated_db):
    """
    Hour grouping correctly separates data by hour:
    - 10:00 UTC: llama row (prompt=120, completion=80)
    - 11:00 UTC: llama row (prompt=95, completion=65)
    - 12:00 UTC: mixtral row
    """
    summary = _db.get_hourly_summary(populated_db, "2026-05-17")
    by_model_hour = {(r["model"], r["hour_utc"]): r for r in summary}

    h10 = by_model_hour[("llama-3.1-8b", 10)]
    assert h10["request_count"] == 1
    assert h10["total_prompt_tokens"] == 120
    assert h10["total_completion_tokens"] == 80

    h11 = by_model_hour[("llama-3.1-8b", 11)]
    assert h11["request_count"] == 1
    assert h11["total_prompt_tokens"] == 95

    h12 = by_model_hour[("mixtral-8x7b", 12)]
    assert h12["request_count"] == 1
    assert h12["total_prompt_tokens"] == 200


def test_get_hourly_summary_empty(mem_db):
    """Empty DB returns empty list."""
    assert _db.get_hourly_summary(mem_db, "2026-05-17") == []


def test_get_hourly_summary_filters_by_date(populated_db):
    """Wrong date returns no data."""
    summary = _db.get_hourly_summary(populated_db, "2099-12-31")
    assert summary == []