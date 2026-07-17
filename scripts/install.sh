#!/usr/bin/env bash
# Install the vNext OpenBiliClaw API + worker. Docker is preferred when available.
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/OpenBiliClaw}"
REPO_URL="${OPENBILICLAW_REPO_URL:-https://github.com/whiteguo233/OpenBiliClaw.git}"
BRANCH="${OPENBILICLAW_BRANCH:-main}"
MODE="${MODE:-auto}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8420}"
SKIP_START="${SKIP_START:-0}"

log() { printf '[openbiliclaw] %s\n' "$*"; }
fail() { printf '[openbiliclaw] ERROR: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is required"
command -v python3 >/dev/null 2>&1 || fail "Python 3.11+ is required"
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
  || fail "Python 3.11+ is required"

if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
  fail "install path exists and is not a directory: $INSTALL_DIR"
fi
mkdir -p "$INSTALL_DIR"
if [ ! -f "$INSTALL_DIR/pyproject.toml" ]; then
  if [ -n "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    fail "install directory is non-empty and is not an OpenBiliClaw checkout"
  fi
  log "Cloning OpenBiliClaw into $INSTALL_DIR"
  git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
elif [ -d "$INSTALL_DIR/.git" ]; then
  log "Using existing checkout at $INSTALL_DIR (local changes are preserved)"
fi

if [ "$MODE" = auto ]; then
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    MODE=docker
  else
    MODE=local
  fi
fi

case "$MODE" in
  docker)
    command -v docker >/dev/null 2>&1 || fail "Docker with Compose is required"
    docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
    ;;
  local)
    # A source install uses a user-managed LiteLLM proxy. Reuse an existing
    # private .env automatically; otherwise collect values without echoing the key.
    if [ -f "$INSTALL_DIR/.env" ]; then
      if [ -z "${OPENBILICLAW_LITELLM_BASE_URL:-}" ]; then
        OPENBILICLAW_LITELLM_BASE_URL="$(awk -F= '$1 == "OPENBILICLAW_LITELLM_BASE_URL" {sub(/^[^=]*=/, ""); print; exit}' "$INSTALL_DIR/.env")"
      fi
      if [ -z "${OPENBILICLAW_LITELLM_API_KEY:-}" ]; then
        OPENBILICLAW_LITELLM_API_KEY="$(awk -F= '$1 == "OPENBILICLAW_LITELLM_API_KEY" {sub(/^[^=]*=/, ""); print; exit}' "$INSTALL_DIR/.env")"
      fi
    fi
    if [ -z "${OPENBILICLAW_LITELLM_BASE_URL:-}" ]; then
      if [ -r /dev/tty ]; then
        printf 'LiteLLM base URL: ' >/dev/tty
        IFS= read -r OPENBILICLAW_LITELLM_BASE_URL </dev/tty
      else
        fail "set OPENBILICLAW_LITELLM_BASE_URL for a source install"
      fi
    fi
    if [ -z "${OPENBILICLAW_LITELLM_API_KEY:-}" ]; then
      if [ -r /dev/tty ]; then
        printf 'LiteLLM API key (input hidden): ' >/dev/tty
        IFS= read -r -s OPENBILICLAW_LITELLM_API_KEY </dev/tty
        printf '\n' >/dev/tty
      else
        fail "set OPENBILICLAW_LITELLM_API_KEY for a source install"
      fi
    fi
    export OPENBILICLAW_LITELLM_BASE_URL OPENBILICLAW_LITELLM_API_KEY
    ;;
  *) fail "MODE must be auto, docker, or local" ;;
esac

arguments=(
  --project-dir "$INSTALL_DIR"
  --mode "$MODE"
  --host "$HOST"
  --port "$PORT"
)
if [ "$SKIP_START" = 1 ]; then
  arguments+=(--skip-start)
fi

if [ "$SKIP_START" = 1 ]; then
  log "Preparing the $MODE runtime and applying migration (services remain stopped)"
else
  log "Starting the $MODE runtime and verifying migration, API, worker, and protected access"
fi
python3 "$INSTALL_DIR/scripts/runtime_bootstrap.py" "${arguments[@]}"
log "Runtime secrets are stored in $INSTALL_DIR/.env with mode 0600 and are reused on rerun."
if [ "$MODE" = docker ]; then
  log "Configure provider credentials and the obc-interactive, obc-analysis, and obc-embedding aliases at http://127.0.0.1:4000/ui"
fi
log "Web and extension clients use the generated vNext API contract."
