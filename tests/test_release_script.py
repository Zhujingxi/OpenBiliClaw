"""Tests for the repository release-version consistency tool."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "release.py"

BACKEND_VERSION = "0.3.161"
EXTENSION_VERSION = "0.2.9"


@pytest.fixture
def release_module() -> ModuleType:
    """Load the script as a module after proving the expected file exists."""
    if not SCRIPT_PATH.exists():

        class MissingReleaseModule:
            def __getattr__(self, _name: str) -> object:
                raise AssertionError("scripts/release.py has not been implemented")

        return cast("ModuleType", MissingReleaseModule())
    spec = importlib.util.spec_from_file_location("release_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def release_root(tmp_path: Path) -> Path:
    """Create a complete, internally consistent repository-format fixture tree."""
    files = {
        "pyproject.toml": f'[project]\nname = "openbiliclaw"\nversion = "{BACKEND_VERSION}"\n',
        "src/openbiliclaw/__init__.py": (
            f'"""Package metadata."""\n\n__version__ = "{BACKEND_VERSION}"\n'
        ),
        "docs/index.html": (
            '<script type="application/ld+json">\n'
            f'{{"name": "OpenBiliClaw", "softwareVersion": "{BACKEND_VERSION}"}}\n'
            "</script>\n"
        ),
        "uv.lock": (
            '[[package]]\nname = "dependency"\nversion = "9.9.9"\n\n'
            f'[[package]]\nname = "openbiliclaw"\nversion = "{BACKEND_VERSION}"\n'
            'source = { editable = "." }\n'
        ),
        "README.md": f"## 最近更新\n\n📌 最新版本：**v{BACKEND_VERSION}（2026-07-10）**\n",
        "README_EN.md": (f"## Recent Updates\n\n📌 Latest: **v{BACKEND_VERSION} (2026-07-10)**\n"),
        "packaging/openbiliclaw.iss": (
            "; Compile on Windows:\n"
            f";     iscc /DMyAppVersion={BACKEND_VERSION} packaging\\openbiliclaw.iss\n"
            "; Produces:\n"
            f";     dist\\release\\OpenBiliClaw-windows-{BACKEND_VERSION}-Setup.exe\n"
        ),
        "extension/manifest.json": (
            '{\n  "manifest_version": 3,\n'
            f'  "version": "{EXTENSION_VERSION}",\n  "name": "OpenBiliClaw"\n}}\n'
        ),
        "extension/package.json": (
            '{\n  "name": "openbiliclaw-extension",\n'
            f'  "version": "{EXTENSION_VERSION}",\n  "private": true\n}}\n'
        ),
        "extension/package-lock.json": (
            '{\n  "name": "openbiliclaw-extension",\n'
            f'  "version": "{EXTENSION_VERSION}",\n'
            '  "lockfileVersion": 3,\n  "packages": {\n    "": {\n'
            '      "name": "openbiliclaw-extension",\n'
            f'      "version": "{EXTENSION_VERSION}"\n'
            '    },\n    "node_modules/example": {\n      "version": "7.8.9"\n    }\n  }\n}\n'
        ),
        "docs/changelog.md": f"# Changelog\n\n## v{BACKEND_VERSION}: Fixture\n",
    }
    for relative_path, content in files.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _fake_uv_lock(calls: list[Path]) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(*_args: object, cwd: Path, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cwd)
        pyproject = (cwd / "pyproject.toml").read_text(encoding="utf-8")
        version = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
        assert version is not None
        lock_path = cwd / "uv.lock"
        lock_text = lock_path.read_text(encoding="utf-8")
        updated, count = re.subn(
            r'(\[\[package\]\]\nname = "openbiliclaw"\nversion = ")[^"]+("\n)',
            rf"\g<1>{version.group(1)}\2",
            lock_text,
        )
        assert count == 1
        lock_path.write_text(updated, encoding="utf-8")
        return subprocess.CompletedProcess(["uv", "lock"], 0)

    return run


def test_parsers_extract_versions_from_real_formats(
    release_module: ModuleType, release_root: Path
) -> None:
    cases = {
        "toml": ("pyproject.toml", (BACKEND_VERSION,)),
        "python": ("src/openbiliclaw/__init__.py", (BACKEND_VERSION,)),
        "json": ("extension/package.json", (EXTENSION_VERSION,)),
        "package-lock": (
            "extension/package-lock.json",
            (EXTENSION_VERSION, EXTENSION_VERSION),
        ),
        "iss": ("packaging/openbiliclaw.iss", (BACKEND_VERSION, BACKEND_VERSION)),
        "html": ("docs/index.html", (BACKEND_VERSION,)),
        "uv-lock": ("uv.lock", (BACKEND_VERSION,)),
        "readme-cn": ("README.md", (BACKEND_VERSION,)),
        "readme-en": ("README_EN.md", (BACKEND_VERSION,)),
    }

    for kind, (relative_path, expected) in cases.items():
        text = (release_root / relative_path).read_text(encoding="utf-8")
        assert release_module.parse_versions(text, kind) == expected


def test_check_versions_accepts_consistent_independent_groups(
    release_module: ModuleType, release_root: Path
) -> None:
    result = release_module.check_versions(release_root)

    assert result.exit_code == 0
    assert f"backend: {BACKEND_VERSION}" in result.report
    assert f"extension: {EXTENSION_VERSION}" in result.report


def test_check_versions_names_only_the_disagreeing_file(
    release_module: ModuleType, release_root: Path
) -> None:
    path = release_root / "src/openbiliclaw/__init__.py"
    path.write_text('__version__ = "0.3.160"\n', encoding="utf-8")

    result = release_module.check_versions(release_root)

    assert result.exit_code == 1
    inconsistent_lines = [line for line in result.report.splitlines() if "inconsistent" in line]
    assert inconsistent_lines == [
        "src/openbiliclaw/__init__.py: inconsistent (0.3.160; expected 0.3.161)"
    ]


@pytest.mark.parametrize("project_field", ["root", "packages-root"])
def test_package_lock_project_versions_are_enforced(
    release_module: ModuleType, release_root: Path, project_field: str
) -> None:
    path = release_root / "extension/package-lock.json"
    text = path.read_text(encoding="utf-8")
    if project_field == "root":
        text = text.replace(f'  "version": "{EXTENSION_VERSION}"', '  "version": "8.8.8"', 1)
    else:
        marker = f'      "version": "{EXTENSION_VERSION}"'
        text = text.replace(marker, '      "version": "8.8.8"', 1)
    path.write_text(text, encoding="utf-8")

    result = release_module.check_versions(release_root)

    assert result.exit_code == 1
    assert "extension/package-lock.json: inconsistent" in result.report


def test_package_lock_dependency_versions_are_ignored(
    release_module: ModuleType, release_root: Path
) -> None:
    path = release_root / "extension/package-lock.json"
    text = path.read_text(encoding="utf-8").replace('"version": "7.8.9"', '"version": "1.2.3"')
    path.write_text(text, encoding="utf-8")

    assert release_module.check_versions(release_root).exit_code == 0


def test_iss_versions_are_reported_but_never_fail_check(
    release_module: ModuleType, release_root: Path
) -> None:
    path = release_root / "packaging/openbiliclaw.iss"
    path.write_text(path.read_text(encoding="utf-8").replace(BACKEND_VERSION, "0.1.0", 1))

    result = release_module.check_versions(release_root)

    assert result.exit_code == 0
    assert "packaging/openbiliclaw.iss: warning" in result.report


def test_missing_changelog_heading_warns_without_failing(
    release_module: ModuleType, release_root: Path
) -> None:
    (release_root / "docs/changelog.md").write_text("# Changelog\n", encoding="utf-8")

    result = release_module.check_versions(release_root)

    assert result.exit_code == 0
    assert f"warning: docs/changelog.md has no ## v{BACKEND_VERSION} heading" in result.report


def test_backend_bump_preserves_extension_and_converges(
    release_module: ModuleType, release_root: Path
) -> None:
    extension_hashes = {
        path: hashlib.sha256((release_root / path).read_bytes()).hexdigest()
        for path in (
            "extension/manifest.json",
            "extension/package.json",
            "extension/package-lock.json",
        )
    }
    calls: list[Path] = []

    result = release_module.bump_versions(
        release_root, backend="0.4.0", uv_lock_runner=_fake_uv_lock(calls)
    )

    assert calls == [release_root]
    assert result.exit_code == 0
    assert "warning: docs/changelog.md has no ## v0.4.0 heading" in result.report
    assert release_module.parse_versions(
        (release_root / "packaging/openbiliclaw.iss").read_text(encoding="utf-8"), "iss"
    ) == ("0.4.0", "0.4.0")
    assert all(
        hashlib.sha256((release_root / path).read_bytes()).hexdigest() == before
        for path, before in extension_hashes.items()
    )
    (release_root / "docs/changelog.md").write_text(
        "# Changelog\n\n## v0.4.0: Fixture\n", encoding="utf-8"
    )
    assert release_module.check_versions(release_root).exit_code == 0


def test_extension_bump_targets_only_project_json_fields_and_preserves_formatting(
    release_module: ModuleType, release_root: Path
) -> None:
    lock_path = release_root / "extension/package-lock.json"
    before = lock_path.read_text(encoding="utf-8")
    before_shape = before.replace(EXTENSION_VERSION, "<VERSION>")

    result = release_module.bump_versions(release_root, extension="0.3.0")

    after = lock_path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert after.replace("0.3.0", "<VERSION>") == before_shape
    assert '"version": "7.8.9"' in after
    assert release_module.check_versions(release_root).exit_code == 0


def test_bump_is_fail_closed_when_any_target_is_missing(
    release_module: ModuleType, release_root: Path
) -> None:
    (release_root / "README_EN.md").unlink()
    before = _tree_hashes(release_root)

    with pytest.raises(ValueError, match="README_EN.md"):
        release_module.bump_versions(release_root, backend="0.4.0")

    assert _tree_hashes(release_root) == before


def test_bump_is_fail_closed_when_a_pattern_is_ambiguous(
    release_module: ModuleType, release_root: Path
) -> None:
    path = release_root / "src/openbiliclaw/__init__.py"
    path.write_text(path.read_text(encoding="utf-8") + f'__version__ = "{BACKEND_VERSION}"\n')
    before = _tree_hashes(release_root)

    with pytest.raises(ValueError, match="expected 1 version field"):
        release_module.bump_versions(release_root, backend="0.4.0")

    assert _tree_hashes(release_root) == before


def test_invalid_version_is_rejected_before_any_write(
    release_module: ModuleType, release_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _tree_hashes(release_root)

    with pytest.raises(ValueError, match="semantic version"):
        release_module.bump_versions(release_root, backend="nope")
    assert _tree_hashes(release_root) == before

    exit_code = release_module.main(["--bump", "nope", "--root", str(release_root)])

    assert exit_code == 1
    assert "invalid semantic version" in capsys.readouterr().err
    assert _tree_hashes(release_root) == before


def test_cli_check_is_read_only_and_returns_consistency_status(
    release_module: ModuleType, release_root: Path
) -> None:
    before = _tree_hashes(release_root)

    assert release_module.main(["--check", "--root", str(release_root)]) == 0
    assert _tree_hashes(release_root) == before
    assert release_module.main(["--root", str(release_root)]) == 0
    assert _tree_hashes(release_root) == before

    init_path = release_root / "src/openbiliclaw/__init__.py"
    init_path.write_text('__version__ = "0.3.160"\n', encoding="utf-8")
    inconsistent_before = _tree_hashes(release_root)
    assert release_module.main(["--check", "--root", str(release_root)]) == 1
    assert _tree_hashes(release_root) == inconsistent_before


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (FileNotFoundError("uv executable not found"), "uv executable not found"),
        (subprocess.CalledProcessError(2, ["uv", "lock"]), "exit status 2"),
    ],
)
def test_uv_lock_failure_keeps_text_bumps_and_requires_manual_relock(
    release_module: ModuleType,
    release_root: Path,
    failure: Exception,
    reason: str,
) -> None:
    def failing_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise failure

    result = release_module.bump_versions(
        release_root, backend="0.4.0", uv_lock_runner=failing_runner
    )

    assert result.exit_code == 1
    assert f"uv.lock: manual re-lock required ({reason})" in result.report
    assert 'version = "0.4.0"' in (release_root / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{BACKEND_VERSION}"' in (release_root / "uv.lock").read_text(
        encoding="utf-8"
    )
