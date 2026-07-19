"""Generate ``tests/contracts/quality-baseline.json`` from live tool output.

Run this once when establishing the baseline (or intentionally updating it
after an approved change), then review the diff before committing::

    python -m mypy src/ --show-error-codes --no-color-output \
        > build/mypy.txt || true
    python -m pytest -q --junitxml=build/pytest-junit.xml || true
    python -m pytest --cov=openbiliclaw --cov-report=xml:build/coverage.xml -q || true
    python scripts/generate_quality_baseline.py \
        --junit-xml build/pytest-junit.xml \
        --mypy-output build/mypy.txt \
        --coverage-xml build/coverage.xml \
        > tests/contracts/quality-baseline.json

Note: do NOT pass ``--no-error-summary`` — the comparator requires the
mypy summary line (``Success: no issues found ...`` / ``Found N errors
...``) as part of its fail-closed grammar validation.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# Import the checker's parsing logic by file path so baseline generation and
# enforcement cannot drift apart, and so this script works without scripts/
# being a package.
_CHECKER_PATH = Path(__file__).resolve().parent / "check_quality_baseline.py"
_spec = importlib.util.spec_from_file_location("check_quality_baseline", _CHECKER_PATH)
assert _spec is not None and _spec.loader is not None
_checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_checker)

parse_coverage_line_percent = _checker.parse_coverage_line_percent
parse_junit_failures_and_skips = _checker.parse_junit_failures_and_skips
parse_mypy_output = _checker.parse_mypy_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit-xml", type=Path, required=True)
    parser.add_argument("--mypy-output", type=Path, required=True)
    parser.add_argument("--coverage-xml", type=Path, default=None)
    parser.add_argument(
        "--noise-tolerance",
        type=float,
        default=0.5,
        help="Coverage noise tolerance in percentage points (default 0.5)",
    )
    args = parser.parse_args(argv)

    failures, _errors, skips, _collection_errors = parse_junit_failures_and_skips(args.junit_xml)
    mypy_diags = parse_mypy_output(args.mypy_output.read_text(encoding="utf-8"))

    coverage: dict[str, object] = {"noise_tolerance": args.noise_tolerance}
    if args.coverage_xml is not None and args.coverage_xml.exists():
        coverage["line_percent"] = round(parse_coverage_line_percent(args.coverage_xml), 2)

    baseline = {
        "version": 1,
        "description": (
            "Normalized quality baseline for the incremental architecture refactor. "
            "New pytest failures / mypy diagnostics are rejected by "
            "scripts/check_quality_baseline.py; removals are always allowed. "
            "known_failures entries are fingerprinted over the first three lines of "
            "the failure message (exception headline + traceback frame + nested "
            "cause), with the headline bounded to 120 chars and the tail bounded to "
            "80 chars so a long headline cannot crowd out the nested cause; "
            "whitespace-collapsed with numbers/tmp paths scrubbed: a failure "
            "at the same node with a different exception type, headline, or nested "
            "cause is rejected as a new bug. "
            "Update intentionally by re-running scripts/generate_quality_baseline.py "
            "and reviewing the diff."
        ),
        "pytest": {
            "known_failures": [
                {"node_id": node, "fingerprint": failures[node]} for node in sorted(failures)
            ],
            "known_skips": sorted(skips),
        },
        "mypy": {
            "known_diagnostics": mypy_diags,
        },
        "coverage": coverage,
    }
    json.dump(baseline, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
