# Phase 3 — Configuration: Detailed Implementation Plan

**Project:** token-sidecar  
**Phase:** 3 of 7  
**Parent plan:** `../implementation_plan.md`  
**Requirements:** `../requirements.md`  
**Goal:** Make every runtime value config-driven; no hardcoded values anywhere in any module.

---

## Context — What Exists Already

During Phase 2 we fast-tracked a lightweight `config_loader.py` so the proxy wasn't hardcoded:

```python
# config_loader.py (Phase 2 snapshot)
def load_config(config_path=None) -> dict:
    if config_path is None:
        base = pathlib.Path(__file__).parent   # ← resolves relative to this file
        config_path = base / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)
```

`sidecar.py`'s `create_app()` already accepts a full config dict (not hardcoded values inside the app).

**What Phase 3 must add:**
1. Formalise `load_config` into a typed `Config` class
2. Validate required keys at startup — fail fast with clear errors
3. Add `--config <path>` CLI argument to `sidecar.py`
4. Audit every module and remove all remaining hardcoded values in favour of the Config object
5. Make log level configurable throughout the app

---

## Exit Criteria Checklist

- [ ] `Config` class replaces raw dict return from `load_config()`
- [ ] Startup raises `ValueError` with a descriptive message for any missing required key (listen_host, listen_port, upstream_url, db_path)
- [ ] `--config <path>` CLI arg overrides default config.yaml location in `sidecar.py`
- [ ] Log level from config.yaml is applied to the aiohttp app logger
- [ ] Audit pass: no module reads `localhost`, port numbers, or file paths directly — all go through Config
- [ ] All existing tests still pass (6 proxy + 11 db = 17/17)
- [ ] New test added for `--config` override path

---

## Step-by-Step Instructions

### Step 1 — Audit Existing Code for Hardcoded Values

Before writing any new code, audit every `.py` file in the project root and `tests/` to find all hardcoded values that should come from Config. Document them here so we know what must change.

**Files to audit:**
- `sidecar.py`
- `config_loader.py`
- `db.py`
- `setup_launchd.py` (placeholder)
- `queries/summary.py` (placeholder)

**Expected findings — values that need to be config-driven:**

| File | Hardcoded value | Config key |
|------|----------------|------------|
| sidecar.py | `"localhost"`, `1240` | `proxy.listen_host`, `proxy.listen_port` |
| sidecar.py | `"http://localhost:1234"` (in example) | `proxy.upstream_url` |
| config_loader.py | `"config.yaml"` (default filename) | N/A — this is the default, correct |

> **Note:** The current `sidecar.py`'s `create_app()` already receives its values as parameters. The hardcoding risk is only in `__main__` at the bottom of sidecar.py where it calls `load_config()` and accesses keys directly. We'll fix that.

---

### Step 2 — Formalise Config Class

Replace `config_loader.py`'s dict return with a typed `Config` dataclass (or simple class) that provides:

- Typed attributes: `listen_host`, `listen_port`, `upstream_url`, `database_path`, `log_level`
- Constructor validates required keys and raises `ValueError` with a clear message
- Expand `~` in `database_path` using `pathlib.Path.expanduser()`
- Store the raw dict for forward compatibility

**Commands:**
```bash
cd ~/Documents/hermes_projects/token_sidecar
# No files to create — edit config_loader.py in place (Step 3)
```

**Verification after Step 3:** Run `uv run python -c "from config_loader import load_config; c = load_config(); print(c.listen_port)"` — should print `1240`.

---

### Step 3 — Rewrite `config_loader.py`

Replace the existing `load_config()` with a new implementation:

