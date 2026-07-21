"""Repository-wide architecture acceptance policies for the vNext cut-over."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import click
from typer.main import get_command
from typer.testing import CliRunner

from openbiliclaw.api.app import create_app
from openbiliclaw.cli import app

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "openbiliclaw"

OBSOLETE_PACKAGES = (
    "agent",
    "bilibili",
    "discovery",
    "eval",
    "integrations",
    "llm",
    "memory",
    "model_config",
    "recommendation",
    "runtime",
    "saved_sync",
    "soul",
    "sources",
    "storage",
    "youtube",
)
OBSOLETE_MODULES = (
    PACKAGE / "cli_models.py",
    PACKAGE / "config.py",
    PACKAGE / "config_write.py",
    PACKAGE / "docker_runtime.py",
    PACKAGE / "published_time.py",
    PACKAGE / "api" / "auth.py",
    PACKAGE / "api" / "model_config_models.py",
    PACKAGE / "api" / "model_config_routes.py",
    PACKAGE / "api" / "models.py",
    PACKAGE / "api" / "runtime_context.py",
)
DESKTOP_ARTIFACTS = (
    ROOT / "packaging",
    ROOT / ".github" / "workflows" / "release-desktop.yml",
    ROOT / ".github" / "workflows" / "build-installers.yml",
    ROOT / "docker" / "ollama-bundled.Dockerfile",
)
OBSOLETE_SUPPORT_ARTIFACTS = (
    ROOT / ".planning" / "desktop-after-expand.png",
    ROOT / ".planning" / "phases" / "image-proxy" / "PLAN.md",
    ROOT / ".planning" / "phases" / "image-proxy" / "SPEC.md",
    ROOT / ".planning" / "todos" / "completed" / "2026-06-15-cognition-cursor-incremental-read.md",
    ROOT / ".planning" / "todos" / "completed" / "2026-06-15-insight-soft-invalidation-unwired.md",
    ROOT / "docs" / "images" / "chrome-web-store" / "demo-covers" / "08-delight-local-first.png",
    ROOT / "docs" / "images" / "screenshot-interest-probe.png",
    ROOT / "skills" / "openbiliclaw-adapter",
    ROOT / "scripts" / "build_chrome_webstore_assets.py",
    ROOT / "scripts" / "build_readme_hero_demo.py",
    ROOT / "scripts" / "build_chrome_webstore_demo_covers.py",
    ROOT / "scripts" / "capture_chrome_webstore_ui.py",
    ROOT / "scripts" / "chrome_webstore_demo.py",
    ROOT / "src" / "openbiliclaw" / "web" / "desktop" / "assets" / "css" / "README-THEME.md",
    ROOT / "tests" / "fixtures" / "awareness_singular_note.json",
    ROOT / "tests" / "js" / "mobile-app-launch.test.mjs",
    ROOT / "tests" / "js" / "mobile-model-settings-controller.test.mjs",
    ROOT / "tests" / "js" / "mobile-probe-notification-helpers.test.mjs",
)
PROVIDER_MODULES = ("anthropic", "google", "openai", "ollama")
SOURCE_IDS = frozenset(
    {"bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu", "reddit"}
)
SQL_STATEMENT = re.compile(
    r"^\s*(?:PRAGMA|SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|BEGIN|COMMIT|ROLLBACK)\b",
    re.IGNORECASE,
)


def _python_files(path: Path) -> tuple[Path, ...]:
    return tuple(sorted(candidate for candidate in path.rglob("*.py") if candidate.is_file()))


def _artifact_exists(path: Path) -> bool:
    if path.is_file():
        return True
    return path.is_dir() and any(
        candidate.is_file() and "__pycache__" not in candidate.parts
        for candidate in path.rglob("*")
    )


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return tuple(names)


def _qualified_call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if not isinstance(node.func, ast.Attribute):
        return ""
    parts = [node.func.attr]
    current = node.func.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    elif isinstance(current, ast.Call) and isinstance(current.func, ast.Name):
        parts.append(current.func.id)
    return ".".join(reversed(parts))


def _contains_json_path(node: ast.AST, *, known_paths: frozenset[str] = frozenset()) -> bool:
    return any(
        (
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and ".json" in value.value.lower()
        )
        or (isinstance(value, ast.Name) and value.id in known_paths)
        for value in ast.walk(node)
    )


def _write_mode(node: ast.Call) -> bool:
    mode_index = 0 if isinstance(node.func, ast.Attribute) else 1
    mode: ast.AST | None = node.args[mode_index] if len(node.args) > mode_index else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and any(marker in mode.value for marker in "wax+")
    )


def _profile_json_write_calls(source: str) -> tuple[tuple[int, str], ...]:
    """Find JSON file persistence without rejecting ordinary serialization/writes."""

    tree = ast.parse(source)
    json_paths = frozenset(
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in ((*node.targets,) if isinstance(node, ast.Assign) else (node.target,))
        if isinstance(target, ast.Name)
        and node.value is not None
        and _contains_json_path(node.value)
    )
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _qualified_call_name(node)
        method = name.rsplit(".", 1)[-1]
        has_json_path = _contains_json_path(node, known_paths=json_paths)
        if has_json_path and (
            method in {"write_text", "write_bytes"} or method == "open" and _write_mode(node)
        ):
            violations.append((node.lineno, name))
    return tuple(violations)


def test_obsolete_backend_packages_are_absent() -> None:
    present = [name for name in OBSOLETE_PACKAGES if _python_files(PACKAGE / name)]
    present.extend(str(path.relative_to(ROOT)) for path in OBSOLETE_MODULES if path.is_file())
    assert present == []


def test_desktop_application_and_packaging_artifacts_are_absent() -> None:
    present = [str(path.relative_to(ROOT)) for path in DESKTOP_ARTIFACTS if _artifact_exists(path)]
    assert present == []

    browser_desktop = PACKAGE / "web" / "desktop"
    assert (browser_desktop / "index.html").is_file()
    assert (browser_desktop / "assets" / "js" / "app.js").is_file()


def test_obsolete_openclaw_planning_fixture_and_demo_artifacts_are_absent() -> None:
    present = [
        str(path.relative_to(ROOT)) for path in OBSOLETE_SUPPORT_ARTIFACTS if _artifact_exists(path)
    ]
    assert present == []


def test_release_automation_has_no_native_desktop_or_bundled_provider_path() -> None:
    active_release_files = (
        ROOT / ".claude" / "skills" / "release" / "SKILL.md",
        ROOT / ".github" / "scripts" / "sync-aggregate-release.sh",
        ROOT / ".github" / "workflows" / "release-docker.yml",
        ROOT / ".github" / "workflows" / "verify-release-completeness.yml",
        ROOT / "scripts" / "release.py",
    )
    forbidden = (
        "desktop-v",
        "release-desktop",
        "openbiliclaw-ollama",
        "ollama-bundled.Dockerfile",
        "packaging/openbiliclaw.iss",
    )
    violations = [
        f"{path.relative_to(ROOT)}: {marker}"
        for path in active_release_files
        for marker in forbidden
        if marker in path.read_text(encoding="utf-8")
    ]
    assert violations == []


def test_deployment_configuration_has_one_vnext_settings_authority() -> None:
    template = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert tomllib.loads(template) == {}
    assert "/api/v1/settings" in template
    assert "data/vnext/openbiliclaw.db" in template
    for obsolete in ("[models]", "[scheduler]", "data/openbiliclaw.db", "openbiliclaw models"):
        assert obsolete not in template

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "config.example.toml" not in dockerfile
    for name in ("docker-compose.yml", "docker-compose.prebuilt.yml"):
        compose = (ROOT / name).read_text(encoding="utf-8")
        assert "OPENBILICLAW_CONFIG_TEMPLATE" not in compose
        assert "openbiliclaw_config" not in compose
        assert 'command: ["openbiliclaw", "worker"' in compose

    for name in ("scripts/install.sh", "scripts/install.ps1"):
        installer = (ROOT / name).read_text(encoding="utf-8")
        assert "runtime_bootstrap.py" in installer
        assert "agent_bootstrap.py" not in installer
        assert "OPENBILICLAW_LITELLM_BASE_URL" in installer
        assert "OPENBILICLAW_LITELLM_API_KEY" in installer
        assert "openbiliclaw models" not in installer

    bootstrap = ROOT / "scripts" / "runtime_bootstrap.py"
    assert bootstrap.is_file()
    assert not any(
        imported == "openbiliclaw" or imported.startswith("openbiliclaw.")
        for imported in _imports(bootstrap)
    )
    assert not (ROOT / "scripts" / "agent_bootstrap.py").exists()
    ai_doc = (ROOT / "docs" / "modules" / "vnext-ai.md").read_text(encoding="utf-8")
    assert "scripts/runtime_bootstrap.py" in ai_doc
    assert "agent_bootstrap.py" not in ai_doc


def test_application_features_do_not_import_provider_sdks() -> None:
    violations: list[str] = []
    for path in _python_files(PACKAGE / "features"):
        for imported in _imports(path):
            if imported in PROVIDER_MODULES or imported.startswith(
                tuple(f"{provider}." for provider in PROVIDER_MODULES)
            ):
                violations.append(f"{path.relative_to(ROOT)}: {imported}")
    assert violations == []


def test_platform_conditionals_live_only_in_source_packages() -> None:
    roots = (PACKAGE / "api", PACKAGE / "features", PACKAGE / "infrastructure")
    violations: list[str] = []
    for root in roots:
        for path in _python_files(root):
            if (PACKAGE / "infrastructure" / "sources") in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                conditions: tuple[ast.AST, ...]
                if isinstance(node, (ast.If, ast.IfExp)):
                    conditions = (node.test,)
                elif isinstance(node, ast.Match):
                    conditions = (node.subject,) + tuple(
                        condition
                        for case in node.cases
                        for condition in (case.pattern, case.guard)
                        if condition is not None
                    )
                else:
                    continue
                literals = {
                    value.value
                    for condition in conditions
                    for value in ast.walk(condition)
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                }
                matched = literals & SOURCE_IDS
                if matched:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {sorted(matched)}")
    assert violations == []


def test_profile_feature_has_no_json_file_write_path() -> None:
    violations: list[str] = []
    for path in _python_files(PACKAGE / "features" / "profile"):
        for line, name in _profile_json_write_calls(path.read_text(encoding="utf-8")):
            violations.append(f"{path.relative_to(ROOT)}:{line}: {name}")
    assert violations == []


def test_profile_json_policy_distinguishes_serialization_from_file_persistence() -> None:
    allowed = """
