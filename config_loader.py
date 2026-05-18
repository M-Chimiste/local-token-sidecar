"""
Configuration loader for token-sidecar.

Loads config.yaml from:
  1. Path supplied via --config CLI argument (parse_cli_args())
  2. The project root directory (same dir as this file), defaulting to config.yaml

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
        _raw:          Original dict for forward compatibility.
    """

    listen_host: str
    listen_port: int
    upstream_url: str
    database_path: pathlib.Path
    log_level: str
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
        logging_cfg = _get(d, "logging") or {}

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

        return cls(
            listen_host=str(proxy["listen_host"]),
            listen_port=int(proxy["listen_port"]),
            upstream_url=str(proxy["upstream_url"]),
            database_path=db_path,
            log_level=logging_cfg.get("level", "INFO").upper(),
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