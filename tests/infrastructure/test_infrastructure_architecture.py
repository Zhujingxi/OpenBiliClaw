from __future__ import annotations

import ast
from pathlib import Path


def test_infrastructure_imports_only_technical_boundaries() -> None:
    root = Path("src/openbiliclaw/infrastructure")
    allowed = ("openbiliclaw.infrastructure", "openbiliclaw.core")
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        project_imports = [name for name in imports if name.startswith("openbiliclaw")]
        assert not [name for name in project_imports if not name.startswith(allowed)], path
