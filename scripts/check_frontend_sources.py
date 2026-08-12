"""Fail when the target frontend workspace contains handwritten JavaScript."""

from __future__ import annotations

import argparse
from pathlib import Path

_FORBIDDEN = {".js", ".mjs", ".cjs"}
_IGNORED_PARTS = {"dist", "node_modules"}


def offenders(root: Path) -> tuple[Path, ...]:
    """Return forbidden source/test files outside generated build directories."""

    return tuple(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in _FORBIDDEN
        and not (_IGNORED_PARTS & set(path.relative_to(root).parts))
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("frontend"))
    args = parser.parse_args()
    found = offenders(args.root)
    if found:
        rendered = "\n".join(str(path) for path in found)
        raise SystemExit(f"handwritten JavaScript is forbidden:\n{rendered}")


if __name__ == "__main__":
    main()
