#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Token Counter Sidecar — Uninstall Script
# =============================================================================
# Removes the LaunchAgent and optionally user data.
#
# Usage:
#   ./uninstall.sh           # remove launchd only, keep DB + logs
#   ./uninstall.sh --purge   # also delete ~/.token_sidecar/ and uv env
#   ./uninstall.sh --force   # skip confirmations
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.athena.token-sidecar"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

PURGE=false
FORCE=false
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=true ;;
    --force) FORCE=true ;;
  esac
done

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
# 1. Unload + remove LaunchAgent
# ---------------------------------------------------------------------------
info "Removing LaunchAgent..."

if launchctl bootout gui/$(id -u) "$LABEL" 2>/dev/null; then
  info "Unloaded: $LABEL"
elif [[ ! -f "$PLIST_DEST" ]]; then
  warn "LaunchAgent not loaded and plist not found — skipping."
fi

if [[ -f "$PLIST_DEST" ]]; then
  confirm "Remove plist at $PLIST_DEST?" && rm "$PLIST_DEST" && info "Plist removed."
fi

# ---------------------------------------------------------------------------
# 2. Optionally purge all user data
# ---------------------------------------------------------------------------
if [[ "$PURGE" == "true" ]]; then
  if confirm "Delete ALL sidecar data (~/.token_sidecar/) and uv environment?"; then
    rm -rf "$HOME/.token_sidecar"
    info "Removed ~/.token_sidecar/"
    if [[ -d "$SCRIPT_DIR/.venv" ]]; then
      rm -rf "$SCRIPT_DIR/.venv"
      info "Removed .venv/"
    fi
    if [[ -f "$SCRIPT_DIR/uv.lock" ]]; then
      rm -f "$SCRIPT_DIR/uv.lock"
      info "Removed uv.lock (re-run install to regenerate)"
    fi
  fi
else
  confirm "Remove data directory ~/.token_sidecar/?" && rm -rf "$HOME/.token_sidecar" && info "Removed ~/.token_sidecar/"
fi

info "Uninstall complete."