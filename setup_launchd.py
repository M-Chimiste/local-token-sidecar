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


def _get_launch_agents_dir() -> pathlib.Path:
    home = pathlib.Path.home()
    return home / "Library" / "LaunchAgents"


def _get_plist_path() -> pathlib.Path:
    return _get_launch_agents_dir() / PLIST_FILENAME


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


def install(config_path: Optional[str] = None) -> None:
    """
    Load config, generate the plist XML, and write it to ~/Library/LaunchAgents/.
    Prints instructions for loading the agent.
    """
    # Guard against root — LaunchAgent is a user-space concept
    if os.geteuid() == 0:
        sys.exit("ERROR: Do not run as root. This is a user-scope LaunchAgent.\n"
                "       Run as your normal user account.")

    # Load config to get database path (for log directory)
    from config_loader import load_config
    effective_config_path = config_path or os.environ.get("TOKEN_SIDECAR_CONFIG")
    cfg = load_config(config_path)

    # Sidecar script and project root
    sidecar_script = PROJECT_ROOT / "sidecar.py"

    # Log paths under ~/.token_sidecar/
    log_dir = pathlib.Path(cfg.database_path).parent.resolve()
    log_out_path = log_dir / "sidecar.log"
    log_err_path = log_dir / "sidecar.error.log"
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

    # Ensure the directory exists with restricted permissions
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    plist_content = generate_plist_content(
        project_dir=PROJECT_ROOT,
        sidecar_script=sidecar_script,
        log_out_path=log_out_path,
        log_err_path=log_err_path,
        environment_variables=environment_variables,
    )

    plist_path = _get_plist_path()

    # Write plist
    with open(plist_path, "w", encoding="utf-8") as fh:
        fh.write(plist_content)
    # If central sync is enabled the plist may contain the DSN env var.
    os.chmod(plist_path, 0o600 if cfg.central.enabled else 0o644)

    print(f"Plist written to:\n  {plist_path}\n")
    _print_load_instructions()


def unload() -> None:
    """
    Stop the LaunchAgent (unload from launchd) without removing the plist file.
    Idempotent — does not error if already unloaded.
    """
    if os.geteuid() == 0:
        sys.exit("ERROR: Do not run as root.")

    domain = f"gui/{_current_uid()}"

    try:
        _run_launchctl(["bootout", domain, PLIST_LABEL], check=True)
        print(f"Unloaded: {PLIST_LABEL}")
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
        if "could not find" in stderr.lower() or "not loaded" in stderr.lower():
            print(f"Already unloaded (or never loaded): {PLIST_LABEL}")
        else:
            sys.exit(f"launchctl bootout failed:\n{stderr}")


def remove() -> None:
    """Stop the agent and delete the plist file."""
    if os.geteuid() == 0:
        sys.exit("ERROR: Do not run as root.")

    unload()

    plist_path = _get_plist_path()
    if plist_path.exists():
        plist_path.unlink()
        print(f"Removed plist: {plist_path}")
    else:
        print(f"No plist file found at {plist_path} (nothing to remove)")


def status() -> None:
    """Check and display the current launchd state of the agent."""
    if os.geteuid() == 0:
        sys.exit("ERROR: Do not run as root.")

    plist_path = _get_plist_path()

    # Use `launchctl list` to check if a named job is currently loaded.
    # This works reliably regardless of TTY state (unlike `print` which
    # emits domain info in non-TTY contexts even when label is unknown).
    try:
        result = _run_launchctl(["list", PLIST_LABEL], check=True)
        stdout = result.stdout.decode("utf-8", errors="replace")
        if stdout.strip():
            print(f"[LOADED]   {PLIST_LABEL}")
        else:
            # Empty output means no process with this label
            print(f"[UNLOADED] {PLIST_LABEL}")
    except subprocess.CalledProcessError as exc:
        # Non-zero exit from `launchctl list <label>` means not loaded
        print(f"[UNLOADED] {PLIST_LABEL}")

    plist_path = _get_plist_path()
    print(f"Plist path: {plist_path}")
    print(f"Plist exists: {plist_path.exists()}")


def _print_load_instructions() -> None:
    print(textwrap.dedent("""\
        To load the LaunchAgent (starts immediately and on every login):
          launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.athena.token-sidecar.plist

        Or more simply (load or reload if already installed):
          launchctl kickstart -kp gui/$(id -u)/com.athena.token-sidecar

        To unload and stop it without removing the plist:
          uv run python setup_launchd.py unload
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

    install_parser = subparsers.add_parser(
        "install",
        help="Generate plist from config.yaml and write to ~/Library/LaunchAgents/",
    )
    install_parser.add_argument(
        "--config",
        help="Path to config.yaml (default: project root)",
        default=None,
    )

    subparsers.add_parser("unload", help="Stop the agent without removing the plist")
    subparsers.add_parser("remove", help="Stop and delete the LaunchAgent")
    subparsers.add_parser("status", help="Show loaded/unloaded state")

    args = parser.parse_args()

    if args.command == "install":
        install(args.config)
    elif args.command == "unload":
        unload()
    elif args.command == "remove":
        remove()
    elif args.command == "status":
        status()


if __name__ == "__main__":
    main()