```python
# config_loader.py

"""
Configuration loader for token-sidecar.

Loads config.yaml from:
  1. Path supplied via --config CLI argument
  2. The project root directory (same dir as this file), defaulting to config.yaml

On load, validates required keys and raises ValueError with a descriptive message if any are missing.
Paths containing ~ are expanded using pathlib.Path.expanduser().
"""

from __future__ import annotations

import argparse
import pathlib
from dataclasses import dataclass

import yaml


REQUIRED_KEYS = ["proxy.listen_host", "proxy.listen_port", "proxy.upstream_url", "database.path"]


@dataclass(frozen=True)
class Config:
    """
    Immutable configuration object for the token-sidecar.

    Attributes:
        listen_host:     Host the proxy binds to (e.g. "localhost")
        listen_port:     Port the proxy listens on (e.g. 1240)
        upstream_url:    Full URL of the upstream LM Studio API (e.g. "http://localhost:1234")
        database_path:   Expanded filesystem path to the SQLite DB file
        log_level:       Logging level string (DEBUG, INFO, WARNING, ERROR)
        _raw:            Original dict for forward compatibility
    """

    listen_host: str
    listen_port: int
    upstream_url: str
    database_path: pathlib.Path
    log_level: str
    _raw: dict

    @classmethod
    def from_dict(cls, d: dict) -> Config:
        """Validate and build a Config from the raw YAML dict."""
        # Resolve dotted keys like "proxy.listen_host"
        proxy = d.get("proxy", {})
        database = d.get("database", {})
        logging_cfg = d.get("logging", {})

        missing = [
            key for key in REQUIRED_KEYS
            if cls._get(d, key) is None and key != "log_level"  # log_level is optional
        ]
        if missing:
            raise ValueError(
                f"Missing required config keys: {', '.join(missing)}. "
                f"Please set them in config.yaml."
            )

        db_path_raw = database.get("path", "~/.token_sidecar/tokens.db")
        db_path = pathlib.Path(db_path_raw).expanduser()

        return cls(
            listen_host=proxy.get("listen_host", "localhost"),
            listen_port=int(proxy.get("listen_port", 1240)),
            upstream_url=proxy.get("upstream_url", "http://localhost:1234"),
            database_path=db_path,
            log_level=logging_cfg.get("level", "INFO").upper(),
            _raw=d,
        )

    @staticmethod
    def _get(d: dict, key: str):
        """Resolve a dotted key path into a dict."""
        parts = key.split(".")
        val = d
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                return None
        return val


def load_config(config_path: pathlib.Path | str | None = None) -> Config:
    """
    Load and validate config.yaml.

    Args:
        config_path: Path to config file. If None, defaults to config.yaml in the
                     same directory as this module.

    Returns:
        A validated Config instance.

    Raises:
        FileNotFoundError:  Config file does not exist.
        ValueError:         One or more required keys are missing from the YAML.
    """
    if config_path is None:
        base = pathlib.Path(__file__).parent
        config_path = base / "config.yaml"
    else:
        config_path = pathlib.Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            f"Use --config <path> to specify a custom location."
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
```

**Verification:**
```bash
cd ~/Documents/hermes_projects/token_sidecar
uv run python -c "from config_loader import load_config; c = load_config(); print(c.listen_port, c.upstream_url)"
# Expected output: 1240 http://localhost:1234
```

---

### Step 4 — Add `--config` CLI Arg to `sidecar.py`

Update the `if __name__ == "__main__":` block at the bottom of `sidecar.py`:

**Before (current):**
```python
# sidecar.py — current __main__ block
if __name__ == "__main__":
    config = load_config()
    app = create_app(config)
    host = config["proxy"]["listen_host"]
    port = config["proxy"]["listen_port"]
```

**After:**
```python
# sidecar.py — updated __main__ block
if __name__ == "__main__":
    cli_args = parse_cli_args()
    cfg = load_config(cli_args.config)

    # Configure aiohttp app logger to use the log level from config.yaml
    import logging
    numeric_level = getattr(logging, cfg.log_level, logging.INFO)
    logging.basicConfig(level=numeric_level,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    app = create_app(cfg)

    print(f"Starting token-sidecar on {cfg.listen_host}:{cfg.listen_port}")
    print(f"Upstream: {cfg.upstream_url}  |  DB: {cfg.database_path}")

    web.run_app(app, host=cfg.listen_host, port=cfg.listen_port)
```

Also update `create_app`'s type hint from `dict` to `Config`.

**Verification:**
```bash
cd ~/Documents/hermes_projects/token_sidecar
uv run python sidecar.py --config /nonexistent/path.yaml 2>&1 | head -5
# Expected: FileNotFoundError message mentioning the path

# Then start normally and verify startup output:
uv run python sidecar.py &
sleep 2; curl -s http://localhost:1240/v1/models && kill %1
```

---

### Step 5 — Update `db.py` to Accept Config Object (not dict)

Currently `init_db`, `log_token_usage`, etc. accept a raw `db_path: str`. This is fine since the DB path comes from `Config.database_path.pathlib.Path` which has a `__str__` method.

**No code changes needed in db.py** — the interface already takes a string path, and we pass `str(cfg.database_path)`.

However, add an assertion check at the top of each public function to catch None/empty strings:
```python
# Add after imports in db.py
assert db_path and isinstance(db_path, str), "db_path must be a non-empty string"
```

---

### Step 6 — Audit Pass: Remove Remaining Hardcoded Values

Inspect every `.py` file for remaining hardcoded values:

**`sidecar.py`** — check `__main__` block:
- [x] Already uses `cfg.listen_host`, `cfg.listen_port`, `cfg.upstream_url`
- [ ] Check the handler functions (not in __main__) — they already receive config object
- [ ] No hardcoded port values remain

**`setup_launchd.py`** and **`queries/summary.py`** are placeholders — no changes needed yet.

---

### Step 7 — Add Test for `--config` Override

Add a new test to `tests/test_proxy.py`:

```python
async def test_sidecar_starts_with_custom_config_file(db_path, aiohttp_client,
                                                       upstream_chat_server):
    """Passing --config <path> uses the specified file instead of default."""
    import tempfile, os, pathlib

    custom_db_dir = tempfile.mkdtemp()
    custom_config_path = os.path.join(custom_db_dir, "custom.yaml")

    # Write a config that points at our test upstream
    mock_client = await aiohttp_client(upstream_chat_server)
    upstream_base = str(mock_client.make_url("")).rstrip("/")
    import yaml
    with open(custom_config_path, "w") as f:
        yaml.dump({
            "proxy": {"listen_host": "localhost", "listen_port": 0,
                      "upstream_url": upstream_base},
            "database": {"path": os.path.join(custom_db_dir, "test.db")},
            "logging": {"level": "CRITICAL"},
        }, f)

    # Re-initialise DB at the custom path
    import db as _db
    _db.init_db(os.path.join(custom_db_dir, "test.db"))

    cfg = load_config(pathlib.Path(custom_config_path))
    assert cfg.upstream_url == upstream_base

    sidecar_app = create_app(cfg)
    async with TestClient(TestServer(sidecar_app)) as sc:
        resp = await sc.post("/v1/chat/completions",
                             json={"model": "custom-model",
                                   "messages": [{"role":"user","content":"hi"}]})
        assert resp.status == 200
```

Also add a test for missing config file:
```python
def test_load_config_raises_on_missing_file():
    with pytest.raises(FileNotFoundError) as exc_info:
        load_config("/nonexistent/config.yaml")
    assert "Config file not found" in str(exc_info.value)


def test_load_config_raises_on_missing_required_keys(tmp_path):
    (tmp_path / "bad.yaml").write_text("proxy: {}\ndatabase: {}")
    with pytest.raises(ValueError) as exc_info:
        load_config(tmp_path / "bad.yaml")
    assert "Missing required config keys" in str(exc_info.value)
```

Add these to `tests/test_proxy.py` (for the Config tests, convert them to sync def test_ functions — no `@pytest.mark.asyncio` needed since they don't use async).

---

### Step 8 — Run Full Test Suite

```bash
cd ~/Documents/hermes_projects/token_sidecar
uv run python -m pytest tests/ -v --tb=short
```

Expected: **17/17 passing** (11 db + 6 proxy, plus any new config validation tests)

---

### Step 9 — Git Commit

```bash
git add -A
git status   # review before committing
git commit -m "Phase 3: formalise Config class, --config CLI override, startup validation"
```

Commit message should reference the bug fixed in passing both header and content_type kwarg to aiohttp Response.

---

## Exit Criteria Checklist

- [ ] `Config` dataclass replaces raw dict return from `load_config()`
- [ ] Startup raises `ValueError` with descriptive message for any missing required key
- [ ] `--config <path>` CLI arg overrides default config.yaml location in `sidecar.py`
- [ ] Log level from config.yaml is applied to the aiohttp app logger (`cfg.log_level`)
- [ ] Audit pass complete — no module reads hardcoded localhost, port numbers, or paths directly
- [ ] All 17 existing tests still pass (11 db + 6 proxy)
- [ ] New config validation tests added and passing
- [ ] Git committed with Phase 3 changes
- [ ] `project_status.md` updated

---

## Notes & Gotchas

1. **aiohttp `web.run_app` log level:** aiohttp's own internal logger is controlled separately from the application logger set via `logging.basicConfig`. For now, setting root logger level via `basicConfig` is sufficient for observability.

2. **`pathlib.Path` vs string for db_path:** SQLite accepts both. Always pass `str(cfg.database_path)` when calling sqlite3 functions to avoid any platform path object edge cases.

3. **Test isolation:** Each test that uses a custom config file should initialise its own temp DB at the path specified in that config, since the sidecar reads `db.init_db()` on every request (if the table doesn't exist).

4. **`log_level` is optional:** The Config validator sets it to `"INFO"` if missing from config.yaml — no need to fail startup over it.

5. **Dotted key resolution:** The `_get(d, "proxy.listen_host")` helper traverses nested dicts cleanly; `proxy.get("listen_host", None)` would miss top-level keys that aren't under a sub-dict.