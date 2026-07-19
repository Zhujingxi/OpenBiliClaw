"""Quality-baseline comparator for the incremental refactor.

Compares a live pytest JUnit XML report and a live mypy diagnostic stream
against the checked-in baseline at ``tests/contracts/quality-baseline.json``
and decides whether the run may pass.

Rules (per docs/plans/2026-07-19-incremental-architecture-refactor-plan.md §7.2):

- pytest failures/errors are keyed by node ID. Only failures whose node ID
  appears in ``pytest.known_failures`` are tolerated. New failures, new
  errors, collection errors, or a missing/unparseable JUnit XML fail closed.
- pytest skips are keyed by node ID. New skips must be explicitly added to
  ``pytest.known_skips``; existing skips disappearing is allowed.
- mypy diagnostics are keyed by ``{path, error_code, normalized_message}``
  (line numbers stripped, ``note:`` lines ignored). New diagnostics are
  rejected even if the total count is unchanged. Removed diagnostics are
  always allowed.
- Coverage is recorded in the baseline as ``coverage.line_percent`` and may
  not drop more than ``coverage.noise_tolerance`` percentage points.

Usage (called from CI after the underlying tools have already run — the
underlying tool's non-zero exit must not abort the pipeline before this
script runs)::

    python scripts/check_quality_baseline.py \
        --junit-xml build/pytest-junit.xml \
        --mypy-output build/mypy.txt \
        [--coverage-xml build/coverage.xml]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BASELINE_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "contracts" / "quality-baseline.json"
)

# Matches mypy error lines like:
#   src/openbiliclaw/cli_models.py:123:45: error: Message here  [arg-type]
MYPY_LINE_RE = re.compile(
    r"^(?P<path>[^:\s][^:]*?\.py):(?P<line>\d+)(?::\d+)?:\s*"
    r"(?P<severity>error|warning|note):\s*(?P<message>.*?)"
    r"(?:\s*\[(?P<code>[a-z0-9_-]+)\])?\s*$"
)

# Mypy summary grammar. We require at least one of these forms at the end
# of a non-empty stream; anything else is treated as unparseable/crash
# output and fails closed.
MYPY_SUCCESS_RE = re.compile(r"^Success: no issues found in \d+ source files\b")
MYPY_FOUND_ERRORS_RE = re.compile(r"^Found \d+ errors? in \d+ files?\b")

# Recognizable non-diagnostic lines mypy may emit alongside the summary
# (config notes, unused-section notes, etc.). Anything else that is not a
# diagnostic line and not one of these makes the stream unparseable.
MYPY_NOISE_RE = re.compile(
    r"^(?:pyproject\.toml|mypy\.ini|setup\.cfg|tox\.ini): note: |"
    r"^note: |"
    r"^mypy: (?:INTERNAL ERROR|error: |warning: )"
)


def _load_baseline(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _normalize_mypy_message(message: str) -> str:
    """Strip location/identifier noise from a mypy message so it is stable.

    Removes quoted filenames with line numbers, normalizes whitespace. Does
    NOT strip identifier names — we want a true semantic change (different
    symbol) to register as a different diagnostic.
    """
    # Collapse whitespace
    msg = re.sub(r"\s+", " ", message).strip()
    # Normalize numeric literals that often appear in overload listings
    msg = re.sub(r"\b\d+\b", "<N>", msg)
    return msg


class MypyOutputError(ValueError):
    """Raised when mypy stdout is empty, unparseable, or crash-only."""


def parse_mypy_output(text: str) -> list[dict[str, str]]:
    """Extract keyed diagnostics from mypy stdout.

    Ignores ``note:`` lines (overload listing noise). Returns a sorted list
    of dicts with keys ``path``, ``error_code``, ``message`` (normalized).

    Raises ``MypyOutputError`` when the stream is empty, ends without a
    recognizable summary line, or contains non-empty lines that are neither
    diagnostics nor recognized noise. This makes the comparator fail closed
    on mypy crashes, config errors, or missing output.
    """
    out: set[tuple[str, str, str]] = set()
    lines = [line.rstrip() for line in text.splitlines()]
    if not any(line.strip() for line in lines):
        raise MypyOutputError("mypy output is empty")

    summary_seen = False
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line:
            continue
        if MYPY_SUCCESS_RE.match(line) or MYPY_FOUND_ERRORS_RE.match(line):
            summary_seen = True
            continue
        if MYPY_NOISE_RE.match(line):
            continue
        m = MYPY_LINE_RE.match(line)
        if m:
            if m.group("severity") == "note":
                continue
            if m.group("severity") == "warning":
                # Warnings aren't part of the baseline contract; mypy strict
                # mode promotes them to errors in any case we care about.
                continue
            path = m.group("path")
            # Normalize path separators so macOS/Linux CI runs agree
            path = path.replace("\\", "/")
            code = m.group("code") or ""
            message = _normalize_mypy_message(m.group("message"))
            out.add((path, code, message))
            continue
        # Non-empty line that is not a diagnostic, not a summary, and not
        # recognized noise — treat as unparseable (crash/config error).
        raise MypyOutputError(f"unparseable mypy output line: {line!r}")

    if not summary_seen:
        raise MypyOutputError(
            "mypy output lacks a recognizable summary line "
            "(expected 'Success: no issues found in N source files' or "
            "'Found N errors in M files')"
        )

    return sorted(
        ({"path": p, "error_code": c, "message": m} for (p, c, m) in out),
        key=lambda d: (d["path"], d["error_code"], d["message"]),
    )


def _normalize_failure_fingerprint(raw: str) -> str:
    """Normalize a JUnit ``<failure message="...">`` attribute into a stable
    fingerprint.

    The first line of pytest's failure message carries the exception type
    and the headline message (e.g. ``AssertionError: Traceback (most recent
    call last):`` followed by the nested error). We keep the exception
    type plus the first content line, collapse whitespace, strip numeric
    literals and tmp paths so the fingerprint is stable across runs and
    machines, then truncate to keep the baseline JSON readable.
    """
    # Keep only the first two lines: exception headline + immediate cause.
    head = "\\n".join((raw or "").splitlines()[:2])
    head = re.sub(r"\\s+", " ", head).strip()
    # Strip machine-specific tmp paths and numeric literals.
    head = re.sub(r"/[^\\s]*?(?:pytest-of-[^/]+|tmp[Tt][^/]*)/[^\\s]*", "<TMP>", head)
    head = re.sub(r"\\b\\d+\\b", "<N>", head)
    return head[:200]


def parse_junit_failures_and_skips(
    xml_path: Path,
) -> tuple[dict[str, str], set[str], list[str]]:
    """Return ``(failures, skipped_node_ids, collection_errors)``.

    ``failures`` maps node ID → normalized failure fingerprint (exception
    type + headline). Node IDs are ``<classname>::<name>`` with the
    classname's dotted module path converted to a ``tests/...`` path when
    possible so IDs match pytest's command-line node format.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    failures: dict[str, str] = {}
    skips: set[str] = set()
    collection_errors: list[str] = []

    def node_id(classname: str, name: str) -> str:
        # classname is a dotted module path (e.g. "tests.test_foo"); name is
        # the test function / parametrized id. Some skip entries come from
        # pytest's own skip-report path with an empty classname and a dotted
        # module in the name — handle both shapes.
        if not classname and "." in (name or "") and "::" not in name:
            # name like "tests.test_desktop_web_autoload_margin_e2e" — module
            # path only, no test function. Treat as a module-level skip.
            parts = name.split(".")
            return "/".join(parts) + ".py"
        cls = (classname or "").replace(".", "/")
        if cls and not cls.endswith(".py"):
            cls = f"{cls}.py"
        return f"{cls}::{name}" if cls else name

    # testsuite/testcase may sit directly on <testsuites> or nested.
    for testcase in root.iter("testcase"):
        classname = testcase.get("classname", "") or ""
        name = testcase.get("name", "") or ""
        nid = node_id(classname, name)
        failure_el = next((child for child in testcase if child.tag in {"failure", "error"}), None)
        has_skip = any(child.tag == "skipped" for child in testcase)
        if failure_el is not None:
            failures[nid] = _normalize_failure_fingerprint(failure_el.get("message", ""))
        if has_skip:
            skips.add(nid)

    # Collection errors appear as <testsuite errors="N"> with no testcase,
    # or as top-level <error> elements.
    for testsuite in root.iter("testsuite"):
        errors_attr = testsuite.get("errors")
        if errors_attr and errors_attr != "0":
            # Each non-testcase <error> child is a collection-level problem.
            for child in testsuite:
                if child.tag == "error":
                    collection_errors.append(
                        f"{testsuite.get('name', '<unknown>')}: {child.get('message', '')}"
                    )

    return failures, skips, collection_errors


