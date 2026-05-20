"""
Configuration loader for token-sidecar.

Loads config.yaml from:
  1. Path supplied via --config CLI argument (parse_cli_args())
  2. TOKEN_SIDECAR_CONFIG environment variable
  3. The project root directory (same dir as this file), defaulting to config.yaml

On load, validates required keys and raises ValueError with a descriptive message
if any are missing.
Paths containing ~ are expanded using pathlib.Path.expanduser().
"""

from __future__ import annotations

import argparse
import os
import pathlib
from dataclasses import dataclass, field

import yaml


REQUIRED_KEYS: list[str] = [
    "proxy.listen_host",
    "proxy.listen_port",
    "proxy.upstream_url",
    "database.path",
]


@dataclass(frozen=True)
class DashboardConfig:
    """
    Read-only dashboard service configuration (postgres box only).

    Attributes:
        enabled:           Gate for `setup_launchd.py install --service dashboard`.
                           Does NOT stop `python dashboard.py` from serving when
                           invoked directly.
        listen_host:       Bind address (default "0.0.0.0").
        listen_port:       TCP port (default 8080).
        feed_initial_rows: Activity-feed bootstrap + trim length.
        poll_interval_ms:  Client live-tick poll cadence in ms.
    """

    enabled: bool = False
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    feed_initial_rows: int = 50
    poll_interval_ms: int = 2200


@dataclass(frozen=True)
class CentralDatabaseConfig:
    """Configuration for optional centralized Postgres reporting."""

    enabled: bool = False
    driver: str = "postgres"
    dsn_env: str = "TOKEN_SIDECAR_POSTGRES_DSN"
    dsn: str | None = None
    flush_interval_seconds: float = 5.0
    batch_size: int = 100


