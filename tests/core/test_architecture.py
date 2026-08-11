from __future__ import annotations

import ast
import sys
from importlib.util import resolve_name
from pathlib import Path


def _imported_modules(node: ast.AST, *, package: str = "openbiliclaw.core") -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if not isinstance(node, ast.ImportFrom):
        return ()
    if node.level:
        relative = "." * node.level
        if node.module is not None:
            return (resolve_name(relative + node.module, package),)
        return tuple(resolve_name(relative + alias.name, package) for alias in node.names)
    if node.module == "openbiliclaw":
        return tuple(f"openbiliclaw.{alias.name}" for alias in node.names)
    return (node.module,) if node.module is not None else ()


def _is_allowed(module: str) -> bool:
    root = module.split(".", maxsplit=1)[0]
    return (
        root in sys.stdlib_module_names
        or module == "pydantic"
        or module.startswith("pydantic.")
        or module == "openbiliclaw.core"
        or module.startswith("openbiliclaw.core.")
    )


def test_core_imports_only_stdlib_pydantic_and_itself() -> None:
    core = Path(__file__).parents[2] / "src" / "openbiliclaw" / "core"
    violations: list[str] = []

    for path in sorted(core.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative_parent = path.relative_to(core).parent
        package_suffix = ".".join(relative_parent.parts)
        package = "openbiliclaw.core" + (f".{package_suffix}" if package_suffix else "")
        for node in ast.walk(tree):
            for module in _imported_modules(node, package=package):
                if not _is_allowed(module):
                    line = node.lineno if isinstance(node, ast.stmt) else 0
                    violations.append(f"{path.relative_to(core)}:{line}: {module}")

    assert not violations, "core imports outside its allowlist:\n" + "\n".join(violations)


def test_import_allowlist_rejects_legacy_and_future_non_core_packages() -> None:
    forbidden = (
        "openbiliclaw.llm",
        "openbiliclaw.api",
        "openbiliclaw.runtime",
        "openbiliclaw.storage",
        "openbiliclaw.ai",
        "openbiliclaw.hosts",
        "openbiliclaw.infrastructure",
        "openbiliclaw.application",
        "openbiliclaw.composition",
        "fastapi",
    )

    assert all(not _is_allowed(module) for module in forbidden)
    relative_import = ast.parse("from .. import core, runtime").body[0]
    resolved = _imported_modules(relative_import)
    assert resolved == ("openbiliclaw.core", "openbiliclaw.runtime")
    assert not _is_allowed(resolved[1])
