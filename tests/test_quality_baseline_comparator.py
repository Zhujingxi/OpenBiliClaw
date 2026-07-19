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

JUnitStructureError = _checker.JUnitStructureError
MypyOutputError = _checker.MypyOutputError
compare_pytest = _checker.compare_pytest
main = _checker.main
parse_coverage_line_percent = _checker.parse_coverage_line_percent
parse_junit_failures_and_skips = _checker.parse_junit_failures_and_skips
parse_mypy_output = _checker.parse_mypy_output
parse_mypy_summary = _checker.parse_mypy_summary
parse_mypy_summary_kind = _checker.parse_mypy_summary_kind


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _minimal_junit(
    path: Path,
    *,
    failures: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    skips: list[str] | None = None,
) -> Path:
    """Write a minimal JUnit XML with one passing test plus any outcomes."""
    import html

    cases = ['<testcase classname="tests.test_ok" name="test_pass"/>']
    failure_count = 0
    error_count = 0
    skip_count = 0
    for node, fingerprint in (failures or {}).items():
        cls, _, name = node.partition("::")
        cls_attr = cls.replace("/", ".").removesuffix(".py")
        cases.append(
            f'<testcase classname="{cls_attr}" name="{name}">'
            f'<failure message="{html.escape(fingerprint, quote=True)}">boom</failure>'
            "</testcase>"
        )
        failure_count += 1
    for node, fingerprint in (errors or {}).items():
        cls, _, name = node.partition("::")
        cls_attr = cls.replace("/", ".").removesuffix(".py")
        cases.append(
            f'<testcase classname="{cls_attr}" name="{name}">'
            f'<error message="{html.escape(fingerprint, quote=True)}">boom</error>'
            "</testcase>"
        )
        error_count += 1
    for node in skips or []:
        cls, _, name = node.partition("::")
        cls_attr = cls.replace("/", ".").removesuffix(".py")
        cases.append(
            f'<testcase classname="{cls_attr}" name="{name}">'
            '<skipped message="skip">skip</skipped>'
            "</testcase>"
        )
        skip_count += 1
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="pytest" tests="{len(cases)}" '
        f'failures="{failure_count}" errors="{error_count}" '
        f'skipped="{skip_count}">' + "".join(cases) + "</testsuite></testsuites>"
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
MYPY_CLEAN_SINGULAR = "Success: no issues found in 1 source file\n"
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
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
            "--pytest-exit-code",
            "0",
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
            "--mypy-exit-code",
            "0",
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
    # The fingerprint stored in the baseline must be the NORMALIZED form
    # produced by the comparator (numbers stripped to <N>, then digested).
    baseline = _baseline(
        tmp_path / "baseline.json",
        known_failures=[
            {"node_id": node, "fingerprint": _checker._normalize_failure_fingerprint(fp)}
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
            "--mypy-exit-code",
            "0",
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
            "--mypy-exit-code",
            "0",
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
            "--mypy-exit-code",
            "1",
            "--pytest-exit-code",
            "0",
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
            "--mypy-exit-code",
            "1",
            "--pytest-exit-code",
            "0",
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
    # Crash-only noise must be rejected: INTERNAL ERROR is not benign noise
    # and fails closed as an unparseable line (review-t_e03bfeff P2).
    with pytest.raises(MypyOutputError, match="unparseable"):
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
        baseline, {"tests/test_x.py::test_y": "AssertionError: new"}, {}, set(), []
    )
    assert problems and "DIFFERENT failure fingerprint" in problems[0]
    # Matching fingerprint passes.
    assert (
        compare_pytest(baseline, {"tests/test_x.py::test_y": "AssertionError: old"}, {}, set(), [])
        == []
    )
    # Legacy string-form entries still allow any fingerprint at that node.
    legacy = {"pytest": {"known_failures": ["tests/test_x.py::test_y"], "known_skips": []}}
    assert compare_pytest(legacy, {"tests/test_x.py::test_y": "anything"}, {}, set(), []) == []


# ---------------------------------------------------------------------------
# review-t_cce76b68 F1: exit/artifact reconciliation matrix
# ---------------------------------------------------------------------------


def test_pytest_exit0_with_failing_junit_fails_closed(tmp_path: Path) -> None:
    """pytest exit 0 (all passed) + JUnit containing a failure is a
    contradictory pair: must fail closed even if the failure is allowlisted."""
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 1


def test_pytest_exit1_with_clean_junit_fails_closed(tmp_path: Path) -> None:
    """pytest exit 1 (tests failed) + clean JUnit is contradictory."""
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
            "1",
        ]
    )
    assert rc == 1


