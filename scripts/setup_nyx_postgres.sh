#!/usr/bin/env bash
set -euo pipefail

# One-shot setup for the token-sidecar central Postgres database on nyx.
#
# What it does:
#   1. Verifies Homebrew and uv are available
#   2. Finds or installs a Homebrew Postgres formula
#   3. Starts the Postgres service
#   4. Runs scripts/bootstrap_postgres.py to create DB, roles, schema, and grants
#   5. Prints the env file path containing the generated DSNs

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REPORT_HOST="nyx"
REPORT_PORT="5432"
ADMIN_DSN="${TOKEN_SIDECAR_ADMIN_DSN:-postgresql:///postgres}"
POSTGRES_SERVICE="${POSTGRES_SERVICE:-}"
ROTATE_PASSWORDS=false
SKIP_BREW_INSTALL=false
SKIP_SERVICE_START=false
EXTRA_BOOTSTRAP_ARGS=()

info() { echo "[INFO]  $*" >&2; }
warn() { echo "[WARN]  $*" >&2; }
fail() { echo "[ERROR] $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  scripts/setup_nyx_postgres.sh [options]

Options:
  --report-host HOST        Hostname/IP placed in generated DSNs (default: nyx)
  --report-port PORT        Port placed in generated DSNs (default: 5432)
  --admin-dsn DSN           Admin DSN for bootstrap (default: TOKEN_SIDECAR_ADMIN_DSN or postgresql:///postgres)
  --postgres-service NAME   Homebrew service/formula name (default: auto-detect)
  --rotate-passwords        Rotate existing writer/reader role passwords
  --skip-brew-install       Fail if Postgres is not already installed
  --skip-service-start      Do not start Homebrew Postgres service
  --help                    Show this help

Environment:
  TOKEN_SIDECAR_ADMIN_DSN       Optional admin DSN
  TOKEN_SIDECAR_WRITER_PASSWORD Optional fixed writer role password
  TOKEN_SIDECAR_READER_PASSWORD Optional fixed reader role password
  POSTGRES_SERVICE              Optional Homebrew service/formula override

Examples:
  scripts/setup_nyx_postgres.sh
  scripts/setup_nyx_postgres.sh --report-host nyx.tailnet-name.ts.net
  scripts/setup_nyx_postgres.sh --rotate-passwords
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --report-host)
      REPORT_HOST="${2:?Missing value for --report-host}"
      shift 2
      ;;
    --report-port)
      REPORT_PORT="${2:?Missing value for --report-port}"
      shift 2
      ;;
    --admin-dsn)
      ADMIN_DSN="${2:?Missing value for --admin-dsn}"
      shift 2
      ;;
    --postgres-service)
      POSTGRES_SERVICE="${2:?Missing value for --postgres-service}"
      shift 2
      ;;
    --rotate-passwords)
      ROTATE_PASSWORDS=true
      shift
      ;;
    --skip-brew-install)
      SKIP_BREW_INSTALL=true
      shift
      ;;
    --skip-service-start)
      SKIP_SERVICE_START=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      EXTRA_BOOTSTRAP_ARGS+=("$1")
      shift
      ;;
  esac
done

detect_postgres_service() {
  if [[ -n "$POSTGRES_SERVICE" ]]; then
    echo "$POSTGRES_SERVICE"
    return 0
  fi

  local candidates=(
    postgresql
    postgresql@18
    postgresql@17
    postgresql@16
    postgresql@15
    postgresql@14
  )

  local candidate
  for candidate in "${candidates[@]}"; do
    if brew list --formula "$candidate" &>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done

  return 1
}

main() {
  cd "$PROJECT_ROOT"

  command -v brew >/dev/null 2>&1 || fail "Homebrew not found."
  command -v uv >/dev/null 2>&1 || fail "uv not found. Install uv first."

  local service
  if service="$(detect_postgres_service)"; then
    info "Using Homebrew Postgres formula/service: $service"
  else
    if [[ "$SKIP_BREW_INSTALL" == "true" ]]; then
      fail "No Homebrew Postgres formula found. Install with: brew install postgresql"
    fi
    info "No Homebrew Postgres formula found; installing postgresql..."
    brew install postgresql
    service="postgresql"
  fi

  if [[ "$SKIP_SERVICE_START" != "true" ]]; then
    info "Starting Postgres service: $service"
    if ! brew services start "$service"; then
      warn "brew services start returned non-zero; attempting restart."
      brew services restart "$service"
    fi
  fi

  info "Installing Python dependencies with uv..."
  uv sync --quiet

  local bootstrap_cmd=(
    uv run python scripts/bootstrap_postgres.py
    --admin-dsn "$ADMIN_DSN"
    --report-host "$REPORT_HOST"
    --report-port "$REPORT_PORT"
  )

  if [[ "$ROTATE_PASSWORDS" == "true" ]]; then
    bootstrap_cmd+=(--rotate-passwords)
  fi
  if [[ ${#EXTRA_BOOTSTRAP_ARGS[@]} -gt 0 ]]; then
    bootstrap_cmd+=("${EXTRA_BOOTSTRAP_ARGS[@]}")
  fi

  info "Bootstrapping token_sidecar database, roles, schema, and grants..."
  "${bootstrap_cmd[@]}"

  cat <<EOF

Done.

Credentials were written on nyx to:
  ~/.token_sidecar/postgres.env

To test central reporting from this repo on nyx:
  source ~/.token_sidecar/postgres.env
  uv run python -m queries.summary by-model --backend postgres --format table

For each sidecar machine:
  1. Copy the TOKEN_SIDECAR_POSTGRES_DSN export from nyx's ~/.token_sidecar/postgres.env
  2. Set that env var before installing/running the sidecar
  3. Set node.id in config.yaml and enable database.central.enabled

If other machines cannot connect over Tailscale, update postgresql.conf and
pg_hba.conf as described in project_docs/postgres_setup.md.
EOF
}

main
