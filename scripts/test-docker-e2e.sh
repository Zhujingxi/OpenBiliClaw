#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

free_port() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

export COMPOSE_PROJECT_NAME="openbiliclaw-e2e-${$}"
export OBC_E2E_API_PORT="${OBC_E2E_API_PORT:-$(free_port)}"
export OBC_E2E_LITELLM_PORT="${OBC_E2E_LITELLM_PORT:-$(free_port)}"
secret_hex() {
  python3 -c 'import secrets; print(secrets.token_hex(32))'
}

export LITELLM_POSTGRES_PASSWORD="$(secret_hex)"
export LITELLM_MASTER_KEY="sk-$(secret_hex)"
export OBC_E2E_FAKE_PROVIDER_KEY="sk-$(secret_hex)"
export OPENBILICLAW_SECRET_KEY="$(secret_hex)"
export OPENBILICLAW_ACCESS_TOKEN="$(secret_hex)"
export OPENBILICLAW_SESSION_SECRET="$(secret_hex)"
export OBC_E2E_WEB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
export OPENBILICLAW_WEB_PASSWORD_HASH="$(
  OBC_E2E_WEB_PASSWORD="$OBC_E2E_WEB_PASSWORD" uv run --frozen python -c \
    'import os; from openbiliclaw.auth_core import hash_password; print(hash_password(os.environ["OBC_E2E_WEB_PASSWORD"]))'
)"
read -r OBC_E2E_EXTENSION_KEY OBC_E2E_EXTENSION_RECORD < <(
  uv run --frozen python -c \
    'from openbiliclaw.auth_core import generate_extension_access_key; _, key, record = generate_extension_access_key(); print(key, record)'
)
export OBC_E2E_EXTENSION_KEY
export OPENBILICLAW_EXTENSION_ACCESS_KEYS="[\"${OBC_E2E_EXTENSION_RECORD}\"]"
export COMPOSE_PROGRESS=plain

compose=(
  docker compose
  --project-name "$COMPOSE_PROJECT_NAME"
  -f docker-compose.yml
  -f tests/docker_e2e/docker-compose.e2e.yml
)

cleanup() {
  result=$?
  trap - EXIT INT TERM
  if [[ $result -ne 0 ]]; then
    "${compose[@]}" ps >&2 || true
    "${compose[@]}" logs --no-color --tail=200 >&2 || true
  fi
  "${compose[@]}" down --volumes --remove-orphans --timeout 10 >/dev/null 2>&1 || true
  exit "$result"
}
trap cleanup EXIT INT TERM

"${compose[@]}" up --detach --build --wait --wait-timeout 300
python3 tests/docker_e2e/run_product_e2e.py --configure-litellm
"${compose[@]}" restart --timeout 10 litellm
"${compose[@]}" up --detach --wait --wait-timeout 120 litellm
python3 tests/docker_e2e/run_product_e2e.py
