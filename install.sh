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

info "Creating data directory: $DATA_DIR"
mkdir -p "$DATA_DIR" && chmod 700 "$DATA_DIR"

# Use the project's db.py to init schema
info "Initializing database from config.yaml..."
uv run python -c "
import sys, os
sys.path.insert(0, '$SCRIPT_DIR')
from config_loader import load_config
from db import init_db
cfg = load_config('$CONFIG_FILE')
db_path = os.path.expanduser(str(cfg.database_path))
os.makedirs(os.path.dirname(db_path), exist_ok=True)
init_db(db_path, node_id=cfg.node_id)
print(f'DB schema ready at {db_path}')
"

# ---------------------------------------------------------------------------
# 4. Generate LaunchAgent plist from config.yaml
# ---------------------------------------------------------------------------
info "Generating LaunchAgent plist..."

# Extract values from config.yaml via Python (simple one-liner)
IFS=" " read -r LISTEN_PORT UPSTREAM_URL DB_PATH <<< "$(cd "$SCRIPT_DIR" && uv run python -c "
import yaml, os
with open('$CONFIG_FILE') as f:
    cfg = yaml.safe_load(f)
print(cfg['proxy']['listen_port'], cfg['proxy']['upstream_url'], os.path.expanduser(cfg['database']['path']))
")"

mkdir -p "$(dirname "$PLIST_DEST")"
uv run python setup_launchd.py install --config "$CONFIG_FILE"
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
info "Database: $DB_PATH"
info "Logs:     $DATA_DIR/sidecar.log, $DATA_DIR/sidecar.error.log"
info ""
info "To start the sidecar immediately (without reboot):"
info "  cd $SCRIPT_DIR && uv run python sidecar.py"
info ""
info "To verify it's running:"
info "  curl http://localhost:$LISTEN_PORT/health || curl http://localhost:$LISTEN_PORT/v1/models"
