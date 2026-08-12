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


def _allowed(module: str) -> bool:
    root = module.split(".", maxsplit=1)[0]
    return (
        root in sys.stdlib_module_names
        or root in {"pydantic", "pydantic_ai"}
        or module.startswith("openbiliclaw.content.integration")
        or module.startswith("openbiliclaw.core")
        or module.startswith("openbiliclaw.access.models")
    )


def test_content_integration_imports_only_contract_boundaries() -> None:
    root = Path(__file__).parents[2] / "src" / "openbiliclaw" / "content" / "integration"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).parent
        package = "openbiliclaw.content.integration" + (
            "." + ".".join(relative.parts) if relative.parts else ""
        )
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            for module in _imports(node, package):
                if not _allowed(module):
                    violations.append(
                        f"{path.relative_to(root)}:{getattr(node, 'lineno', 0)}: {module}"
                    )
    assert not violations, "Content Integration import violations:\n" + "\n".join(violations)


def test_allowlist_rejects_concrete_providers_and_product_modules() -> None:
    assert all(
        not _allowed(module)
        for module in (
            "openbiliclaw.content.providers.bilibili",
            "openbiliclaw.understanding",
            "openbiliclaw.recommendation",
            "openbiliclaw.assistant",
            "openbiliclaw.hosts",
            "openbiliclaw.sources",
        )
    )
