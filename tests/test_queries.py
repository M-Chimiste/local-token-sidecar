"""
Tests for queries/summary.py — CLI query commands.

Uses pytest fixtures + subprocess invocation (not import) to test the full
CLI surface including click argument parsing and output formatting.

Test pattern:
  - Set TOKEN_SIDECAR_DB env var to point at a temporary fixture database.
  - Invoke `python -m queries.summary <subcommand> [...]` as a subprocess.
  - Assert return code, stdout/stderr content.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import textwrap

import pytest


# -----------------------------------------------------------------------
# Path setup — add project root so we can `import db`
# -----------------------------------------------------------------------
PROJECT_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from queries.summary import resolve_format


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest.fixture
def sample_db(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a fixture SQLite DB with token usage across two models and dates."""
    db_path = tmp_path / "tokens.db"

    # Import the init function to create schema
    import db as _db_module
    _db_module.init_db(str(db_path))

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # ── Day 1: two models, multiple requests ────────────────────────────
    day1 = "2026-05-16"
    rows_d1 = [
        # model, prompt_tok, completion_tok, total_tok, response_ms
        ("minimax-m2.7",      1000, 200, 1200, 3500),
        ("minimax-m2.7",       800, 150,  950, 2100),
        ("qwen3.6-27b-mlx",   3000, 500, 3500, 8200),
    ]
    for model, pt, ct, tt, ms in rows_d1:
        cur.execute(
            "INSERT INTO token_usage (timestamp,model,prompt_tokens,"
            "completion_tokens,total_tokens,response_ms) VALUES (?,?,?,?,?,?)",
            (f"{day1}T10:00:00+00:00", model, pt, ct, tt, ms),
        )

    # ── Day 2: one shared request + a new model on day 2 ────────────────
    day2 = "2026-05-17"
    rows_d2 = [
        ("minimax-m2.7",       39,   5,   44, 3522),   # from Phase 2 E2E
        ("gemma-4-it-4b",     2000, 300, 2300, 4100),
    ]
    for model, pt, ct, tt, ms in rows_d2:
        cur.execute(
            "INSERT INTO token_usage (timestamp,model,prompt_tokens,"
            "completion_tokens,total_tokens,response_ms) VALUES (?,?,?,?,?,?)",
            (f"{day2}T14:00:00+00:00", model, pt, ct, tt, ms),
        )

    conn.commit()
    conn.close()

    return db_path


def _env(db_path: pathlib.Path) -> dict[str, str]:
    """Return a clean env dict with TOKEN_SIDECAR_DB set."""
    env = {**os.environ, "TOKEN_SIDECAR_DB": str(db_path)}
    # Remove any config-override signals to ensure we use our fixture
    return env


# -----------------------------------------------------------------------
# Tests — resolve_format()
# -----------------------------------------------------------------------

def test_resolve_format_prefers_explicit_json() -> None:
    assert resolve_format("json") == "json"


def test_resolve_format_prefers_explicit_table() -> None:
    assert resolve_format("table") == "table"


def test_resolve_format_defaults_to_table_in_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert resolve_format(None) == "table"


def test_resolve_format_defaults_to_json_when_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert resolve_format(None) == "json"


# -----------------------------------------------------------------------
# Tests — daily subcommand
# -----------------------------------------------------------------------

