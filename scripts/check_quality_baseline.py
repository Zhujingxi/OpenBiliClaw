"""Quality-baseline comparator for the incremental refactor.

Compares a live pytest JUnit XML report and a live mypy diagnostic stream
against the checked-in baseline at ``tests/contracts/quality-baseline.json``
and decides whether the run may pass.

Rules (per docs/plans/2026-07-19-incremental-architecture-refactor-plan.md §7.2):

- pytest failures/errors are keyed by node ID. Only failures whose node ID
  appears in ``pytest.known_failures`` are tolerated, and every
  ``known_failures`` entry MUST be a well-formed
  ``{node_id, fingerprint}`` object whose fingerprint matches the exact
  grammar ``<preview> | sha256:<64 hex>`` (digested over the normalized
  ``message`` attribute PLUS the full element text, so a mutated
  exception in the JUnit body cannot hide behind an unchanged/generic
  headline). Legacy plain-string entries, fingerprintless objects,
  malformed fingerprints, duplicates, or unknown shapes are rejected as a
  corrupt baseline (exit 2) before any comparison. New failures, new
  errors, collection errors, or a missing/unparseable JUnit XML fail
  closed.
- pytest skips are keyed by node ID. New skips must be explicitly added to
  ``pytest.known_skips``; existing skips disappearing is allowed.
- mypy diagnostics are keyed by ``{path, error_code, normalized_message}``
  (line numbers stripped, ``note:`` lines ignored). New diagnostics are
  rejected even if the total count is unchanged. Removed diagnostics are
  always allowed. The ``Found N errors`` summary is reconciled against the
  number of parsed error *occurrences* (before identity dedup), so two
  same-message diagnostics at different lines count as two.
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
import hashlib
import json
import math
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
# output and fails closed. The grammar is fullmatch-anchored so a trailer
# after the supported sentence is rejected (review-t_e03bfeff P2).
MYPY_SUCCESS_RE = re.compile(r"^Success: no issues found in \d+ source files?$")
MYPY_FOUND_ERRORS_RE = re.compile(
    r"^Found (?P<count>\d+) errors? in \d+ files?(?: \(checked \d+ source files?\))?$"
)

# Recognizable non-diagnostic lines mypy may emit alongside the summary
# (config notes, unused-section notes, etc.). Anything else that is not a
# diagnostic line and not one of these makes the stream unparseable.
# NOTE: ``mypy: INTERNAL ERROR`` is NOT benign noise — it indicates a crash
# and must fail closed via the unparseable-line path (review-t_e03bfeff P2).
MYPY_NOISE_RE = re.compile(
    r"^(?:pyproject\.toml|mypy\.ini|setup\.cfg|tox\.ini): note: |"
    r"^note: "
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


def parse_mypy_summary(text: str) -> tuple[str | None, int | None]:
    """Return the kind and declared error count of the mypy summary line.

    Returns ``("success", 0)`` for ``Success: no issues found ...``,
    ``("errors", N)`` for ``Found N errors ...``, or ``(None, None)`` when
    neither is present. Used by the comparator to reconcile the raw process
    exit code against the semantic content of the artifact
    (review-t_cce76b68 F1, review-t_e03bfeff P1-2).

    Raises ``MypyOutputError`` when the stream contains MORE THAN ONE
    summary-shaped line: contradictory summaries (e.g. a stale
    ``Found 1 error ...`` line followed by ``Success: no issues found ...``)
    are the classic concatenated-artifact fail-open path and must fail
    closed rather than silently returning the first match
    (review-t_e03bfeff round-4 P2).
    """
    found: tuple[str, int] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        match: tuple[str, int] | None = None
        if MYPY_SUCCESS_RE.match(line):
            match = ("success", 0)
        else:
            m = MYPY_FOUND_ERRORS_RE.match(line)
            if m:
                match = ("errors", int(m.group("count")))
        if match is not None:
            if found is not None:
                raise MypyOutputError(
                    "mypy output contains multiple summary lines "
                    f"({line!r} after an earlier summary); refusing to "
                    "accept a concatenated/stale artifact (fail closed)"
                )
            found = match
    if found is None:
        return None, None
    return found


def parse_mypy_summary_kind(text: str) -> str | None:
    """Return the kind of mypy summary line present in ``text``.

    Returns ``"success"`` for ``Success: no issues found ...``, ``"errors"``
    for ``Found N errors ...``, or ``None`` when neither is present. Kept
    for backward compatibility with existing callers and tests.
    """
    kind, _ = parse_mypy_summary(text)
    return kind


def parse_mypy_output(text: str) -> list[dict[str, str]]:
    """Extract keyed diagnostics from mypy stdout.

    Ignores ``note:`` lines (overload listing noise). Returns a sorted list
    of deduplicated identity dicts with keys ``path``, ``error_code``,
    ``message`` (normalized). Identity dedup is for baseline comparison
    only; the number of parsed error *occurrences* (two same-message
    diagnostics at different lines count twice) is tracked separately by
    ``count_mypy_error_occurrences`` for summary reconciliation
    (review-t_e03bfeff P1-2 follow-up).

    Raises ``MypyOutputError`` when the stream is empty, ends without a
    recognizable summary line, or contains non-empty lines that are neither
    diagnostics nor recognized noise. This makes the comparator fail closed
    on mypy crashes, config errors, or missing output.
    """
    identities, _occurrences = _parse_mypy_rows(text)
    return sorted(
        ({"path": p, "error_code": c, "message": m} for (p, c, m) in identities),
        key=lambda d: (d["path"], d["error_code"], d["message"]),
    )


def count_mypy_error_occurrences(text: str) -> int:
    """Count parsed mypy error occurrences (before identity dedup).

    Two diagnostics at different lines with the same normalized
    ``{path, code, message}`` collapse to one identity but remain two
    occurrences; the mypy summary's declared error count is reconciled
    against occurrences, not identities. Raises ``MypyOutputError`` under
    the same fail-closed grammar rules as ``parse_mypy_output``.
    """
    _identities, occurrences = _parse_mypy_rows(text)
    return occurrences


def _parse_mypy_rows(text: str) -> tuple[set[tuple[str, str, str]], int]:
    """Parse mypy stdout into ``(deduplicated identities, occurrence count)``."""
    out: set[tuple[str, str, str]] = set()
    occurrences = 0
    lines = [line.rstrip() for line in text.splitlines()]
    if not any(line.strip() for line in lines):
        raise MypyOutputError("mypy output is empty")

    summary_seen = False
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line:
            continue
        if summary_seen:
            # The summary is the TERMINAL line of a well-formed mypy stream.
            # Anything non-empty after it — diagnostics, a second summary,
            # noise — means a concatenated/stale artifact and must fail
            # closed (review-t_e03bfeff round-4 P2).
            raise MypyOutputError(
                f"mypy output has content after the terminal summary line: {line!r}; "
                "refusing to accept a concatenated/stale artifact (fail closed)"
            )
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
            occurrences += 1
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

    return out, occurrences


def _normalize_failure_fingerprint(raw: str) -> str:
    """Normalize JUnit failure content into a stable fingerprint.

    ``raw`` is the FULL semantic content of the ``<failure>``/``<error>``
    outcome: the normalized ``message`` attribute plus the full element
    text, joined with a channel separator (see
    ``_failure_outcome_content``). The first line of pytest's failure
    message carries the exception type and the headline message; the
    second line is typically the traceback frame; the third line carries
    the nested cause (e.g. ``ModuleNotFoundError: No module named
    'tomllib'``), and for many pytest failures the element TEXT is where
    the actual traceback and exception live while the attribute holds
    only a generic headline. Because JUnit XML attributes flatten
    newlines to spaces, we cannot rely on line structure at comparison
    time, and ANY blind omission (head[:N] + tail[-M:], or hashing only
    the attribute while ignoring the body) lets a semantic mutation hide
    in the un-hashed region (review-t_e03bfeff round-4 P1: a 186-char
    headline plus a 200-char nested-cause payload mutated
    ``ModuleNotFoundError`` -> ``SecurityError`` in the omitted middle
    and produced an identical fingerprint; run 180 P2: a baseline
    ``<failure>ModuleNotFoundError: old</failure>`` mutated to
    ``<failure>SecurityError: replacement</failure>`` hashed the empty
    attribute both times and passed).

    The fingerprint therefore digests the FULL normalized semantic text
    (attribute AND element text) with SHA-256 and prepends a bounded
    human-readable preview so baseline diffs stay reviewable:
    ``<preview> | sha256:<hex>``. Whitespace is collapsed and numeric
    literals / tmp paths are scrubbed for stability BEFORE the digest,
    so two semantically identical outcomes hash identically while any
    semantic change anywhere — attribute or body — changes the
    fingerprint.
    """
    normalized = re.sub(r"\s+", " ", (raw or "")).strip()
    normalized = re.sub(r"/[^\s]*?(?:pytest-of-[^/]+|tmp[Tt][^/]*)/[^\s]*", "<TMP>", normalized)
    normalized = re.sub(r"\b\d+\b", "<N>", normalized)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    preview = normalized[:120]
    return f"{preview} | sha256:{digest}"


def _failure_outcome_content(outcome_el: ET.Element) -> str:
    """Return the full fingerprintable content of a ``<failure>``/``<error>``.

    A standard JUnit ``<failure>`` may omit the optional ``message``
    attribute or carry only a generic headline there while the actual
    exception type, message, and traceback live in the element TEXT.
    Hashing only the attribute is a blind channel: two failures with the
    same (or no) attribute but different bodies — a different root cause
    — must NOT produce the same fingerprint (review-t_e03bfeff run 180
    P2). Both channels are therefore always included, normalized the
    same way, and joined with a fixed separator so a semantic change in
    either channel changes the digest. Nested child markup (rare in
    practice) is serialized into the text channel as well so nothing
    semantic is dropped.
    """
    message = re.sub(r"\s+", " ", (outcome_el.get("message") or "")).strip()
    text_parts: list[str] = []
    if outcome_el.text:
        text_parts.append(outcome_el.text)
    for child in outcome_el:
        text_parts.append(ET.tostring(child, encoding="unicode"))
        if child.tail:
            text_parts.append(child.tail)
    text = re.sub(r"\s+", " ", " ".join(part for part in text_parts if part)).strip()
    return f"message: {message}\ntext: {text}"


# Grammar of a well-formed known_failures fingerprint as emitted by
# ``_normalize_failure_fingerprint``: ``<preview> | sha256:<64 hex>``.
_FINGERPRINT_GRAMMAR_RE = re.compile(r"^.{0,120} \| sha256:[0-9a-f]{64}$", re.DOTALL)


class BaselineSchemaError(ValueError):
    """Raised when the checked-in baseline contains a malformed known_failures entry.

    A stale, legacy, or hand-edited baseline entry that lacks an exact
    fingerprint is a fail-open path: fingerprint enforcement is silently
    skipped for that node and any new failure cause there passes
    (review-t_e03bfeff run 180 P2-1). The comparator therefore validates
    the schema strictly and refuses to run at all on a corrupt baseline.
    """


def validate_known_failures(known_failures_raw: Any) -> dict[str, str]:
    """Validate ``pytest.known_failures`` and return ``{node_id: fingerprint}``.

    Every entry MUST be a well-formed object with a non-empty string
    ``node_id`` and a string ``fingerprint`` matching the exact grammar
    ``<preview> | sha256:<64 hex>`` emitted by
    ``_normalize_failure_fingerprint``. Plain node-ID strings (the legacy
    form), fingerprintless objects, unknown shapes, wrong types, empty
    values, malformed fingerprints, and duplicate node IDs are all
    rejected as a corrupt baseline — fail closed, never skip
    fingerprint enforcement (review-t_e03bfeff run 180 P2-1).
    """
    if not isinstance(known_failures_raw, list):
        raise BaselineSchemaError(
            "baseline pytest.known_failures must be a list of "
            "{node_id, fingerprint} objects; refusing to run on a corrupt "
            f"baseline (got {type(known_failures_raw).__name__})"
        )
    entries: dict[str, str] = {}
    for index, entry in enumerate(known_failures_raw):
        where = f"baseline pytest.known_failures[{index}]"
        if not isinstance(entry, dict):
            raise BaselineSchemaError(
                f"{where} is not an object (got {type(entry).__name__}: {entry!r}); "
                "the legacy plain-string form bypasses fingerprint enforcement "
                "and is no longer accepted — regenerate the baseline with "
                "scripts/generate_quality_baseline.py"
            )
        unknown_keys = sorted(set(entry) - {"node_id", "fingerprint"})
        if unknown_keys:
            raise BaselineSchemaError(
                f"{where} has unknown keys {unknown_keys}; refusing to run on a corrupt baseline"
            )
        node = entry.get("node_id")
        if not isinstance(node, str) or not node:
            raise BaselineSchemaError(
                f"{where} must have a non-empty string node_id; refusing to "
                f"run on a corrupt baseline (got {node!r})"
            )
        fingerprint = entry.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise BaselineSchemaError(
                f"{where} (node {node!r}) must have a non-empty string "
                "fingerprint; an entry without a fingerprint silently "
                "disables cause comparison for that node (fail closed)"
            )
        if not _FINGERPRINT_GRAMMAR_RE.match(fingerprint):
            raise BaselineSchemaError(
                f"{where} (node {node!r}) has a malformed fingerprint "
                f"{fingerprint!r}; expected the exact grammar "
                "'<preview> | sha256:<64 hex>' emitted by "
                "scripts/generate_quality_baseline.py — a fingerprint the "
                "comparator cannot verify fails closed"
            )
        if node in entries:
            raise BaselineSchemaError(
                f"baseline pytest.known_failures lists node {node!r} more "
                "than once; refusing to run on a corrupt baseline"
            )
        entries[node] = fingerprint
    return entries


class JUnitStructureError(ValueError):
    """Raised when a JUnit report is structurally invalid or contradictory."""


def parse_junit_failures_and_skips(
    xml_path: Path,
) -> tuple[dict[str, str], dict[str, str], set[str], list[str]]:
    """Return ``(failures, errors, skipped_node_ids, collection_errors)``.

    ``failures`` maps node ID → normalized failure fingerprint (exception
    type + headline). ``errors`` maps node ID → normalized error fingerprint.
    ``skipped_node_ids`` is the set of node IDs with a ``<skipped>`` child.
    ``collection_errors`` lists non-testcase ``<error>`` messages. Node IDs
    are ``<classname>::<name>`` with the classname's dotted module path
    converted to a ``tests/...`` path when possible so IDs match pytest's
    command-line node format.

    Raises ``JUnitStructureError`` when the report is structurally empty
    (no testsuite / no testcase), when declared aggregate counters do not
    match the parsed rows, when the same node ID appears twice, or when
    declared failures/errors/skipped counts are not actually present as
    parsed elements. A structurally empty or contradictory JUnit must fail
    closed — it is indistinguishable from a truncated or stale artifact
    (review-t_cce76b68 F2, review-t_e03bfeff P2-3/P2-4).
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    failures: dict[str, str] = {}
    errors: dict[str, str] = {}
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

    # Structural validation: a usable JUnit must contain at least one
    # testsuite with at least one testcase. ``<testsuites/>`` (the empty
    # root) is the signature of a crashed/empty pytest run.
    testsuites = list(root.iter("testsuite"))
    testcases = list(root.iter("testcase"))
    if not testsuites:
        raise JUnitStructureError(
            f"JUnit report at {xml_path} has no <testsuite> elements; "
            "refusing to treat a structurally empty report as a successful run"
        )
    if not testcases:
        raise JUnitStructureError(
            f"JUnit report at {xml_path} has no <testcase> elements; "
            "refusing to treat a structurally empty report as a successful run"
        )

    # Aggregate-counter validation: declared totals must match the parsed
    # rows, and declared failures/errors/skipped must be present as parsed
    # elements. A counter mismatch indicates a truncated or hand-edited
    # artifact.
    declared_tests = 0
    declared_failures = 0
    declared_errors = 0
    declared_skipped = 0
    for suite in testsuites:
        declared_tests += int(suite.get("tests", "0") or "0")
        declared_failures += int(suite.get("failures", "0") or "0")
        declared_errors += int(suite.get("errors", "0") or "0")
        declared_skipped += int(suite.get("skipped", "0") or "0")
    if declared_tests != len(testcases):
        raise JUnitStructureError(
            f"JUnit report at {xml_path} declares tests={declared_tests} "
            f"but contains {len(testcases)} <testcase> elements"
        )

    # testsuite/testcase may sit directly on <testsuites> or nested.
    seen_node_ids: set[str] = set()
    parsed_failure_count = 0
    parsed_error_count = 0
    parsed_skipped_count = 0
    for testcase in testcases:
        classname = testcase.get("classname", "") or ""
        name = testcase.get("name", "") or ""
        nid = node_id(classname, name)
        if nid in seen_node_ids:
            raise JUnitStructureError(
                f"JUnit report at {xml_path} contains duplicate node id {nid!r}"
            )
        seen_node_ids.add(nid)
        outcome_children = [child for child in testcase if child.tag in {"failure", "error"}]
        skip_children = [child for child in testcase if child.tag == "skipped"]
        if len(outcome_children) > 1:
            raise JUnitStructureError(
                f"JUnit report at {xml_path} has testcase {nid!r} with "
                f"{len(outcome_children)} failure/error outcome elements; a "
                "testcase has exactly one outcome (fail closed)"
            )
        if outcome_children and skip_children:
            raise JUnitStructureError(
                f"JUnit report at {xml_path} has testcase {nid!r} with both "
                "a failure/error outcome and a <skipped> element; these are "
                "mutually exclusive outcomes (fail closed)"
            )
        if len(skip_children) > 1:
            raise JUnitStructureError(
                f"JUnit report at {xml_path} has testcase {nid!r} with "
                f"{len(skip_children)} <skipped> elements; a testcase has "
                "exactly one outcome (fail closed)"
            )
        outcome_el = outcome_children[0] if outcome_children else None
        has_skip = bool(skip_children)
        if outcome_el is not None:
            fingerprint = _normalize_failure_fingerprint(_failure_outcome_content(outcome_el))
            if outcome_el.tag == "failure":
                failures[nid] = fingerprint
                parsed_failure_count += 1
            else:
                errors[nid] = fingerprint
                parsed_error_count += 1
        if has_skip:
            skips.add(nid)
            parsed_skipped_count += 1

    if declared_failures != parsed_failure_count:
        raise JUnitStructureError(
            f"JUnit report at {xml_path} declares failures={declared_failures} "
            f"but only {parsed_failure_count} <failure> elements were parsed"
        )

    # Collection errors appear as <testsuite errors="N"> with no testcase,
    # or as top-level <error> elements. These add to parsed_error_count.
    for testsuite in testsuites:
        errors_attr = testsuite.get("errors")
        if errors_attr and errors_attr != "0":
            # Each non-testcase <error> child is a collection-level problem.
            for child in testsuite:
                if child.tag == "error":
                    collection_errors.append(
                        f"{testsuite.get('name', '<unknown>')}: {child.get('message', '')}"
                    )
                    parsed_error_count += 1

    if declared_errors != parsed_error_count:
        raise JUnitStructureError(
            f"JUnit report at {xml_path} declares errors={declared_errors} "
            f"but only {parsed_error_count} <error> elements were parsed"
        )

    if declared_skipped != parsed_skipped_count:
        raise JUnitStructureError(
            f"JUnit report at {xml_path} declares skipped={declared_skipped} "
            f"but only {parsed_skipped_count} <skipped> elements were parsed"
        )

    return failures, errors, skips, collection_errors


