"""
Tests for setup_launchd.py — LaunchAgent plist generation and management.

Uses pytest with tmp_path fixtures to test without touching the real
~/Library/LaunchAgents/ directory or calling actual launchctl.
"""

from __future__ import annotations

import os
import pathlib
import plistlib
import subprocess
from unittest.mock import patch, MagicMock

import pytest


# -----------------------------------------------------------------------
# Import module under test
# -----------------------------------------------------------------------
import sys
PROJECT_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from setup_launchd import (
    generate_plist_content,
    generate_dashboard_plist_content,
    generate_oracle_plist_content,
    PLIST_LABEL,
    DASHBOARD_PLIST_LABEL,
    ORACLE_PLIST_LABEL,
    DASHBOARD_ENV_FILE,
    LOCAL_ENV_FILE,
    POSTGRES_ENV_FILE,
)


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest.fixture
def project_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "token_sidecar"


@pytest.fixture
def sidecar_script(project_dir: pathlib.Path) -> pathlib.Path:
    """A dummy sidecar.py path inside the fake project directory."""
    # Create parent dir first — this was missing, causing FileNotFoundError
    project_dir.mkdir(parents=True, exist_ok=True)
    sc = project_dir / "sidecar.py"
    sc.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return sc


@pytest.fixture
def log_out_path(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / ".token_sidecar" / "sidecar.log"
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p


@pytest.fixture
def log_err_path(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / ".token_sidecar" / "sidecar.error.log"
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p


# -----------------------------------------------------------------------
# Tests — generate_plist_content()
# -----------------------------------------------------------------------

def test_plist_label_is_correct(
    project_dir: pathlib.Path,
    sidecar_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> None:
    xml = generate_plist_content(project_dir, sidecar_script, log_out_path, log_err_path)
    # plistlib uses \n\t indentation; check the label appears in any form
    assert f"<string>{PLIST_LABEL}</string>" in xml


def test_program_arguments_contains_python_and_sidecar(
    project_dir: pathlib.Path,
    sidecar_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> None:
    """ProgramArguments must be a list [python_exe, sidecar.py]."""
    xml = generate_plist_content(project_dir, sidecar_script, log_out_path, log_err_path)
    parsed = plistlib.loads(xml.encode("utf-8"))
    args = parsed["ProgramArguments"]
    assert isinstance(args, list)
    assert len(args) == 2
    # First arg must be an existing Python interpreter (sys.executable)
    assert pathlib.Path(args[0]).exists(), f"Python path does not exist: {args[0]}"
    # Second arg must be the sidecar script path
    assert args[1] == str(sidecar_script.resolve())


def test_run_at_load_true(
    project_dir: pathlib.Path,
    sidecar_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> None:
    xml = generate_plist_content(project_dir, sidecar_script, log_out_path, log_err_path)
    parsed = plistlib.loads(xml.encode("utf-8"))
    assert parsed["RunAtLoad"] is True


def test_keep_alive_successful_exit_false(
    project_dir: pathlib.Path,
    sidecar_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> None:
    """
    KeepAlive = {SuccessfulExit: False} means restart after crash (non-zero exit)
    but NOT after clean exit. This prevents an infinite restart loop.
    """
    xml = generate_plist_content(project_dir, sidecar_script, log_out_path, log_err_path)
    parsed = plistlib.loads(xml.encode("utf-8"))
    assert "KeepAlive" in parsed
    assert isinstance(parsed["KeepAlive"], dict)
    assert parsed["KeepAlive"]["SuccessfulExit"] is False


def test_standard_out_and_error_paths(
    project_dir: pathlib.Path,
    sidecar_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> None:
    xml = generate_plist_content(project_dir, sidecar_script, log_out_path, log_err_path)
    parsed = plistlib.loads(xml.encode("utf-8"))
    assert str(log_out_path) in (parsed.get("StandardOutPath") or "")
    assert str(log_err_path) in (parsed.get("StandardErrorPath") or "")


def test_working_directory_set(
    project_dir: pathlib.Path,
    sidecar_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> None:
    xml = generate_plist_content(project_dir, sidecar_script, log_out_path, log_err_path)
    parsed = plistlib.loads(xml.encode("utf-8"))
    assert str(project_dir.resolve()) == parsed.get("WorkingDirectory", "")


def test_environment_variables_can_be_embedded(
    project_dir: pathlib.Path,
    sidecar_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> None:
    """Central sync DSNs can be passed to launchd via EnvironmentVariables."""
    xml = generate_plist_content(
        project_dir,
        sidecar_script,
        log_out_path,
        log_err_path,
        environment_variables={
            "TOKEN_SIDECAR_CONFIG": "/tmp/config.yaml",
            "TOKEN_SIDECAR_POSTGRES_DSN": "postgresql://example",
        },
    )
    parsed = plistlib.loads(xml.encode("utf-8"))
    env = parsed["EnvironmentVariables"]
    assert env["TOKEN_SIDECAR_CONFIG"] == "/tmp/config.yaml"
    assert env["TOKEN_SIDECAR_POSTGRES_DSN"] == "postgresql://example"


def test_xml_is_valid_and_parseable(
    project_dir: pathlib.Path,
    sidecar_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> None:
    """The generated XML must parse without raising an exception."""
    xml = generate_plist_content(project_dir, sidecar_script, log_out_path, log_err_path)
    # Must not raise
    parsed = plistlib.loads(xml.encode("utf-8"))
    assert isinstance(parsed, dict)
    assert "Label" in parsed


def test_no_hardcoded_user_paths_in_xml(
    project_dir: pathlib.Path,
    sidecar_script: pathlib.Path,
    log_out_path: pathlib.Path,
    log_err_path: pathlib.Path,
) -> None:
    """Plist must not contain hardcoded config-driven paths like ~/Library/LaunchAgents."""
    xml = generate_plist_content(project_dir, sidecar_script, log_out_path, log_err_path)
    # Only the dynamically-generated temp dir paths should appear.
    # sys.executable is a real absolute path and will contain /Users/<name> — that
    # is expected and correct. The key check: no hardcoded ~/Library/LaunchAgents.
    assert "~/Library" not in xml


# -----------------------------------------------------------------------
# Tests — root guard (all commands refuse to run as euid 0)
# -----------------------------------------------------------------------

def test_install_rejects_root_user() -> None:
    """install() must refuse to run as root."""
    from setup_launchd import install

    with patch("os.geteuid", return_value=0):
        with pytest.raises(SystemExit) as exc_info:
            install()
        assert "Do not run as root" in str(exc_info.value)


def test_unload_rejects_root_user() -> None:
    """unload() must refuse to run as root."""
    from setup_launchd import unload

    with patch("os.geteuid", return_value=0):
        with pytest.raises(SystemExit) as exc_info:
            unload()
        assert "Do not run as root" in str(exc_info.value)


def test_remove_rejects_root_user() -> None:
    """remove() must refuse to run as root."""
    from setup_launchd import remove

    with patch("os.geteuid", return_value=0):
        with pytest.raises(SystemExit) as exc_info:
            remove()
        assert "Do not run as root" in str(exc_info.value)


def test_status_rejects_root_user() -> None:
    """status() must refuse to run as root."""
    from setup_launchd import status

    with patch("os.geteuid", return_value=0):
        with pytest.raises(SystemExit) as exc_info:
            status()
        assert "Do not run as root" in str(exc_info.value)


# -----------------------------------------------------------------------
# Tests — launchctl command construction
# -----------------------------------------------------------------------

def test_unload_calls_correct_launchctl_command() -> None:
    """
    unload() must invoke: launchctl bootout gui/<uid> com.athena.token-sidecar
    where <uid> is the real user id.
    """
    from setup_launchd import unload

    mock_run = MagicMock()
    completed = MagicMock()
    completed.stderr = b""
    mock_run.return_value = completed

    with patch("subprocess.run", mock_run):
        unload()

    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert call_args[:2] == ["launchctl", "bootout"]
    assert call_args[3] == PLIST_LABEL
    # domain is gui/<real uid>
    assert call_args[2].startswith("gui/")


def test_unload_idempotent_when_not_loaded() -> None:
    """
    When launchctl returns 'Could not find', unload() should exit cleanly
    (not raise) and print a message.
    """
    from setup_launchd import unload

    error = subprocess.CalledProcessError(1, [], stderr=b"Could not find specified job")

    with patch("subprocess.run", MagicMock(side_effect=error)):
        # Should NOT raise — handled gracefully
        unload()


def test_remove_deletes_plist_file(tmp_path: pathlib.Path) -> None:
    """
    remove() must delete the plist file if it exists.
    We mock os.geteuid and subprocess to isolate the file deletion path.
    """
    fake_agents = tmp_path / "LaunchAgents"
    fake_agents.mkdir(parents=True, exist_ok=True)
    fake_plist = fake_agents / f"{PLIST_LABEL}.plist"
    fake_plist.write_text(
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<!DOCTYPE plist PUBLIC '-//Apple//DTD PLIST 1.0//EN' "
        "'http://www.apple.com/DTDs/PropertyList'>"
        "<plist version='1.0'><dict></dict></plist>",
        encoding="utf-8",
    )

    with patch("os.geteuid", return_value=42), \
         patch("subprocess.run", MagicMock()), \
         patch("setup_launchd._get_plist_path", return_value=fake_plist):
        from setup_launchd import remove
        remove()

    assert not fake_plist.exists()


def test_status_prints_loaded_when_launchctl_succeeds() -> None:
    """status() should print [LOADED] when the agent is in launchd."""
    from setup_launchd import status

    mock_run = MagicMock()
    completed = MagicMock()
    completed.stderr = b"some service info"
    mock_run.return_value = completed

    with patch("subprocess.run", mock_run):
        # Capture stdout
        import io, contextlib
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            status()
        output = f.getvalue()

    assert "[LOADED]" in output


def test_status_prints_unloaded_when_not_found() -> None:
    """status() should print [UNLOADED] when launchctl returns 'Could not find'."""
    from setup_launchd import status

    error = subprocess.CalledProcessError(1, [], stderr=b"Could not find specified job")

    with patch("subprocess.run", MagicMock(side_effect=error)):
        import io, contextlib
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            status()
        output = f.getvalue()

    assert "[UNLOADED]" in output


# -----------------------------------------------------------------------
# Tests — dashboard plist
# -----------------------------------------------------------------------

@pytest.fixture
def dashboard_script(project_dir: pathlib.Path) -> pathlib.Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    p = project_dir / "dashboard.py"
    p.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return p


def test_dashboard_plist_uses_correct_label(
    project_dir: pathlib.Path,
    dashboard_script: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    xml = generate_dashboard_plist_content(
        project_dir, dashboard_script,
        tmp_path / "dashboard.log", tmp_path / "dashboard.error.log",
    )
    parsed = plistlib.loads(xml.encode())
    assert parsed["Label"] == DASHBOARD_PLIST_LABEL
    assert parsed["Label"] != PLIST_LABEL


def test_dashboard_plist_uses_shell_wrapper_that_sources_env_conditionally(
    project_dir: pathlib.Path,
    dashboard_script: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    xml = generate_dashboard_plist_content(
        project_dir, dashboard_script,
        tmp_path / "dashboard.log", tmp_path / "dashboard.error.log",
    )
    parsed = plistlib.loads(xml.encode())
    args = parsed["ProgramArguments"]
    assert args[0] == "/bin/sh"
    assert args[1] == "-c"
    wrapper = args[2]
    # Must be conditional (`if -f`), must always exec python (no `&&` short-circuit
    # before exec), and must reference the env file path.
    assert "if [ -f" in wrapper
    assert str(DASHBOARD_ENV_FILE) in wrapper
    assert "exec" in wrapper
    # The bug we're guarding against: `. file && exec ...` crash-loops if file
    # vanishes. The fragment that immediately precedes exec must be `; ` not `&&`.
    fi_idx = wrapper.index("fi;")
    exec_idx = wrapper.index("exec")
    between = wrapper[fi_idx:exec_idx]
    assert "&&" not in between


def test_dashboard_plist_keep_alive_uses_successful_exit_false(
    project_dir: pathlib.Path,
    dashboard_script: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    xml = generate_dashboard_plist_content(
        project_dir, dashboard_script,
        tmp_path / "dashboard.log", tmp_path / "dashboard.error.log",
    )
    parsed = plistlib.loads(xml.encode())
    # Same policy as the sidecar plist: respawn on crash, NOT on clean exit
    # (so a clean exit on missing DSN doesn't trigger a crash loop).
    assert parsed["KeepAlive"] == {"SuccessfulExit": False}
    assert parsed["RunAtLoad"] is True


def test_dashboard_plist_is_valid_xml(
    project_dir: pathlib.Path,
    dashboard_script: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    xml = generate_dashboard_plist_content(
        project_dir, dashboard_script,
        tmp_path / "dashboard.log", tmp_path / "dashboard.error.log",
    )
    parsed = plistlib.loads(xml.encode())
    assert parsed["Label"] == DASHBOARD_PLIST_LABEL
    assert str(tmp_path / "dashboard.log") == parsed["StandardOutPath"]
    assert str(tmp_path / "dashboard.error.log") == parsed["StandardErrorPath"]


# -----------------------------------------------------------------------
# Tests — oracle plist / preflight
# -----------------------------------------------------------------------

@pytest.fixture
def oracle_script(project_dir: pathlib.Path) -> pathlib.Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    api_dir = project_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    p = api_dir / "token_oracle_api.py"
    p.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return p


def test_oracle_plist_uses_correct_label(
    project_dir: pathlib.Path,
    oracle_script: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    xml = generate_oracle_plist_content(
        project_dir, oracle_script,
        tmp_path / "oracle.log", tmp_path / "oracle.error.log",
    )
    parsed = plistlib.loads(xml.encode())
    assert parsed["Label"] == ORACLE_PLIST_LABEL
    assert parsed["Label"] != DASHBOARD_PLIST_LABEL
    assert parsed["Label"] != PLIST_LABEL


def test_oracle_plist_sources_env_files_and_execs_python(
    project_dir: pathlib.Path,
    oracle_script: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    xml = generate_oracle_plist_content(
        project_dir, oracle_script,
        tmp_path / "oracle.log", tmp_path / "oracle.error.log",
    )
    parsed = plistlib.loads(xml.encode())
    args = parsed["ProgramArguments"]
    assert args[0] == "/bin/sh"
    assert args[1] == "-c"
    wrapper = args[2]
    assert str(LOCAL_ENV_FILE) in wrapper
    assert str(DASHBOARD_ENV_FILE) in wrapper
    assert str(POSTGRES_ENV_FILE) in wrapper
    assert "set -a" in wrapper
    assert "exec" in wrapper
    assert str(oracle_script.resolve()) in wrapper


def test_oracle_plist_keep_alive_uses_successful_exit_false(
    project_dir: pathlib.Path,
    oracle_script: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    xml = generate_oracle_plist_content(
        project_dir, oracle_script,
        tmp_path / "oracle.log", tmp_path / "oracle.error.log",
    )
    parsed = plistlib.loads(xml.encode())
    assert parsed["KeepAlive"] == {"SuccessfulExit": False}
    assert parsed["RunAtLoad"] is True
    assert str(tmp_path / "oracle.log") == parsed["StandardOutPath"]
    assert str(tmp_path / "oracle.error.log") == parsed["StandardErrorPath"]


def test_oracle_preflight_requires_enabled_gate() -> None:
    from config_loader import CentralDatabaseConfig, Config
    from setup_launchd import _oracle_preflight

    cfg = Config(
        listen_host="localhost",
        listen_port=1240,
        upstream_url="http://localhost:1234",
        database_path=pathlib.Path("/tmp/tokens.db"),
        log_level="INFO",
        node_id="test",
        central=CentralDatabaseConfig(),
        _raw={},
    )
    errors = _oracle_preflight(cfg)
    assert any("oracle.enabled is false" in err for err in errors)


def test_oracle_preflight_accepts_repo_dotenv(monkeypatch, tmp_path: pathlib.Path) -> None:
    from config_loader import CentralDatabaseConfig, Config, OracleConfig
    from setup_launchd import _oracle_preflight

    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN_SIDECAR_QUERY_DSN=postgresql://example\n", encoding="utf-8")

    cfg = Config(
        listen_host="localhost",
        listen_port=1240,
        upstream_url="http://localhost:1234",
        database_path=pathlib.Path("/tmp/tokens.db"),
        log_level="INFO",
        node_id="test",
        central=CentralDatabaseConfig(),
        oracle=OracleConfig(enabled=True),
        _raw={},
    )

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *_a, **_kw): pass
        def fetchone(self): return (1,)

    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def cursor(self): return FakeCursor()

    monkeypatch.delenv("TOKEN_SIDECAR_QUERY_DSN", raising=False)
    monkeypatch.setattr("setup_launchd.LOCAL_ENV_FILE", env_file)
    monkeypatch.setattr("setup_launchd.DASHBOARD_ENV_FILE", tmp_path / "missing-env.sh")
    monkeypatch.setattr("setup_launchd.POSTGRES_ENV_FILE", tmp_path / "missing-postgres.env")
    monkeypatch.setattr("psycopg.connect", lambda *_a, **_kw: FakeConn())

    assert _oracle_preflight(cfg) == []


def test_unload_oracle_calls_oracle_launchctl_label() -> None:
    from setup_launchd import unload

    mock_run = MagicMock()
    completed = MagicMock()
    completed.stderr = b""
    mock_run.return_value = completed

    with patch("subprocess.run", mock_run):
        unload(service="oracle")

    call_args = mock_run.call_args[0][0]
    assert call_args[:2] == ["launchctl", "bootout"]
    assert call_args[3] == ORACLE_PLIST_LABEL