def test_mypy_exit0_with_errors_summary_fails_closed(tmp_path: Path) -> None:
    """mypy exit 0 (clean) + Found-errors summary is contradictory."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_WITH_ERROR)
    baseline = _baseline(tmp_path / "baseline.json")
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 1


def test_mypy_exit1_with_success_summary_fails_closed(tmp_path: Path) -> None:
    """mypy exit 1 (diagnostics) + Success summary is contradictory."""
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
            "1",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 1


# ---------------------------------------------------------------------------
# review-t_cce76b68 F2: structurally empty / contradictory JUnit
# ---------------------------------------------------------------------------


def test_empty_junit_fails_closed(tmp_path: Path) -> None:
    """<testsuites/> (no testsuite, no testcase) must fail closed."""
    junit = _write(tmp_path / "junit.xml", '<?xml version="1.0"?><testsuites/>')
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
    assert rc == 2


def test_junit_with_no_testcase_fails_closed(tmp_path: Path) -> None:
    """A testsuite with zero testcase elements is also structurally empty."""
    junit = _write(
        tmp_path / "junit.xml",
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="0" '
        'failures="0" errors="0"></testsuite></testsuites>',
    )
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
    assert rc == 2


def test_junit_counter_mismatch_fails_closed(tmp_path: Path) -> None:
    """Declared test count must match parsed testcase rows."""
    junit = _write(
        tmp_path / "junit.xml",
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="5" '
        'failures="0" errors="0">'
        '<testcase classname="tests.test_ok" name="test_pass"/>'
        "</testsuite></testsuites>",
    )
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
    assert rc == 2


def test_junit_duplicate_node_id_fails_closed(tmp_path: Path) -> None:
    junit = _write(
        tmp_path / "junit.xml",
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="2" '
        'failures="0" errors="0">'
        '<testcase classname="tests.test_ok" name="test_pass"/>'
        '<testcase classname="tests.test_ok" name="test_pass"/>'
        "</testsuite></testsuites>",
    )
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
    assert rc == 2


def test_junit_declared_failure_but_unparsed_fails_closed(tmp_path: Path) -> None:
    """Declared failures=N without matching <failure> elements is corrupt."""
    junit = _write(
        tmp_path / "junit.xml",
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="1" '
        'failures="1" errors="0">'
        '<testcase classname="tests.test_ok" name="test_pass"/>'
        "</testsuite></testsuites>",
    )
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
    assert rc == 2


# ---------------------------------------------------------------------------
# review-t_cce76b68 F3: non-finite / out-of-range coverage
# ---------------------------------------------------------------------------


def test_nan_coverage_fails_closed(tmp_path: Path) -> None:
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(tmp_path / "baseline.json")
    coverage = _write(
        tmp_path / "coverage.xml",
        '<?xml version="1.0"?><coverage line-rate="nan"></coverage>',
    )
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--coverage-xml",
            str(coverage),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 2


def test_inf_coverage_fails_closed(tmp_path: Path) -> None:
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(tmp_path / "baseline.json")
    coverage = _write(
        tmp_path / "coverage.xml",
        '<?xml version="1.0"?><coverage line-rate="inf"></coverage>',
    )
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--coverage-xml",
            str(coverage),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 2


def test_out_of_range_coverage_fails_closed(tmp_path: Path) -> None:
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(tmp_path / "baseline.json")
    coverage = _write(
        tmp_path / "coverage.xml",
        '<?xml version="1.0"?><coverage line-rate="1.5"></coverage>',
    )
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--coverage-xml",
            str(coverage),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 2


def test_parse_coverage_line_percent_unit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        parse_coverage_line_percent(
            _write(tmp_path / "c1.xml", '<?xml version="1.0"?><coverage line-rate="nan"/>')
        )
    with pytest.raises(ValueError, match="non-finite"):
        parse_coverage_line_percent(
            _write(tmp_path / "c2.xml", '<?xml version="1.0"?><coverage line-rate="inf"/>')
        )
    with pytest.raises(ValueError, match="0..100"):
        parse_coverage_line_percent(
            _write(tmp_path / "c3.xml", '<?xml version="1.0"?><coverage line-rate="1.5"/>')
        )
    with pytest.raises(ValueError, match="non-numeric"):
        parse_coverage_line_percent(
            _write(tmp_path / "c4.xml", '<?xml version="1.0"?><coverage line-rate="abc"/>')
        )
    ok = parse_coverage_line_percent(
        _write(tmp_path / "c5.xml", '<?xml version="1.0"?><coverage line-rate="0.85"/>')
    )
    assert ok == pytest.approx(85.0)


# ---------------------------------------------------------------------------
# review-t_cce76b68 F4: singular mypy summary grammar
# ---------------------------------------------------------------------------


def test_mypy_singular_success_summary_passes(tmp_path: Path) -> None:
    """'Success: no issues found in 1 source file' (singular) must be
    accepted as a valid success summary — one-file runs are legitimate."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN_SINGULAR)
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


