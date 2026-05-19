#!/usr/bin/env python3
"""Bootstrap the central Postgres database, roles, schema, and grants."""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import postgres_store

try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
except ImportError:  # pragma: no cover - dependency check path
    psycopg = None  # type: ignore[assignment]
    sql = None  # type: ignore[assignment]


DEFAULT_DATABASE = "token_sidecar"
DEFAULT_WRITER_ROLE = "token_sidecar_writer"
DEFAULT_READER_ROLE = "token_sidecar_reader"


def generate_password() -> str:
    """Return a URL-safe random password suitable for Postgres roles."""
    return secrets.token_urlsafe(32)


def build_dsn(role: str, password: str, host: str, port: int, database: str) -> str:
    """Build a libpq URL DSN with password escaping."""
    return (
        f"postgresql://{quote(role, safe='')}:"
        f"{quote(password, safe='')}@{host}:{port}/{quote(database, safe='')}"
    )


def with_database(admin_dsn: str, database: str) -> str:
    """Return an admin DSN pointing at the target database."""
    params = conninfo_to_dict(admin_dsn)
    params["dbname"] = database
    return make_conninfo("", **params)


def connect(dsn: str):
    if psycopg is None:
        raise RuntimeError("psycopg is not installed. Run `uv sync` first.")
    conn = psycopg.connect(dsn)
    conn.autocommit = True
    return conn


def database_exists(conn, database: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
        return cur.fetchone() is not None


def ensure_database(conn, database: str) -> bool:
    """Create the target database if needed. Return True if created."""
    if database_exists(conn, database):
        return False
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    return True


def role_exists(conn, role: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        return cur.fetchone() is not None


def ensure_role(
    conn,
    role: str,
    password: str | None,
    *,
    rotate_existing: bool,
) -> tuple[bool, str | None]:
    """
    Create or optionally update a login role.

    Returns ``(created_or_updated, known_password)``. For existing roles, the
    password is left untouched unless a password was supplied or rotation was
    requested, so reruns do not break deployed sidecars.
    """
    exists = role_exists(conn, role)
    if exists and password is None and not rotate_existing:
        return False, None

    effective_password = password or generate_password()
    with conn.cursor() as cur:
        if exists:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(role),
                    sql.Literal(effective_password),
                )
            )
        else:
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role),
                    sql.Literal(effective_password),
                )
            )
    return True, effective_password


