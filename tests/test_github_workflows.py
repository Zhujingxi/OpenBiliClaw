"""Regression checks for GitHub Actions workflows."""

from pathlib import Path


def test_web_guided_init_e2e_sanitizes_apt_sources_before_playwright_install() -> None:
    """Playwright --with-deps should not fail on stale Microsoft apt sources."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    e2e_job = workflow.split("web-guided-init-e2e:", 1)[1]

    cleanup_step = e2e_job.index("Sanitize apt sources for Playwright")
    install_step = e2e_job.index("Install Playwright Chromium")

    assert cleanup_step < install_step
    assert "microsoft" in e2e_job
    assert "azure-cli" in e2e_job
    assert "| xargs -r sudo rm -f || true" in e2e_job


def test_issue_98_e2e_treats_playwright_as_an_optional_test_dependency() -> None:
    """The default ``[dev,x]`` CI job must collect tests without Playwright installed."""
    source = Path("tests/test_desktop_web_issue_98_e2e.py").read_text(encoding="utf-8")

    assert 'pytest.importorskip("playwright.sync_api")' in source
    assert "from playwright.sync_api import" not in source