def parse_coverage_line_percent(coverage_xml_path: Path) -> float:
    """Extract overall line-rate (0..100) from a coverage.py XML report."""
    tree = ET.parse(coverage_xml_path)
    root = tree.getroot()
    line_rate = root.get("line-rate")
    if line_rate is None:
        raise ValueError(f"coverage XML at {coverage_xml_path} has no line-rate attribute")
    return float(line_rate) * 100.0


def compare_pytest(
    baseline: dict[str, Any],
    failures: dict[str, str],
    skips: set[str],
    collection_errors: list[str],
) -> list[str]:
    problems: list[str] = []
    if collection_errors:
        problems.append(f"pytest collection errors (fail closed): {collection_errors}")
    # known_failures entries may be plain node-ID strings (legacy form,
    # fingerprint not enforced) or objects with ``node_id`` + ``fingerprint``
    # (reviewer-required form: a new failure cause at the same node is
    # rejected). Known skips stay plain node-ID strings.
    known_failures_raw = baseline.get("pytest", {}).get("known_failures", [])
    known_failure_nodes: set[str] = set()
    known_failure_fingerprints: dict[str, str] = {}
    for entry in known_failures_raw:
        if isinstance(entry, str):
            known_failure_nodes.add(entry)
        elif isinstance(entry, dict) and "node_id" in entry:
            node = str(entry["node_id"])
            known_failure_nodes.add(node)
            if "fingerprint" in entry:
                known_failure_fingerprints[node] = str(entry["fingerprint"])
    known_skips = set(baseline.get("pytest", {}).get("known_skips", []))

    new_failures = sorted(set(failures) - known_failure_nodes)
    if new_failures:
        problems.append(f"new pytest failures not in baseline: {new_failures}")

    fingerprint_mismatches = sorted(
        node
        for node, live_fp in failures.items()
        if node in known_failure_fingerprints and live_fp != known_failure_fingerprints[node]
    )
    if fingerprint_mismatches:
        problems.append(
            "pytest failures at allowlisted nodes with a DIFFERENT failure "
            f"fingerprint (possible new bug masked by the allowlist): {fingerprint_mismatches}"
        )

    new_skips = skips - known_skips
    if new_skips:
        problems.append(
            f"new pytest skips not in baseline (must be justified + baselined): {sorted(new_skips)}"
        )
    return problems


