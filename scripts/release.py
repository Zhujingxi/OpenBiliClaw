#!/usr/bin/env python3
"""Check and update OpenBiliClaw's mechanical release-version fields."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

VersionFile = tuple[str, str, str, str]

# The single source of truth for mechanically managed version fields.
VERSION_FILES: tuple[VersionFile, ...] = (
    ("pyproject.toml", "toml", "backend", "enforced"),
    ("src/openbiliclaw/__init__.py", "python", "backend", "enforced"),
    ("docs/index.html", "html", "backend", "enforced"),
    ("uv.lock", "uv-lock", "backend", "enforced"),
    ("README.md", "readme-cn", "backend", "enforced"),
    ("README_EN.md", "readme-en", "backend", "enforced"),
    ("packaging/openbiliclaw.iss", "iss", "backend", "warn-only"),
    ("extension/manifest.json", "json", "extension", "enforced"),
    ("extension/package.json", "json", "extension", "enforced"),
    ("extension/package-lock.json", "package-lock", "extension", "enforced"),
)

SEMVER = r"\d+\.\d+\.\d+"
SEMVER_RE = re.compile(rf"^{SEMVER}$")

_SINGLE_PATTERNS: dict[str, re.Pattern[str]] = {
    "toml": re.compile(rf'(?m)(^version = ")(?P<version>{SEMVER})("$)'),
    "python": re.compile(rf'(?m)(^__version__ = ")(?P<version>{SEMVER})("$)'),
    "json": re.compile(
        rf'(?m)(^[ \t]*"version"[ \t]*:[ \t]*")(?P<version>{SEMVER})("[ \t]*,?[ \t]*$)'
    ),
    "html": re.compile(rf'(?m)("softwareVersion"[ \t]*:[ \t]*")(?P<version>{SEMVER})(")'),
    "uv-lock": re.compile(
        rf'(?m)(^\[\[package\]\]\r?\nname = "openbiliclaw"\r?\nversion = ")'
        rf"(?P<version>{SEMVER})(\")"
    ),
    "readme-cn": re.compile(rf"(?m)(^📌 最新版本：\*\*v)(?P<version>{SEMVER})"),
    "readme-en": re.compile(rf"(?m)(^📌 Latest: \*\*v)(?P<version>{SEMVER})"),
}

_PACKAGE_LOCK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf'(?m)(^  "version"[ \t]*:[ \t]*")(?P<version>{SEMVER})("[ \t]*,?[ \t]*$)'),
    re.compile(
        rf'(?ms)(^  "packages"[ \t]*:[ \t]*\{{\r?\n    ""[ \t]*:[ \t]*\{{'
        rf'(?:(?!\r?\n    \}}).)*?^      "version"[ \t]*:[ \t]*")'
        rf"(?P<version>{SEMVER})(\")"
    ),
)

_ISS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"(?m)(^;     iscc /DMyAppVersion=)(?P<version>{SEMVER})"
        rf"( packaging\\openbiliclaw\.iss$)"
    ),
    re.compile(
        rf"(?m)(^;     dist\\release\\OpenBiliClaw-windows-)"
        rf"(?P<version>{SEMVER})(-Setup\.exe$)"
    ),
)

_EXPECTED_COUNTS: dict[str, int] = {
    "toml": 1,
    "python": 1,
    "json": 1,
    "package-lock": 2,
    "iss": 2,
    "html": 1,
    "uv-lock": 1,
    "readme-cn": 1,
    "readme-en": 1,
}


class CheckResult(NamedTuple):
    """A check or bump outcome suitable for both library and CLI callers."""

    exit_code: int
    report: str


class UvLockRunner(Protocol):
    """Callable contract used to make ``uv lock`` injectable in tests."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]: ...


class _EntryState(NamedTuple):
    relative_path: str
    kind: str
    group: str
    policy: str
    versions: tuple[str, ...]


def _patterns_for_kind(kind: str) -> tuple[re.Pattern[str], ...]:
    if kind == "package-lock":
        return _PACKAGE_LOCK_PATTERNS
    if kind == "iss":
        return _ISS_PATTERNS
    try:
        return (_SINGLE_PATTERNS[kind],)
    except KeyError as exc:
        raise ValueError(f"unknown version-file kind: {kind}") from exc


def parse_versions(text: str, kind: str) -> tuple[str, ...]:
    """Extract only the project version fields associated with ``kind``."""
    return tuple(
        match.group("version")
        for pattern in _patterns_for_kind(kind)
        for match in pattern.finditer(text)
    )


def _read_entry(root: Path, version_file: VersionFile) -> _EntryState:
    relative_path, kind, group, policy = version_file
    path = root / relative_path
    if not path.is_file():
        raise ValueError(f"{relative_path}: file is missing")
    text = path.read_text(encoding="utf-8")
    versions = parse_versions(text, kind)
    expected = _EXPECTED_COUNTS[kind]
    if len(versions) != expected:
        raise ValueError(
            f"{relative_path}: expected {expected} version field(s), found {len(versions)}"
        )
    return _EntryState(relative_path, kind, group, policy, versions)


def _expected_group_version(states: list[_EntryState], group: str) -> str | None:
    versions = [
        version
        for state in states
        if state.group == group and state.policy == "enforced"
        for version in state.versions
    ]
    if not versions:
        return None
    return Counter(versions).most_common(1)[0][0]