def test_daily_shows_all_models_for_date(
    sample_db: pathlib.Path,
) -> None:
    """--date 2026-05-16 should list both models."""
    result = subprocess_run(
        ["-m", "queries.summary", "daily", "--date", "2026-05-16"],
        env=_env(sample_db),
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    models = {r["model"] for r in data}
    assert models == {"minimax-m2.7", "qwen3.6-27b-mlx"}


def test_daily_aggregates_multiple_requests_per_model(
    sample_db: pathlib.Path,
) -> None:
    """Day 1 has two requests to minimax-m2.7 (1000+800 prompt, 200+150 completion).

    The daily view must aggregate into a single row per model.
    """
    result = subprocess_run(
        ["-m", "queries.summary", "daily", "--date", "2026-05-16"],
        env=_env(sample_db),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)

    minimax_row = next(r for r in data if r["model"] == "minimax-m2.7")
    # Two requests: 1000+800=1800 prompt, 200+150=350 completion
    assert minimax_row["total_prompt_tokens"] == 1800
    assert minimax_row["total_completion_tokens"] == 350
    assert minimax_row["request_count"] == 2


def test_daily_model_filter(
    sample_db: pathlib.Path,
) -> None:
    """--model qwen3.6-27b-mlx should return only that model."""
    result = subprocess_run(
        ["-m", "queries.summary", "daily",
         "--date", "2026-05-16", "--model", "qwen3.6-27b-mlx"],
        env=_env(sample_db),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert len(data) == 1
    assert data[0]["model"] == "qwen3.6-27b-mlx"


def test_daily_no_data_for_date(
    sample_db: pathlib.Path,
) -> None:
    """A date with no rows should exit cleanly (not raise)."""
    result = subprocess_run(
        ["-m", "queries.summary", "daily", "--date", "2020-01-01"],
        env=_env(sample_db),
    )
    assert result.returncode == 0
    # Should print a message to stderr, stdout is empty
    assert not result.stdout.strip()


def test_daily_json_output_is_valid(
    sample_db: pathlib.Path,
) -> None:
    """JSON output must be parseable and contain expected keys."""
    result = subprocess_run(
        ["-m", "queries.summary", "daily", "--date", "2026-05-16",
         "--format", "json"],
        env=_env(sample_db),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    row = data[0]
    for key in ("model", "request_count", "total_prompt_tokens",
                "total_completion_tokens", "total_tokens", "date"):
        assert key in row, f"Missing key: {key}"


def test_daily_invalid_date_format(
    sample_db: pathlib.Path,
) -> None:
    """Bad date format should exit non-zero with a usage error."""
    result = subprocess_run(
        ["-m", "queries.summary", "daily", "--date", "not-a-date"],
        env=_env(sample_db),
    )
    assert result.returncode != 0
    assert "Invalid date" in result.stderr or "Error" in result.stderr


# -----------------------------------------------------------------------
# Tests — hourly subcommand
# -----------------------------------------------------------------------

def test_hourly_requires_date_flag(
    sample_db: pathlib.Path,
) -> None:
    """Running `hourly` without --date should fail with usage error."""
    result = subprocess_run(
        ["-m", "queries.summary", "hourly"],
        env=_env(sample_db),
    )
    assert result.returncode != 0
    # click reports missing option
    assert "--date" in result.stderr or "Missing" in result.stderr


def test_hourly_returns_hours_and_models_for_date(
    sample_db: pathlib.Path,
) -> None:
    """Day 2 (2026-05-17) has requests at hour=14 for two models."""
    result = subprocess_run(
        ["-m", "queries.summary", "hourly",
         "--date", "2026-05-17", "--format", "json"],
        env=_env(sample_db),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    # Both rows should be at hour 14 (UTC)
    hours = {r["hour_utc"] for r in data}
    assert hours == {14}


def test_hourly_json_includes_hour_key(
    sample_db: pathlib.Path,
) -> None:
    """JSON rows must include 'hour_utc' field."""
    result = subprocess_run(
        ["-m", "queries.summary", "hourly",
         "--date", "2026-05-17", "--format", "json"],
        env=_env(sample_db),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert all("hour_utc" in row for row in data)


def test_hourly_no_data_for_date(
    sample_db: pathlib.Path,
) -> None:
    """A date with no rows should exit cleanly."""
    result = subprocess_run(
        ["-m", "queries.summary", "hourly", "--date", "2020-01-01"],
        env=_env(sample_db),
    )
    assert result.returncode == 0
    assert not result.stdout.strip()


# -----------------------------------------------------------------------
# Tests — by-model subcommand
# -----------------------------------------------------------------------

def test_by_model_returns_all_models(
    sample_db: pathlib.Path,
) -> None:
    """by-model should list all three models across both days."""
    result = subprocess_run(
        ["-m", "queries.summary", "by-model", "--format", "json"],
        env=_env(sample_db),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    models = {r["model"] for r in data}
    assert models == {"minimax-m2.7", "qwen3.6-27b-mlx", "gemma-4-it-4b"}


def test_by_model_sorted_descending_by_total_tokens(
    sample_db: pathlib.Path,
) -> None:
    """Results must be sorted by total_tokens descending."""
    result = subprocess_run(
        ["-m", "queries.summary", "by-model", "--format", "json"],
        env=_env(sample_db),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    totals = [r["total_tokens"] for r in data]
    assert totals == sorted(totals, reverse=True)


def test_by_model_no_date_field_in_json(
    sample_db: pathlib.Path,
) -> None:
    """by-model aggregates have no single date — 'date' key must not appear."""
    result = subprocess_run(
        ["-m", "queries.summary", "by-model", "--format", "json"],
        env=_env(sample_db),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    for row in data:
        assert "date" not in row, f"'date' should not be present in by-model: {row}"


# -----------------------------------------------------------------------
# Helper
# -----------------------------------------------------------------------

def subprocess_run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run the project venv's python with the given args; return CompletedProcess."""
    import subprocess as _subprocess

    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python3"
    return _subprocess.run(
        [str(venv_python)] + args,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
    )


# -----------------------------------------------------------------------
# Pytest config — suppress "demo" warning from click
# -----------------------------------------------------------------------

def pytest_configure(config: pytest.Config) -> None:
    import warnings
    warnings.filterwarnings("ignore", message=".*click.*")