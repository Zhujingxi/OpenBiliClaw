"""Recommendation package dependency-boundary checks."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def _imports(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return (node.module,)
    return ()


def test_recommendation_imports_only_approved_boundaries() -> None:
    root = Path(__file__).parents[2] / "src" / "openbiliclaw" / "recommendation"
    violations: list[str] = []
    legacy = {"curator.py", "delight.py", "engine.py", "exclusion.py", "visual_profile.py"}
    for path in root.rglob("*.py"):
        if path.parent == root and path.name in legacy:
            continue  # Legacy implementation remains isolated until Plan 15 cutover.
        for node in ast.walk(ast.parse(path.read_text())):
            for module in _imports(node):
                top = module.split(".")[0]
                allowed = (
                    top in sys.stdlib_module_names
                    or top in {"pydantic", "pydantic_ai"}
                    or module.startswith("openbiliclaw.recommendation")
                    or module.startswith("openbiliclaw.content.integration")
                    or module.startswith("openbiliclaw.understanding.projections")
                    or module.startswith("openbiliclaw.ai")
                    or module.startswith("openbiliclaw.core")
                    or module.startswith("openbiliclaw.infrastructure")
                    or module.startswith("openbiliclaw.access.models")
                )
                if module.startswith("openbiliclaw") and not allowed:
                    violations.append(f"{path.name}:{getattr(node, 'lineno', 0)}: {module}")
    assert not violations, "recommendation import violations:\n" + "\n".join(violations)
