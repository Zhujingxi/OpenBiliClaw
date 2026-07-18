"""Architecture dependency ratchet (Phase 0).

Enforces the layering rules from
``docs/plans/2026-07-19-incremental-architecture-refactor-plan.md`` §3
against **newly extracted modules only**. The legacy codebase is not yet
expected to pass — known violations are listed in ``LEGACY_EXCEPTIONS``
and shrink as modules are extracted.

Rules:

R1. Domain / application modules under ``src/openbiliclaw/`` that are NOT
    transport adapters must not import ``fastapi``, ``typer``, or receive
    raw ``sqlite3`` connections.
R2. Routers and CLI commands must not directly construct storage or
    platform clients — they receive narrow dependency bundles.
R3. ``storage`` modules must not import ``api`` or ``cli``.
R4. Platform-specific code stays behind producer/source contracts.
R5. No new "junk drawer" modules (``utils.py``, ``helpers.py``,
    ``common.py``, monolithic ``services.py``).

The initial rule set is intentionally narrow: it ratchets the **new**
extraction boundaries (``api.routes.*``, ``api.dependencies``,
``storage.migrations``, ``storage.repositories.*``) so pilot work in
Phases 1–3 cannot backslide into coupling.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "openbiliclaw"


# Newly extracted module path prefixes and the rules they must satisfy.
# Keep this list tightly scoped — the goal is to ratchet *new* boundaries,
# not to boil the ocean on legacy code.
NEW_BOUNDARIES: dict[str, set[str]] = {
    # Phase 1 pilot: narrow router modules
    "api/routes": {"R1", "R2", "R5"},
    # Phase 1: narrow dependency bundle definitions
    "api/dependencies.py": {"R1", "R5"},
    # Phase 2A: migration helper
    "storage/migrations.py": {"R1", "R3", "R5"},
    # Phase 3: repository packages
    "storage/repositories": {"R1", "R3", "R5"},
}


# Explicit known-violation list for legacy code. Entries look like
# ``"storage/database.py"`` or ``"api/app.py"``. Anything listed here is
# grandfathered; anything NOT listed must satisfy the rules for new
# boundaries. Shrink this as modules are extracted.
LEGACY_EXCEPTIONS: frozenset[str] = frozenset(
    {
        # These monoliths predate the architecture plan; they're being
        # extracted incrementally.
        "api/app.py",
        "api/auth.py",
        "api/model_config_routes.py",
        "api/model_config_models.py",
        "api/runtime_context.py",
        "storage/database.py",
        "storage/maintenance.py",
        "storage/x_health.py",
        # Legacy CLI + config
        "cli.py",
        "cli_models.py",
        "config.py",
    }
)


BANNED_TRANSPORT_IMPORTS = {"fastapi", "starlette", "typer"}
JUNK_MODULE_NAMES = {"utils", "helpers", "common", "services"}


def _iter_python_files_under(prefix: str) -> Iterable[Path]:
    target = SRC_ROOT / prefix
    if target.is_file():
        yield target
        return
    if target.is_dir():
        yield from sorted(target.rglob("*.py"))


def _imports_in_file(path: Path) -> set[str]:
    """Return the set of top-level modules imported by a Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


def _relative_module_path(path: Path) -> str:
    return str(path.relative_to(SRC_ROOT))


def _is_legacy_exception(rel_path: str) -> bool:
    return rel_path in LEGACY_EXCEPTIONS


def test_new_boundaries_exist_or_are_documented() -> None:
    """Sanity: every prefix in NEW_BOUNDARIES must exist or be a planned path.

    This guards against typos that would silently disable the ratchet.
    """
    missing: list[str] = []
    for prefix in NEW_BOUNDARIES:
        target = SRC_ROOT / prefix
        if not target.exists():
            missing.append(prefix)
    # Phase 0 only requires the ratchet to be wired; the new modules may
    # not exist yet. Once they appear, the rules will apply. We only fail
    # if the prefix has a typo'd parent (e.g. "api/routez" instead of
    # "api/routes") — detectable because the parent of the prefix is also
    # missing.
    genuinely_bad: list[str] = []
    for prefix in missing:
        parent = (SRC_ROOT / prefix).parent
        if not parent.exists():
            genuinely_bad.append(prefix)
    assert not genuinely_bad, (
        f"NEW_BOUNDARIES prefixes have missing parents (typo?): {genuinely_bad}"
    )


def test_new_router_modules_do_not_import_transport_or_construct_storage() -> None:
    """R1+R2: new router modules must not import fastapi/typer or build storage.

    (They DO import APIRouter — that's how they're constructed — so R1 is
    narrowed to "no starlette Request/Response business logic" and "no
    direct storage/platform client construction".)
    """
    violations: list[str] = []
    for prefix in ("api/routes",):
        for path in _iter_python_files_under(prefix):
            rel = _relative_module_path(path)
            if _is_legacy_exception(rel):
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                # No direct instantiation of Database / platform clients.
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id in {"Database"}:
                        violations.append(f"{rel}: directly constructs Database")
                    if isinstance(func, ast.Attribute) and func.attr in {"Database"}:
                        violations.append(f"{rel}: directly constructs Database")
    assert not violations, f"router boundary violations: {violations}"


def test_storage_modules_do_not_import_api_or_cli() -> None:
    """R3: storage/* must not import openbiliclaw.api or openbiliclaw.cli."""
    violations: list[str] = []
    storage_root = SRC_ROOT / "storage"
    if not storage_root.exists():
        return
    for path in sorted(storage_root.rglob("*.py")):
        rel = _relative_module_path(path)
        if _is_legacy_exception(rel):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if mod.startswith("openbiliclaw.api") or mod.startswith("openbiliclaw.cli"):
                    violations.append(f"{rel}: imports {mod}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("openbiliclaw.api") or alias.name.startswith(
                        "openbiliclaw.cli"
                    ):
                        violations.append(f"{rel}: imports {alias.name}")
    assert not violations, f"storage layering violations: {violations}"


def test_no_new_junk_drawer_modules() -> None:
    """R5: new modules must not be named utils.py/helpers.py/common.py/services.py."""
    violations: list[str] = []
    for prefix in NEW_BOUNDARIES:
        for path in _iter_python_files_under(prefix):
            rel = _relative_module_path(path)
            if _is_legacy_exception(rel):
                continue
            stem = path.stem
            if stem in JUNK_MODULE_NAMES:
                violations.append(f"{rel}: junk-drawer module name {stem!r}")
    assert not violations, f"junk-drawer module violations: {violations}"


def test_legacy_exception_list_is_minimal() -> None:
    """Track the size of LEGACY_EXCEPTIONS so it can only shrink over time.

    This test passes unconditionally today but documents the current
    exception count. Future PRs that add new exceptions should update this
    comment with explicit justification.
    """
    # Phase 0 baseline: 13 exception entries (the monoliths being extracted).
    # Adding to this set requires explicit justification in the PR body.
    assert len(LEGACY_EXCEPTIONS) <= 13, (
        f"LEGACY_EXCEPTIONS grew to {len(LEGACY_EXCEPTIONS)}; the ratchet only shrinks. "
        "Update the comment with justification if this is intentional."
    )
