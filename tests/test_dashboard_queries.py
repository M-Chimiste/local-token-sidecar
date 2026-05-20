"""Unit tests for dashboard.py SQL builders + small helpers (no DB)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import dashboard


# ───────────────────────── range_bound_sql ──────────────────────────────

def test_range_bound_sql_1d_uses_utc_date_equality():
    sql, params = dashboard.range_bound_sql("1d")
    assert "AT TIME ZONE 'UTC'" in sql
    assert "::date" in sql
    assert "= (now() AT TIME ZONE 'UTC')::date" in sql
    assert params == []


def test_range_bound_sql_7d_uses_six_day_offset():
    sql, params = dashboard.range_bound_sql("7d")
    assert "INTERVAL '6 days'" in sql
    assert params == []


def test_range_bound_sql_30d_uses_twentynine_day_offset():
    sql, params = dashboard.range_bound_sql("30d")
    assert "INTERVAL '29 days'" in sql
    assert params == []


def test_range_bound_sql_all_emits_no_filter():
    sql, params = dashboard.range_bound_sql("all")
    assert sql == ""
    assert params == []


def test_range_bound_sql_rejects_unknown_range():
    with pytest.raises(ValueError):
        dashboard.range_bound_sql("nope")


# ───────────────────────── filter_sql ──────────────────────────────────

def test_filter_sql_empty_returns_empty():
    sql, params = dashboard.filter_sql([], [])
    assert sql == ""
    assert params == []


def test_filter_sql_models_only_uses_text_array_param():
    sql, params = dashboard.filter_sql(["a", "b"], [])
    assert "model = ANY(%s::text[])" in sql
    assert "node_id" not in sql
    assert params == [["a", "b"]]


def test_filter_sql_nodes_only():
    sql, params = dashboard.filter_sql([], ["athena"])
    assert "node_id = ANY(%s::text[])" in sql
    assert "model = ANY" not in sql
    assert params == [["athena"]]


def test_filter_sql_both_dimensions_orders_params_models_then_nodes():
    sql, params = dashboard.filter_sql(["m1", "m2"], ["athena", "metis"])
    assert sql.count("ANY(%s::text[])") == 2
    assert params == [["m1", "m2"], ["athena", "metis"]]


# ───────────────────────── pick_granularity ────────────────────────────

def test_pick_granularity_1d_is_hourly():
    assert dashboard.pick_granularity("1d", None) == "hour"


def test_pick_granularity_7d_and_30d_are_daily():
    assert dashboard.pick_granularity("7d", None) == "day"
    assert dashboard.pick_granularity("30d", None) == "day"


def test_pick_granularity_all_short_span_is_daily():
    assert dashboard.pick_granularity("all", 50 * 86400) == "day"
    assert dashboard.pick_granularity("all", 90 * 86400) == "day"  # inclusive boundary


def test_pick_granularity_all_long_span_falls_back_to_weekly():
    assert dashboard.pick_granularity("all", 91 * 86400) == "week"
    assert dashboard.pick_granularity("all", 365 * 86400) == "week"


def test_pick_granularity_all_none_span_defaults_to_daily():
    assert dashboard.pick_granularity("all", None) == "day"


def test_pick_granularity_rejects_unknown_range():
    with pytest.raises(ValueError):
        dashboard.pick_granularity("xyz", None)


# ───────────────────────── bucket_label / bucket_key ───────────────────

def test_bucket_label_hour_uses_24h_clock():
    ts = datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc)
    assert dashboard.bucket_label(ts, "hour") == "14:00"


def test_bucket_label_day_uses_short_month():
    ts = datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc)
    assert dashboard.bucket_label(ts, "day") == "May 9"


def test_bucket_label_week_is_wk_prefixed():
    ts = datetime(2026, 5, 11, 0, 0, tzinfo=timezone.utc)
    assert dashboard.bucket_label(ts, "week") == "Wk May 11"


def test_bucket_key_hour_includes_hour_segment():
    ts = datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc)
    assert dashboard.bucket_key(ts, "hour") == "2026-05-19T14"


def test_bucket_key_day_is_iso_date():
    ts = datetime(2026, 5, 9, 12, 30, tzinfo=timezone.utc)
    assert dashboard.bucket_key(ts, "day") == "2026-05-09"


# ───────────────────────── ISO / row mapping ───────────────────────────

def test_iso_appends_zulu_for_utc():
    ts = datetime(2026, 5, 19, 14, 32, 1, 123456, tzinfo=timezone.utc)
    assert dashboard._iso(ts).endswith("Z")
    assert "+00:00" not in dashboard._iso(ts)


def test_iso_attaches_utc_to_naive_datetime():
    ts = datetime(2026, 5, 19, 14, 32, 1)
    out = dashboard._iso(ts)
    assert out.endswith("Z")
    assert "14:32:01" in out


def test_row_to_json_maps_schema_columns_to_design_field_names():
    row = (
        "evt-1",                                              # event_id
        datetime(2026, 5, 19, 14, 32, 1, tzinfo=timezone.utc),  # timestamp
        "athena",                                             # node_id
        "minimax-m2.7",                                       # model
        180, 47, 227,                                         # prompt/completion/total
        1953.09,                                              # response_ms
        "/v1/chat/completions",                               # endpoint
        200,                                                  # status_code
    )
    j = dashboard._row_to_json(row)
    assert j == {
        "id":         "evt-1",
        "ts":         "2026-05-19T14:32:01Z",
        "host":       "athena",
        "model":      "minimax-m2.7",
        "prompt":     180,
        "completion": 47,
        "total":      227,
        "ms":         1953.09,
        "endpoint":   "/v1/chat/completions",
        "status":     200,
    }


def test_row_to_json_coerces_null_numeric_fields_to_zero():
    row = (
        "evt-2",
        datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc),
        "metis", "m", None, None, None, None, "/v1/completions", None,
    )
    j = dashboard._row_to_json(row)
    assert j["prompt"] == 0
    assert j["completion"] == 0
    assert j["total"] == 0
    assert j["ms"] == 0.0
    assert j["status"] == 0


# ───────────────────────── Probe filter ────────────────────────────────

def test_probe_filter_is_null_safe():
    """Every WHERE clause must COALESCE endpoint/model so NULLs don't slip through."""
    assert "COALESCE(endpoint" in dashboard.PROBE_FILTER_SQL
    assert "COALESCE(model" in dashboard.PROBE_FILTER_SQL
    assert "permission-probe-%" in dashboard.PROBE_FILTER_SQL
