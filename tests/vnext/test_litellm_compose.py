"""Static distribution and policy parity checks for both LiteLLM Compose paths."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
COMPOSE_FILES = (ROOT / "docker-compose.yml", ROOT / "docker-compose.prebuilt.yml")


def _prebuilt_env_bootstrap(source: str) -> str:
    start_marker = "#   # BEGIN private .env bootstrap"
    end_marker = "#   # END private .env bootstrap"
    lines = source.splitlines()
    start = lines.index(start_marker)
    end = lines.index(end_marker)
    shell_lines = lines[start + 1 : end]
    assert shell_lines
    assert all(line.startswith("#   ") for line in shell_lines)
    return "\n".join(line.removeprefix("#   ") for line in shell_lines)


def _fake_docker(tmp_path: Path, *, exit_code: int = 0) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "docker"
    executable.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    executable.chmod(0o700)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}


def test_both_compose_paths_mount_the_same_policy_and_bind_admin_to_loopback() -> None:
    for path in COMPOSE_FILES:
        compose = yaml.safe_load(path.read_text(encoding="utf-8"))
        service = compose["services"]["litellm"]
        assert service["command"] == ["--config=/app/config.yaml"]
        assert service["volumes"] == ["./litellm/config.yaml:/app/config.yaml:ro"]
        assert service["ports"] == ["127.0.0.1:${LITELLM_PORT:-4000}:4000"]

    policy = yaml.safe_load((ROOT / "litellm/config.yaml").read_text(encoding="utf-8"))
    assert policy["model_list"] == []
    assert policy["litellm_settings"]["num_retries"] == 2
    assert policy["litellm_settings"]["cache"] is True
    assert policy["litellm_settings"]["cache_params"] == {"type": "local"}
    assert policy["litellm_settings"]["turn_off_message_logging"] is True


def test_docker_product_e2e_configures_db_backed_aliases_after_proxy_startup() -> None:
    policy = yaml.safe_load(
        (ROOT / "tests/docker_e2e/litellm-config.yaml").read_text(encoding="utf-8")
    )
    driver = (ROOT / "tests/docker_e2e/run_product_e2e.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    overlay = (ROOT / "tests/docker_e2e/docker-compose.e2e.yml").read_text(encoding="utf-8")
    harness = (ROOT / "scripts/test-docker-e2e.sh").read_text(encoding="utf-8")

    assert policy["model_list"] == []
    assert 'STORE_MODEL_IN_DB: "True"' in compose
    assert "OBC_E2E_FAKE_PROVIDER_KEY" in overlay
    assert "pull_policy: always" in overlay
    assert '"/model/new"' in driver
    assert "configure_litellm_aliases()" in driver
    assert "os.environ/OBC_E2E_FAKE_PROVIDER_KEY" in driver
    assert "os.environ['OBC_E2E_FAKE_PROVIDER_KEY']" not in driver
    for alias in ("obc-interactive", "obc-analysis", "obc-embedding"):
        assert alias in driver

    initial_health = driver.index('unconfigured = json_request("GET", "/system/ai-health")')
    configure = driver.index("configure_litellm_aliases()", initial_health)
    verified_health = driver.index("wait_for_alias_health()", configure)
    assert initial_health < configure < verified_health

    configure_phase = harness.index("--configure-litellm")
    restart = harness.index("restart --timeout 10 litellm", configure_phase)
    bounded_wait = harness.index("--wait-timeout 120 litellm", restart)
    product_phase = harness.index("run_product_e2e.py", bounded_wait)
    assert configure_phase < restart < bounded_wait < product_phase
    assert "restart --timeout 10 litellm-postgres" not in harness


def test_prebuilt_download_and_release_instructions_include_policy_file() -> None:
    prebuilt = (ROOT / "docker-compose.prebuilt.yml").read_text(encoding="utf-8")
    docker_doc = (ROOT / "docs/docker-deployment.md").read_text(encoding="utf-8")
    release_helper = (ROOT / ".github/scripts/sync-aggregate-release.sh").read_text(
        encoding="utf-8"
    )

    for text in (prebuilt, docker_doc, release_helper):
        assert "litellm/config.yaml" in text


def test_prebuilt_quick_start_privately_generates_and_preserves_compose_secrets(
    tmp_path: Path,
) -> None:
    source = (ROOT / "docker-compose.prebuilt.yml").read_text(encoding="utf-8")
    bootstrap = _prebuilt_env_bootstrap(source)
    compose_path = tmp_path / "docker-compose.prebuilt.yml"
    compose_path.write_text(source, encoding="utf-8")
    bootstrap_env = _fake_docker(tmp_path)

    first = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", bootstrap],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=bootstrap_env,
    )
    assert first.returncode == 0, first.stderr
    credential_lines = [
        line for line in first.stdout.splitlines() if line.startswith("BOOTSTRAP_STATUS:")
    ]
    assert len(credential_lines) == 1
    disclosed = yaml.safe_load(credential_lines[0].removeprefix("BOOTSTRAP_STATUS:"))["details"]
    assert first.stderr == ""
    env_path = tmp_path / ".env"
    assert env_path.stat().st_mode & 0o777 == 0o600
    first_bytes = env_path.read_bytes()
    values = dict(
        line.split("=", 1)
        for line in first_bytes.decode().splitlines()
        if line and not line.startswith("#")
    )
    assert len(values["LITELLM_POSTGRES_PASSWORD"]) == 64
    assert values["LITELLM_MASTER_KEY"].startswith("sk-")
    assert len(values["OPENBILICLAW_SECRET_KEY"]) == 64
    assert len(values["OPENBILICLAW_ACCESS_TOKEN"]) == 64
    assert len(values["OPENBILICLAW_SESSION_SECRET"]) == 64
    assert values["OPENBILICLAW_WEB_PASSWORD_HASH"].startswith("scrypt$")
    assert len(yaml.safe_load(values["OPENBILICLAW_EXTENSION_ACCESS_KEYS"])) == 1
    assert disclosed["web_password"] not in first_bytes.decode()
    assert disclosed["extension_access_key"] not in first_bytes.decode()

    second = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", bootstrap],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=bootstrap_env,
    )
    assert second.returncode == 0, second.stderr
    assert second.stdout == ""
    assert second.stderr == ""
    assert env_path.read_bytes() == first_bytes

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is unavailable for Compose rendering")
    rendered = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            '"$1" compose --env-file .env -f docker-compose.prebuilt.yml config >/dev/null',
            "compose-render",
            docker,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "COMPOSE_PROJECT_NAME": "openbiliclaw-contract"},
    )
    assert rendered.returncode == 0, rendered.stderr
    assert rendered.stdout == ""
    assert values["OPENBILICLAW_SESSION_SECRET"] not in rendered.stderr


def test_prebuilt_failure_and_unsafe_env_do_not_disclose_or_commit_browser_access(
    tmp_path: Path,
) -> None:
    source = (ROOT / "docker-compose.prebuilt.yml").read_text(encoding="utf-8")
    bootstrap = _prebuilt_env_bootstrap(source)
    (tmp_path / "docker-compose.prebuilt.yml").write_text(source, encoding="utf-8")
    failed = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", bootstrap],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=_fake_docker(tmp_path, exit_code=1),
    )
    assert failed.returncode != 0
    assert "first_run_access" not in failed.stdout
    values = dict(
        line.split("=", 1)
        for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert "OPENBILICLAW_WEB_PASSWORD_HASH" not in values
    assert "OPENBILICLAW_EXTENSION_ACCESS_KEYS" not in values
    assert not list(tmp_path.glob(".env.stage-*"))

    outside = tmp_path / "outside.env"
    outside.write_text("DO_NOT_TOUCH=1\n", encoding="utf-8")
    (tmp_path / ".env").unlink()
    (tmp_path / ".env").symlink_to(outside)
    linked = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", bootstrap],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        },
    )
    assert linked.returncode != 0
    assert "private regular file" in linked.stderr
    assert outside.read_text(encoding="utf-8") == "DO_NOT_TOUCH=1\n"


def test_compose_does_not_claim_unverified_signature() -> None:
    source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "signed upstream release" not in source


def test_compose_forwards_vnext_browser_auth_and_public_admin_configuration() -> None:
    expected = {
        "OPENBILICLAW_WEB_PASSWORD_HASH": (
            "${OPENBILICLAW_WEB_PASSWORD_HASH:?Set OPENBILICLAW_WEB_PASSWORD_HASH "
            "to the installer-generated scrypt hash}"
        ),
        "OPENBILICLAW_SESSION_SECRET": (
            "${OPENBILICLAW_SESSION_SECRET:?Set OPENBILICLAW_SESSION_SECRET to a generated secret}"
        ),
        "OPENBILICLAW_EXTENSION_ACCESS_KEYS": (
            "${OPENBILICLAW_EXTENSION_ACCESS_KEYS:?Set OPENBILICLAW_EXTENSION_ACCESS_KEYS "
            "to installer-generated digest records}"
        ),
        "OPENBILICLAW_LITELLM_ADMIN_URL": (
            "${OPENBILICLAW_LITELLM_ADMIN_URL:-http://127.0.0.1:${LITELLM_PORT:-4000}/ui}"
        ),
    }
    for path in COMPOSE_FILES:
        source = path.read_text(encoding="utf-8")
        environment = yaml.safe_load(source)["services"]["api"]["environment"]
        assert {key: environment[key] for key in expected} == expected
        worker_environment = yaml.safe_load(source)["services"]["worker"]["environment"]
        assert "OPENBILICLAW_EXTENSION_ACCESS_KEYS" not in worker_environment
        assert "OPENBILICLAW_EXTENSION_DEVICE_KEY" not in source
