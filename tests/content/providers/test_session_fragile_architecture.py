from __future__ import annotations

import ast
import sys
from pathlib import Path


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


def _allowed(module: str, provider: str) -> bool:
    root = module.split(".", maxsplit=1)[0]
    return (
        root in sys.stdlib_module_names
        or root == "pydantic"
        or module.startswith("openbiliclaw.content.integration")
        or module.startswith(f"openbiliclaw.content.providers.{provider}")
        or module.startswith("openbiliclaw.access")
        or module.startswith("openbiliclaw.core")
    )


def test_session_fragile_providers_import_only_approved_boundaries() -> None:
    source = Path(__file__).parents[3] / "src" / "openbiliclaw" / "content" / "providers"
    violations: list[str] = []
    for provider in ("douyin", "rednote"):
        root = source / provider
        for path in root.rglob("*.py"):
            package = f"openbiliclaw.content.providers.{provider}"
            tree = ast.parse(path.read_text())
            violations.extend(
                f"{provider}/{path.name}:{getattr(node, 'lineno', 0)}: {module}"
                for node in ast.walk(tree)
                for module in _imports(node, package)
                if not _allowed(module, provider)
            )
    assert violations == []
    forbidden = (
        "openbiliclaw.sources",
        "openbiliclaw.runtime",
        "openbiliclaw.understanding",
        "openbiliclaw.recommendation",
        "openbiliclaw.assistant",
        "openbiliclaw.hosts",
        "playwright",
    )
    assert all(not _allowed(module, "douyin") for module in forbidden)