@dataclass(frozen=True)
class Config:
    """
    Immutable configuration object for the token-sidecar.

    Attributes:
        listen_host:   Host the proxy binds to (e.g. "localhost").
        listen_port:   Port the proxy listens on (e.g. 1240).
        upstream_url:  Full URL of the upstream LM Studio API
                       (e.g. "http://localhost:1234").
        database_path: Expanded filesystem path to the SQLite DB file.
        log_level:     Logging level string (DEBUG, INFO, WARNING, ERROR).
        dashboard:     Optional dashboard service settings (postgres box only).
        node_id:       Stable reporting identity for this sidecar machine.
        central:       Optional central Postgres sync configuration.
        _raw:          Original dict for forward compatibility.
    """

    listen_host: str
    listen_port: int
    upstream_url: str
    database_path: pathlib.Path
    log_level: str
    node_id: str
    central: CentralDatabaseConfig
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    _raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> Config:
        """
        Validate and build a Config from the raw YAML dict.

        Raises:
            ValueError: if any required key is missing.
        """
        # Resolve dotted keys from nested dict structure
        proxy = _get(d, "proxy") or {}
        database = _get(d, "database") or {}
        node = _get(d, "node") or {}
        logging_cfg = _get(d, "logging") or {}
        dashboard_cfg = _get(d, "dashboard") or {}
        central_cfg = database.get("central") or {}

        # Check for missing required keys (not log_level which is optional)
        # Use explicit 'key not in dict' checks so that falsy values like 0 are accepted.
        missing: list[str] = []
        if "listen_host" not in proxy or not proxy["listen_host"]:
            missing.append("proxy.listen_host")
        if "listen_port" not in proxy:
            missing.append("proxy.listen_port")
        if "upstream_url" not in proxy or not proxy["upstream_url"]:
            missing.append("proxy.upstream_url")
        if "path" not in database or not database["path"]:
            missing.append("database.path")

        if missing:
            raise ValueError(
                f"Missing required config keys: {', '.join(missing)}. "
                f"Please set them in config.yaml before starting the sidecar."
            )

        db_path_raw = database.get("path", "~/.token_sidecar/tokens.db")
        db_path = pathlib.Path(db_path_raw).expanduser()
        central_enabled = _as_bool(central_cfg.get("enabled", False))
        node_id = str(node.get("id") or "").strip()
        if central_enabled and not node_id:
            raise ValueError(
                "Missing required config key: node.id. "
                "Set a stable node id when database.central.enabled is true."
            )
        if not node_id:
            node_id = "local"

        central_driver = str(central_cfg.get("driver", "postgres")).lower()
        if central_enabled and central_driver != "postgres":
            raise ValueError(
                "Unsupported database.central.driver: "
                f"{central_driver!r}. Only 'postgres' is supported."
            )

        dsn_env = str(
            central_cfg.get("dsn_env", "TOKEN_SIDECAR_POSTGRES_DSN")
        )
        central_dsn = os.environ.get(dsn_env)
        if central_enabled and not central_dsn:
            raise ValueError(
                f"Central Postgres sync is enabled, but {dsn_env} is not set."
            )

        flush_interval = float(central_cfg.get("flush_interval_seconds", 5))
        batch_size = int(central_cfg.get("batch_size", 100))
        if central_enabled and flush_interval <= 0:
            raise ValueError("database.central.flush_interval_seconds must be > 0.")
        if central_enabled and batch_size <= 0:
            raise ValueError("database.central.batch_size must be > 0.")

        dashboard = DashboardConfig(
            enabled=bool(dashboard_cfg.get("enabled", False)),
            listen_host=str(dashboard_cfg.get("listen_host", "0.0.0.0")),
            listen_port=int(dashboard_cfg.get("listen_port", 8080)),
            feed_initial_rows=int(dashboard_cfg.get("feed_initial_rows", 50)),
            poll_interval_ms=int(dashboard_cfg.get("poll_interval_ms", 2200)),
        )

        return cls(
            listen_host=str(proxy["listen_host"]),
            listen_port=int(proxy["listen_port"]),
            upstream_url=str(proxy["upstream_url"]),
            database_path=db_path,
            log_level=logging_cfg.get("level", "INFO").upper(),
            node_id=node_id,
            central=CentralDatabaseConfig(
                enabled=central_enabled,
                driver=central_driver,
                dsn_env=dsn_env,
                dsn=central_dsn,
                flush_interval_seconds=flush_interval,
                batch_size=batch_size,
            ),
            dashboard=dashboard,
            _raw=dict(d),
        )


def _get(d: dict, key: str):
    """Resolve a dotted key path into a dict (e.g. 'proxy.listen_host')."""
    parts = key.split(".")
    val: dict | None = d
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)  # type: ignore[assignment]
        else:
            return None
    return val


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def load_config(config_path: pathlib.Path | str | None = None) -> Config:
    """
    Load and validate config.yaml.

    Resolution order:
      1. ``config_path`` argument (from --config CLI or explicit call)
      2. ``TOKEN_SIDECAR_CONFIG`` environment variable
      3. ``config.yaml`` in the same directory as this module

    Args:
        config_path: Path to config file. If None, falls back to env var then
                     project-local config.yaml.

    Returns:
        A validated Config instance.

    Raises:
        FileNotFoundError:  Config file does not exist.
        ValueError:         One or more required keys are missing from the YAML.
    """
    if config_path is None:
        # Check environment variable next
        env = os.environ.get("TOKEN_SIDECAR_CONFIG")
        if env:
            config_path = env
        else:
            base = pathlib.Path(__file__).parent
            config_path = base / "config.yaml"
    else:
        config_path = pathlib.Path(config_path)

    # Normalize to Path (env var may be a plain string)
    config_path = pathlib.Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            f"Use --config <path> or TOKEN_SIDECAR_CONFIG=<path> to specify a location."
        )

    with open(config_path) as f:
        d = yaml.safe_load(f)

    return Config.from_dict(d or {})


def parse_cli_args() -> argparse.Namespace:
    """Parse --config argument for CLI override."""
    parser = argparse.ArgumentParser(description="Token Counter Sidecar")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.yaml (default: ./config.yaml)",
    )
    return parser.parse_args()
