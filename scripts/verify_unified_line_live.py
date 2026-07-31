"""Live end-to-end verifier for the landed unified interest line.

The coordinator starts a real backend against an initialized, isolated project
root, then runs this script. The verifier submits exactly three recommendation
feedback events (two dislikes and one like), watches the on-disk audit/state
surfaces, and proves that the feedback was consumed by the unified pipeline
rather than the retired legacy overwrite path.

This file is intentionally not an ordinary pytest: it performs localhost HTTP
requests and can trigger real provider work. Unit tests import only the pure
``compute_ledger_delta`` and ``select_feedback_cards`` helpers.

Usage:
    OPENBILICLAW_PROJECT_ROOT=/path/to/initialized/isolated/root \
        python scripts/verify_unified_line_live.py \
        --base http://127.0.0.1:8420 [--server-log /path/to/server.log]

Exit codes: 0 all checks passed, 1 a live check/request failed, 2 bad input.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Acceptance thresholds come directly from this verifier's live-test contract:
# wait at most 300 seconds, while requiring the ledger row to land well below
# the scheduler's old 600-second INTEREST minimum.
POLL_TIMEOUT_SECONDS = 300.0
LATENCY_LIMIT_SECONDS = 600.0
POLL_INTERVAL_SECONDS = 2.0
READ_HTTP_TIMEOUT_SECONDS = 30.0
# ``POST /api/feedback`` now returns before the preference LLM runs (the
# pipeline ingest is scheduled as a background task with a 300-second provider
# budget). Keep a transport margin so the background analysis and the evidence
# poll still complete within this window.
FEEDBACK_HTTP_TIMEOUT_SECONDS = 330.0
# The shipped feedback scheduler debounces for 5 seconds. Once the core evidence
# is visible, observe two full debounce windows before declaring the appended
# log interval quiet; a first-run migration uses its explicit completion log as
# the start of the same quiet window.
LOG_SETTLE_SECONDS = 10.0

PROBE_KEYWORD = "量子速读"
DISLIKE_NOTE = "统一兴趣线落地主干实测：我明确不喜欢“量子速读”，请把量子速读列为长期避雷主题。"
LIKE_NOTE = "统一兴趣线落地主干实测：这条内容值得继续推荐。"

PIPELINE_LEDGER_KEY = ("pipeline_layer_update", "feedback")
LEGACY_WRITE_POINT = "feedback_preference_overwrite"

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_BAD_INPUT = 2

LedgerKey = tuple[str, str]
LedgerCounts = dict[LedgerKey, int]


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested with canned inputs; no sockets or file access)
# ---------------------------------------------------------------------------


def compute_ledger_delta(
    before: Mapping[LedgerKey, int],
    after: Mapping[LedgerKey, int],
) -> LedgerCounts:
    """Return exact ``after - before`` counts for every changed ledger key."""
    delta: LedgerCounts = {}
    for key in sorted(set(before) | set(after)):
        difference = int(after.get(key, 0)) - int(before.get(key, 0))
        if difference:
            delta[key] = difference
    return delta


def select_feedback_cards(items: object, limit: int = 3) -> list[dict[str, Any]]:
    """Prefer unique cards without feedback, then top up from remaining cards."""
    if not isinstance(items, list) or limit <= 0:
        return []

    fresh: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        recommendation_id = item.get("id")
        if (
            not isinstance(recommendation_id, int)
            or isinstance(recommendation_id, bool)
            or recommendation_id <= 0
            or recommendation_id in seen_ids
        ):
            continue
        seen_ids.add(recommendation_id)
        card = dict(item)
        if str(item.get("feedback_type") or "").strip():
            fallback.append(card)
        else:
            fresh.append(card)
    return [*fresh, *fallback][:limit]


# ---------------------------------------------------------------------------
# Live runner (coordinator-only; never called by the unit tests)
# ---------------------------------------------------------------------------


class VerificationInputError(ValueError):
    """The verifier cannot safely start because its input is invalid."""


class VerificationRuntimeError(RuntimeError):
    """A live request or observation failed before checks could complete."""


@dataclass(frozen=True)
class CheckResult:
    """One live assertion and the observation behind its verdict."""

    name: str
    passed: bool
    observed: str
    threshold: str
    skipped: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "skipped": self.skipped,
            "observed": self.observed,
            "threshold": self.threshold,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class LogSnapshot:
    """Identity and byte boundary of the server log before feedback."""

    path: Path
    device: int
    inode: int
    size: int


@dataclass(frozen=True)
class LocalRuntime:
    """Resolved local paths and config values the target backend must share."""

    data_path: Path
    log_path: Path
    data_dir: str
    feedback_batch_threshold: int


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        required=True,
        help="running local backend origin, for example http://127.0.0.1:8420",
    )
    parser.add_argument(
        "--server-log",
        type=Path,
        help="optional configured backend log; only bytes appended during this run are checked",
    )
    return parser.parse_args(list(argv))


def _validated_base(raw_base: str) -> str:
    base = raw_base.strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "http" or not parsed.hostname:
        raise VerificationInputError("--base must be an absolute http:// loopback origin.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise VerificationInputError("--base must not contain credentials, a query, or a fragment.")
    if parsed.path not in {"", "/"}:
        raise VerificationInputError("--base must be an origin without an additional path.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise VerificationInputError(f"--base has an invalid port: {exc}") from exc
    if port is None:
        raise VerificationInputError("--base must include the backend port.")
    hostname = parsed.hostname.casefold()
    if hostname != "localhost":
        try:
            if not ipaddress.ip_address(hostname).is_loopback:
                raise VerificationInputError("--base must point to a loopback address.")
        except ValueError as exc:
            raise VerificationInputError("--base must point to a loopback address.") from exc
    return base


def _project_root_from_env() -> Path:
    raw_root = os.environ.get("OPENBILICLAW_PROJECT_ROOT", "").strip()
    if not raw_root:
        raise VerificationInputError(
            "OPENBILICLAW_PROJECT_ROOT must point at an initialized isolated root."
        )
    root = Path(raw_root).expanduser().resolve()
    if not root.is_dir():
        raise VerificationInputError(f"OPENBILICLAW_PROJECT_ROOT does not exist: {root}")
    return root


def _local_runtime(root: Path) -> LocalRuntime:
    from openbiliclaw.config import load_config

    try:
        config = load_config()
    except Exception as exc:
        raise VerificationInputError(f"Cannot load isolated-root config: {exc}") from exc
    data_path = config.data_path.expanduser().resolve()
    if not data_path.is_relative_to(root):
        raise VerificationInputError(
            f"Configured data directory escapes the isolated root: {data_path}"
        )
    if config.scheduler.unified_interest_line is not True:
        raise VerificationInputError(
            "scheduler.unified_interest_line must be true for this live verifier."
        )
    threshold = config.scheduler.feedback_batch_threshold
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold != 3:
        raise VerificationInputError(
            "scheduler.feedback_batch_threshold must be the landed default 3 "
            f"for this three-feedback verifier; got {threshold!r}."
        )
    return LocalRuntime(
        data_path=data_path,
        log_path=config.logging.file_path.expanduser().resolve(),
        data_dir=str(config.data_dir),
        feedback_batch_threshold=threshold,
    )


def _read_json_object(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    if not path.exists():
        if missing_ok:
            return {}
        raise VerificationInputError(f"Required initialized-state file is missing: {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationRuntimeError(f"Cannot read JSON state {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise VerificationRuntimeError(f"JSON state is not an object: {path}")
    return loaded


def _marker(state: Mapping[str, Any]) -> str:
    return str(state.get("unified_interest_line_migrated_at") or "").strip()


def _matching_dislikes(preference: Mapping[str, Any], keyword: str) -> list[str]:
    raw_dislikes = preference.get("disliked_topics")
    if not isinstance(raw_dislikes, list):
        return []
    normalized_keyword = keyword.casefold()
    return [
        text
        for item in raw_dislikes
        if (text := str(item).strip()) and normalized_keyword in text.casefold()
    ]


def _snapshot_ledger_counts(database_path: Path) -> LedgerCounts:
    if not database_path.is_file():
        raise VerificationInputError(f"Initialized database is missing: {database_path}")
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
            rows = connection.execute(
                """
                SELECT write_point, source, COUNT(*)
                FROM profile_update_ledger
                GROUP BY write_point, source
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise VerificationRuntimeError(
            f"Cannot snapshot profile_update_ledger in {database_path}: {exc}"
        ) from exc
    return {
        (str(write_point or ""), str(source or "")): int(count)
        for write_point, source, count in rows
    }


