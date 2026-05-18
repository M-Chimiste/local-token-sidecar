"""Tests for central Postgres sync orchestration."""

from __future__ import annotations

import sqlite3

import pytest

import central_sync
import db as _db


@pytest.fixture
def queued_db(tmp_path):
    db_path = str(tmp_path / "tokens.db")
    _db.init_db(db_path, node_id="athena")
    _db.log_token_usage(
        db_path,
        model="sync-model",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        response_ms=25.0,
        node_id="athena",
        event_id="sync-1",
        endpoint="/v1/chat/completions",
        status_code=200,
    )
    return db_path


def test_flush_once_inserts_and_deletes_acknowledged_rows(monkeypatch, queued_db):
    captured = {}

    def fake_insert(dsn, rows):
        captured["dsn"] = dsn
        captured["rows"] = rows
        return [row["event_id"] for row in rows]

    monkeypatch.setattr(
        central_sync.postgres_store,
        "insert_token_usage_batch",
        fake_insert,
    )

    flushed = central_sync.flush_once(
        db_path=queued_db,
        postgres_dsn="postgresql://example",
        batch_size=100,
        node_id="athena",
    )

    assert flushed == 1
    assert captured["dsn"] == "postgresql://example"
    assert captured["rows"][0]["event_id"] == "sync-1"
    assert _db.get_unsynced_token_usage(queued_db, limit=100, node_id="athena") == []


def test_flush_once_records_failure_and_keeps_rows(monkeypatch, queued_db):
    def fake_insert(dsn, rows):
        raise RuntimeError("postgres down")

    monkeypatch.setattr(
        central_sync.postgres_store,
        "insert_token_usage_batch",
        fake_insert,
    )

    with pytest.raises(RuntimeError, match="postgres down"):
        central_sync.flush_once(
            db_path=queued_db,
            postgres_dsn="postgresql://example",
            batch_size=100,
            node_id="athena",
        )

    queued = _db.get_unsynced_token_usage(queued_db, limit=100, node_id="athena")
    assert [row["event_id"] for row in queued] == ["sync-1"]

    conn = sqlite3.connect(queued_db)
    try:
        attempts, error = conn.execute(
            "SELECT sync_attempts, last_sync_error FROM token_usage "
            "WHERE event_id = ?",
            ("sync-1",),
        ).fetchone()
    finally:
        conn.close()

    assert attempts == 1
    assert "postgres down" in error
