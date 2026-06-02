#!/usr/bin/env python3
"""
LaunchAgent generator and management script for token-sidecar.

Handles lifecycle of the macOS LaunchAgent plist:
  - install   — generate plist from config.yaml, write to ~/Library/LaunchAgents/
  - unload    — stop the agent (keep plist file)
  - remove    — stop + delete the plist
  - status    — show loaded/unloaded state

Usage:
    uv run python setup_launchd.py install [--config <path>]
    uv run python setup_launchd.py uninstall
    uv run python setup_launchd.py status
"""

from __future__ import annotations

import argparse
import os
import pathlib
import plistlib
import subprocess
import sys
import textwrap
from typing import Optional


# Project root is one directory up from this script's location (scripts/ → project/)
PROJECT_ROOT = pathlib.Path(__file__).parent.resolve()
PLIST_LABEL = "com.athena.token-sidecar"
PLIST_FILENAME = f"{PLIST_LABEL}.plist"

DASHBOARD_PLIST_LABEL = "com.athena.token-sidecar-dashboard"
DASHBOARD_PLIST_FILENAME = f"{DASHBOARD_PLIST_LABEL}.plist"

ORACLE_PLIST_LABEL = "com.athena.token-oracle-api"
ORACLE_PLIST_FILENAME = f"{ORACLE_PLIST_LABEL}.plist"

# Optional env file the dashboard plist sources to get TOKEN_SIDECAR_QUERY_DSN
# without baking secrets into the plist.
DASHBOARD_ENV_FILE = pathlib.Path.home() / ".token_sidecar" / "env.sh"
LOCAL_ENV_FILE = PROJECT_ROOT / ".env"
POSTGRES_ENV_FILE = pathlib.Path.home() / ".token_sidecar" / "postgres.env"


def _get_launch_agents_dir() -> pathlib.Path:
    home = pathlib.Path.home()
    return home / "Library" / "LaunchAgents"


def _get_plist_path(service: str = "sidecar") -> pathlib.Path:
    if service == "dashboard":
        return _get_launch_agents_dir() / DASHBOARD_PLIST_FILENAME
    if service == "oracle":
        return _get_launch_agents_dir() / ORACLE_PLIST_FILENAME
    return _get_launch_agents_dir() / PLIST_FILENAME


def _label_for(service: str) -> str:
    if service == "dashboard":
        return DASHBOARD_PLIST_LABEL
    if service == "oracle":
        return ORACLE_PLIST_LABEL
    return PLIST_LABEL


# ---------------------------------------------------------------------------
# Plist generation
# ---------------------------------------------------------------------------

def generate_plist_content(
    project_dir: pathlib.Path,
    sidecar_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
    environment_variables: dict[str, str] | None = None,
) -> str:
    """
    Build and return the LaunchAgent plist XML as a string.

    Uses .venv/bin/python3 so the sidecar runs with all dependencies loaded.
    KeepAlive uses SuccessfulExit=false so it restarts after crashes but NOT
    after clean exits (which would cause an infinite restart loop on exit:0).
    """
    # Resolve symlinks for sidecar path only. For python_exe, use PROJECT_ROOT's
    # .venv/bin/python3 via os.path.abspath to get the absolute path WITHOUT
    # following the venv shim symlink (which points into ~/.local/share/uv/).
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python3"
    python_exe = pathlib.Path(os.path.abspath(str(venv_python)))
    sidecar_path = sidecar_script.resolve()

    plist_data = {
        "Label": PLIST_LABEL,
        "ProgramArguments": [
            str(python_exe),
            str(sidecar_path),
        ],
        "RunAtLoad": True,
        # Restart after crash; do NOT restart on clean exit (exit code 0)
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(log_out_path),
        "StandardErrorPath": str(log_err_path),
        "WorkingDirectory": str(project_dir),
    }
    if environment_variables:
        plist_data["EnvironmentVariables"] = environment_variables

    with open(plist_data["StandardOutPath"], "a", encoding="utf-8") as fh:
        # Ensure log directory exists before launchd tries to write
        pass

    buf = plistlib.dumps(plist_data, sort_keys=False)
    return buf.decode("utf-8")


