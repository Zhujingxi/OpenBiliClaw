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
    root = module.split(".", 1)[0]
    return (
        root in sys.stdlib_module_names
        or root == "pydantic"
        or module.startswith("openbiliclaw.access")
        or module.startswith("openbiliclaw.core")
        or module == "openbiliclaw.infrastructure.credentials.vault"
        or module == "openbiliclaw.infrastructure.telemetry"
    )


def test_access_imports_only_trust_and_runtime_boundaries() -> None:
    root = Path(__file__).parents[2] / "src" / "openbiliclaw" / "access"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).parent
        package = "openbiliclaw.access" + ("." + ".".join(relative.parts) if relative.parts else "")
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            for module in _imports(node, package):
                if not _allowed(module):
                    violations.append(
                        f"{path.relative_to(root)}:{getattr(node, 'lineno', 0)}: {module}"
                    )
    assert not violations, "Access import violations:\n" + "\n".join(violations)


def test_model_visible_packages_cannot_import_credential_vault() -> None:
    source = Path(__file__).parents[2] / "src" / "openbiliclaw"
    violations: list[str] = []
    for package_name in ("ai", "assistant"):
        package = source / package_name
        if not package.exists():
            continue
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text())
            if any(
                module.startswith("openbiliclaw.infrastructure.credentials")
                for node in ast.walk(tree)
                for module in _imports(node, f"openbiliclaw.{package_name}")
            ):
                violations.append(str(path.relative_to(source)))
    assert not violations, "model-visible credential imports: " + ", ".join(violations)
