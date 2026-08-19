#!/usr/bin/env python3
"""Authenticated in-container health probe without exposing the bearer token."""

from __future__ import annotations

import tomllib
import urllib.request
from pathlib import Path

from openbiliclaw.infrastructure.credentials.keyring import ProtectedFileBackend
from openbiliclaw.infrastructure.credentials.vault import CredentialVault

runtime = Path("/app/runtime")
with (runtime / "config.toml").open("rb") as stream:
    settings = tomllib.load(stream)
# Bearer is optional: LAN-trusted deployments run without one.
reference = settings["host"].get("bearer_secret_ref", "").removeprefix("vault:")


def probe(secret: memoryview | None = None) -> None:
    headers = {}
    if secret is not None:
        headers["Authorization"] = f"Bearer {bytes(secret).decode()}"
    request = urllib.request.Request("http://127.0.0.1:8420/v1/runtime/health", headers=headers)
    with urllib.request.urlopen(request, timeout=4) as response:
        if response.status != 200:
            raise SystemExit(1)


if reference:
    vault = CredentialVault(ProtectedFileBackend(runtime / "credentials.json"))
    vault.resolve(reference, probe)
else:
    probe()