def generate_dashboard_plist_content(
    project_dir: pathlib.Path,
    dashboard_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> str:
    """
    Build the dashboard LaunchAgent plist.

    Uses a /bin/sh wrapper that conditionally sources ~/.token_sidecar/env.sh
    (so a missing env file does NOT abort before python runs). When DSN is
    unset, dashboard.py exits 0 and KeepAlive: {SuccessfulExit: false} keeps
    the agent down — no crash loop.
    """
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python3"
    python_exe = pathlib.Path(os.path.abspath(str(venv_python)))
    dashboard_path = dashboard_script.resolve()

    wrapper = (
        f'if [ -f "{DASHBOARD_ENV_FILE}" ]; then . "{DASHBOARD_ENV_FILE}"; fi; '
        f'exec "{python_exe}" "{dashboard_path}"'
    )

    plist_data = {
        "Label": DASHBOARD_PLIST_LABEL,
        "ProgramArguments": ["/bin/sh", "-c", wrapper],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(log_out_path),
        "StandardErrorPath": str(log_err_path),
        "WorkingDirectory": str(project_dir),
        "ProcessType": "Background",
    }

    buf = plistlib.dumps(plist_data, sort_keys=False)
    return buf.decode("utf-8")


def _source_env_fragment(path: pathlib.Path) -> str:
    """Shell fragment that sources an env file and exports simple assignments."""
    return f'if [ -f "{path}" ]; then set -a; . "{path}"; set +a; fi'


def generate_oracle_plist_content(
    project_dir: pathlib.Path,
    oracle_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> str:
    """
    Build the Token Oracle API LaunchAgent plist.

    Sources repo `.env`, ~/.token_sidecar/env.sh, and postgres.env before
    exec'ing python so local development and installed nyx setups both work.
    """
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python3"
    python_exe = pathlib.Path(os.path.abspath(str(venv_python)))
    oracle_path = oracle_script.resolve()

    wrapper = (
        f"{_source_env_fragment(LOCAL_ENV_FILE)}; "
        f"{_source_env_fragment(DASHBOARD_ENV_FILE)}; "
        f"{_source_env_fragment(POSTGRES_ENV_FILE)}; "
        f'exec "{python_exe}" "{oracle_path}"'
    )

    plist_data = {
        "Label": ORACLE_PLIST_LABEL,
        "ProgramArguments": ["/bin/sh", "-c", wrapper],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(log_out_path),
        "StandardErrorPath": str(log_err_path),
        "WorkingDirectory": str(project_dir),
        "ProcessType": "Background",
    }

    buf = plistlib.dumps(plist_data, sort_keys=False)
    return buf.decode("utf-8")


# ---------------------------------------------------------------------------
# Pre-flight checks (dashboard install)
# ---------------------------------------------------------------------------

def _dashboard_preflight(cfg) -> list[str]:
    """
    Return a list of human-readable failure messages. Empty list means OK.

    Checks:
      1. config.dashboard.enabled is True
      2. TOKEN_SIDECAR_QUERY_DSN is set in current env, OR ~/.token_sidecar/env.sh
         exists and exports it
      3. psycopg / psycopg_pool imports succeed in the active venv
      4. SELECT 1 round-trip against the DSN succeeds
    """
    errors: list[str] = []

    if not cfg.dashboard.enabled:
        errors.append(
            "config.yaml: dashboard.enabled is false. "
            "Flip it to true on the postgres box before installing the dashboard plist."
        )

    dsn = os.environ.get("TOKEN_SIDECAR_QUERY_DSN", "").strip()
    if not dsn:
        if DASHBOARD_ENV_FILE.exists():
            try:
                contents = DASHBOARD_ENV_FILE.read_text()
            except OSError as exc:
                errors.append(f"could not read {DASHBOARD_ENV_FILE}: {exc}")
                contents = ""
            if "TOKEN_SIDECAR_QUERY_DSN" not in contents:
                errors.append(
                    f"{DASHBOARD_ENV_FILE} exists but does not export "
                    f"TOKEN_SIDECAR_QUERY_DSN. Add: "
                    f"export TOKEN_SIDECAR_QUERY_DSN=postgresql://..."
                )
        else:
            errors.append(
                f"TOKEN_SIDECAR_QUERY_DSN is not set in this shell and "
                f"{DASHBOARD_ENV_FILE} does not exist. Create it with:\n"
                f"    mkdir -p {DASHBOARD_ENV_FILE.parent}\n"
                f"    echo 'export TOKEN_SIDECAR_QUERY_DSN=postgresql://...' > {DASHBOARD_ENV_FILE}\n"
                f"    chmod 600 {DASHBOARD_ENV_FILE}"
            )

    try:
        import psycopg  # noqa: F401
        import psycopg_pool  # noqa: F401
    except ImportError as exc:
        errors.append(
            f"psycopg/psycopg_pool not importable in the active venv: {exc}. "
            f"Run `uv sync` from {PROJECT_ROOT}."
        )

    if dsn and not errors:
        try:
            import psycopg
            with psycopg.connect(dsn, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
        except Exception as exc:
            errors.append(f"could not reach Postgres with TOKEN_SIDECAR_QUERY_DSN: {exc}")

    return errors


def _oracle_preflight(cfg) -> list[str]:
    """
    Return human-readable Token Oracle launchd preflight failures.

    Checks config gate, DSN discoverability, imports, and a SELECT 1 round-trip.
    """
    errors: list[str] = []

    if not cfg.oracle.enabled:
        errors.append(
            "config.yaml: oracle.enabled is false. "
            "Flip it to true on the postgres box before installing the oracle plist."
        )

    try:
        from pg_common import resolve_env_value
        dsn = resolve_env_value(
            cfg.oracle.dsn_env,
            (LOCAL_ENV_FILE, DASHBOARD_ENV_FILE, POSTGRES_ENV_FILE),
        )
    except Exception as exc:
        errors.append(f"could not inspect Oracle DSN env files: {exc}")
        dsn = None

    if not dsn:
        errors.append(
            f"{cfg.oracle.dsn_env} is not set in this shell, {LOCAL_ENV_FILE}, "
            f"{DASHBOARD_ENV_FILE}, or {POSTGRES_ENV_FILE}. Add an export like:\n"
            f"    export {cfg.oracle.dsn_env}=postgresql://..."
        )

    try:
        import aiohttp  # noqa: F401
        import psycopg  # noqa: F401
        import psycopg_pool  # noqa: F401
    except ImportError as exc:
        errors.append(
            f"aiohttp/psycopg/psycopg_pool not importable in the active venv: {exc}. "
            f"Run `uv sync` from {PROJECT_ROOT}."
        )

    if dsn and not errors:
        try:
            import psycopg
            with psycopg.connect(dsn, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
        except Exception as exc:
            errors.append(f"could not reach Postgres with {cfg.oracle.dsn_env}: {exc}")

    return errors


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def _run_launchctl(args: list[str], check: bool = True) -> subprocess.CompletedProcess[bytes]:
    """Run a launchctl command; return CompletedProcess."""
    return subprocess.run(
        ["launchctl"] + args,
        capture_output=True,
        check=check,
    )


def _current_uid() -> str:
    return str(os.getuid())


def install(config_path: Optional[str] = None, service: str = "sidecar") -> None:
    """
    Load config, generate the plist XML, and write it to ~/Library/LaunchAgents/.
    Prints instructions for loading the agent.

    For service == "dashboard", a pre-flight checks DSN reachability and
    config.dashboard.enabled before any state is written.
    """
    # Guard against root — LaunchAgent is a user-space concept
    if os.geteuid() == 0:
        sys.exit("ERROR: Do not run as root. This is a user-scope LaunchAgent.\n"
                "       Run as your normal user account.")

    # Load config to get database path (for log directory)
    from config_loader import load_config
    effective_config_path = config_path or os.environ.get("TOKEN_SIDECAR_CONFIG")
    cfg = load_config(config_path)

    # Log paths under ~/.token_sidecar/
    log_dir = pathlib.Path(cfg.database_path).parent.resolve()
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    if service == "dashboard":
        errors = _dashboard_preflight(cfg)
        if errors:
            sys.stderr.write("ERROR: dashboard pre-flight checks failed:\n")
            for e in errors:
                sys.stderr.write(f"  - {e}\n")
            sys.exit(1)

        plist_content = generate_dashboard_plist_content(
            project_dir=PROJECT_ROOT,
            dashboard_script=PROJECT_ROOT / "dashboard.py",
            log_out_path=log_dir / "dashboard.log",
            log_err_path=log_dir / "dashboard.error.log",
        )
    elif service == "oracle":
        errors = _oracle_preflight(cfg)
        if errors:
            sys.stderr.write("ERROR: oracle pre-flight checks failed:\n")
            for e in errors:
                sys.stderr.write(f"  - {e}\n")
            sys.exit(1)

        plist_content = generate_oracle_plist_content(
            project_dir=PROJECT_ROOT,
            oracle_script=PROJECT_ROOT / "api" / "token_oracle_api.py",
            log_out_path=log_dir / "oracle.log",
            log_err_path=log_dir / "oracle.error.log",
        )
    else:
        config_file = (
            pathlib.Path(effective_config_path).expanduser().resolve()
            if effective_config_path
            else PROJECT_ROOT / "config.yaml"
        )
        environment_variables = {
            "TOKEN_SIDECAR_CONFIG": str(config_file),
        }
        if cfg.central.enabled and cfg.central.dsn:
            environment_variables[cfg.central.dsn_env] = cfg.central.dsn

        plist_content = generate_plist_content(
            project_dir=PROJECT_ROOT,
            sidecar_script=PROJECT_ROOT / "sidecar.py",
            log_out_path=log_dir / "sidecar.log",
            log_err_path=log_dir / "sidecar.error.log",
            environment_variables=environment_variables,
        )

    plist_path = _get_plist_path(service)

    with open(plist_path, "w", encoding="utf-8") as fh:
        fh.write(plist_content)
    # If the sidecar plist embeds the central DSN, keep it owner-readable only.
    if service == "sidecar" and cfg.central.enabled:
        os.chmod(plist_path, 0o600)
    else:
        os.chmod(plist_path, 0o644)

    print(f"Plist written to:\n  {plist_path}\n")
    _print_load_instructions(service)


def unload(service: str = "sidecar") -> None:
    """
    Stop the LaunchAgent (unload from launchd) without removing the plist file.
    Idempotent — does not error if already unloaded.
    """
    if os.geteuid() == 0:
        sys.exit("ERROR: Do not run as root.")

    label = _label_for(service)
    domain = f"gui/{_current_uid()}"

    try:
        _run_launchctl(["bootout", domain, label], check=True)
        print(f"Unloaded: {label}")
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
        if "could not find" in stderr.lower() or "not loaded" in stderr.lower():
            print(f"Already unloaded (or never loaded): {label}")
        else:
            sys.exit(f"launchctl bootout failed:\n{stderr}")


def remove(service: str = "sidecar") -> None:
    """Stop the agent and delete the plist file."""
    if os.geteuid() == 0:
        sys.exit("ERROR: Do not run as root.")

    unload(service)

    plist_path = _get_plist_path(service)
    if plist_path.exists():
        plist_path.unlink()
        print(f"Removed plist: {plist_path}")
    else:
        print(f"No plist file found at {plist_path} (nothing to remove)")


def status(service: str = "sidecar") -> None:
    """Check and display the current launchd state of the agent."""
    if os.geteuid() == 0:
        sys.exit("ERROR: Do not run as root.")

    label = _label_for(service)
    plist_path = _get_plist_path(service)

    # Use `launchctl list` to check if a named job is currently loaded.
    # This works reliably regardless of TTY state (unlike `print` which
    # emits domain info in non-TTY contexts even when label is unknown).
    try:
        result = _run_launchctl(["list", label], check=True)
        stdout = result.stdout.decode("utf-8", errors="replace")
        if stdout.strip():
            print(f"[LOADED]   {label}")
        else:
            # Empty output means no process with this label
            print(f"[UNLOADED] {label}")
    except subprocess.CalledProcessError:
        # Non-zero exit from `launchctl list <label>` means not loaded
        print(f"[UNLOADED] {label}")

    print(f"Plist path: {plist_path}")
    print(f"Plist exists: {plist_path.exists()}")


def _print_load_instructions(service: str = "sidecar") -> None:
    label = _label_for(service)
    plist = _get_plist_path(service)
    print(textwrap.dedent(f"""\
        To load the LaunchAgent (starts immediately and on every login):
          launchctl bootstrap gui/$(id -u) {plist}

        Or more simply (load or reload if already installed):
          launchctl kickstart -kp gui/$(id -u)/{label}

        To unload and stop it without removing the plist:
          uv run python setup_launchd.py unload --service {service}
    """))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage the token-sidecar LaunchAgent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_service_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--service",
            choices=("sidecar", "dashboard", "oracle"),
            default="sidecar",
            help="Which LaunchAgent to act on (default: sidecar).",
        )

    install_parser = subparsers.add_parser(
        "install",
        help="Generate plist from config.yaml and write to ~/Library/LaunchAgents/",
    )
    install_parser.add_argument(
        "--config",
        help="Path to config.yaml (default: project root)",
        default=None,
    )
    _add_service_arg(install_parser)

    unload_parser = subparsers.add_parser("unload", help="Stop the agent without removing the plist")
    _add_service_arg(unload_parser)
    remove_parser = subparsers.add_parser("remove", help="Stop and delete the LaunchAgent")
    _add_service_arg(remove_parser)
    status_parser = subparsers.add_parser("status", help="Show loaded/unloaded state")
    _add_service_arg(status_parser)

    args = parser.parse_args()

    if args.command == "install":
        install(args.config, args.service)
    elif args.command == "unload":
        unload(args.service)
    elif args.command == "remove":
        remove(args.service)
    elif args.command == "status":
        status(args.service)


if __name__ == "__main__":
    main()
