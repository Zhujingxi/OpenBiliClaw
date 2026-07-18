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

    python scripts/check_quality_baseline.py \\
        --junit-xml build/pytest-junit.xml \\
        --mypy-output build/mypy.txt \\
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

BASELINE_PATH = Path(__file__).resolve().parent.parent / "tests" / "contracts" / "quality-baseline.json"

# Matches mypy error lines like:
#   src/openbiliclaw/cli_models.py:123:45: error: Message here  [arg-type]
MYPY_LINE_RE = re.compile(
    r"^(?P<path>[^:\s][^:]*?\.py):(?P<line>\d+)(?::\d+)?:\s*"
    r"(?P<severity>error|warning|note):\s*(?P<message>.*?)"
    r"(?:\s*\[(?P<code>[a-z0-9_-]+)\])?\s*$"
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


def parse_mypy_output(text: str) -> list[dict[str, str]]:
    """Extract keyed diagnostics from mypy stdout.

    Ignores ``note:`` lines (overload listing noise). Returns a sorted list
    of dicts with keys ``path``, ``error_code``, ``message`` (normalized).
    """
    out: set[tuple[str, str, str]] = set()
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        m = MYPY_LINE_RE.match(line)
        if not m:
            continue
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
    return sorted(
        ({"path": p, "error_code": c, "message": m} for (p, c, m) in out),
        key=lambda d: (d["path"], d["error_code"], d["message"]),
    )


def parse_junit_failures_and_skips(xml_path: Path) -> tuple[set[str], set[str], list[str]]:
    """Return ``(failed_node_ids, skipped_node_ids, collection_errors)``.

    Node IDs are ``<classname>::<name>`` with the classname's dotted module
    path converted to a ``tests/...`` path when possible so IDs match
    pytest's command-line node format.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    failures: set[str] = set()
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
        has_failure = any(child.tag in {"failure", "error"} for child in testcase)
        has_skip = any(child.tag == "skipped" for child in testcase)
        if has_failure:
            failures.add(nid)
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
    failures: set[str],
    skips: set[str],
    collection_errors: list[str],
) -> list[str]:
    problems: list[str] = []
    if collection_errors:
        problems.append(f"pytest collection errors (fail closed): {collection_errors}")
    known_failures = set(baseline.get("pytest", {}).get("known_failures", []))
    known_skips = set(baseline.get("pytest", {}).get("known_skips", []))
    new_failures = failures - known_failures
    if new_failures:
        problems.append(f"new pytest failures not in baseline: {sorted(new_failures)}")
    new_skips = skips - known_skips
    if new_skips:
        problems.append(
            f"new pytest skips not in baseline (must be justified + baselined): "
            f"{sorted(new_skips)}"
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

    try:
        failures, skips, collection_errors = parse_junit_failures_and_skips(args.junit_xml)
    except ET.ParseError as exc:
        print(f"FAIL: cannot parse pytest JUnit XML: {exc}", file=sys.stderr)
        return 2
    problems.extend(compare_pytest(baseline, failures, skips, collection_errors))

    mypy_text = args.mypy_output.read_text(encoding="utf-8", errors="replace")
    live_mypy = parse_mypy_output(mypy_text)
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
