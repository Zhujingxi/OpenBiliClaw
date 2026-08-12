"""Fail when project-owned source/test trees contain handwritten JavaScript."""

from __future__ import annotations

import argparse
from pathlib import Path

_FORBIDDEN = {".js", ".mjs", ".cjs"}
_IGNORED_PARTS = {"dist", "node_modules", ".git", ".venv", "build", "artifacts"}
_DEFAULT_TREES = ("frontend", "extension", "src", "tests", "scripts", "packaging")


def offenders(root: Path) -> tuple[Path, ...]:
    """Return forbidden project files outside generated/third-party directories."""
    return tuple(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in _FORBIDDEN
        and not (_IGNORED_PARTS & set(path.relative_to(root).parts))
    )


def repository_offenders(root: Path) -> tuple[Path, ...]:
    """Scan every production/source/test tree required by the refactor gate."""
    return tuple(
        path for name in _DEFAULT_TREES if (root / name).exists() for path in offenders(root / name)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    found = repository_offenders(args.root)
    if found:
        rendered = "\n".join(str(path) for path in found)
        raise SystemExit(f"handwritten JavaScript is forbidden:\n{rendered}")


if __name__ == "__main__":
    main()
