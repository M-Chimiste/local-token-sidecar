"""Optional live Postgres tests for centralized reporting."""

from __future__ import annotations

import os
import uuid

import pytest

import postgres_store


pytestmark = pytest.mark.skipif(
    not os.environ.get("TOKEN_SIDECAR_TEST_POSTGRES_DSN"),
    reason="Set TOKEN_SIDECAR_TEST_POSTGRES_DSN to run live Postgres tests",
)


def test_postgres_schema_insert_and_summary_roundtrip():
    dsn = os.environ["TOKEN_SIDECAR_TEST_POSTGRES_DSN"]
    event_id = f"test-{uuid.uuid4().hex}"

    postgres_store.init_schema(dsn)
    try:
        acknowledged = postgres_store.insert_token_usage_batch(
            dsn,
            [
                {
                    "event_id": event_id,
                    "timestamp": "2026-05-17T10:00:00+00:00",
                    "node_id": "pytest-node",
                    "model": "pytest-model",
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                    "response_ms": 123.4,
                    "endpoint": "/v1/chat/completions",
                    "status_code": 200,
                }
            ],
        )
        assert acknowledged == [event_id]

        daily = postgres_store.get_daily_summary(
            dsn,
            date="2026-05-17",
            node_id="pytest-node",
        )
        row = next(r for r in daily if r["model"] == "pytest-model")
        assert row["request_count"] >= 1
        assert row["total_tokens"] >= 18

        # Duplicate event_ids are acknowledged but not double counted.
        postgres_store.insert_token_usage_batch(
            dsn,
            [
                {
                    "event_id": event_id,
                    "timestamp": "2026-05-17T10:00:00+00:00",
                    "node_id": "pytest-node",
                    "model": "pytest-model",
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                    "response_ms": 123.4,
                    "endpoint": "/v1/chat/completions",
                    "status_code": 200,
                }
            ],
        )
        by_model = postgres_store.get_by_model_summary(dsn, node_id="pytest-node")
        model_row = next(r for r in by_model if r["model"] == "pytest-model")
        assert model_row["total_tokens"] >= 18
    finally:
        with postgres_store._connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM token_usage WHERE event_id = %s",
                    (event_id,),
                )