def compare_mypy(baseline: dict[str, Any], live: list[dict[str, str]]) -> list[str]:
    problems: list[str] = []
    known = baseline.get("mypy", {}).get("known_diagnostics", [])
    known_keys = {(d["path"], d["error_code"], d["message"]) for d in known}
    live_keys = {(d["path"], d["error_code"], d["message"]) for d in live}
    new = live_keys - known_keys
    if new:
        formatted = sorted(f"{p}: [{c}] {m}" for (p, c, m) in new)
        problems.append(f"new mypy diagnostics not in baseline: {formatted}")
    return problems


def compare_coverage(baseline: dict[str, Any], live_percent: float) -> list[str]:
    cov = baseline.get("coverage", {})
    baseline_percent = cov.get("line_percent")
    if baseline_percent is None:
        return []  # coverage tracking disabled in baseline
    tolerance = float(cov.get("noise_tolerance", 0.5))
    if live_percent < baseline_percent - tolerance:
        return [
            f"coverage dropped from {baseline_percent:.2f}% to {live_percent:.2f}% "
            f"(tolerance {tolerance}%)"
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit-xml", type=Path, required=True)
    parser.add_argument("--mypy-output", type=Path, required=True)
    parser.add_argument("--coverage-xml", type=Path, default=None)
    parser.add_argument(
        "--mypy-exit-code",
        type=int,
        default=None,
        help=(
            "Raw exit code of the mypy process. When provided, any value "
            "other than 0 (clean) or 1 (diagnostics found) is treated as a "
            "tool crash/config error and fails closed regardless of output."
        ),
    )
    parser.add_argument(
        "--pytest-exit-code",
        type=int,
        default=None,
        help=(
            "Raw exit code of the pytest process. When provided, values "
            "other than 0 (all passed) or 1 (tests failed) are treated as "
            "collection/usage errors and fail closed regardless of JUnit "
            "content."
        ),
    )
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args(argv)

    if not args.baseline.exists():
        print(f"FAIL: baseline not found at {args.baseline}", file=sys.stderr)
        return 2
    if not args.junit_xml.exists():
        print(f"FAIL: pytest JUnit XML missing at {args.junit_xml}", file=sys.stderr)
        return 2
    if not args.mypy_output.exists():
        print(f"FAIL: mypy output missing at {args.mypy_output}", file=sys.stderr)
        return 2

    baseline = _load_baseline(args.baseline)
    problems: list[str] = []

    if args.mypy_exit_code is not None and args.mypy_exit_code not in (0, 1):
        problems.append(
            f"mypy exited with code {args.mypy_exit_code} (tool crash/config error); "
            "only 0 (clean) or 1 (diagnostics found) are comparable"
        )
    if args.pytest_exit_code is not None and args.pytest_exit_code not in (0, 1):
        problems.append(
            f"pytest exited with code {args.pytest_exit_code} (collection/usage error); "
            "only 0 (all passed) or 1 (tests failed) are comparable"
        )

    try:
        failures, skips, collection_errors = parse_junit_failures_and_skips(args.junit_xml)
    except ET.ParseError as exc:
        print(f"FAIL: cannot parse pytest JUnit XML: {exc}", file=sys.stderr)
        return 2
    problems.extend(compare_pytest(baseline, failures, skips, collection_errors))

    mypy_text = args.mypy_output.read_text(encoding="utf-8", errors="replace")
    try:
        live_mypy = parse_mypy_output(mypy_text)
    except MypyOutputError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    problems.extend(compare_mypy(baseline, live_mypy))

    if args.coverage_xml is not None:
        if not args.coverage_xml.exists():
            print(f"FAIL: coverage XML missing at {args.coverage_xml}", file=sys.stderr)
            return 2
        try:
            live_percent = parse_coverage_line_percent(args.coverage_xml)
        except (ET.ParseError, ValueError) as exc:
            print(f"FAIL: cannot parse coverage XML: {exc}", file=sys.stderr)
            return 2
        problems.extend(compare_coverage(baseline, live_percent))

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    print("OK: quality baseline holds (no new pytest/mypy diagnostics, coverage stable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
