#!/usr/bin/env python3
"""Seed the runtime vault from a mounted secret file without logging it."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from openbiliclaw.infrastructure.credentials.keyring import ProtectedFileBackend
from openbiliclaw.infrastructure.credentials.vault import CredentialVault

runtime = Path(os.environ.get("OPENBILICLAW_RUNTIME_DIR", "/app/runtime"))
secret_file = Path(os.environ.get("OPENBILICLAW_MODEL_KEY_FILE", "/run/secrets/model_api_key"))
template_file = Path(os.environ.get("OPENBILICLAW_CONFIG_TEMPLATE", "/app/config.docker.toml"))
config_file = runtime / "config.toml"

runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
if config_file.exists():
    raise SystemExit(0)
if not secret_file.is_file():
    raise SystemExit(f"missing mounted model key file: {secret_file}")

secret = bytearray(secret_file.read_bytes().strip())
try:
    if not secret:
        raise SystemExit("mounted model key file is empty")
    vault = CredentialVault(ProtectedFileBackend(runtime / "credentials.json"))
    model_secret_ref = vault.store(bytes(secret))
finally:
    secret[:] = b"\0" * len(secret)

bearer = bytearray(secrets.token_urlsafe(32).encode())
try:
    bearer_secret_ref = vault.store(bytes(bearer))
finally:
    bearer[:] = b"\0" * len(bearer)

config = (
    template_file.read_text(encoding="utf-8")
    .replace("DOCKER_MODEL_SECRET_REF", model_secret_ref)
    .replace("DOCKER_BEARER_SECRET_REF", bearer_secret_ref)
)
config_file.write_text(config, encoding="utf-8")
os.chmod(config_file, 0o600)
