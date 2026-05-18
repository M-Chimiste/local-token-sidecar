# Phase 4 — LaunchAgent Setup: Detailed Implementation Plan

**Project:** token-sidecar  
**Phase:** 4 of 7  
**Parent plan:** `../implementation_plan.md`  
**Requirements:** `../requirements.md`  
**Goal:** Make the sidecar start automatically on login and survive reboots via a macOS LaunchAgent.

---

## Context from Requirements & High-Level Plan

### AC3 — Auto-start on Login
> "A LaunchAgent plist is created at ~/Library/LaunchAgents/com.athena.token-sidecar.plist"

The high-level implementation plan (Phase 4) specifies:
1. `setup_launchd.py` generates an XML plist file from config.yaml values and writes it to the LaunchAgents directory.
2. The script optionally installs it via `launchctl load`.
3. A separate uninstall function removes the plist and calls `launchctl unload`.

### AC5 — No External Services
> "No Docker, no systemd, no external service dependencies — pure Python + macOS built-ins"

Everything must be self-contained in Python with no pip packages for this phase.

---

## Design Decisions

### Why a Python Generator Script Over Hand-Written Plist?
- The plist contains dynamic values (paths expanded from `~`, the sidecar's listen port)
- Regenerating from current `config.yaml` on each run keeps it in sync
- Can be re-run safely idempotently (overwrite + reload)

### Plist Keys Required

| Key | Value Source | Rationale |
|-----|-------------|-----------|
| `Label` | `"com.athena.token-sidecar"` constant | Matches AC3 spec |
| `ProgramArguments` | `[sys.executable, `<sidecar.py>`]` | Ensures the venv's Python is used |
| `RunAtLoad` | `True` | Starts on login per AC3 |
| `KeepAlive` | `{"SuccessfulExit": False}` | Restart after crash; don't restart on clean exit |
| `StandardOutPath` | `~/.token_sidecar/sidecar.log` | Configurable log output |
| `StandardErrorPath` | `~/.token_sidecar/sidecar.error.log` | Separate error stream |
| `WorkingDirectory` | `<project_dir>` | Predictable CWD for relative paths |

### Working Directory vs ProgramArguments
- We pass the **absolute path** to `sidecar.py` in `ProgramArguments`
- The venv Python at `sys.executable` resolves symlinks automatically on macOS
- This is more reliable than setting `WorkingDirectory` alone

### Security Note: Gatekeeper Compatibility
- LaunchAgent plist points to user-level `~/Library/LaunchAgents/` — no admin required
- `launchctl load/unload` operates on the user's session, not system-wide
- No code signing required for user-scope agents

---

## Step-by-Step Plan

### Step 1 — Create `setup_launchd.py`

Write a single self-contained Python script at the project root that:

**a) Define a `generate_plist_content(project_dir: pathlib.Path, config: Config) -> str` helper:**
- Expands all paths (`~/.token_sidecar/`) to absolute for plist
- Returns XML string conforming to `com.athena.token-sidecar`

**b) Define an `install(config_path: str | None = None) -> None` function:**
- Load config via `load_config()` from the project's own `config_loader`
- Resolve `project_dir` as the directory containing `setup_launchd.py`'s parent
  (`pathlib.Path(__file__).parent` if running as a script, or `Path.cwd()`)
- Build plist content with `generate_plist_content()`
- Create `~/.token_sidecar/` dir if missing (mode `0o700`)
- Write plist to `~/Library/LaunchAgents/com.athena.token-sidecar.plist`
  - File mode `0o644` (user-readable, owner-writable — standard for LaunchAgents)
- Print the plist path and next steps

**c) Define a `uninstall() -> None` function:**
- Call `launchctl bootout gui/$(id -u) com.athena.token-sidecar 2>/dev/null` to unload first
  - Use `gui/<uid>` domain so it only affects the current GraphicalSession, not system-wide
  - Silently skip if not loaded (no error)