def test_parse_mypy_summary_kind_unit() -> None:
    assert parse_mypy_summary_kind(MYPY_CLEAN) == "success"
    assert parse_mypy_summary_kind(MYPY_CLEAN_SINGULAR) == "success"
    assert parse_mypy_summary_kind(MYPY_WITH_ERROR) == "errors"
    assert parse_mypy_summary_kind("") is None
    assert parse_mypy_summary_kind("garbage\n") is None


# ---------------------------------------------------------------------------
# review-t_cce76b68 F2 (unit-level): parse_junit_failures_and_skips raises
# ---------------------------------------------------------------------------


def test_parse_junit_failures_and_skips_rejects_empty(tmp_path: Path) -> None:
    empty = _write(tmp_path / "empty.xml", '<?xml version="1.0"?><testsuites/>')
    with pytest.raises(JUnitStructureError, match="testsuite"):
        parse_junit_failures_and_skips(empty)


def test_parse_junit_failures_and_skips_rejects_counter_mismatch(tmp_path: Path) -> None:
    bad = _write(
        tmp_path / "bad.xml",
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="5" '
        'failures="0" errors="0">'
        '<testcase classname="tests.test_ok" name="test_pass"/>'
        "</testsuite></testsuites>",
    )
    with pytest.raises(JUnitStructureError, match="tests"):
        parse_junit_failures_and_skips(bad)


# ---------------------------------------------------------------------------
# review-t_e03bfeff P1-1: fingerprint must capture the nested cause line
# ---------------------------------------------------------------------------


