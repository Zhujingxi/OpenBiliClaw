"""Direct unit tests for the quality-baseline comparator's fail-closed rules.

These tests exercise ``scripts/check_quality_baseline.py`` end to end via
its ``main()`` entry point: missing inputs, empty mypy streams, crash-only
mypy output, malformed lines, unexpected tool exit codes, fingerprinted
pytest allowlisting, and the clean-success path. The comparator is the
last line of defense in CI — every path that used to "fail open" must now
return a nonzero exit.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# Load the comparator by file path (scripts/ is not an installed package).
_CHECKER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_quality_baseline.py"
_spec = importlib.util.spec_from_file_location("check_quality_baseline", _CHECKER_PATH)
assert _spec is not None and _spec.loader is not None
_checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_checker)

MypyOutputError = _checker.MypyOutputError
compare_pytest = _checker.compare_pytest
parse_mypy_output = _checker.parse_mypy_output
main = _checker.main


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _minimal_junit(path: Path, *, failures: dict[str, str] | None = None) -> Path:
    """Write a minimal JUnit XML with one passing test plus any failures."""
    cases = ['<testcase classname="tests.test_ok" name="test_pass"/>']
    for node, fingerprint in (failures or {}).items():
        cls, _, name = node.partition("::")
        cls_attr = cls.replace("/", ".").removesuffix(".py")
        cases.append(
            f'<testcase classname="{cls_attr}" name="{name}">'
            f'<failure message="{fingerprint}">boom</failure>'
            "</testcase>"
        )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="pytest" tests="{len(cases)}" errors="0">'
        + "".join(cases)
        + "</testsuite></testsuites>"
    )
    return _write(path, xml)


def _baseline(path: Path, *, known_failures: list | None = None) -> Path:
    data = {
        "version": 1,
        "pytest": {"known_failures": known_failures or [], "known_skips": []},
        "mypy": {"known_diagnostics": []},
        "coverage": {},
    }
    return _write(path, json.dumps(data))


MYPY_CLEAN = "Success: no issues found in 227 source files\n"
MYPY_WITH_ERROR = (
    "src/openbiliclaw/foo.py:10:5: error: Incompatible types  [assignment]\n"
    "Found 1 error in 1 file (checked 227 source files)\n"
)


def test_missing_junit_xml_fails(tmp_path: Path) -> None:
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(tmp_path / "baseline.json")
    rc = main(
        [
            "--junit-xml",
            str(tmp_path / "absent.xml"),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
        ]
    )
    assert rc == 2


def test_missing_mypy_output_fails(tmp_path: Path) -> None:
    junit = _minimal_junit(tmp_path / "junit.xml")
    baseline = _baseline(tmp_path / "baseline.json")
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(tmp_path / "absent.txt"),
            "--baseline",
            str(baseline),
        ]
    )
    assert rc == 2


def test_empty_mypy_output_fails_closed(tmp_path: Path) -> None:
    """The P1 repro: an empty mypy stream used to pass; it must now fail."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", "")
    baseline = _baseline(tmp_path / "baseline.json")
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
        ]
    )
    assert rc == 2


def test_crash_only_mypy_output_fails_closed(tmp_path: Path) -> None:
    """Crash-only stderr must not be accepted as a clean run."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", "mypy: INTERNAL ERROR: traceback follows\n")
    baseline = _baseline(tmp_path / "baseline.json")
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
        ]
    )
    assert rc == 2


def test_mypy_output_without_summary_fails_closed(tmp_path: Path) -> None:
    """Diagnostics without a summary line mean truncated/crashed output."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(
        tmp_path / "mypy.txt",
        "src/openbiliclaw/foo.py:10:5: error: Incompatible types  [assignment]\n",
    )
    baseline = _baseline(tmp_path / "baseline.json")
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
        ]
    )
    assert rc == 2


def test_mypy_output_with_malformed_line_fails_closed(tmp_path: Path) -> None:
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(
        tmp_path / "mypy.txt",
        "this is not a mypy diagnostic at all\n" + MYPY_CLEAN,
    )
    baseline = _baseline(tmp_path / "baseline.json")
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
        ]
    )
    assert rc == 2


