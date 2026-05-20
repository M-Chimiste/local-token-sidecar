#!/usr/bin/env python3
"""
Token sidecar query CLI.

Usage:
    python -m queries.summary daily [--date YYYY-MM-DD] [--model NAME] [--format json|table] [--backend auto|sqlite|postgres] [--node NODE]
    python -m queries.summary hourly --date YYYY-MM-DD [--format json|table] [--backend auto|sqlite|postgres] [--node NODE]
    python -m queries.summary by-model [--format json|table] [--backend auto|sqlite|postgres] [--node NODE]

Environment:
    TOKEN_SIDECAR_DB            Override local SQLite path
    TOKEN_SIDECAR_QUERY_DSN     Preferred Postgres read/reporting DSN
    TOKEN_SIDECAR_POSTGRES_DSN  Fallback Postgres DSN
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

import click
from tabulate import tabulate


# -----------------------------------------------------------------------
# Path setup — add project root so we can `import db`
# -----------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import db as _db
import postgres_store
from config_loader import load_config


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def resolve_db_path() -> str:
    """Return the database path to query.

    Priority:
      1. TOKEN_SIDECAR_DB environment variable (for scripting / tests)
      2. Config object resolved via config_loader (expands ~)
    """
    env = os.environ.get("TOKEN_SIDECAR_DB", "")
    if env:
        return env
    # load_config reads from project root by default; pass None for --config override
    cfg = load_config()
    return str(cfg.database_path)


def resolve_postgres_dsn() -> str | None:
    """Return the reporting Postgres DSN, if configured."""
    return (
        os.environ.get("TOKEN_SIDECAR_QUERY_DSN")
        or os.environ.get("TOKEN_SIDECAR_POSTGRES_DSN")
    )


def resolve_backend(requested: str) -> tuple[str, str | None]:
    """Resolve auto/sqlite/postgres into an active backend and optional DSN."""
    dsn = resolve_postgres_dsn()
    if requested == "sqlite":
        return "sqlite", None
    if requested == "postgres":
        if not dsn:
            raise click.ClickException(
                "Postgres backend requested, but neither TOKEN_SIDECAR_QUERY_DSN "
                "nor TOKEN_SIDECAR_POSTGRES_DSN is set."
            )
        return "postgres", dsn
    if dsn:
        return "postgres", dsn
    return "sqlite", None


def resolve_format(requested: str | None) -> str:
    """Return 'table' or 'json'.

    - Explicit --format flag always wins.
    - In a TTY (interactive terminal): default to table.
    - Otherwise (pipe / redirect): default to JSON for safe parsing.
    """
    if requested is not None:
        return requested
    return "table" if sys.stdout.isatty() else "json"


def parse_date(value: str | None) -> str | None:
    """Parse a YYYY-MM-DD string into a date; return None for today."""
    if value is None:
        return None  # db layer will use today's date
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise click.BadParameter(f"Invalid date format: {value!r}. Use YYYY-MM-DD.")
    return value


def print_json(data) -> None:
    json.dump(data, sys.stdout, indent=2)


def utc_today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def should_fallback_to_sqlite(requested_backend: str, exc: Exception) -> bool:
    """Return True for auto-mode Postgres failures after warning the user."""
    if requested_backend != "auto":
        return False
    click.echo(
        f"Postgres query failed; falling back to SQLite: {exc}",
        err=True,
    )
    return True


# -----------------------------------------------------------------------
# Click CLI
# -----------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.version_option(version="1.0.0")
def cli() -> None:
    """Token sidecar — query your collected token usage data.

    Run a subcommand to see detailed help:

        python -m queries.summary daily --help
        python -m queries.summary hourly --help
        python -m queries.summary by-model --help
    """
    # Show help when called with no subcommand (click default)
    if len(sys.argv) == 1:
        click.echo(cli.get_help())


# ────────────────────────────────────────────────────────────────────────
# daily
# ────────────────────────────────────────────────────────────────────────

@cli.command()
@click.option(
    "--date",
    "date_str",
    default=None,
    help="Date in YYYY-MM-DD format. Defaults to today (UTC).",
)
@click.option(
    "--model",
    "model_name",
    default=None,
    help="Filter results to a specific model.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default=None,
    help='Output format: "table" (default in terminal) or "json" (default when piped).',
)
@click.option(
    "--backend",
    "backend",
    type=click.Choice(["auto", "sqlite", "postgres"]),
    default="auto",
    show_default=True,
    help="Read from local SQLite or central Postgres.",
)
@click.option(
    "--node",
    "node_id",
    default=None,
    help="Filter central/local results to one node_id.",
)
def daily(
    date_str: str | None,
    model_name: str | None,
    output_format: str | None,
    backend: str,
    node_id: str | None,
) -> None:
    """Daily token usage summary grouped by model.

    Shows one row per model with total requests and token counts for the day.
    """
    date = parse_date(date_str)
    fmt = resolve_format(output_format)
    backend_name, dsn = resolve_backend(backend)

    target_date = date or utc_today()
    if backend_name == "postgres":
        try:
            rows = postgres_store.get_daily_summary(
                dsn or "",
                date=target_date,
                node_id=node_id,
            )
        except Exception as exc:
            if not should_fallback_to_sqlite(backend, exc):
                raise click.ClickException(f"Postgres query failed: {exc}") from exc
            rows = _db.get_daily_summary(
                resolve_db_path(),
                date=date,
                node_id=node_id,
            )
    else:
        rows = _db.get_daily_summary(
            resolve_db_path(),
            date=date,
            node_id=node_id,
        )
    if model_name:
        rows = [r for r in rows if r["model"] == model_name]

    if not rows:
        msg = f"No data for {date or 'today'}"
        if model_name:
            msg += f" (model: {model_name})"
        click.echo(msg, err=True)
        sys.exit(0)

    # Normalise to list-of-dict regardless of what db layer returned
    rows = [_normalise_row(r, row_date=target_date) for r in rows]

    if fmt == "json":
        print_json(rows)
        return

    table_data = [
        [
            r["date"] or (date or ""),
            r["model"],
            f"{r['request_count']:,}",
            f"{r['total_prompt_tokens']:,}",
            f"{r['total_completion_tokens']:,}",
            f"{r['total_tokens']:,}",
        ]
        for r in rows
    ]
    click.echo(
        tabulate(
            table_data,
            headers=["Date", "Model", "Requests", "Prompt Tokens",
                     "Completion Tokens", "Total Tokens"],
            tablefmt="simple",
        )
    )


# ────────────────────────────────────────────────────────────────────────
# hourly
# ────────────────────────────────────────────────────────────────────────

@cli.command()
@click.option(
    "--date",
    "date_str",
    required=True,
    help="Date in YYYY-MM-DD format (required).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default=None,
    help='Output format: "table" (default in terminal) or "json" (default when piped).',
)
@click.option(
    "--backend",
    "backend",
    type=click.Choice(["auto", "sqlite", "postgres"]),
    default="auto",
    show_default=True,
    help="Read from local SQLite or central Postgres.",
)
@click.option(
    "--node",
    "node_id",
    default=None,
    help="Filter central/local results to one node_id.",
)
def hourly(
    date_str: str,
    output_format: str | None,
    backend: str,
    node_id: str | None,
) -> None:
    """Hourly breakdown of token usage for a specific date.

    Shows one row per (model, hour) pair, sorted by hour then tokens descending.
    """
    date = parse_date(date_str)  # validates format; raises click.BadParameter if bad
    fmt = resolve_format(output_format)
    backend_name, dsn = resolve_backend(backend)

    if backend_name == "postgres":
        try:
            rows = postgres_store.get_hourly_summary(
                dsn or "",
                date=date or utc_today(),
                node_id=node_id,
            )
        except Exception as exc:
            if not should_fallback_to_sqlite(backend, exc):
                raise click.ClickException(f"Postgres query failed: {exc}") from exc
            rows = _db.get_hourly_summary(
                resolve_db_path(),
                date=date,
                node_id=node_id,
            )
    else:
        rows = _db.get_hourly_summary(
            resolve_db_path(),
            date=date,
            node_id=node_id,
        )
    if not rows:
        click.echo(f"No data for {date}.", err=True)
        sys.exit(0)

    rows = [_normalise_row(r, has_hour=True) for r in rows]

    if fmt == "json":
        print_json(rows)
        return

    table_data = [
        [
            f"{r['hour_utc']:02d}:00",
            r["model"],
            f"{r['request_count']:,}",
            f"{r['total_prompt_tokens']:,}",
            f"{r['total_completion_tokens']:,}",
            f"{r['total_tokens']:,}",
        ]
        for r in rows
    ]
    click.echo(
        tabulate(
            table_data,
            headers=["Hour (UTC)", "Model", "Requests", "Prompt Tokens",
                     "Completion Tokens", "Total Tokens"],
            tablefmt="simple",
        )
    )


# ────────────────────────────────────────────────────────────────────────
# by-model
# ────────────────────────────────────────────────────────────────────────

@cli.command()
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default=None,
    help='Output format: "table" (default in terminal) or "json" (default when piped).',
)
@click.option(
    "--backend",
    "backend",
    type=click.Choice(["auto", "sqlite", "postgres"]),
    default="auto",
    show_default=True,
    help="Read from local SQLite or central Postgres.",
)
@click.option(
    "--node",
    "node_id",
    default=None,
    help="Filter central/local results to one node_id.",
)
def by_model(output_format: str | None, backend: str, node_id: str | None) -> None:
    """All-time totals grouped by model name.

    Sorts models descending by total tokens so the heaviest users appear first.
    """
    fmt = resolve_format(output_format)
    backend_name, dsn = resolve_backend(backend)

    if backend_name == "postgres":
        try:
            raw_rows = postgres_store.get_by_model_summary(dsn or "", node_id=node_id)
        except Exception as exc:
            if not should_fallback_to_sqlite(backend, exc):
                raise click.ClickException(f"Postgres query failed: {exc}") from exc
            raw_rows = _db.get_by_model_summary(resolve_db_path(), node_id=node_id)
    else:
        raw_rows = _db.get_by_model_summary(resolve_db_path(), node_id=node_id)

    rows = [_normalise_row(row) for row in raw_rows]

    if not rows:
        click.echo("No data in database yet.", err=True)
        sys.exit(0)

    if fmt == "json":
        print_json(rows)
        return

    table_data = [
        [
            r["model"],
            f"{r['request_count']:,}",
            f"{r['total_prompt_tokens']:,}",
            f"{r['total_completion_tokens']:,}",
            f"{r['total_tokens']:,}",
        ]
        for r in rows
    ]
    click.echo(
        tabulate(
            table_data,
            headers=["Model", "Requests", "Prompt Tokens",
                     "Completion Tokens", "Total Tokens"],
            tablefmt="simple",
        )
    )


# -----------------------------------------------------------------------
# Row normalisation — ensure consistent dict keys across all views
#
# db.py get_daily_summary returns:
#   model, request_count, total_prompt_tokens,
#   total_completion_tokens, total_tokens, avg_response_ms
#
# db.py get_hourly_summary returns:
#   model, hour_utc, request_count, total_prompt_tokens,
#   total_completion_tokens, total_tokens
#
# We normalise to always have: date (daily only), model, request_count,
# total_prompt_tokens, total_completion_tokens, total_tokens.
# -----------------------------------------------------------------------

def _normalise_row(src: dict, row_date: str | None = None, has_hour: bool = False) -> dict:
    """Map a raw DB result row into the canonical shape used by this CLI.

    Parameters
    ----------
    src : dict
        Raw row from a db.py query.
    row_date : str | None
        ISO date string (YYYY-MM-DD) to inject when the source has no date column.
        For daily view: always pass the target date.
        For by-model and hourly views: leave as None (no date field).
    has_hour : bool
        Whether this is an hourly row (inject hour_utc instead of date).
    """
    out = {
        "model":                   src["model"],
        "request_count":           int(src["request_count"]),
        "total_prompt_tokens":     int(src["total_prompt_tokens"] or 0),
        "total_completion_tokens": int(src["total_completion_tokens"] or 0),
        "total_tokens":            int(src["total_tokens"] or 0),
    }
    if has_hour:
        out["hour_utc"] = int(src["hour_utc"])
    else:
        # Daily rows: caller passes the target date.
        # by-model aggregate: no single date — don't add the field at all.
        if row_date is not None:
            out["date"] = row_date
    return out


# -----------------------------------------------------------------------
# Entry point (allows: python -m queries.summary)
# -----------------------------------------------------------------------

if __name__ == "__main__":
    cli()