def grant_privileges(conn, database: str, writer_role: str, reader_role: str) -> None:
    """Grant writer and reader privileges on the initialized schema."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                sql.Identifier(database),
                sql.Identifier(writer_role),
                sql.Identifier(reader_role),
            )
        )
        cur.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(
                sql.Identifier(writer_role),
                sql.Identifier(reader_role),
            )
        )
        cur.execute(
            sql.SQL("GRANT INSERT ON token_usage TO {}").format(
                sql.Identifier(writer_role)
            )
        )
        cur.execute(
            sql.SQL(
                "GRANT SELECT ON token_usage, token_usage_daily, "
                "token_usage_hourly, token_usage_by_model, token_usage_by_node TO {}"
            ).format(sql.Identifier(reader_role))
        )


def write_env_file(
    path: Path,
    *,
    writer_dsn: str | None,
    reader_dsn: str | None,
) -> None:
    """Write known DSNs to a shell-friendly env file with user-only perms."""
    path = path.expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    lines = [
        "# token-sidecar central Postgres credentials",
        "# Source this on sidecar/reporting machines as appropriate.",
    ]
    if writer_dsn:
        lines.append(f"export TOKEN_SIDECAR_POSTGRES_DSN='{writer_dsn}'")
    else:
        lines.append("# TOKEN_SIDECAR_POSTGRES_DSN unchanged; existing writer password is unknown.")
    if reader_dsn:
        lines.append(f"export TOKEN_SIDECAR_QUERY_DSN='{reader_dsn}'")
    else:
        lines.append("# TOKEN_SIDECAR_QUERY_DSN unchanged; existing reader password is unknown.")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create token-sidecar Postgres database, roles, schema, grants, "
            "and optional local env file."
        )
    )
    parser.add_argument(
        "--admin-dsn",
        default=os.environ.get("TOKEN_SIDECAR_ADMIN_DSN", "postgresql:///postgres"),
        help=(
            "Admin DSN for the maintenance DB. Defaults to "
            "TOKEN_SIDECAR_ADMIN_DSN or postgresql:///postgres."
        ),
    )
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--writer-role", default=DEFAULT_WRITER_ROLE)
    parser.add_argument("--reader-role", default=DEFAULT_READER_ROLE)
    parser.add_argument(
        "--writer-password",
        default=os.environ.get("TOKEN_SIDECAR_WRITER_PASSWORD"),
        help="Writer password. Defaults to TOKEN_SIDECAR_WRITER_PASSWORD or generated on create/rotate.",
    )
    parser.add_argument(
        "--reader-password",
        default=os.environ.get("TOKEN_SIDECAR_READER_PASSWORD"),
        help="Reader password. Defaults to TOKEN_SIDECAR_READER_PASSWORD or generated on create/rotate.",
    )
    parser.add_argument(
        "--rotate-passwords",
        action="store_true",
        help="Rotate passwords for existing writer/reader roles.",
    )
    parser.add_argument(
        "--report-host",
        default="nyx",
        help="Host name/IP to place in printed sidecar/reporting DSNs.",
    )
    parser.add_argument("--report-port", type=int, default=5432)
    parser.add_argument(
        "--write-env",
        default="~/.token_sidecar/postgres.env",
        help="Write known DSNs to this env file. Use --no-write-env to disable.",
    )
    parser.add_argument(
        "--no-write-env",
        action="store_true",
        help="Do not write a local env file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with connect(args.admin_dsn) as admin_conn:
        created_db = ensure_database(admin_conn, args.database)
        writer_changed, writer_password = ensure_role(
            admin_conn,
            args.writer_role,
            args.writer_password,
            rotate_existing=args.rotate_passwords,
        )
        reader_changed, reader_password = ensure_role(
            admin_conn,
            args.reader_role,
            args.reader_password,
            rotate_existing=args.rotate_passwords,
        )

    target_admin_dsn = with_database(args.admin_dsn, args.database)
    postgres_store.init_schema(target_admin_dsn)

    with connect(target_admin_dsn) as target_conn:
        grant_privileges(
            target_conn,
            args.database,
            args.writer_role,
            args.reader_role,
        )

    writer_dsn = (
        build_dsn(
            args.writer_role,
            writer_password,
            args.report_host,
            args.report_port,
            args.database,
        )
        if writer_password
        else None
    )
    reader_dsn = (
        build_dsn(
            args.reader_role,
            reader_password,
            args.report_host,
            args.report_port,
            args.database,
        )
        if reader_password
        else None
    )

    if not args.no_write_env:
        write_env_file(
            Path(args.write_env),
            writer_dsn=writer_dsn,
            reader_dsn=reader_dsn,
        )

    print("Postgres bootstrap complete.")
    print(f"Database: {args.database} ({'created' if created_db else 'already existed'})")
    print(
        f"Writer role: {args.writer_role} "
        f"({'created/updated' if writer_changed else 'already existed; password unchanged'})"
    )
    print(
        f"Reader role: {args.reader_role} "
        f"({'created/updated' if reader_changed else 'already existed; password unchanged'})"
    )
    print("")
    if writer_dsn:
        print("Sidecar writer DSN:")
        print(f"  {writer_dsn}")
    else:
        print("Sidecar writer DSN: existing password unchanged; rerun with --rotate-passwords to print a fresh DSN.")
    if reader_dsn:
        print("Reporting reader DSN:")
        print(f"  {reader_dsn}")
    else:
        print("Reporting reader DSN: existing password unchanged; rerun with --rotate-passwords to print a fresh DSN.")
    if not args.no_write_env:
        print("")
        print(f"Env file: {Path(args.write_env).expanduser()}")


if __name__ == "__main__":
    main()
