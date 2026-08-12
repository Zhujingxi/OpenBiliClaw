from __future__ import annotations

import ast
from pathlib import Path

PROVIDERS = {"reddit", "x", "zhihu", "linuxdo", "weibo"}


def test_manual_provider_packages_do_not_import_legacy_or_product_modules() -> None:
    root = Path(__file__).parents[3] / "src" / "openbiliclaw" / "content" / "providers"
    forbidden = (
        "openbiliclaw.sources",
        "openbiliclaw.runtime",
        "openbiliclaw.recommendation",
        "openbiliclaw.understanding",
        "openbiliclaw.assistant",
        "openbiliclaw.hosts",
    )
    violations: list[str] = []
    for provider in PROVIDERS:
        for path in (root / provider).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                else:
                    continue
                if any(name.startswith(forbidden) for name in names):
                    violations.append(str(path))
    assert not violations
