#!/usr/bin/env python3
"""Initialise the central Postgres schema for token-sidecar reporting."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import postgres_store


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create token-sidecar Postgres tables, indexes, and views."
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help=(
            "Postgres DSN with DDL privileges. Defaults to "
            "TOKEN_SIDECAR_ADMIN_DSN, then TOKEN_SIDECAR_POSTGRES_DSN."
        ),
    )
    args = parser.parse_args()

    dsn = (
        args.dsn
        or os.environ.get("TOKEN_SIDECAR_ADMIN_DSN")
        or os.environ.get("TOKEN_SIDECAR_POSTGRES_DSN")
    )
    if not dsn:
        raise SystemExit(
            "No DSN supplied. Set TOKEN_SIDECAR_ADMIN_DSN or pass --dsn."
        )

    postgres_store.init_schema(dsn)
    print("Postgres schema ready.")


if __name__ == "__main__":
    main()