def test_mypy_crash_exit_code_fails_even_with_valid_grammar(tmp_path: Path) -> None:
    """Exit code 2 (usage/config error) is never comparable, even if the
    output happens to contain a summary-shaped line."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(tmp_path / "baseline.json")
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "2",
        ]
    )
    assert rc == 1


def test_pytest_crash_exit_code_fails(tmp_path: Path) -> None:
    """pytest exit 2 (interrupted) / 4 (usage error) must fail closed."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(tmp_path / "baseline.json")
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--pytest-exit-code",
            "2",
        ]
    )
    assert rc == 1


def test_known_failure_with_matching_fingerprint_passes(tmp_path: Path) -> None:
    node = "tests/test_x.py::test_y"
    fp = "AssertionError: expected 1 got 2"
    junit = _minimal_junit(tmp_path / "junit.xml", failures={node: fp})
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(
        tmp_path / "baseline.json",
        known_failures=[{"node_id": node, "fingerprint": fp}],
    )
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 0


def test_known_failure_with_different_fingerprint_fails(tmp_path: Path) -> None:
    """The review-#87 P2 repro: a new failure cause at an allowlisted node
    used to pass; it must now be rejected."""
    node = "tests/test_x.py::test_y"
    junit = _minimal_junit(
        tmp_path / "junit.xml",
        failures={node: "AssertionError: unrelated new bug"},
    )
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(
        tmp_path / "baseline.json",
        known_failures=[
            {"node_id": node, "fingerprint": "ModuleNotFoundError: No module named 'tomllib'"}
        ],
    )
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 1


def test_new_mypy_diagnostic_fails(tmp_path: Path) -> None:
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_WITH_ERROR)
    baseline = _baseline(tmp_path / "baseline.json")
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
        ]
    )
    assert rc == 1


def test_expected_baseline_diagnostics_pass(tmp_path: Path) -> None:
    """A diagnostic already recorded in the baseline is tolerated."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_WITH_ERROR)
    baseline = _baseline(tmp_path / "baseline.json")
    # Record the exact normalized diagnostic the parser will extract.
    parsed = parse_mypy_output(MYPY_WITH_ERROR)
    data = json.loads(baseline.read_text())
    data["mypy"]["known_diagnostics"] = parsed
    baseline.write_text(json.dumps(data))
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
        ]
    )
    assert rc == 0


def test_clean_success_passes(tmp_path: Path) -> None:
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(tmp_path / "baseline.json")
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 0


def test_parse_mypy_output_grammar_unit() -> None:
    """Direct parser-level checks for the grammar validator."""
    with pytest.raises(MypyOutputError, match="empty"):
        parse_mypy_output("")
    with pytest.raises(MypyOutputError, match="summary"):
        parse_mypy_output("src/a.py:1:1: error: x  [assignment]\n")
    with pytest.raises(MypyOutputError, match="unparseable"):
        parse_mypy_output("garbage\n" + MYPY_CLEAN)
    # Crash-only noise must be rejected: the noise regex matches the crash
    # line but the missing summary still fails closed.
    with pytest.raises(MypyOutputError, match="summary"):
        parse_mypy_output("mypy: INTERNAL ERROR: boom\n")
    assert parse_mypy_output(MYPY_CLEAN) == []
    diags = parse_mypy_output(MYPY_WITH_ERROR)
    assert diags == [
        {
            "path": "src/openbiliclaw/foo.py",
            "error_code": "assignment",
            "message": "Incompatible types",
        }
    ]


def test_compare_pytest_fingerprint_mismatch_unit() -> None:
    baseline = {
        "pytest": {
            "known_failures": [
                {"node_id": "tests/test_x.py::test_y", "fingerprint": "AssertionError: old"}
            ],
            "known_skips": [],
        }
    }
    problems = compare_pytest(
        baseline, {"tests/test_x.py::test_y": "AssertionError: new"}, set(), []
    )
    assert problems and "DIFFERENT failure fingerprint" in problems[0]
    # Matching fingerprint passes.
    assert (
        compare_pytest(baseline, {"tests/test_x.py::test_y": "AssertionError: old"}, set(), [])
        == []
    )
    # Legacy string-form entries still allow any fingerprint at that node.
    legacy = {"pytest": {"known_failures": ["tests/test_x.py::test_y"], "known_skips": []}}
    assert compare_pytest(legacy, {"tests/test_x.py::test_y": "anything"}, set(), []) == []
