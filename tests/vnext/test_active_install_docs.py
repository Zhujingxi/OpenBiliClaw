from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = (
    "README.md",
    "README_EN.md",
    "docs/agent-install.md",
    "docs/agent-deployment.md",
    "docs/docker-deployment.md",
    "docs/faq.md",
    "docs/manual-e2e.md",
)


@pytest.mark.parametrize("name", ACTIVE)
def test_active_docs_do_not_advertise_removed_commands(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    for command in (
        "openbiliclaw start",
        "openbiliclaw init",
        "openbiliclaw models",
        "openbiliclaw recommend",
        "openbiliclaw profile",
        "openbiliclaw setup-embedding",
        "openbiliclaw serve-api",
    ):
        assert command not in text


@pytest.mark.parametrize("name", ("README.md", "README_EN.md"))
def test_readme_marks_static_ui_as_pending(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "Task 22" in text
    assert "static" in text.lower()


def test_install_docs_require_litellm_and_both_runtime_processes() -> None:
    combined = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("docs/agent-install.md", "docs/agent-deployment.md")
    )
    assert "OPENBILICLAW_LITELLM_BASE_URL" in combined
    assert "OPENBILICLAW_LITELLM_API_KEY" in combined
    assert "openbiliclaw serve" in combined
    assert "openbiliclaw worker" in combined
    assert "0600" in combined
