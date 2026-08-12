"""Final-cutover audits: exactly one target implementation remains."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from openbiliclaw.composition.build import BuildOptions, build_application
from openbiliclaw.core.config import AppSettings
from openbiliclaw.infrastructure.sqlite.schema import TARGET_TABLE_OWNERS

ROOT = Path(__file__).parents[1]
LEGACY = {
    "agent",
    "api",
    "auth_core",
    "bilibili",
    "cli",
    "config",
    "discovery",
    "docker_runtime",
    "eval",
    "integrations",
    "llm",
    "logging_setup",
    "memory",
    "network",
    "proc",
    "published_time",
    "runtime",
    "saved_sync",
    "soul",
    "sources",
    "storage",
    "tls_proxy",
    "youtube",
}


def test_legacy_files_and_imports_are_gone() -> None:
    package = ROOT / "src/openbiliclaw"
    for name in LEGACY:
        assert not (package / name).exists()
        assert not (package / f"{name}.py").exists()
    forbidden = tuple(f"openbiliclaw.{name}" for name in LEGACY)
    for path in (*package.rglob("*.py"), *ROOT.glob("scripts/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = (
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert not any(
            name == prefix or name.startswith(prefix + ".")
            for name in imported
            for prefix in forbidden
        ), path


def test_console_script_and_configuration_match_current_runtime() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"] == {
        "openbiliclaw": "openbiliclaw.composition.entrypoints:main"
    }
    sample = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
    assert set(sample) == set(AppSettings.model_fields)
    AppSettings.model_validate(sample)


def test_target_routes_schema_and_assets_are_owned(tmp_path: Path) -> None:
    application = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    assert application.hosts.api is not None
    routes = {
        (method, route.path)
        for route in application.hosts.api.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert ("GET", "/v1/recommendations") in routes
    assert ("POST", "/v1/feedback") in routes
    assert ("POST", "/v1/assistant/turns") in routes
    assert ("GET", "/v1/runtime/health") in routes
    assert "schema_migrations" not in TARGET_TABLE_OWNERS
    assert {
        "observations",
        "assistant_conversations",
        "pending_actions",
    } <= TARGET_TABLE_OWNERS.keys()
    assert (ROOT / "frontend/apps/web/src").is_dir()
    assert not (ROOT / "src/openbiliclaw/web").exists()


def test_entrypoint_rejects_removed_cli_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.composition.entrypoints import main

    monkeypatch.setattr("sys.argv", ["openbiliclaw", "init"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
