"""Active vNext installer and deployment documentation contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ("scripts/install.sh", "scripts/install.ps1"))
def test_human_installers_use_litellm_and_vnext_bootstrap(name: str) -> None:
    source = _read(name)

    assert "agent_bootstrap.py" in source
    assert "OPENBILICLAW_LITELLM_BASE_URL" in source
    assert "OPENBILICLAW_LITELLM_API_KEY" in source
    assert "Task 22" in source
    assert "init_complete" not in source
    assert "--provider" not in source
    assert "--skip-init" not in source


def test_shell_installer_hides_key_and_propagates_failure() -> None:
    source = _read("scripts/install.sh")

    assert "read -r -s OPENBILICLAW_LITELLM_API_KEY" in source
    assert "set -euo pipefail" in source
    assert 'python3 "$INSTALL_DIR/scripts/agent_bootstrap.py"' in source
    assert "Runtime secrets are stored" in source


def test_powershell_installer_hides_key_and_propagates_failure() -> None:
    source = _read("scripts/install.ps1")
    bootstrap = _read("scripts/agent_bootstrap.py")

    assert "Read-Host 'LiteLLM API key' -AsSecureString" in source
    assert "$ErrorActionPreference = 'Stop'" in source
    assert "if ($LASTEXITCODE -ne 0)" in source
    assert "Runtime secrets are stored" in source
    # PowerShell delegates lifecycle management to this Python bootstrap. Its
    # Windows path must verify creation time, executable, and command line.
    assert "Get-CimInstance Win32_Process" in bootstrap
    assert "CreationDate" in bootstrap
    assert "ExecutablePath" in bootstrap
    assert "CommandLine" in bootstrap
    assert '["taskkill", "/PID", str(pid), "/T"]' in bootstrap


@pytest.mark.parametrize("name", ("docker-compose.yml", "docker-compose.prebuilt.yml"))
def test_compose_api_worker_share_database_queue_and_volume(name: str) -> None:
    compose = yaml.safe_load(_read(name))
    api = compose["services"]["api"]
    worker = compose["services"]["worker"]

    assert (
        api["environment"]["OPENBILICLAW_DATABASE_URL"]
        == worker["environment"]["OPENBILICLAW_DATABASE_URL"]
    )
    assert (
        api["environment"]["OPENBILICLAW_HUEY_PATH"]
        == worker["environment"]["OPENBILICLAW_HUEY_PATH"]
    )
    assert "openbiliclaw_data:/app/runtime/data" in api["volumes"]
    assert "openbiliclaw_data:/app/runtime/data" in worker["volumes"]


@pytest.mark.parametrize(
    "name",
    (
        "README.md",
        "README_EN.md",
        "docs/agent-install.md",
        "docs/agent-deployment.md",
        "docs/docker-deployment.md",
        "docs/faq.md",
        "docs/manual-e2e.md",
    ),
)
def test_active_docs_use_only_operational_cli_and_mark_static_ui_pending(name: str) -> None:
    source = _read(name)

    assert "Task 22" in source
    for command in (
        "openbiliclaw start",
        "openbiliclaw init",
        "openbiliclaw models",
        "openbiliclaw recommend",
        "openbiliclaw profile",
        "openbiliclaw setup-embedding",
        "openbiliclaw serve-api",
    ):
        assert command not in source


def test_docs_define_secret_migration_dual_process_and_protected_check_contract() -> None:
    install = _read("docs/agent-install.md")
    deployment = _read("docs/agent-deployment.md")

    for marker in (
        "0600",
        "openbiliclaw db migrate",
        "openbiliclaw doctor",
        "openbiliclaw serve",
        "openbiliclaw worker",
        "bearer-protected",
    ):
        assert marker in install
    assert "API 与 worker" in deployment
    assert "LiteLLM" in deployment
