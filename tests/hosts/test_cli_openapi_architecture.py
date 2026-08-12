from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.application.reads import JobHealthResult
from openbiliclaw.hosts.api import HostDependencies, create_app
from openbiliclaw.hosts.cli import CliRuntime, create_cli

from .test_api import Facade


@dataclass(slots=True)
class BrokenFacade(Facade):
    application_error: bool = False

    async def job_health(self) -> JobHealthResult:
        if self.application_error:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "conflict")
        raise RuntimeError("not available")


def test_cli_all_commands_and_exit_codes() -> None:
    runner = CliRunner()
    facade = Facade()
    app = create_cli(CliRuntime(facade=facade, console=Console(force_terminal=False)))
    claim = "claim_" + "a" * 32
    commands = [
        (["status"], "job_health"),
        (["config-diagnostics"], "config_diagnostics"),
        (["model-diagnostics"], "model_diagnostics"),
        (["profile"], "show_profile"),
        (["recommend", "--limit", "3"], "get_recommendations"),
        (["start"], "start"),
        (["connect", "demo", "builtin.manual", "connect:1"], "connect_source"),
        (["recommend-refresh", "refresh:1"], "refresh_recommendations"),
        (["profile-edit", claim, "remove", "profile:1", "account"], "edit_profile"),
        (["search", "demo", "cats"], "search_content"),
        (
            ["content", "demo", "video", "1", "https://example.com/1"],
            "get_content_details",
        ),
    ]
    for command, owner in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, (command, result.output, result.exception)
        assert facade.calls[-1] == owner
    assert runner.invoke(app, ["recommend", "--limit", "0"]).exit_code == 2
    for value in ("0", "-1", "51"):
        assert runner.invoke(app, ["search", "demo", "cats", "--limit", value]).exit_code == 2
    assert runner.invoke(app, ["connect", "BAD", "x", "short"]).exit_code == 2
    plain = create_cli(CliRuntime(BrokenFacade(), Console()))
    application = create_cli(CliRuntime(BrokenFacade(application_error=True), Console()))
    assert runner.invoke(plain, ["status"]).exit_code == 1
    assert runner.invoke(application, ["status"]).exit_code == 2


def test_openapi_matches_deterministic_snapshot() -> None:
    schema = create_app(HostDependencies(facade=Facade())).openapi()
    snapshot = Path(__file__).with_name("openapi.snapshot.json")
    expected = json.dumps(schema, sort_keys=True, indent=2) + "\n"
    assert expected == snapshot.read_text()
    assert "ErrorEnvelope" in expected
    assert "JobEvent" in expected
    assert "SecretStr" not in expected


def _imports(path: Path) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            result.add(node.module)
    return result


def _allowed_host_import(module: str) -> bool:
    allowed = (
        "openbiliclaw.hosts",
        "openbiliclaw.application",
        "openbiliclaw.assistant",
        "openbiliclaw.core",
        "openbiliclaw.access",
        "openbiliclaw.content.integration",
        "openbiliclaw.observations",
        "openbiliclaw.recommendation",
        "openbiliclaw.understanding",
        "openbiliclaw.proc",
    )
    stdlib = {
        "__future__",
        "asyncio",
        "collections",
        "contextlib",
        "dataclasses",
        "enum",
        "hmac",
        "ipaddress",
        "json",
        "pathlib",
        "time",
        "typing",
    }
    external = {"fastapi", "starlette", "pydantic", "typer", "rich"}
    if module.startswith("openbiliclaw"):
        return module.startswith(allowed)
    return module.split(".", 1)[0] in stdlib | external


def test_host_architecture_uses_allowlist_and_domain_framework_isolation() -> None:
    root = Path(__file__).parents[2] / "src" / "openbiliclaw"
    for path in (root / "hosts").rglob("*.py"):
        for module in _imports(path):
            assert _allowed_host_import(module), (path, module)
    domain_names = (
        "application",
        "assistant",
        "core",
        "content",
        "observations",
        "understanding",
        "recommendation",
    )
    for name in domain_names:
        for path in (root / name).rglob("*.py"):
            assert not any(
                module.split(".", 1)[0] in {"fastapi", "typer"} for module in _imports(path)
            ), path


def test_host_architecture_rejects_unknown_third_party_import() -> None:
    assert not _allowed_host_import("totally_random_package.client")
