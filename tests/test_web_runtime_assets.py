from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
RUNTIME_SHARED_MODULES = (
    "model-config-render.js",
    "model-config-state.js",
    "saved-sync-core.js",
)


def _tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return {path.decode() for path in result.stdout.split(b"\0") if path}


def _copy_clean_wheel_tree(destination: Path, tracked: set[str]) -> None:
    prefixes = ("src/openbiliclaw/",)
    required_root_files = {"README.md", "pyproject.toml"}
    for relative in sorted(tracked):
        if relative not in required_root_files and not relative.startswith(prefixes):
            continue
        source = ROOT / relative
        assert source.is_file(), f"tracked package input is missing: {relative}"
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _build_wheel(source: Path, output: Path) -> Path:
    output.mkdir()
    if importlib.util.find_spec("pip") is not None:
        command = [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(output),
            str(source),
        ]
    elif uv := shutil.which("uv"):
        command = [uv, "build", "--wheel", "--out-dir", str(output), str(source)]
    else:
        raise RuntimeError("packaging test requires pip or uv")

    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def test_clean_checkout_tracks_runtime_shared_javascript() -> None:
    tracked = _tracked_files()

    for name in RUNTIME_SHARED_MODULES:
        relative = f"src/openbiliclaw/web/shared/{name}"
        assert relative in tracked, f"clean checkouts omit runtime asset: {relative}"
        assert (ROOT / relative).is_file()


def test_clean_checkout_wheel_contains_runtime_shared_javascript(tmp_path: Path) -> None:
    clean_source = tmp_path / "source"
    _copy_clean_wheel_tree(clean_source, _tracked_files())
    wheel = _build_wheel(clean_source, tmp_path / "wheel")

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())

    for name in RUNTIME_SHARED_MODULES:
        member = f"openbiliclaw/web/shared/{name}"
        assert member in members, f"wheel omits runtime asset: {member}"


def test_supported_runtime_builds_consume_tracked_web_tree_without_node() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    spec = (ROOT / "packaging/openbiliclaw.spec").read_text(encoding="utf-8")
    package = (ROOT / "package.json").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    release_docker = (ROOT / ".github/workflows/release-docker.yml").read_text(encoding="utf-8")
    desktop_workflows = [
        (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8"),
        (ROOT / ".github/workflows/build-installers.yml").read_text(encoding="utf-8"),
    ]

    assert "COPY src ./src" in dockerfile
    assert '(str(project_root / "src" / "openbiliclaw" / "web"), "openbiliclaw/web")' in spec
    assert "Never required by any Python install path" in package
    assert "npm run check:web-build" in ci
    assert "context: ." in release_docker
    assert all("python packaging/build.py" in workflow for workflow in desktop_workflows)
    for runtime_path in [dockerfile, spec, ci, release_docker, *desktop_workflows]:
        assert "npm run build:web" not in runtime_path


def test_web_ci_typechecks_before_runtime_drift_and_javascript_tests() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    _, separator, web_and_following_jobs = ci.partition("\n  web-test:\n")
    assert separator, "web-test CI job is missing"
    web_job, separator, _ = web_and_following_jobs.partition("\n  web-guided-init-e2e:\n")
    assert separator, "web-test CI job boundary is missing"

    install = "run: npm ci"
    typecheck = "run: npm run typecheck:web"
    drift_check = "run: npm run check:web-build"
    javascript_tests = "run: node --test tests/js/"
    commands = (install, typecheck, drift_check, javascript_tests)
    for command in commands:
        assert command in web_job, f"web-test CI job is missing: {command}"
    command_positions = [web_job.index(command) for command in commands]
    assert command_positions == sorted(command_positions)