def check_versions(root: Path) -> CheckResult:
    """Report version consistency without modifying the repository at ``root``."""
    states: list[_EntryState] = []
    errors: dict[str, tuple[str, str]] = {}
    for version_file in VERSION_FILES:
        relative_path, _kind, _group, policy = version_file
        try:
            states.append(_read_entry(root, version_file))
        except (OSError, UnicodeError, ValueError) as exc:
            errors[relative_path] = (policy, str(exc))

    lines: list[str] = []
    failed = False
    expected_by_group: dict[str, str] = {}
    for group in ("backend", "extension"):
        expected = _expected_group_version(states, group)
        if expected is None:
            lines.append(f"{group}: unable to determine version")
            failed = True
        else:
            expected_by_group[group] = expected
            lines.append(f"{group}: {expected}")

    for version_file in VERSION_FILES:
        relative_path, _kind, group, policy = version_file
        if relative_path in errors:
            _error_policy, message = errors[relative_path]
            if policy == "warn-only":
                lines.append(f"{relative_path}: warning ({message})")
            else:
                lines.append(f"{relative_path}: inconsistent ({message})")
                failed = True
            continue

        state = next(item for item in states if item.relative_path == relative_path)
        expected = expected_by_group.get(group)
        if expected is None or all(version == expected for version in state.versions):
            continue
        actual = ", ".join(state.versions)
        if policy == "warn-only":
            lines.append(f"{relative_path}: warning ({actual}; expected {expected})")
        else:
            lines.append(f"{relative_path}: inconsistent ({actual}; expected {expected})")
            failed = True

    backend_version = expected_by_group.get("backend")
    if backend_version is not None:
        changelog_path = root / "docs/changelog.md"
        try:
            changelog = changelog_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            lines.append("warning: docs/changelog.md is missing or unreadable")
        else:
            heading = re.compile(rf"(?m)^## v{re.escape(backend_version)}(?:[\s/:：]|$)")
            if heading.search(changelog) is None:
                lines.append(f"warning: docs/changelog.md has no ## v{backend_version} heading")

    return CheckResult(int(failed), "\n".join(lines))


def _rewrite_versions(text: str, kind: str, version: str) -> str:
    def replace_version(match: re.Match[str]) -> str:
        start, end = match.span("version")
        relative_start = start - match.start()
        relative_end = end - match.start()
        return f"{match.group(0)[:relative_start]}{version}{match.group(0)[relative_end:]}"

    updated = text
    replacement_count = 0
    for pattern in _patterns_for_kind(kind):
        updated, count = pattern.subn(replace_version, updated)
        replacement_count += count
    expected = _EXPECTED_COUNTS[kind]
    if replacement_count != expected:
        raise ValueError(
            f"expected {expected} version field(s), found {replacement_count} during rewrite"
        )
    return updated


def _default_uv_lock_runner(
    args: Sequence[str],
    *,
    cwd: Path,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _validate_requested_version(label: str, version: str | None) -> None:
    if version is not None and SEMVER_RE.fullmatch(version) is None:
        raise ValueError(f"invalid semantic version for {label}: {version!r}; expected X.Y.Z")


def bump_versions(
    root: Path,
    *,
    backend: str | None = None,
    extension: str | None = None,
    uv_lock_runner: UvLockRunner = _default_uv_lock_runner,
) -> CheckResult:
    """Pre-validate and update selected groups, then return a fresh check result."""
    _validate_requested_version("backend", backend)
    _validate_requested_version("extension", extension)
    requested = {"backend": backend, "extension": extension}
    selected = [entry for entry in VERSION_FILES if requested[entry[2]] is not None]
    if not selected:
        raise ValueError("at least one of backend or extension must be provided")

    # Build every write in memory before changing the first file. uv.lock is validated here
    # but regenerated by uv after the other backend fields have been updated.
    states: dict[str, _EntryState] = {}
    for entry in selected:
        state = _read_entry(root, entry)
        states[state.relative_path] = state
    planned_writes: dict[Path, str] = {}
    for relative_path, kind, group, _policy in selected:
        if kind == "uv-lock":
            continue
        path = root / relative_path
        text = path.read_text(encoding="utf-8")
        version = requested[group]
        if version is None:
            raise AssertionError("selected version group has no requested version")
        try:
            planned_writes[path] = _rewrite_versions(text, kind, version)
        except ValueError as exc:
            raise ValueError(f"{relative_path}: {exc}") from exc

    if len(states) != len(selected):
        raise AssertionError("version target pre-validation was incomplete")
    for path, text in planned_writes.items():
        path.write_text(text, encoding="utf-8")

    uv_failure: str | None = None
    if backend is not None:
        try:
            uv_lock_runner(("uv", "lock"), cwd=root, check=True)
        except FileNotFoundError as exc:
            uv_failure = str(exc)
        except subprocess.CalledProcessError as exc:
            uv_failure = f"exit status {exc.returncode}"

    result = check_versions(root)
    if uv_failure is None:
        return result
    report = f"uv.lock: manual re-lock required ({uv_failure})\n{result.report}"
    return CheckResult(1, report)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check versions (default)")
    parser.add_argument("--bump", metavar="X.Y.Z", help="update backend version fields")
    parser.add_argument("--extension", metavar="X.Y.Z", help="update extension version fields")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root (defaults to this script's repository)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the release checker/updater CLI and return its process exit code."""
    args = _build_parser().parse_args(argv)
    if args.check and (args.bump is not None or args.extension is not None):
        print("error: --check cannot be combined with mutation flags", file=sys.stderr)
        return 1
    try:
        if args.bump is not None or args.extension is not None:
            result = bump_versions(args.root, backend=args.bump, extension=args.extension)
        else:
            result = check_versions(args.root)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.report)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
