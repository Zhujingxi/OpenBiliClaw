#!/bin/sh
# Entry for the bundled-bge-m3 Ollama image. Offline-first: seed the baked model
# into the (possibly volume-backed) store BEFORE serving. Loud failure — the
# healthcheck (ollama list | grep bge-m3) reports unhealthy rather than silently
# degrading. Network pull is opt-in via OPENBILICLAW_OLLAMA_ALLOW_PULL=1.
set -eu
TARGET="${OLLAMA_MODELS:-/root/.ollama}/models"

if /usr/local/bin/seed-bge-m3.sh /opt/bge-m3-seed "$TARGET"; then
  exec ollama serve
fi

echo "[entrypoint] seed did not complete." >&2
if [ "${OPENBILICLAW_OLLAMA_ALLOW_PULL:-0}" = "1" ]; then
  echo "[entrypoint] OPENBILICLAW_OLLAMA_ALLOW_PULL=1 -> pulling bge-m3 at runtime." >&2
  ollama serve & spid=$!
  i=0; while [ "$i" -lt 30 ]; do ollama list >/dev/null 2>&1 && break; i=$((i+1)); sleep 1; done
  ollama pull bge-m3 || echo "[entrypoint] opt-in pull failed; healthcheck stays unhealthy." >&2
  wait "$spid"
else
  echo "[entrypoint] network pull is opt-in (set OPENBILICLAW_OLLAMA_ALLOW_PULL=1)." >&2
  echo "[entrypoint] serving anyway; healthcheck will report unhealthy until bge-m3 is present." >&2
  exec ollama serve
fi
