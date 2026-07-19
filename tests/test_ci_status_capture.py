"""Regression test for the CI status-capture contract under GitHub's shell.

Review t_cce76b68 (2026-07-19) found that the quality-baseline comparator
job was guaranteed red: GitHub Actions runs each ``run:`` block with
``bash --noprofile --norc -e -o pipefail`` and ``continue-on-error`` only
changes the step outcome — it does NOT disable shell errexit. So an
expected nonzero pytest/mypy exit aborted the script before the
``build/*.status`` line executed, and the comparator step then failed
because the status file was missing.

These tests execute the *actual* ``run:`` snippets from
``.github/workflows/ci.yml`` under the same shell flags against a
known-failing tool stand-in and assert that BOTH the artifact and the
status file are written with the correct nonzero status. If someone
removes the ``set +e`` guard (or reorders the capture), this test fails.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# GitHub's default bash invocation for `run:` steps on Linux runners.
GITHUB_BASH = ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail"]


def _extract_run_block(step_name_fragment: str) -> str:
    """Pull the indented ``run: |`` body of the named step out of ci.yml."""
    text = CI_YML.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if step_name_fragment in line:
            start = i
            break
    assert start is not None, f"step containing {step_name_fragment!r} not found in ci.yml"
    run_idx = None
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("- name:") and i > start:
            break
        if stripped == "run: |":
            run_idx = i
            break
    assert run_idx is not None, f"run: block for {step_name_fragment!r} not found"
    # Collect the indented literal block body (deeper indent than `run:`).
    run_indent = len(lines[run_idx]) - len(lines[run_idx].lstrip())
    body: list[str] = []
    for i in range(run_idx + 1, len(lines)):
        line = lines[i]
        if line.strip() == "":
            body.append("")
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= run_indent:
            break
        body.append(line[run_indent + 2 :])
    script = "\n".join(body).strip("\n")
    assert script, f"empty run body for {step_name_fragment!r}"
    return script


def _write_fake_tool(directory: Path, name: str, exit_code: int) -> Path:
    """A stand-in for an expected-to-fail tool (mypy/pytest)."""
    tool = directory / name
    tool.write_text(f"#!/bin/sh\necho '{name} ran'\nexit {exit_code}\n", encoding="utf-8")
    tool.chmod(0o755)
    return tool


def _substitute_tool(script: str, pattern: str, replacement: str) -> str:
    out, n = re.subn(pattern, replacement, script, count=1)
    assert n == 1, f"expected exactly one tool invocation matching {pattern!r}"
    return out


def _run_github_shell(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*GITHUB_BASH, "-c", script],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_mypy_step_writes_status_under_bash_errexit(tmp_path: Path) -> None:
    """The ci.yml mypy step must write build/mypy.status even when mypy
    exits 1 under `bash -e -o pipefail` (the reviewer's P1 repro shape:
    `false | tee ...` must not abort the status write)."""
    _write_fake_tool(tmp_path, "fake-mypy", exit_code=1)
    script = _extract_run_block("Run MyPy (capture output for baseline comparator)")
    script = _substitute_tool(script, r"python -m mypy src/", "./fake-mypy")
    result = _run_github_shell(script, tmp_path)

    status_file = tmp_path / "build" / "mypy.status"
    artifact = tmp_path / "build" / "mypy.txt"
    assert status_file.exists(), (
        "build/mypy.status missing after expected-nonzero mypy — errexit guard regressed.\n"
        f"script:\n{script}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert status_file.read_text(encoding="utf-8").strip() == "mypy_exit=1"
    # The tee'd artifact must exist and contain the tool output.
    assert artifact.exists()
    assert "fake-mypy ran" in artifact.read_text(encoding="utf-8")
    # After `set -e` is re-enabled, a later failing command must still abort:
    # prove the guard is scoped, not a blanket `set +e` for the whole step.
    assert re.search(r"set -e\s*\n\s*echo \"mypy_exit=", script), (
        "errexit must be re-enabled before the status write line"
    )


def test_pytest_step_writes_status_under_bash_errexit(tmp_path: Path) -> None:
    """The ci.yml pytest step must write build/pytest.status with the real
    nonzero pytest exit under `bash -e -o pipefail` (reviewer repro: the
    known failing aggregate-release node exits 1)."""
    _write_fake_tool(tmp_path, "fake-pytest", exit_code=1)
    script = _extract_run_block("Run pytest (single run, emits JUnit XML + coverage)")
    script = _substitute_tool(
        script,
        r"python -m pytest -q\s*\\\n\s*--junitxml=\S+\s*\\\n\s*--cov=\S+ --cov-report=\S+",
        "./fake-pytest",
    )
    result = _run_github_shell(script, tmp_path)

    status_file = tmp_path / "build" / "pytest.status"
    assert status_file.exists(), (
        "build/pytest.status missing after expected-nonzero pytest — errexit guard regressed.\n"
        f"script:\n{script}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert status_file.read_text(encoding="utf-8").strip() == "pytest_exit=1"
    assert re.search(r"set -e\s*\n\s*echo \"pytest_exit=", script), (
        "errexit must be re-enabled before the status write line"
    )


def test_status_capture_preserves_zero_exit(tmp_path: Path) -> None:
    """A clean (exit 0) tool must record status 0 — the guard must not
    swallow the distinction between pass and fail."""
    _write_fake_tool(tmp_path, "fake-pytest", exit_code=0)
    script = _extract_run_block("Run pytest (single run, emits JUnit XML + coverage)")
    script = _substitute_tool(
        script,
        r"python -m pytest -q\s*\\\n\s*--junitxml=\S+\s*\\\n\s*--cov=\S+ --cov-report=\S+",
        "./fake-pytest",
    )
    result = _run_github_shell(script, tmp_path)
    assert result.returncode == 0, (
        f"clean tool run must not fail the step: rc={result.returncode} "
        f"stdout={result.stdout} stderr={result.stderr}"
    )
    status_file = tmp_path / "build" / "pytest.status"
    assert status_file.read_text(encoding="utf-8").strip() == "pytest_exit=0"


def test_pipeline_status_uses_pipestatus_not_tee(tmp_path: Path) -> None:
    """The mypy step captures PIPESTATUS[0] (the tool), not the pipeline's
    last command (`tee`). A tee-success/tool-fail pipeline must record the
    tool's nonzero status."""
    _write_fake_tool(tmp_path, "fake-mypy", exit_code=1)
    script = _extract_run_block("Run MyPy (capture output for baseline comparator)")
    script = _substitute_tool(script, r"python -m mypy src/", "./fake-mypy")
    _run_github_shell(script, tmp_path)
    status = (tmp_path / "build" / "mypy.status").read_text(encoding="utf-8").strip()
    assert status == "mypy_exit=1", (
        f"expected the tool's exit (1), got {status!r} — the step must read "
        "PIPESTATUS[0], not $? of the tee pipeline"
    )


@pytest.mark.parametrize("exit_code", [2, 4])
def test_crash_exit_codes_are_captured_not_swallowed(tmp_path: Path, exit_code: int) -> None:
    """Crash/usage-error exits (2, 4) must also land in the status file so
    the comparator can fail closed on them — never abort the step."""
    _write_fake_tool(tmp_path, "fake-pytest", exit_code=exit_code)
    script = _extract_run_block("Run pytest (single run, emits JUnit XML + coverage)")
    script = _substitute_tool(
        script,
        r"python -m pytest -q\s*\\\n\s*--junitxml=\S+\s*\\\n\s*--cov=\S+ --cov-report=\S+",
        "./fake-pytest",
    )
    _run_github_shell(script, tmp_path)
    status_file = tmp_path / "build" / "pytest.status"
    assert status_file.exists()
    assert status_file.read_text(encoding="utf-8").strip() == f"pytest_exit={exit_code}"
