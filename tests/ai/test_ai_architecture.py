from __future__ import annotations

import ast
import sys
from importlib.util import resolve_name
from pathlib import Path


def _imports(node: ast.AST, package: str) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if not isinstance(node, ast.ImportFrom):
        return ()
    if node.level:
        relative = "." * node.level + (node.module or "")
        if node.module:
            return (resolve_name(relative, package),)
        return tuple(resolve_name("." * node.level + alias.name, package) for alias in node.names)
    return (node.module,) if node.module else ()


def _allowed(module: str) -> bool:
    root = module.split(".", 1)[0]
    return (
        root in sys.stdlib_module_names
        or root in {"pydantic", "pydantic_ai"}
        or module.startswith("openbiliclaw.ai")
        or module.startswith("openbiliclaw.core")
        or module == "openbiliclaw.infrastructure.telemetry"
    )


def test_ai_imports_only_technical_boundaries() -> None:
    root = Path(__file__).parents[2] / "src" / "openbiliclaw" / "ai"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).parent
        package = "openbiliclaw.ai" + ("." + ".".join(relative.parts) if relative.parts else "")
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            for module in _imports(node, package):
                if not _allowed(module):
                    violations.append(
                        f"{path.relative_to(root)}:{getattr(node, 'lineno', 0)}: {module}"
                    )
    assert not violations, "AI runtime import violations:\n" + "\n".join(violations)


def test_allowlist_rejects_product_and_legacy_modules() -> None:
    assert all(
        not _allowed(name)
        for name in (
            "openbiliclaw.llm",
            "openbiliclaw.eval",
            "openbiliclaw.agent",
            "openbiliclaw.recommendation",
            "openbiliclaw.understanding",
            "openbiliclaw.api",
        )
    )