def parse_coverage_line_percent(coverage_xml_path: Path) -> float:
    """Extract overall line-rate (0..100) from a coverage.py XML report.

    Fails closed on non-finite (NaN / ±inf) or out-of-range values: a NaN
    line-rate silently bypasses every downstream comparison (``nan < x`` is
    always False) and +inf trivially satisfies any baseline (review-t_cce76b68
    F3).
    """
    tree = ET.parse(coverage_xml_path)
    root = tree.getroot()
    line_rate = root.get("line-rate")
    if line_rate is None:
        raise ValueError(f"coverage XML at {coverage_xml_path} has no line-rate attribute")
    try:
        rate = float(line_rate)
    except ValueError as exc:
        raise ValueError(
            f"coverage XML at {coverage_xml_path} has non-numeric line-rate {line_rate!r}"
        ) from exc
    if not math.isfinite(rate):
        raise ValueError(
            f"coverage XML at {coverage_xml_path} has non-finite line-rate {line_rate!r}; "
            "refusing to compare a NaN/inf coverage figure"
        )
    percent = rate * 100.0
    if not (0.0 <= percent <= 100.0):
        raise ValueError(
            f"coverage XML at {coverage_xml_path} reports line-rate {percent:.2f}% "
            "outside the valid 0..100 range"
        )
    return percent


