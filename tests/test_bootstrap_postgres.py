"""Tests for the local Postgres bootstrap helper script."""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "bootstrap_postgres.py"

spec = importlib.util.spec_from_file_location("bootstrap_postgres", SCRIPT_PATH)
bootstrap_postgres = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bootstrap_postgres)


def test_build_dsn_escapes_password_and_database() -> None:
    dsn = bootstrap_postgres.build_dsn(
        "token_sidecar_writer",
        "p@ss/word",
        "nyx.tailnet.ts.net",
        5432,
        "token_sidecar",
    )

    assert dsn == (
        "postgresql://token_sidecar_writer:p%40ss%2Fword"
        "@nyx.tailnet.ts.net:5432/token_sidecar"
    )


def test_write_env_file_uses_user_only_permissions(tmp_path: Path) -> None:
    env_file = tmp_path / ".token_sidecar" / "postgres.env"

    bootstrap_postgres.write_env_file(
        env_file,
        writer_dsn="postgresql://writer:pw@nyx:5432/token_sidecar",
        reader_dsn="postgresql://reader:pw@nyx:5432/token_sidecar",
    )

    content = env_file.read_text(encoding="utf-8")
    mode = stat.S_IMODE(env_file.stat().st_mode)

    assert "TOKEN_SIDECAR_POSTGRES_DSN" in content
    assert "TOKEN_SIDECAR_QUERY_DSN" in content
    assert mode == 0o600


def test_generate_password_is_long_and_urlsafe() -> None:
    password = bootstrap_postgres.generate_password()
    assert len(password) >= 32
    assert "\n" not in password