def _snapshot_recommendation_cards(database_path: Path) -> list[dict[str, Any]]:
    """Newest recommendation IDs for fallback selection and backend binding."""
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
            rows = connection.execute(
                """
                SELECT id, bvid, COALESCE(feedback_type, '')
                FROM recommendations
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise VerificationRuntimeError(
            f"Cannot snapshot recommendations in {database_path}: {exc}"
        ) from exc
    return [
        {
            "id": int(recommendation_id),
            "bvid": str(bvid or ""),
            "feedback_type": str(feedback_type or ""),
            "_verification_source": "database_fallback",
        }
        for recommendation_id, bvid, feedback_type in rows
    ]


def _bind_api_cards_to_database(
    api_items: list[object],
    database_cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fail before POST if the local API cards do not belong to the watched DB."""
    database_by_id = {int(card["id"]): card for card in database_cards}
    bound: list[dict[str, Any]] = []
    for item in api_items:
        if not isinstance(item, dict):
            continue
        recommendation_id = item.get("id")
        if (
            not isinstance(recommendation_id, int)
            or isinstance(recommendation_id, bool)
            or recommendation_id <= 0
        ):
            continue
        database_card = database_by_id.get(recommendation_id)
        if database_card is None:
            raise VerificationInputError(
                "Backend/root identity mismatch: API recommendation "
                f"{recommendation_id} is absent from the isolated database."
            )
        api_bvid = str(item.get("bvid") or "")
        if api_bvid != str(database_card.get("bvid") or ""):
            raise VerificationInputError(
                "Backend/root identity mismatch: API recommendation "
                f"{recommendation_id} has a different content identity."
            )
        api_feedback = str(item.get("feedback_type") or "").strip()
        database_feedback = str(database_card.get("feedback_type") or "").strip()
        if api_feedback != database_feedback:
            raise VerificationInputError(
                "Backend/root identity mismatch: API recommendation "
                f"{recommendation_id} has feedback={api_feedback!r}, "
                f"database has {database_feedback!r}."
            )
        card = dict(item)
        card["_verification_source"] = "api"
        bound.append(card)
    return bound


def _write_point_count(counts: Mapping[LedgerKey, int], write_point: str) -> int:
    return sum(
        count for (row_write_point, _), count in counts.items() if row_write_point == write_point
    )


def _serialized_counts(counts: Mapping[LedgerKey, int]) -> list[dict[str, Any]]:
    return [
        {"write_point": write_point, "source": source, "count": count}
        for (write_point, source), count in sorted(counts.items())
    ]


def _snapshot_log(path: Path) -> LogSnapshot:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise VerificationInputError(f"--server-log is not a readable file: {resolved}")
    try:
        stat = resolved.stat()
    except OSError as exc:
        raise VerificationInputError(f"Cannot stat --server-log {resolved}: {exc}") from exc
    return LogSnapshot(
        path=resolved,
        device=int(stat.st_dev),
        inode=int(stat.st_ino),
        size=int(stat.st_size),
    )


def _read_log_delta(snapshot: LogSnapshot) -> tuple[str | None, str]:
    try:
        stat = snapshot.path.stat()
    except OSError as exc:
        return None, f"log unavailable after feedback: {exc}"
    if int(stat.st_dev) != snapshot.device or int(stat.st_ino) != snapshot.inode:
        return None, "log rotated; appended feedback interval cannot be proven clean"
    if int(stat.st_size) < snapshot.size:
        return None, "log truncated; appended feedback interval cannot be proven clean"
    try:
        with snapshot.path.open("rb") as handle:
            handle.seek(snapshot.size)
            appended = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return None, f"cannot read appended log bytes: {exc}"
    return appended, ""


def _feedback_error_lines(appended: str) -> list[dict[str, Any]]:
    # Project file logs use an uppercase ``%(levelname)s`` token. Keeping this
    # case-sensitive avoids mistaking INFO prose such as "feedback error rate"
    # for an ERROR-level record.
    error_pattern = re.compile(r"\bERROR\b")
    return [
        {"line": index, "text": line[:500]}
        for index, line in enumerate(appended.splitlines(), start=1)
        if error_pattern.search(line) and "feedback" in line.casefold()
    ]


def _scan_log_delta(snapshot: LogSnapshot) -> tuple[bool, str, dict[str, Any]]:
    appended, read_error = _read_log_delta(snapshot)
    if appended is None:
        return False, read_error, {"error": read_error}
    matches = _feedback_error_lines(appended)
    if matches:
        return (
            False,
            f"{len(matches)} appended ERROR-level line(s) mention feedback",
            {"matches": matches[:20], "match_count": len(matches)},
        )
    return True, "0 appended ERROR-level lines mention feedback", {"match_count": 0}


def _request_json(
    opener: OpenerDirector,
    *,
    method: str,
    url: str,
    payload: Mapping[str, object] | None = None,
    timeout_seconds: float = READ_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    body = None
    headers = {
        "Accept": "application/json",
        "X-OBC-Auth": "1",
    }
    if payload is not None:
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = response.getcode()
            response_body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise VerificationRuntimeError(
            f"{method} {url} returned HTTP {exc.code}: {detail}"
        ) from exc
    except (OSError, URLError) as exc:
        raise VerificationRuntimeError(f"{method} {url} failed: {exc}") from exc
    if status < 200 or status >= 300:
        raise VerificationRuntimeError(f"{method} {url} returned unexpected HTTP {status}.")
    try:
        decoded = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise VerificationRuntimeError(f"{method} {url} returned invalid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise VerificationRuntimeError(f"{method} {url} did not return a JSON object.")
    return decoded


def _verify_backend_identity(
    opener: OpenerDirector,
    *,
    base: str,
    local_runtime: LocalRuntime,
) -> dict[str, Any]:
    """Bind the loopback backend to the isolated root before mutating it."""
    server_config = _request_json(
        opener,
        method="GET",
        url=f"{base}/api/config",
    )
    logging_config = server_config.get("logging")
    scheduler_config = server_config.get("scheduler")
    if not isinstance(logging_config, dict) or not isinstance(scheduler_config, dict):
        raise VerificationInputError(
            "GET /api/config lacks logging/scheduler identity fields; refusing feedback POSTs."
        )
    raw_server_log_path = str(logging_config.get("file_path") or "").strip()
    server_log_path = Path(raw_server_log_path).expanduser()
    if not raw_server_log_path or not server_log_path.is_absolute():
        raise VerificationInputError(
            "GET /api/config did not expose an absolute logging.file_path; "
            "cannot bind the backend to the isolated root."
        )
    server_log_path = server_log_path.resolve()
    server_data_dir = str(server_config.get("data_dir") or "")
    server_threshold = scheduler_config.get("feedback_batch_threshold")
    mismatches: list[str] = []
    if server_log_path != local_runtime.log_path:
        mismatches.append(f"log path server={server_log_path}, isolated={local_runtime.log_path}")
    if server_data_dir != local_runtime.data_dir:
        mismatches.append(
            f"data_dir server={server_data_dir!r}, isolated={local_runtime.data_dir!r}"
        )
    if server_threshold != local_runtime.feedback_batch_threshold:
        mismatches.append(
            "feedback_batch_threshold "
            f"server={server_threshold!r}, isolated={local_runtime.feedback_batch_threshold!r}"
        )
    if mismatches:
        raise VerificationInputError(
            "Loopback backend does not match OPENBILICLAW_PROJECT_ROOT: " + "; ".join(mismatches)
        )
    return {
        "server_log_path": str(server_log_path),
        "data_dir": server_data_dir,
        "feedback_batch_threshold": server_threshold,
    }


def _submit_feedback(
    opener: OpenerDirector,
    *,
    base: str,
    recommendation_id: int,
    feedback_type: str,
    note: str,
) -> dict[str, Any]:
    response = _request_json(
        opener,
        method="POST",
        url=f"{base}/api/feedback",
        payload={
            "recommendation_id": recommendation_id,
            "feedback_type": feedback_type,
            "note": note,
        },
        timeout_seconds=FEEDBACK_HTTP_TIMEOUT_SECONDS,
    )
    if (
        response.get("ok") is not True
        or response.get("recommendation_id") != recommendation_id
        or response.get("feedback_type") != feedback_type
    ):
        raise VerificationRuntimeError(
            f"POST /api/feedback returned an inconsistent response: {response}"
        )
    return response


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _render_check(check: CheckResult) -> str:
    status = "SKIP" if check.skipped else ("PASS" if check.passed else "FAIL")
    return f"[{status}] {check.name} — {check.observed} (threshold: {check.threshold})"


def _run_live(args: argparse.Namespace) -> tuple[int, list[CheckResult], dict[str, Any]]:
    root = _project_root_from_env()
    base = _validated_base(str(args.base))
    local_runtime = _local_runtime(root)
    data_path = local_runtime.data_path
    database_path = data_path / "openbiliclaw.db"
    feedback_state_path = data_path / "memory" / "feedback_state.json"
    preference_path = data_path / "memory" / "preference.json"

    ledger_before = _snapshot_ledger_counts(database_path)
    state_before = _read_json_object(feedback_state_path, missing_ok=True)
    preference_before = _read_json_object(preference_path, missing_ok=True)
    matches_before = _matching_dislikes(preference_before, PROBE_KEYWORD)
    if matches_before:
        raise VerificationInputError(
            f'Probe keyword "{PROBE_KEYWORD}" already exists in disliked_topics: {matches_before}'
        )

    log_snapshot: LogSnapshot | None = None
    if args.server_log is not None:
        requested_log_path = args.server_log.expanduser().resolve()
        if requested_log_path != local_runtime.log_path:
            raise VerificationInputError(
                "--server-log must be the isolated backend's configured log path "
                f"{local_runtime.log_path}; got {requested_log_path}."
            )
        log_snapshot = _snapshot_log(requested_log_path)

    opener = build_opener(ProxyHandler({}))
    backend_identity = _verify_backend_identity(
        opener,
        base=base,
        local_runtime=local_runtime,
    )
    recommendations = _request_json(
        opener,
        method="GET",
        url=f"{base}/api/recommendations",
    )
    raw_api_items = recommendations.get("items")
    if not isinstance(raw_api_items, list):
        raise VerificationRuntimeError("GET /api/recommendations did not return an items list.")
    database_cards = _snapshot_recommendation_cards(database_path)
    api_cards = _bind_api_cards_to_database(raw_api_items, database_cards)
    cards = select_feedback_cards([*api_cards, *database_cards], limit=3)
    if len(cards) < 3:
        raise VerificationInputError(
            "The API plus isolated recommendation history exposed only "
            f"{len(cards)} valid unique card(s); need 3."
        )

    actions = [
        ("dislike", DISLIKE_NOTE),
        ("dislike", DISLIKE_NOTE),
        ("like", LIKE_NOTE),
    ]
    started = time.monotonic()
    feedback_responses: list[dict[str, Any]] = []
    selected_cards: list[dict[str, Any]] = []
    for card, (feedback_type, note) in zip(cards, actions, strict=True):
        recommendation_id = int(card["id"])
        feedback_responses.append(
            _submit_feedback(
                opener,
                base=base,
                recommendation_id=recommendation_id,
                feedback_type=feedback_type,
                note=note,
            )
        )
        selected_cards.append(
            {
                "id": recommendation_id,
                "initial_feedback_type": str(card.get("feedback_type") or ""),
                "submitted_feedback_type": feedback_type,
                "selection_source": str(card.get("_verification_source") or "unknown"),
            }
        )

    # Request time is part of the end-to-end latency measurement above, but the
    # contract grants a full 300-second evidence poll *after* all POSTs return.
    posts_finished_at = time.monotonic()
    deadline = posts_finished_at + POLL_TIMEOUT_SECONDS
    log_settle_not_before = posts_finished_at + LOG_SETTLE_SECONDS
    ledger_after = ledger_before
    state_after = state_before
    preference_after = preference_before
    marker_after = _marker(state_after)
    matches_after: list[str] = []
    pipeline_elapsed_seconds: float | None = None
    last_state_read_error = ""
    last_preference_read_error = ""
    marker_before = _marker(state_before)
    migration_log_completion_observed = bool(marker_before)
    migration_log_completion_at: float | None = None
    migration_log_read_error = ""
    feedback_log_errors_observed = False

    while True:
        ledger_after = _snapshot_ledger_counts(database_path)
        ledger_delta = compute_ledger_delta(ledger_before, ledger_after)
        if pipeline_elapsed_seconds is None and ledger_delta.get(PIPELINE_LEDGER_KEY, 0) > 0:
            pipeline_elapsed_seconds = time.monotonic() - started

        try:
            state_after = _read_json_object(feedback_state_path, missing_ok=True)
            marker_after = _marker(state_after)
            last_state_read_error = ""
        except VerificationRuntimeError as exc:
            # Both state files are written in place. A poll can observe the
            # short truncate/write window; retain the last good snapshot.
            last_state_read_error = str(exc)

        try:
            preference_after = _read_json_object(preference_path, missing_ok=True)
            matches_after = _matching_dislikes(preference_after, PROBE_KEYWORD)
            last_preference_read_error = ""
        except VerificationRuntimeError as exc:
            last_preference_read_error = str(exc)

        if log_snapshot is not None:
            appended_log, migration_log_read_error = _read_log_delta(log_snapshot)
            if appended_log is not None:
                migration_log_read_error = ""
                if (
                    not migration_log_completion_observed
                    and "Unified interest line: migrated" in appended_log
                ):
                    migration_log_completion_observed = True
                    migration_log_completion_at = time.monotonic()
                feedback_log_errors_observed = bool(_feedback_error_lines(appended_log))

        core_observations_complete = (
            pipeline_elapsed_seconds is not None and bool(marker_after) and bool(matches_after)
        )
        now = time.monotonic()
        if log_snapshot is None:
            log_interval_complete = True
        elif migration_log_read_error or feedback_log_errors_observed:
            # The final log check will fail; there is no value in extending the
            # live run after the core evidence is already complete.
            log_interval_complete = True
        elif marker_before:
            log_interval_complete = now >= log_settle_not_before
        elif migration_log_completion_at is not None:
            log_interval_complete = (
                now >= log_settle_not_before
                and now >= migration_log_completion_at + LOG_SETTLE_SECONDS
            )
        else:
            # INFO may be filtered out of a WARNING-only log. In that case keep
            # watching until the shared 300-second deadline, then scan what was
            # actually emitted without treating the missing sentinel as a gate.
            log_interval_complete = False
        if core_observations_complete and log_interval_complete:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))

    finished = time.monotonic()
    elapsed_seconds = finished - started
    ledger_delta = compute_ledger_delta(ledger_before, ledger_after)
    pipeline_rows_added = ledger_delta.get(PIPELINE_LEDGER_KEY, 0)
    legacy_before = _write_point_count(ledger_before, LEGACY_WRITE_POINT)
    legacy_after = _write_point_count(ledger_after, LEGACY_WRITE_POINT)

    checks = [
        CheckResult(
            name="pipeline feedback ledger delta",
            passed=pipeline_rows_added > 0,
            observed=f"{pipeline_rows_added:+d} pipeline_layer_update/source=feedback row(s)",
            threshold="> 0",
        ),
        CheckResult(
            name="pipeline feedback latency",
            passed=(
                pipeline_elapsed_seconds is not None
                and pipeline_elapsed_seconds < LATENCY_LIMIT_SECONDS
            ),
            observed=(
                f"{pipeline_elapsed_seconds:.3f}s"
                if pipeline_elapsed_seconds is not None
                else f"not observed within {POLL_TIMEOUT_SECONDS:.0f}s"
            ),
            threshold=f"< {LATENCY_LIMIT_SECONDS:.0f}s",
        ),
        CheckResult(
            name="retired feedback overwrite stayed idle",
            passed=legacy_after == legacy_before,
            observed=f"before={legacy_before}, after={legacy_after}, delta={legacy_after - legacy_before:+d}",
            threshold="delta = 0",
        ),
        CheckResult(
            name="unified migration marker",
            passed=bool(marker_after),
            observed=(
                f"before={_marker(state_before) or '<absent>'}, after={marker_after or '<absent>'}"
            ),
            threshold="after marker is non-empty",
            detail={"last_read_error": last_state_read_error},
        ),
        CheckResult(
            name="dislike note reached disliked_topics",
            passed=bool(matches_after),
            observed=f'keyword="{PROBE_KEYWORD}", matches={matches_after}',
            threshold="at least one substring match",
            detail={"last_read_error": last_preference_read_error},
        ),
    ]

    log_observation: dict[str, Any]
    if log_snapshot is None:
        checks.append(
            CheckResult(
                name="server log feedback errors",
                passed=True,
                skipped=True,
                observed="--server-log not provided",
                threshold="optional",
            )
        )
        log_observation = {"checked": False}
    else:
        log_passed, log_observed, log_detail = _scan_log_delta(log_snapshot)
        log_detail = {
            **log_detail,
            "migration_completion_observed": migration_log_completion_observed,
            "poll_read_error": migration_log_read_error,
            "settle_seconds": LOG_SETTLE_SECONDS,
        }
        checks.append(
            CheckResult(
                name="server log feedback errors",
                passed=log_passed,
                observed=log_observed,
                threshold="0 appended ERROR-level lines mentioning feedback",
                detail=log_detail,
            )
        )
        log_observation = {
            "checked": True,
            "path": str(log_snapshot.path),
            "start_offset": log_snapshot.size,
            **log_detail,
        }

    all_checks_passed = all(check.passed for check in checks if not check.skipped)
    exit_code = EXIT_OK if all_checks_passed else EXIT_CHECK_FAILED
    summary: dict[str, Any] = {
        "schema": "unified_interest_line_live/1",
        "baseline_commit": _git_head(PROJECT_ROOT),
        "project_root": str(root),
        "data_path": str(data_path),
        "base": base,
        "backend_identity": backend_identity,
        "probe": {
            "keyword": PROBE_KEYWORD,
            "dislike_note": DISLIKE_NOTE,
            "like_note": LIKE_NOTE,
        },
        "selected_cards": selected_cards,
        "fresh_card_count": sum(
            not str(card["initial_feedback_type"]).strip() for card in selected_cards
        ),
        "feedback_responses": feedback_responses,
        "poll_timeout_seconds": POLL_TIMEOUT_SECONDS,
        "latency_limit_seconds": LATENCY_LIMIT_SECONDS,
        "pipeline_elapsed_seconds": pipeline_elapsed_seconds,
        "elapsed_seconds": elapsed_seconds,
        "ledger": {
            "before": _serialized_counts(ledger_before),
            "after": _serialized_counts(ledger_after),
            "delta": _serialized_counts(ledger_delta),
        },
        "feedback_state": {
            "marker_before": _marker(state_before),
            "marker_after": marker_after,
        },
        "preference": {
            "keyword_present_before": False,
            "matched_disliked_topics_after": matches_after,
        },
        "server_log": log_observation,
        "checks": [check.to_dict() for check in checks],
        "all_checks_passed": all_checks_passed,
        "exit_code": exit_code,
    }
    return exit_code, checks, summary


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        exit_code, checks, summary = _run_live(args)
    except VerificationInputError as exc:
        print(f"[FAIL] input — {exc}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "schema": "unified_interest_line_live/1",
                    "all_checks_passed": False,
                    "exit_code": EXIT_BAD_INPUT,
                    "fatal_error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_BAD_INPUT
    except VerificationRuntimeError as exc:
        print(f"[FAIL] live verifier — {exc}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "schema": "unified_interest_line_live/1",
                    "all_checks_passed": False,
                    "exit_code": EXIT_CHECK_FAILED,
                    "fatal_error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_CHECK_FAILED
    except Exception as exc:
        print(f"[FAIL] unexpected verifier error — {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "schema": "unified_interest_line_live/1",
                    "all_checks_passed": False,
                    "exit_code": EXIT_CHECK_FAILED,
                    "fatal_error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_CHECK_FAILED

    for check in checks:
        print(_render_check(check), file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
