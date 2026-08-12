from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from pydantic_ai import Agent, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from openbiliclaw.access.models import (
    AnonymousAccessHandle,
    CredentialAccessHandle,
    Permission,
    VerificationFailure,
    VerificationStrength,
)
from openbiliclaw.content.integration.manifest import CapabilityKind
from openbiliclaw.content.integration.tools import ToolBudget
from openbiliclaw.content.providers.bilibili.auth import BilibiliCredentialVerifier
from openbiliclaw.content.providers.bilibili.capabilities import BilibiliProvider
from openbiliclaw.content.providers.bilibili.client import BilibiliClient
from openbiliclaw.content.providers.bilibili.tools import build_bilibili_tools

FIXTURES = Path(__file__).with_name("fixtures")


class FixtureTransport:
    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = routes
        self.cookies: list[str | None] = []

    async def __call__(
        self, method: str, path: str, query: str, cookie: str | None, body: bytes
    ) -> bytes:
        self.cookies.append(cookie)
        return self.routes[path]


async def _resolve(handle: CredentialAccessHandle) -> str:
    return "SESSDATA=CANARY; bili_jct=csrf"


def _provider(routes: dict[str, str]) -> BilibiliProvider:
    transport = FixtureTransport(
        {path: (FIXTURES / fixture).read_bytes() for path, fixture in routes.items()}
    )
    return BilibiliProvider(BilibiliClient(transport, _resolve))


@pytest.mark.asyncio
async def test_live_cookie_verifier_returns_safe_identity_and_failures() -> None:
    handle = CredentialAccessHandle(
        provider_id="bilibili",
        account_id="42",
        permissions=frozenset({Permission.READ_PRIVATE}),
        credential_ref="cred_" + "a" * 32,
        revision=1,
    )
    good_transport = FixtureTransport(
        {"/x/web-interface/nav": (FIXTURES / "nav_success.json").read_bytes()}
    )
    verifier = BilibiliCredentialVerifier(BilibiliClient(good_transport, _resolve))
    result = await verifier(
        handle, memoryview(json.dumps({"cookie": "SESSDATA=CANARY; bili_jct=csrf"}).encode())
    )
    assert result.strength is VerificationStrength.LIVE
    assert result.safe_account_identity == "safe-user"
    assert "CANARY" not in result.model_dump_json()

    bad_transport = FixtureTransport(
        {"/x/web-interface/nav": (FIXTURES / "auth_failure.json").read_bytes()}
    )
    verifier = BilibiliCredentialVerifier(BilibiliClient(bad_transport, _resolve))
    failure = await verifier(
        handle, memoryview(json.dumps({"cookie": "SESSDATA=CANARY; bili_jct=csrf"}).encode())
    )
    assert failure.sanitized_failure is VerificationFailure.EXPIRED


@pytest.mark.asyncio
async def test_search_tool_uses_same_capability_and_bounds_output() -> None:
    provider = _provider({"/x/web-interface/search/type": "search_success.json"})
    access = AnonymousAccessHandle(
        provider_id="bilibili", account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )
    tools = build_bilibili_tools(
        provider,
        access,
        enabled=frozenset({CapabilityKind.SEARCH}),
        budget=ToolBudget(max_items=1, max_title_chars=5, max_summary_chars=4),
    )

    def model(messages: list[object], info: object) -> ModelResponse:
        if any(
            getattr(part, "part_kind", None) == "tool-return"
            for message in messages
            for part in getattr(message, "parts", ())
        ):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[ToolCallPart("bilibili_search", {"query": "typed", "limit": 99})]
        )

    result = await Agent(FunctionModel(model), tools=tools).run("search")
    tool_returns = [
        part
        for message in result.all_messages()
        for part in message.parts
        if part.part_kind == "tool-return"
    ]
    assert len(tool_returns) == 1
    assert "Typed" in str(tool_returns[0].content)
    assert "A us" in str(tool_returns[0].content)


def _imports(node: ast.AST, package: str) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        if node.level == 0:
            return (node.module or "",)
        parts = package.split(".")
        base = parts[: len(parts) - node.level + 1]
        if node.module:
            base.extend(node.module.split("."))
        return (".".join(base),)
    return ()


def _allowed(module: str) -> bool:
    root = module.split(".", maxsplit=1)[0]
    return (
        root in sys.stdlib_module_names
        or root in {"pydantic", "pydantic_ai", "httpx"}
        or module.startswith("openbiliclaw.content.integration")
        or module.startswith("openbiliclaw.content.providers.bilibili")
        or module.startswith("openbiliclaw.access")
        or module.startswith("openbiliclaw.infrastructure")
        or module.startswith("openbiliclaw.core")
    )


def test_bilibili_provider_imports_only_approved_boundaries() -> None:
    root = Path(__file__).parents[3] / "src" / "openbiliclaw" / "content" / "providers" / "bilibili"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        package = "openbiliclaw.content.providers.bilibili"
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            violations.extend(
                f"{path.name}:{getattr(node, 'lineno', 0)}: {module}"
                for module in _imports(node, package)
                if not _allowed(module)
            )
    assert violations == []
    assert all(
        not _allowed(module)
        for module in (
            "openbiliclaw.understanding",
            "openbiliclaw.recommendation",
            "openbiliclaw.assistant",
            "openbiliclaw.hosts",
            "openbiliclaw.sources",
            "openbiliclaw.bilibili",
        )
    )