def test_fingerprint_captures_nested_cause_line(tmp_path: Path) -> None:
    """The P1-1 repro: mutating only the nested cause on line 3 must change
    the fingerprint and therefore fail comparison."""
    node = (
        "tests/test_aggregate_release_workflow.py"
        "::test_aggregate_release_helper_does_not_backfill_previous_channel_assets"
    )
    original_fp = (
        "AssertionError: Traceback (most recent call last):\n"
        '  File "<stdin>", line 1, in <module>\n'
        "ModuleNotFoundError: No module named 'tomllib'"
    )
    mutated_fp = original_fp.replace("ModuleNotFoundError", "SecurityError")

    junit_orig = _minimal_junit(tmp_path / "orig.xml", failures={node: original_fp})
    junit_mut = _minimal_junit(tmp_path / "mut.xml", failures={node: mutated_fp})
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(
        tmp_path / "baseline.json",
        known_failures=[
            {"node_id": node, "fingerprint": _checker._normalize_failure_fingerprint(original_fp)}
        ],
    )

    # The original failure matches the baseline fingerprint and passes.
    rc = main(
        [
            "--junit-xml",
            str(junit_orig),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 0

    # The mutated nested cause produces a different fingerprint and fails.
    rc = main(
        [
            "--junit-xml",
            str(junit_mut),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 1


# ---------------------------------------------------------------------------
# review-t_e03bfeff P1-2: mypy summary count must reconcile with diagnostics
# ---------------------------------------------------------------------------


def test_mypy_found_errors_count_mismatch_fails_closed(tmp_path: Path) -> None:
    """The P1-2 repro: 'Found 1 error' with zero parsed diagnostic rows and
    mypy exit 1 must fail closed."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(
        tmp_path / "mypy.txt",
        "Found 1 error in 1 file (checked 227 source files)\n",
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
            "--mypy-exit-code",
            "1",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 1


def test_mypy_found_errors_count_match_passes(tmp_path: Path) -> None:
    """A matching summary count and diagnostic row count passes."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_WITH_ERROR)
    baseline = _baseline(tmp_path / "baseline.json")
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
            "--mypy-exit-code",
            "1",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 0


def test_parse_mypy_summary_unit() -> None:
    assert parse_mypy_summary(MYPY_CLEAN) == ("success", 0)
    assert parse_mypy_summary(MYPY_CLEAN_SINGULAR) == ("success", 0)
    assert parse_mypy_summary(MYPY_WITH_ERROR) == ("errors", 1)
    assert parse_mypy_summary("") == (None, None)
    assert parse_mypy_summary("garbage\n") == (None, None)


# ---------------------------------------------------------------------------
# review-t_e03bfeff (repair t_c4bd6233): round-3 fail-closed regressions
# ---------------------------------------------------------------------------


def test_mypy_found_zero_errors_with_exit1_fails_closed(tmp_path: Path) -> None:
    """P1-3 repro: 'Found 0 errors in 0 files (checked 227 source files)' +
    mypy exit 1 must fail closed. Exit 1 means diagnostics were found; a
    zero-error summary is contradictory."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(
        tmp_path / "mypy.txt",
        "Found 0 errors in 0 files (checked 227 source files)\n",
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
            "--mypy-exit-code",
            "1",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 1


def test_mypy_success_summary_with_trailer_fails_closed(tmp_path: Path) -> None:
    """P2-5 repro: 'Success: no issues found in 227 source files UNTRUSTED
    TRAILER' must be rejected as unparseable, not accepted as a success
    summary."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(
        tmp_path / "mypy.txt",
        "Success: no issues found in 227 source files UNTRUSTED TRAILER\n",
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 2


def test_mypy_found_errors_with_trailer_fails_closed(tmp_path: Path) -> None:
    """P2-5 repro: a 'Found N errors' line with an untrusted trailer is
    rejected as unparseable."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(
        tmp_path / "mypy.txt",
        "Found 1 error in 1 file (checked 227 source files) EXTRA\n",
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
            "--mypy-exit-code",
            "1",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 2


def test_mypy_internal_error_line_fails_closed(tmp_path: Path) -> None:
    """P2-5 repro: 'mypy: INTERNAL ERROR' is not benign noise and must fail
    closed as an unparseable line."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(
        tmp_path / "mypy.txt",
        "mypy: INTERNAL ERROR: boom\nSuccess: no issues found in 227 source files\n",
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 2


def test_fingerprint_long_headline_preserves_nested_cause(tmp_path: Path) -> None:
    """P1-4 repro: a 186-char headline must not crowd the nested cause out
    of the fingerprint. Mutating only the nested cause must still change
    the fingerprint and fail comparison."""
    node = "tests/test_x.py::test_y"
    long_headline = "AssertionError: " + "x" * 170
    original_fp = (
        long_headline + "\n"
        '  File "<stdin>", line 1, in <module>\n'
        "ModuleNotFoundError: No module named 'tomllib'"
    )
    mutated_fp = original_fp.replace("ModuleNotFoundError", "SecurityError")

    junit_orig = _minimal_junit(tmp_path / "orig.xml", failures={node: original_fp})
    junit_mut = _minimal_junit(tmp_path / "mut.xml", failures={node: mutated_fp})
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(
        tmp_path / "baseline.json",
        known_failures=[
            {"node_id": node, "fingerprint": _checker._normalize_failure_fingerprint(original_fp)}
        ],
    )

    # The original failure matches the baseline fingerprint and passes.
    rc = main(
        [
            "--junit-xml",
            str(junit_orig),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 0

    # The mutated nested cause produces a different fingerprint and fails.
    rc = main(
        [
            "--junit-xml",
            str(junit_mut),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 1


# ---------------------------------------------------------------------------
# review-t_e03bfeff P2-3: testsuite@skipped must match parsed <skipped>
# ---------------------------------------------------------------------------


def test_junit_skipped_counter_mismatch_fails_closed(tmp_path: Path) -> None:
    """Declared skipped=N without matching <skipped> elements is corrupt."""
    junit = _write(
        tmp_path / "junit.xml",
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="1" '
        'failures="0" errors="0" skipped="1">'
        '<testcase classname="tests.test_ok" name="test_pass"/>'
        "</testsuite></testsuites>",
    )
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
    assert rc == 2


def test_junit_skipped_counter_match_passes(tmp_path: Path) -> None:
    """A matching skipped counter is accepted."""
    node = "tests/test_x.py::test_y"
    junit = _minimal_junit(tmp_path / "junit.xml", skips=[node])
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(tmp_path / "baseline.json", known_failures=[])
    # Add the skip to known_skips so it is tolerated.
    data = json.loads(baseline.read_text())
    data["pytest"]["known_skips"] = [node]
    baseline.write_text(json.dumps(data))
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


def test_parse_junit_skipped_counter_mismatch_unit(tmp_path: Path) -> None:
    bad = _write(
        tmp_path / "bad.xml",
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="1" '
        'failures="0" errors="0" skipped="1">'
        '<testcase classname="tests.test_ok" name="test_pass"/>'
        "</testsuite></testsuites>",
    )
    with pytest.raises(JUnitStructureError, match="skipped"):
        parse_junit_failures_and_skips(bad)


# ---------------------------------------------------------------------------
# review-t_e03bfeff P2-4: <error> outcomes are never allowlisted
# ---------------------------------------------------------------------------


def test_error_outcome_never_allowlisted(tmp_path: Path) -> None:
    """The P2-4 repro: an <error> at a node listed in known_failures must
    still be rejected independently."""
    node = "tests/test_x.py::test_y"
    fp = "ModuleNotFoundError: No module named 'tomllib'"
    junit = _minimal_junit(tmp_path / "junit.xml", errors={node: fp})
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
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 1


def test_compare_pytest_error_rejected_unit() -> None:
    """Direct unit test: <error> outcomes are rejected even when the node is
    in known_failures."""
    baseline = {
        "pytest": {
            "known_failures": [{"node_id": "tests/test_x.py::test_y", "fingerprint": "anything"}],
            "known_skips": [],
        }
    }
    problems = compare_pytest(baseline, {}, {"tests/test_x.py::test_y": "anything"}, set(), [])
    assert problems and "never allowlisted" in problems[0]


# ---------------------------------------------------------------------------
# review-t_e03bfeff (repair t_6e6575ed): checked-in baseline end-to-end probe
# ---------------------------------------------------------------------------

_CHECKED_IN_BASELINE = Path(__file__).resolve().parent / "contracts" / "quality-baseline.json"

# The real aggregate-release nested failure message as pytest reports it in a
# JUnit ``<failure message="...">`` attribute: headline line, traceback frame
# line, nested cause line.
AGGREGATE_RELEASE_NODE = (
    "tests/test_aggregate_release_workflow.py"
    "::test_aggregate_release_helper_does_not_backfill_previous_channel_assets"
)
AGGREGATE_RELEASE_MESSAGE = (
    "AssertionError: Traceback (most recent call last):\n"
    '  File "<stdin>", line 1, in <module>\n'
    "ModuleNotFoundError: No module named 'tomllib'"
)


def test_checked_in_baseline_rejects_obsolete_aggregate_release_failure(tmp_path: Path) -> None:
    """The aggregate-release ModuleNotFoundError was fixed in f6123bcc and
    removed from the checked-in baseline. Recurrence must now be rejected
    as a new failure, not tolerated (repair t_c4bd6233)."""
    junit = _minimal_junit(
        tmp_path / "junit.xml",
        failures={AGGREGATE_RELEASE_NODE: AGGREGATE_RELEASE_MESSAGE},
    )
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(_CHECKED_IN_BASELINE),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 1


def test_checked_in_baseline_rejects_mutated_nested_cause(tmp_path: Path) -> None:
    """ModuleNotFoundError -> SecurityError at the same (now removed) node
    must also be rejected by the checked-in baseline."""
    mutated = AGGREGATE_RELEASE_MESSAGE.replace("ModuleNotFoundError", "SecurityError")
    junit = _minimal_junit(
        tmp_path / "junit.xml",
        failures={AGGREGATE_RELEASE_NODE: mutated},
    )
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    rc = main(
        [
            "--junit-xml",
            str(junit),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(_CHECKED_IN_BASELINE),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 1


# ---------------------------------------------------------------------------
# review-t_e03bfeff (repair t_6e6575ed): summary reconciles with occurrences
# ---------------------------------------------------------------------------

MYPY_TWO_SAME_MESSAGE = (
    "src/openbiliclaw/foo.py:10:5: error: Incompatible types  [assignment]\n"
    "src/openbiliclaw/foo.py:42:9: error: Incompatible types  [assignment]\n"
    "Found 2 errors in 1 file (checked 227 source files)\n"
)


def test_mypy_same_message_two_lines_reconciles_and_passes(tmp_path: Path) -> None:
    """Two same-message diagnostics at different lines: summary declares 2,
    one normalized identity, comparator must return 0 when the identity is
    known. The summary count reconciles against parsed occurrences, not
    deduplicated identities."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(tmp_path / "mypy.txt", MYPY_TWO_SAME_MESSAGE)
    baseline = _baseline(tmp_path / "baseline.json")
    parsed = parse_mypy_output(MYPY_TWO_SAME_MESSAGE)
    assert len(parsed) == 1  # identity dedup still applies for baseline keys
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
            "--mypy-exit-code",
            "1",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 0


def test_mypy_truncated_summary_with_zero_rows_still_fails(tmp_path: Path) -> None:
    """Truncation guard: 'Found 1 error' with zero parsed rows remains a
    contradiction under occurrence-based reconciliation."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(
        tmp_path / "mypy.txt",
        "Found 1 error in 1 file (checked 227 source files)\n",
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
            "--mypy-exit-code",
            "1",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 1


def test_count_mypy_error_occurrences_unit() -> None:
    assert _checker.count_mypy_error_occurrences(MYPY_CLEAN) == 0
    assert _checker.count_mypy_error_occurrences(MYPY_WITH_ERROR) == 1
    assert _checker.count_mypy_error_occurrences(MYPY_TWO_SAME_MESSAGE) == 2


# ---------------------------------------------------------------------------
# review-t_e03bfeff run 171 (repair t_1660b803): round-4 fail-closed regressions
# ---------------------------------------------------------------------------


def test_fingerprint_long_headline_and_long_nested_cause(tmp_path: Path) -> None:
    """Round-4 P1 repro: a long headline AND a long nested-cause payload used
    to let the exception class fall into the blind middle between head[:120]
    and tail[-80:]. Mutating only ``ModuleNotFoundError`` -> ``SecurityError``
    must now change the digest and fail comparison."""
    node = "tests/test_x.py::test_y"
    long_headline = "AssertionError: " + "x" * 170  # 186 chars
    original_fp = (
        long_headline + '\n  File "<stdin>", line 1, in <module>\nModuleNotFoundError: ' + "x" * 200
    )
    mutated_fp = original_fp.replace("ModuleNotFoundError", "SecurityError")

    # The normalized fingerprints must differ even though the mutation sits
    # beyond the human-readable preview window.
    norm_orig = _checker._normalize_failure_fingerprint(original_fp)
    norm_mut = _checker._normalize_failure_fingerprint(mutated_fp)
    assert norm_orig != norm_mut

    junit_orig = _minimal_junit(tmp_path / "orig.xml", failures={node: original_fp})
    junit_mut = _minimal_junit(tmp_path / "mut.xml", failures={node: mutated_fp})
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    baseline = _baseline(
        tmp_path / "baseline.json",
        known_failures=[{"node_id": node, "fingerprint": norm_orig}],
    )

    # The original failure matches the baseline fingerprint and passes.
    rc = main(
        [
            "--junit-xml",
            str(junit_orig),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 0

    # The mutated exception class produces a different fingerprint and fails.
    rc = main(
        [
            "--junit-xml",
            str(junit_mut),
            "--mypy-output",
            str(mypy),
            "--baseline",
            str(baseline),
            "--mypy-exit-code",
            "0",
            "--pytest-exit-code",
            "1",
        ]
    )
    assert rc == 1


def test_mypy_contradictory_multiple_summaries_fail_closed(tmp_path: Path) -> None:
    """Round-4 P2 repro: one allowlisted error followed by 'Found 1 error'
    AND a contradictory 'Success: no issues found' used to return 0. A
    concatenated/stale stream with multiple summaries must fail closed."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(
        tmp_path / "mypy.txt",
        "src/openbiliclaw/foo.py:10:5: error: Incompatible types  [assignment]\n"
        "Found 1 error in 1 file (checked 227 source files)\n"
        "Success: no issues found in 227 source files\n",
    )
    baseline = _baseline(tmp_path / "baseline.json")
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
            "--mypy-exit-code",
            "1",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 2


def test_mypy_content_after_summary_fails_closed(tmp_path: Path) -> None:
    """Round-4 P2 follow-up: any non-empty content after the terminal summary
    (here a diagnostic row) is a concatenated/stale artifact and fails
    closed."""
    junit = _minimal_junit(tmp_path / "junit.xml")
    mypy = _write(
        tmp_path / "mypy.txt",
        "Found 1 error in 1 file (checked 227 source files)\n"
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
            "--mypy-exit-code",
            "1",
            "--pytest-exit-code",
            "0",
        ]
    )
    assert rc == 2


def test_parse_mypy_summary_rejects_multiple_unit() -> None:
    contradictory = (
        "Found 1 error in 1 file (checked 227 source files)\n"
        "Success: no issues found in 227 source files\n"
    )
    with pytest.raises(MypyOutputError, match="multiple summary"):
        parse_mypy_summary(contradictory)


def test_junit_testcase_with_failure_and_skipped_fails_closed(tmp_path: Path) -> None:
    """Round-4 P2 repro: a testcase with both <failure> and <skipped> is
    structurally impossible (mutually exclusive outcomes) and must fail
    closed even when declared counters and allowlists line up."""
    node = "tests/test_x.py::test_y"
    junit = _write(
        tmp_path / "junit.xml",
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="2" '
        'failures="1" errors="0" skipped="1">'
        '<testcase classname="tests.test_ok" name="test_pass"/>'
        '<testcase classname="tests.test_x" name="test_y">'
        '<failure message="AssertionError: boom">boom</failure>'
        '<skipped message="skip">skip</skipped>'
        "</testcase>"
        "</testsuite></testsuites>",
    )
    mypy = _write(tmp_path / "mypy.txt", MYPY_CLEAN)
    fp = _checker._normalize_failure_fingerprint("AssertionError: boom")
    baseline = _baseline(
        tmp_path / "baseline.json",
        known_failures=[{"node_id": node, "fingerprint": fp}],
    )
    data = json.loads(baseline.read_text())
    data["pytest"]["known_skips"] = [node]
    baseline.write_text(json.dumps(data))
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
            "1",
        ]
    )
    assert rc == 2


def test_junit_testcase_with_multiple_failures_fails_closed(tmp_path: Path) -> None:
    """Round-4 P2 follow-up: multiple <failure>/<error> children on one
    testcase are structurally impossible; selecting only the first is a
    fail-open path."""
    junit = _write(
        tmp_path / "junit.xml",
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="2" '
        'failures="2" errors="0" skipped="0">'
        '<testcase classname="tests.test_ok" name="test_pass"/>'
        '<testcase classname="tests.test_x" name="test_y">'
        '<failure message="AssertionError: one">one</failure>'
        '<failure message="AssertionError: two">two</failure>'
        "</testcase>"
        "</testsuite></testsuites>",
    )
    with pytest.raises(JUnitStructureError, match="exactly one outcome"):
        parse_junit_failures_and_skips(junit)


def test_parse_junit_failure_and_skipped_unit(tmp_path: Path) -> None:
    junit = _write(
        tmp_path / "bad.xml",
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="1" '
        'failures="1" errors="0" skipped="1">'
        '<testcase classname="tests.test_x" name="test_y">'
        '<failure message="AssertionError: boom">boom</failure>'
        '<skipped message="skip">skip</skipped>'
        "</testcase>"
        "</testsuite></testsuites>",
    )
    with pytest.raises(JUnitStructureError, match="mutually exclusive"):
        parse_junit_failures_and_skips(junit)
