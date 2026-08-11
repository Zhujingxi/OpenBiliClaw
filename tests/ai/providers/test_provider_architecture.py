from __future__ import annotations

import ast
from pathlib import Path


def test_ai_providers_imports_only_technical_boundaries() -> None:
    root = Path("src/openbiliclaw/ai/providers")
    forbidden = {
        "application",
        "assistant",
        "bilibili",
        "content",
        "discovery",
        "memory",
        "recommendation",
        "soul",
        "understanding",
    }
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("openbiliclaw.")
            ):
                part = node.module.split(".")[1]
                if part in forbidden:
                    offenders.append(f"{path}:{node.lineno}:{node.module}")
    assert offenders == []


def test_vault_is_only_imported_by_model_factory() -> None:
    root = Path("src/openbiliclaw/ai/providers")
    offenders = []
    for path in root.rglob("*.py"):
        if path.name != "factory.py" and "CredentialVault" in path.read_text(encoding="utf-8"):
            offenders.append(str(path))
    assert offenders == []
