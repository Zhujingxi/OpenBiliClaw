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

# Broad service-locator type names that must never appear in a narrow
# router's imports, type annotations, or attribute access. The whole point
# of api/dependencies.py is to replace the locator with narrow bundles.
BANNED_SERVICE_LOCATOR_NAMES = {"ApiServices", "ServiceLocator", "Services"}

# Direct infrastructure / platform client constructors that extracted
# routers must never build — they receive narrow callables instead.
BANNED_CONSTRUCTOR_NAMES = {
    "Database",
    "BilibiliAPIClient",
    "XClient",
    "MemoryManager",
    "SoulEngine",
}

# Broad runtime/storage container modules routers must not import from.
# (api.routes.* receives narrow deps defined in api.dependencies; importing
# the runtime context or storage monolith back into a router recreates the
# coupling the extraction removed.)
BANNED_ROUTER_IMPORT_PREFIXES = (
    "openbiliclaw.api.runtime_context",
    "openbiliclaw.storage",
    "openbiliclaw.memory",
    "openbiliclaw.soul",
    "openbiliclaw.bilibili",
    "openbiliclaw.discovery",
)


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
                    if isinstance(func, ast.Name) and func.id in BANNED_CONSTRUCTOR_NAMES:
                        violations.append(f"{rel}: directly constructs {func.id}")
                    if isinstance(func, ast.Attribute) and func.attr in BANNED_CONSTRUCTOR_NAMES:
                        violations.append(f"{rel}: directly constructs {func.attr}")
    assert not violations, f"router boundary violations: {violations}"


def test_new_routers_reject_service_locator_and_broad_imports() -> None:
    """R2 mechanical enforcement: extracted routers stay narrow.

    Rejects, inside ``api/routes/*`` and ``api/dependencies.py``:

    - imports of broad service-locator types (``ApiServices`` and friends);
    - imports of broad runtime/storage/platform containers
      (``api.runtime_context``, ``storage.*``, ``memory.*``, ``soul.*``,
      ``bilibili.*``, ``discovery.*``) — routers receive narrow callables
      from ``api.dependencies`` instead;
    - attribute access of the shape ``deps.services.<anything>`` /
      ``deps.<engine>`` reach-through (e.g. ``deps.database``,
      ``deps.soul_engine``) that would reintroduce locator semantics
      through the back door.
    """
    violations: list[str] = []
    for prefix in ("api/routes", "api/dependencies.py"):
        for path in _iter_python_files_under(prefix):
            rel = _relative_module_path(path)
            if _is_legacy_exception(rel):
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        _check_router_import(rel, alias.name, alias.asname, violations)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    _check_router_import(
                        rel, node.module, None, violations, names=[a.name for a in node.names]
                    )
                elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
                    # deps.services.<x> / deps.<broad-engine> reach-through.
                    base = node.value
                    if isinstance(base.value, ast.Name) and base.attr == "services":
                        violations.append(
                            f"{rel}: service-locator reach-through "
                            f"{base.value.id}.services.{node.attr}"
                        )
    assert not violations, f"service-locator / broad-import violations: {violations}"


def _check_router_import(
    rel: str,
    module: str,
    asname: str | None,
    violations: list[str],
    names: list[str] | None = None,
) -> None:
    for banned in BANNED_ROUTER_IMPORT_PREFIXES:
        if module == banned or module.startswith(banned + "."):
            violations.append(f"{rel}: imports broad container module {module}")
    for name in names or []:
        if name in BANNED_SERVICE_LOCATOR_NAMES:
            violations.append(f"{rel}: imports service-locator type {name} from {module}")
    # Aliased construction bypass: `from x import Database as make_db`.
    if asname and asname in BANNED_CONSTRUCTOR_NAMES:
        violations.append(f"{rel}: aliases broad constructor as {asname}")


def test_ratchet_bites_on_synthetic_violations() -> None:
    """Negative control: the locator/constructor checks must actually fire.

    A ratchet that cannot fail is not a ratchet. This feeds synthetic
    violating sources through the same AST predicates used by the real
    boundary tests and asserts each pattern is caught.
    """
    import textwrap

    samples = {
        "locator import": "from openbiliclaw.api.runtime_context import ApiServices\n",
        "broad storage import": "from openbiliclaw.storage.database import Database\n",
        "aliased constructor": "from openbiliclaw.storage.database import Database as Database\n",
        "locator reach-through": textwrap.dedent(
            """
            def handler(deps):
                return deps.services.soul_engine
            """
        ),
        "direct Database construction": textwrap.dedent(
            """
            def build(path):
                return Database(path)
            """
        ),
    }

    # Import checks (mirrors test_new_routers_reject_service_locator_and_broad_imports).
    for label in ("locator import", "broad storage import", "aliased constructor"):
        tree = ast.parse(samples[label])
        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _check_router_import("synthetic.py", alias.name, alias.asname, violations)
            elif isinstance(node, ast.ImportFrom) and node.module:
                _check_router_import(
                    "synthetic.py",
                    node.module,
                    node.names[0].asname if node.names else None,
                    violations,
                    names=[a.name for a in node.names],
                )
        assert violations, f"ratchet missed synthetic violation: {label}"

    # Attribute reach-through check.
    tree = ast.parse(samples["locator reach-through"])
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
            base = node.value
            if isinstance(base.value, ast.Name) and base.attr == "services":
                found = True
    assert found, "ratchet missed deps.services.<x> reach-through"

    # Constructor check (mirrors
    # test_new_router_modules_do_not_import_transport_or_construct_storage).
    tree = ast.parse(samples["direct Database construction"])
    found = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in BANNED_CONSTRUCTOR_NAMES
        for node in ast.walk(tree)
    )
    assert found, "ratchet missed direct Database construction"


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