import json
payload = json.dumps({'revision': 1})
stream.write(payload)
Path('profile.txt').write_text(payload)
"""
    forbidden = """
import json
Path('profile.json').write_text('{}')
profile_path = Path('profile-snapshot.json')
profile_path.open('w')
"""

    assert _profile_json_write_calls(allowed) == ()
    assert {name for _line, name in _profile_json_write_calls(forbidden)} == {
        "Path.write_text",
        "profile_path.open",
    }


def test_raw_sql_is_isolated_to_migrations_and_database_operations() -> None:
    allowed = {
        PACKAGE / "infrastructure" / "database" / "base.py",
        PACKAGE / "infrastructure" / "database" / "operations.py",
    }
    violations: list[str] = []
    for path in _python_files(PACKAGE):
        if path in allowed or "alembic" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            call_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            statement = node.args[0]
            if (
                call_name in {"execute", "executemany", "executescript", "exec_driver_sql", "text"}
                and isinstance(statement, ast.Constant)
                and isinstance(statement.value, str)
                and SQL_STATEMENT.match(statement.value)
            ):
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: {call_name}({statement.value[:24]!r})"
                )
    assert violations == []


def test_app_factory_contains_composition_only() -> None:
    path = PACKAGE / "api" / "app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    factory = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "create_app"
    )
    forbidden_calls = {
        "run",
        "schedule",
        "replenish",
        "project",
        "synchronize",
        "create_task",
    }
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(factory)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert calls.isdisjoint(forbidden_calls)

    imports = _imports(path)
    assert not any(
        imported == "openbiliclaw.features"
        or imported.startswith("openbiliclaw.features.")
        or imported == "openbiliclaw.infrastructure"
        or imported.startswith("openbiliclaw.infrastructure.")
        for imported in imports
    )


def test_public_api_and_cli_have_no_legacy_compatibility_surface() -> None:
    created = create_app()
    paths = set(created.openapi()["paths"])
    assert paths
    assert all(path.startswith("/api/v1/") for path in paths)
    assert not any(
        marker in path
        for path in paths
        for marker in ("model-config", "soul", "delight", "saved-sync", "openclaw", "update")
    )
    mounted_paths = {getattr(route, "path", "") for route in created.routes}
    assert {"/setup", "/web", "/m"}.issubset(mounted_paths)

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    command = get_command(app)
    assert isinstance(command, click.Group)
    command_names = set(command.commands)
    assert {"serve", "worker", "doctor", "eval", "db"}.issubset(command_names)
    for obsolete in ("start", "init", "profile", "recommend", "config-show", "cost"):
        assert obsolete not in command_names