- Remove plist file at `~/Library/LaunchAgents/com.athena.token-sidecar.plist`
- Print confirmation

**d) Add a `main()` block with CLI argument parsing:**
```
usage: setup_launchd.py [-h] {install,unload,status}
```
Subcommands:
- `install` — generate plist + install
- `unload` — unload only (keep plist file)
- `remove` — unload + delete plist
- `status` — check if loaded, print plist path

**e) Guard against running as root:**
- If `os.geteuid() == 0`, exit with a clear error: "Do not run as root. This is a user-scope LaunchAgent."

### Step 2 — Make Plist Directory If Missing

Before writing the plist file, create `~/.token_sidecar/` using:

```python
log_dir = config.database_path.parent  # ~/.token_sidecar/
log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
```

This ensures the log directory exists before the LaunchAgent tries to write to it.

### Step 3 — Test Plist Generation (No Root Needed)

Write a test in `tests/test_launchd.py` that:

- Mocks `pathlib.Path.home()` to return a temp dir
- Calls `generate_plist_content()` with a known config
- Parses the output XML with Python's built-in `xml.etree.ElementTree`
- Asserts all required keys are present and have correct values
- Verifies no hardcoded paths remain in the plist content

### Step 4 — Test install() / unload() Functions (Dry-Run)

Because we cannot actually call `launchctl` in tests (requires a GUI session), use `subprocess.run` mocking or environment-variable patching to verify:

- The correct `launchctl` command is constructed
- The plist file is written to the expected path
- Errors (e.g., no write permission) raise with a clear message

### Step 5 — Verify `setup_launchd.py install` Produces Valid Plist

Run manually on this machine:
```bash
cd ~/Documents/hermes_projects/token_sidecar && uv run python setup_launchd.py install --config config.yaml
```

Then verify:
```bash
plutil -p ~/Library/LaunchAgents/com.athena.token-sidecar.plist
```
All keys must be present and correctly typed (boolean, array, string).

### Step 6 — Run Full Test Suite

Run **all tests** including the new `test_launchd.py`:
```bash
uv run python -m pytest tests/ -v --tb=short
```

Must pass: all existing 22 + new launchd tests.

### Step 7 — Git Commit Phase 4 Changes

Commit with message: `"Phase 4: LaunchAgent setup — setup_launchd.py generator, install/unload/remove/status commands"`

---

## Exit Criteria (all must be verified)

1. `uv run python setup_launchd.py install` writes a valid plist to `~/Library/LaunchAgents/com.athena.token-sidecar.plist`
2. `plutil -p <plist>` shows all required keys: `Label`, `ProgramArguments` (array of 2 strings), `RunAtLoad=true`, `KeepAlive` (dict), `StandardOutPath`, `StandardErrorPath`
3. After `launchctl load`, the sidecar process appears in `ps aux | grep sidecar`
4. After a clean exit (`kill` or Ctrl+C), `KeepAlive` restarts it automatically
5. `uv run python setup_launchd.py status` correctly reports loaded/unloaded state
6. `uv run python setup_launchd.py remove` unloads and deletes the plist cleanly
7. All 22+ existing tests still pass; new launchd-specific tests added

---

## File Structure After Phase 4

```
token_sidecar/
├── ...
├── setup_launchd.py      ← NEW (this phase)
│   ├── generate_plist_content(project_dir, config) -> str
│   ├── install(config_path=None)    # load + write plist + print instructions
│   ├── unload()                     # launchctl bootout gui/<uid>
│   └── remove()                     # unload + delete plist file
├── tests/
│   └── test_launchd.py              ← NEW (this phase)
└── ...
```

---

## Out of Scope

- **systemd** (Linux) — not required by AC5; Phase 7 covers documentation only
- **launchd system agents** (`/Library/LaunchDaemons/`) — requires admin privileges, out of scope for a user-scope tool
- **Code signing** — not needed for user-level LaunchAgents on modern macOS (SIP aside)