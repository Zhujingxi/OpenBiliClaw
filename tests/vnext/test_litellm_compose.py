"""Static distribution and policy parity checks for both LiteLLM Compose paths."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
COMPOSE_FILES = (ROOT / "docker-compose.yml", ROOT / "docker-compose.prebuilt.yml")


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
    assert policy["litellm_settings"]["turn_off_message_logging"] is True


def test_prebuilt_download_and_release_instructions_include_policy_file() -> None:
    prebuilt = (ROOT / "docker-compose.prebuilt.yml").read_text(encoding="utf-8")
    docker_doc = (ROOT / "docs/docker-deployment.md").read_text(encoding="utf-8")
    release_helper = (ROOT / ".github/scripts/sync-aggregate-release.sh").read_text(
        encoding="utf-8"
    )

    for text in (prebuilt, docker_doc, release_helper):
        assert "litellm/config.yaml" in text


def test_compose_does_not_claim_unverified_signature() -> None:
    source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "signed upstream release" not in source


def test_compose_forwards_vnext_browser_auth_and_public_admin_configuration() -> None:
    expected = {
        "OPENBILICLAW_WEB_PASSWORD_HASH": "${OPENBILICLAW_WEB_PASSWORD_HASH:-}",
        "OPENBILICLAW_SESSION_SECRET": (
            "${OPENBILICLAW_SESSION_SECRET:?Set OPENBILICLAW_SESSION_SECRET to a generated secret}"
        ),
        "OPENBILICLAW_EXTENSION_ACCESS_KEYS": "${OPENBILICLAW_EXTENSION_ACCESS_KEYS:-[]}",
        "OPENBILICLAW_LITELLM_ADMIN_URL": "${OPENBILICLAW_LITELLM_ADMIN_URL:-}",
    }
    for path in COMPOSE_FILES:
        source = path.read_text(encoding="utf-8")
        environment = yaml.safe_load(source)["services"]["api"]["environment"]
        assert {key: environment[key] for key in expected} == expected
        worker_environment = yaml.safe_load(source)["services"]["worker"]["environment"]
        assert "OPENBILICLAW_EXTENSION_ACCESS_KEYS" not in worker_environment
        assert "OPENBILICLAW_EXTENSION_DEVICE_KEY" not in source
        assert "obc_ext_" not in source