def compare_pytest(
    baseline: dict[str, Any],
    failures: dict[str, str],
    errors: dict[str, str],
    skips: set[str],
    collection_errors: list[str],
) -> list[str]:
    problems: list[str] = []
    if collection_errors:
        problems.append(f"pytest collection errors (fail closed): {collection_errors}")
    # known_failures entries MUST be exact ``{node_id, fingerprint}``
    # objects whose fingerprint matches the comparator's own grammar
    # (``<preview> | sha256:<64 hex>``). The legacy plain-string form and
    # fingerprintless objects silently disabled cause comparison for that
    # node — a fail-open path that let a mutated failure cause pass when
    # the checked-in baseline was stale/legacy/malformed (review-t_e03bfeff
    # run 180 P2-1). The baseline is therefore schema-validated fail-closed
    # BEFORE any comparison. Known skips stay plain node-ID strings.
    # ``<error>`` outcomes are NEVER allowlisted via known_failures — they
    # must be rejected independently (review-t_e03bfeff P2-4).
    known_failures_raw = baseline.get("pytest", {}).get("known_failures", [])
    known_failure_fingerprints = validate_known_failures(known_failures_raw)
    known_failure_nodes = set(known_failure_fingerprints)
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

    # <error> outcomes are rejected unconditionally: they indicate broken
    # test infrastructure (import errors, fixture crashes, etc.) and must
    # never be silently allowlisted (review-t_e03bfeff P2-4).
    if errors:
        problems.append(
            f"pytest <error> outcomes not in baseline (never allowlisted): {sorted(errors)}"
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
    if not isinstance(baseline_percent, (int, float)) or not math.isfinite(baseline_percent):
        return [
            f"coverage baseline has non-finite line_percent {baseline_percent!r}; "
            "refusing to compare (baseline artifact is corrupt)"
        ]
    if not (0.0 <= float(baseline_percent) <= 100.0):
        return [
            f"coverage baseline line_percent {baseline_percent!r} outside the 0..100 range; "
            "refusing to compare (baseline artifact is corrupt)"
        ]
    tolerance_raw = cov.get("noise_tolerance", 0.5)
    try:
        tolerance = float(tolerance_raw)
    except (TypeError, ValueError):
        return [
            f"coverage baseline noise_tolerance {tolerance_raw!r} is not numeric; "
            "refusing to compare (baseline artifact is corrupt)"
        ]
    if not math.isfinite(tolerance) or tolerance < 0.0:
        return [
            f"coverage baseline noise_tolerance {tolerance!r} must be a finite "
            "nonnegative number; refusing to compare (baseline artifact is corrupt)"
        ]
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
        required=True,
        help=(
            "Raw exit code of the mypy process. Required: fail-closed use "
            "demands the comparator know the tool's own verdict so it can "
            "be reconciled against the artifact contents. Values other "
            "than 0 (clean) or 1 (diagnostics found) are treated as a tool "
            "crash/config error and fail closed regardless of output."
        ),
    )
    parser.add_argument(
        "--pytest-exit-code",
        type=int,
        required=True,
        help=(
            "Raw exit code of the pytest process. Required: fail-closed use "
            "demands the comparator know the tool's own verdict so it can "
            "be reconciled against the JUnit contents. Values other than 0 "
            "(all passed) or 1 (tests failed) are treated as collection/"
            "usage errors and fail closed regardless of JUnit content."
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

    # Hard crash gate: anything other than 0/1 is a tool-level failure and
    # there is nothing meaningful to reconcile against the artifacts.
    if args.mypy_exit_code not in (0, 1):
        problems.append(
            f"mypy exited with code {args.mypy_exit_code} (tool crash/config error); "
            "only 0 (clean) or 1 (diagnostics found) are comparable"
        )
    if args.pytest_exit_code not in (0, 1):
        problems.append(
            f"pytest exited with code {args.pytest_exit_code} (collection/usage error); "
            "only 0 (all passed) or 1 (tests failed) are comparable"
        )

    try:
        failures, errors, skips, collection_errors = parse_junit_failures_and_skips(args.junit_xml)
    except ET.ParseError as exc:
        print(f"FAIL: cannot parse pytest JUnit XML: {exc}", file=sys.stderr)
        return 2
    except JUnitStructureError as exc:
        print(f"FAIL: invalid JUnit report: {exc}", file=sys.stderr)
        return 2

    mypy_text = args.mypy_output.read_text(encoding="utf-8", errors="replace")
    try:
        live_mypy = parse_mypy_output(mypy_text)
        mypy_error_occurrences = count_mypy_error_occurrences(mypy_text)
    except MypyOutputError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    # Reconcile raw exit codes with artifact semantics (review-t_cce76b68
    # F1). The process exit status and the parsed artifact must agree —
    # accepting a contradictory pair is the classic stale-artifact fail-open
    # path. Reconciliation happens BEFORE baseline comparison so a
    # contradictory run never reaches the baseline checks.
    pytest_has_failures = bool(failures) or bool(errors) or bool(collection_errors)
    if args.pytest_exit_code == 0 and pytest_has_failures:
        problems.append(
            "pytest exit code 0 (all passed) contradicts JUnit report "
            f"containing {len(failures)} failure(s), {len(errors)} error(s), and "
            f"{len(collection_errors)} collection error(s); "
            "refusing to accept a contradictory artifact (fail closed)"
        )
    if args.pytest_exit_code == 1 and not pytest_has_failures:
        problems.append(
            "pytest exit code 1 (tests failed) contradicts JUnit report "
            "containing zero parsed failures/errors; refusing to accept "
            "a contradictory artifact (fail closed)"
        )

    try:
        mypy_summary_kind, mypy_summary_error_count = parse_mypy_summary(mypy_text)
    except MypyOutputError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    if args.mypy_exit_code == 0 and mypy_summary_kind != "success":
        problems.append(
            f"mypy exit code 0 (clean) contradicts mypy output summary "
            f"kind {mypy_summary_kind!r} (expected 'success'); refusing "
            "to accept a contradictory artifact (fail closed)"
        )
    if args.mypy_exit_code == 1 and mypy_summary_kind != "errors":
        problems.append(
            f"mypy exit code 1 (diagnostics found) contradicts mypy output "
            f"summary kind {mypy_summary_kind!r} (expected 'errors'); "
            "refusing to accept a contradictory artifact (fail closed)"
        )

    # Reconcile the mypy summary's declared error count against the number
    # of parsed error *occurrences* (review-t_e03bfeff P1-2 and follow-up).
    # Occurrences count every error row before identity dedup: two
    # same-message diagnostics at different lines are one identity for
    # baseline comparison but two occurrences for summary reconciliation.
    # A summary claiming N errors with zero parsed rows remains a
    # contradiction (truncation) and fails closed. A ``Found 0 errors``
    # summary with exit code 1 is likewise contradictory: exit 1 means
    # diagnostics were found, so declaring zero errors while exiting 1 is
    # a fail-open path that must be rejected (review-t_e03bfeff P1-3).
    if mypy_summary_error_count is not None and mypy_summary_error_count != mypy_error_occurrences:
        problems.append(
            f"mypy summary declares {mypy_summary_error_count} error(s) "
            f"but {mypy_error_occurrences} diagnostic occurrence(s) were parsed; "
            "refusing to accept a contradictory artifact (fail closed)"
        )
    if (
        args.mypy_exit_code == 1
        and mypy_summary_error_count is not None
        and mypy_summary_error_count == 0
    ):
        problems.append(
            "mypy exit code 1 (diagnostics found) contradicts mypy output "
            "summary 'Found 0 errors'; refusing to accept a contradictory "
            "artifact (fail closed)"
        )

    try:
        problems.extend(compare_pytest(baseline, failures, errors, skips, collection_errors))
    except BaselineSchemaError as exc:
        print(f"FAIL: corrupt baseline: {exc}", file=sys.stderr)
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
