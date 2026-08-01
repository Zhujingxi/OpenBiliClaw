"""Static and optional Compose validation for the public HTTPS gateway."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_OVERLAY = _ROOT / "docker-compose.https.yml"


def _find_compose() -> list[str]:
    candidates: list[list[str]] = []
    if docker := shutil.which("docker"):
        candidates.append([docker, "compose"])
    if docker_compose := shutil.which("docker-compose"):
        candidates.append([docker_compose])
    for command in candidates:
        try:
            result = subprocess.run(
                [*command, "version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return command
    return []


_COMPOSE = _find_compose()


def test_public_https_overlay_keeps_backend_private_and_pins_caddy() -> None:
    source = _OVERLAY.read_text(encoding="utf-8")

    assert "caddy:2.11.4-alpine" in source
    assert 'network_mode: "service:openbiliclaw-backend"' in source
    assert 'FORWARDED_ALLOW_IPS: "127.0.0.1"' in source
    assert '"127.0.0.1:8420:8420"' in source
    assert '"80:80"' in source
    assert '"443:443"' in source
    assert "ports: !override" in source
    assert "--from" in source
    assert "--to" in source
    assert "127.0.0.1:8420" in source
    assert "openbiliclaw_caddy_data:/data" in source
    assert "/api/auth/status" in source
    assert "grep -q '\"enabled\":true'" in source


@pytest.mark.skipif(not _COMPOSE, reason="Docker Compose is unavailable")
@pytest.mark.parametrize("base", ["docker-compose.yml", "docker-compose.prebuilt.yml"])
def test_public_https_overlay_merges_with_both_docker_paths(base: str) -> None:
    env = os.environ.copy()
    env["OPENBILICLAW_DOMAIN"] = "obc.example.com"
    result = subprocess.run(
        [
            *_COMPOSE,
            "-f",
            str(_ROOT / base),
            "-f",
            str(_OVERLAY),
            "config",
        ],
        cwd=_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "openbiliclaw-caddy" in result.stdout
    assert "127.0.0.1:8420" in result.stdout
