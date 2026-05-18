#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Token Counter Sidecar — Install Script
# =============================================================================
# Sets up the full token-sidecar stack:
#   1. Verify prerequisites (uv, Python ≥3.11)
#   2. Install / update Python dependencies via uv
#   3. Create ~/.token_sidecar/ directory and init DB schema
#   4. Generate LaunchAgent plist from config.yaml
#   5. Load the LaunchAgent so it starts on login
#
# Usage:
#   ./install.sh          # interactive (confirm before loading launchd)
#   ./install.sh --force  # skip confirmations, install and load immediately
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.yaml"
LABEL="com.athena.token-sidecar"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

FORCE=false
if [[ "${1:-}" == "--force" ]]; then
  FORCE=true
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "[INFO]  $*" >&2; }
warn()  { echo "[WARN]  $*" >&2; }
fail()  { echo "[ERROR] $*" >&2; exit 1; }

confirm() {
  local msg="$1"
  if [[ "$FORCE" == "true" ]]; then return 0; fi
  echo -n "$msg [y/N] " >&2; read -r reply
  [[ "${reply,,}" == "y" ]] || { info "Skipped."; return 1; }
}

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
info "Checking prerequisites..."

if ! command -v uv &>/dev/null; then
  fail "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# Use uv to find a Python ≥3.11 (uv manages its own toolchain)
UV_PYTHON=$(uv python pin 3.11 2>/dev/null || uv python install 3.11)
PY_VERSION=$(uv run python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if uv run python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
  info "Python $PY_VERSION — OK"
else
  fail "Python ≥3.11 required, found $PY_VERSION"
fi

# ---------------------------------------------------------------------------
# 2. Install Python dependencies
# ---------------------------------------------------------------------------
info "Installing Python dependencies via uv..."
cd "$SCRIPT_DIR"
uv sync --quiet

# ---------------------------------------------------------------------------
# 3. Create data directory + init DB schema
# ---------------------------------------------------------------------------
DATA_DIR="$HOME/.token_sidecar"
DB_PATH_EXPANDED=$(python3 -c "import os; print(os.path.expanduser('~/.token_sidecar/tokens.db'))")

info "Creating data directory: $DATA_DIR"
mkdir -p "$DATA_DIR" && chmod 700 "$DATA_DIR"

# Use the project's db.py to init schema
info "Initializing database at $DB_PATH_EXPANDED..."
uv run python -c "
import sys, os
sys.path.insert(0, '$SCRIPT_DIR')
from db import init_db
db_path = os.path.expanduser('$DB_PATH_EXPANDED')
os.makedirs(os.path.dirname(db_path), exist_ok=True)
init_db(db_path)
print(f'DB schema ready at {db_path}')
"

# ---------------------------------------------------------------------------
# 4. Generate LaunchAgent plist from config.yaml
# ---------------------------------------------------------------------------
info "Generating LaunchAgent plist..."

# Use the venv's Python (has all project deps) for the LaunchAgent
PYEXECUTABLE="$SCRIPT_DIR/.venv/bin/python"

# Extract values from config.yaml via Python (simple one-liner)
IFS=" " read -r LISTEN_PORT UPSTREAM_URL DB_PATH <<< "$(cd "$SCRIPT_DIR" && uv run python -c "
import yaml, os
with open('$CONFIG_FILE') as f:
    cfg = yaml.safe_load(f)
print(cfg['proxy']['listen_port'], cfg['proxy']['upstream_url'], os.path.expanduser(cfg['database']['path']))
")"

# Build plist XML
PLIST_CONTENT="<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYEXECUTABLE</string>
        <string>$SCRIPT_DIR/sidecar.py</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>TOKEN_SIDECAR_CONFIG</key>
        <string>$CONFIG_FILE</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>$DATA_DIR/sidecar.log</string>

    <key>StandardErrorPath</key>
    <string>$DATA_DIR/sidecar.error.log</string>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>"

info "Writing plist to $PLIST_DEST"
mkdir -p "$(dirname "$PLIST_DEST")"

if [[ -f "$PLIST_DEST" ]]; then
  warn "Plist already exists. Overwriting."
fi

printf '%s' "$PLIST_CONTENT" > "$PLIST_DEST"
plutil -lint "$PLIST_DEST" || fail "plist validation failed"

# ---------------------------------------------------------------------------
# 5. Load LaunchAgent (optional, requires confirmation)
# ---------------------------------------------------------------------------
if confirm "Load the LaunchAgent now? (starts sidecar at login)"; then
  launchctl bootout gui/$(id -u) "$LABEL" 2>/dev/null || true
  launchctl bootstrap gui/$(id -u) "$PLIST_DEST"
  info "LaunchAgent loaded. Sidecar will start on next login."
else
  info "Skipped LaunchAgent load. To install manually later, run:"
  info "  launchctl bootstrap gui/\$(id -u) \"$PLIST_DEST\""
fi

# ---------------------------------------------------------------------------
# 6. Verification
# ---------------------------------------------------------------------------
info ""
info "Installation complete!"
info ""
info "Database: $DB_PATH_EXPANDED"
info "Logs:     $DATA_DIR/sidecar.log, $DATA_DIR/sidecar.error.log"
info ""
info "To start the sidecar immediately (without reboot):"
info "  cd $SCRIPT_DIR && uv run python sidecar.py"
info ""
info "To verify it's running:"
info "  curl http://localhost:$LISTEN_PORT/health || curl http://localhost:$LISTEN_PORT/v1/models"