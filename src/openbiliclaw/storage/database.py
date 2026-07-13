"""SQLite database management.

Provides async-compatible SQLite operations for event logs,
content cache, and recommendation history.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import statistics
import time
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from uuid import UUID

from openbiliclaw.discovery.admission import (
    DEFAULT_ADMISSION_MIN_SCORE,
    EXPLORE_ADMISSION_MIN_SCORE,
    EXPLORE_STRATEGY,
    effective_admission_threshold,
)
from openbiliclaw.discovery.inspiration import (
    AxisRow,
    _normalize_match_text,
    derive_inspiration_axis_id,
)
from openbiliclaw.saved_sync.identity import (
    canonical_source_platform,
    content_storage_key,
    make_item_key,
)
from openbiliclaw.saved_sync.models import (
    NATIVE_SAVE_STATUSES,
    NATIVE_SAVE_TERMINAL_STATUSES,
    SavedItemInput,
    SavedListKind,
)

if TYPE_CHECKING:
    from datetime import datetime

    from openbiliclaw.saved_sync.extension_broker import ExtensionNativeSaveJob

logger = logging.getLogger(__name__)
# v0.3.62+: retry budget tightened from 5×100ms (worst-case 500ms
# blocking the asyncio event loop on lock contention) to 8×20ms
# (worst-case 160ms). Same total absolute timeout floor (~160-500ms)
# is preserved by raising attempt count; per-attempt sleep is short
# enough that even if it fires inside an async context the event-loop
# stutter is below human-perception thresholds. Most writes succeed
# on the first try anyway — this only matters under heavy concurrent
# write load (refresh tick + ingest + classify all hammering pool
# rows simultaneously). A future rewrite can move to asyncio.to_thread
# for true non-blocking DB I/O, but that's a larger refactor (every
# caller must become async) — for now this constant tweak is the
# pragmatic middle ground.
_LOCK_RETRY_ATTEMPTS = 8
_LOCK_RETRY_SLEEP_SECONDS = 0.02
_NATIVE_INTERNAL_RUNNER_PREFIX = "__openbiliclaw_"
_LEGACY_NATIVE_SAVE_RUNNER_ID = f"{_NATIVE_INTERNAL_RUNNER_PREFIX}legacy_runner__"
_EXTENSION_NATIVE_SAVE_PLATFORM_SLUGS = {
    "youtube": "yt",
    "xiaohongshu": "xhs",
    "douyin": "dy",
    "twitter": "x",
    "zhihu": "zhihu",
    "reddit": "reddit",
}
_EXTENSION_NATIVE_SAVE_HOSTS = {
    "youtube": ("youtube.com", "youtu.be"),
    "xiaohongshu": ("xiaohongshu.com",),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "twitter": ("x.com", "twitter.com"),
    "zhihu": ("zhihu.com",),
    "reddit": ("reddit.com", "redd.it"),
}
_EXTENSION_NATIVE_SAVE_IDENTITY_QUERY_FIELDS = {
    "youtube": frozenset({"v"}),
    "xiaohongshu": frozenset({"xsec_token", "xsec_source"}),
}
_EXTENSION_NATIVE_SAVE_ACTIONS = frozenset({"favorite", "watch_later"})
_EXTENSION_NATIVE_SAVE_RESULT_STATUSES = frozenset(
    {"synced", "already_synced", "login_required", "rate_limited", "unsupported", "failed"}
)
_EXTENSION_NATIVE_SAVE_SLUGS = frozenset(_EXTENSION_NATIVE_SAVE_PLATFORM_SLUGS.values())
_EXTENSION_NATIVE_SAVE_RESULT_MESSAGES = {
    ("synced", ""): "",
    ("already_synced", ""): "",
    ("login_required", ""): "Platform login required",
    ("rate_limited", ""): "Platform native save rate limited",
    ("unsupported", "unsupported_content_type"): (
        "Content type is unsupported for platform native save"
    ),
    ("failed", "native_save_failed"): "Platform native save failed",
    ("failed", "native_save_timeout"): "Platform native-save task timed out",
    ("failed", "native_content_not_ready"): "Platform content did not become ready",
    ("failed", "native_control_not_found"): "Platform save control was not found",
    ("failed", "native_dialog_not_opened"): "Platform save dialog did not open",
    ("failed", "native_target_not_found"): "Platform save target was not found uniquely",
    ("failed", "native_request_rejected"): "Platform native-save request was rejected",
    ("failed", "native_confirmation_not_observed"): (
        "Platform native-save confirmation was not observed"
    ),
}
_BVID_PATTERN = re.compile(r"(BV[0-9A-Za-z]+)")
_LOCAL_EVIDENCE_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_VIEW_CONTENT_ID_METADATA_KEYS = (
    "content_id",
    "bvid",
    "note_id",
    "aweme_id",
    "video_id",
    "yt_video_id",
    "post_id",
)
_KEYWORD_KIND_REGULAR = "regular"
_KEYWORD_KIND_EXPLORE = "explore"
_KEYWORD_KINDS = {_KEYWORD_KIND_REGULAR, _KEYWORD_KIND_EXPLORE}
_DISCOVERY_KEYWORD_METADATA_COLUMNS = {
    "aspect_id": "TEXT NOT NULL DEFAULT ''",
    "inspiration_backend": "TEXT NOT NULL DEFAULT ''",
    "inspiration_id": "TEXT NOT NULL DEFAULT ''",
    "inspiration_terms": "TEXT NOT NULL DEFAULT ''",
    "expansion_id": "TEXT NOT NULL DEFAULT ''",
    "expansion_label": "TEXT NOT NULL DEFAULT ''",
    "angle_id": "TEXT NOT NULL DEFAULT ''",
    "angle_label": "TEXT NOT NULL DEFAULT ''",
    "query_kind": "TEXT NOT NULL DEFAULT ''",
    "source_domain": "TEXT NOT NULL DEFAULT ''",
    "source_interest": "TEXT NOT NULL DEFAULT ''",
    "generation_reason": "TEXT NOT NULL DEFAULT ''",
    "normalized_keyword": "TEXT NOT NULL DEFAULT ''",
    "grounding_source": "TEXT NOT NULL DEFAULT ''",
}
# Yield-learning columns bolted onto ``discovery_inspiration_axis`` after the
# table shipped — added tolerantly via ADD COLUMN so pre-existing dbs upgrade
# in place (mirrors ``_DISCOVERY_KEYWORD_METADATA_COLUMNS``).
_DISCOVERY_INSPIRATION_AXIS_YIELD_COLUMNS = {
    "window_uses": "INTEGER NOT NULL DEFAULT 0",
    "yield_backfilled_at": "TEXT",
}
# discovery_keywords statuses meaning the keyword was actually leased for a
# fetch (it left 'pending'). 'pending' (never leased) and 'expired' (a stale
# digest superseded a still-pending row) were never consumed, so neither
# counts toward an axis's ``window_uses``. Locked against the status machine
# documented above ``insert_pending_keywords``.
_INSPIRATION_CONSUMED_KEYWORD_STATUSES = frozenset({"claimed", "executing", "used", "failed"})
_INSPIRATION_AXIS_ACTIVE_CAP = 16
_INSPIRATION_AXIS_EXPLORATION_PRIOR = 0.3
# Lifecycle thresholds (Phase 2 Part B). Retirement keys on the backfilled
# ``window_uses`` (keywords actually consumed), NOT the selection-bookkeeping
# ``use_count``: 5 consumption chances with a post-backfill score below 0.08
# (≈ zero admissions, e.g. 0.3/6 = 0.05) means the axis earned its exit.
_INSPIRATION_AXIS_RETIRE_MIN_WINDOW_USES = 5
_INSPIRATION_AXIS_RETIRE_YIELD_SCORE = 0.08
_INSPIRATION_AXIS_PURGE_AFTER_DAYS = 90
_INSPIRATION_AXIS_FRESHNESS_SCALE_DAYS = 30.0
_INSPIRATION_AXIS_KIND_ROTATION = (
    "subgenre",
    "creator_lens",
    "hands_on",
    "anchor",
    "community_vocab",
    "event",
    "method",
)
_INSPIRATION_AXIS_KIND_RANK = {
    axis_kind: index for index, axis_kind in enumerate(_INSPIRATION_AXIS_KIND_ROTATION)
}


def _normalize_keyword_kind(value: object) -> str:
    kind = str(value or "").strip().lower()
    return kind if kind in _KEYWORD_KINDS else _KEYWORD_KIND_REGULAR


def _escape_like_term(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _local_evidence_tokens(query: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[\s,，。:：/|]+", query) if len(part.strip()) >= 2]
    if not parts:
        parts = [query]

    tokens: list[str] = []
    for part in parts:
        tokens.append(part)
        if len(part) >= 4 and _LOCAL_EVIDENCE_CJK_RE.search(part):
            tokens.extend(part[index : index + 2] for index in range(len(part) - 1))

    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _unique_clean_strings(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _json_array(values: Sequence[object] | None) -> str:
    return json.dumps(_unique_clean_strings(values or ()), ensure_ascii=False)


def _load_json_array(value: object) -> list[str]:
    if value is None:
        return []
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return _unique_clean_strings(loaded)


def _json_array_union(existing: object, incoming: Sequence[object]) -> str:
    return _json_array([*_load_json_array(existing), *incoming])


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_axis_datetime(value: object) -> datetime | None:
    from datetime import UTC, datetime

    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _axis_datetime_timestamp(value: object) -> float:
    parsed = _parse_axis_datetime(value)
    return parsed.timestamp() if parsed is not None else 0.0


def _axis_now_utc(now: datetime) -> datetime:
    from datetime import UTC

    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _axis_freshness(row: sqlite3.Row, now: datetime) -> float:
    refreshed_at = _parse_axis_datetime(row["last_refreshed_at"])
    if refreshed_at is None:
        return 0.0
    age_days = max(0.0, (_axis_now_utc(now) - refreshed_at).total_seconds() / 86400.0)
    return 1.0 / (1.0 + (age_days / _INSPIRATION_AXIS_FRESHNESS_SCALE_DAYS))


def _axis_kind_rank(value: object) -> int:
    return _INSPIRATION_AXIS_KIND_RANK.get(
        str(value or "").strip(),
        len(_INSPIRATION_AXIS_KIND_RANK),
    )


def _axis_effective_score(row: sqlite3.Row) -> float:
    """Return the ranking score with a *conditional* exploration prior floor.

    The prior only protects axes that have never been consumed
    (``window_uses == 0`` — genuine exploration). Once an axis has produced
    keywords that were consumed, it ranks on its real ``yield_score`` so a
    proven-bad axis (e.g. 5 uses / 0 admissions → 0.05) sinks below an unused
    one (0.3) instead of being floored back up to parity.
    """

    yield_score = _metric_float(row["yield_score"])
    if _metric_int(row["window_uses"]) > 0:
        return yield_score
    return max(yield_score, _INSPIRATION_AXIS_EXPLORATION_PRIOR)


def _axis_list_sort_key(row: sqlite3.Row, now: datetime) -> tuple[float, float, int, int, str]:
    score = _axis_freshness(row, now) * _axis_effective_score(row)
    return (
        -score,
        -_axis_datetime_timestamp(row["last_refreshed_at"]),
        _metric_int(row["use_count"]),
        _axis_kind_rank(row["axis_kind"]),
        str(row["axis_label"]),
    )


def _axis_cap_sort_key(row: sqlite3.Row) -> tuple[float, float, int, int, str]:
    return (
        -_axis_effective_score(row),
        -_axis_datetime_timestamp(row["last_refreshed_at"]),
        _metric_int(row["use_count"]),
        _axis_kind_rank(row["axis_kind"]),
        str(row["axis_label"]),
    )


def _axis_is_time_expired(row: sqlite3.Row, now: datetime) -> bool:
    if _metric_int(row["time_sensitive"]) <= 0:
        return False
    ttl = row["freshness_ttl_days"]
    if ttl is None:
        return False
    ttl_days = _metric_int(ttl)
    if ttl_days <= 0:
        return False
    refreshed_at = _parse_axis_datetime(row["last_refreshed_at"])
    if refreshed_at is None:
        return False
    age_seconds = (_axis_now_utc(now) - refreshed_at).total_seconds()
    return age_seconds > float(ttl_days) * 86400.0


def _attribute_inspiration_axis_id(
    *,
    angle_id: str,
    source_interest: str,
    angle_label: str,
    known_axis_ids: set[str],
) -> str | None:
    """Resolve a keyword row's owning axis id for yield attribution.

    ``angle_id`` is trusted only when it is a real axis (present in
    ``known_axis_ids``) — that guards against a legacy row whose ``angle_id``
    was set to its ``angle_label`` and merely looks id-shaped. Otherwise the id
    is re-derived from ``(source_interest, angle_label)``, matching how the axis
    itself hashes its id. Returns ``None`` when nothing is attributable.
    """

    if angle_id and angle_id in known_axis_ids:
        return angle_id
    if angle_label:
        return derive_inspiration_axis_id(source_interest, angle_label)
    return None


def _empty_interest_coverage() -> dict[str, object]:
    return {
        "generated_keyword_count": 0,
        "interest_selection_count": 0,
        "selected_keyword_count": 0,
        "candidate_count": 0,
        "candidate_share": 0.0,
        "admitted_count": 0,
        "yield_count": 0,
        "admitted_share": 0.0,
        "dominant_content_type": "",
        "dominant_content_type_share": 0.0,
        "dominant_candidate_platform": "",
        "dominant_candidate_platform_share": 0.0,
        "dominant_candidate_content_type": "",
        "dominant_candidate_content_type_share": 0.0,
        "last_interest_selected_at": "",
        "last_selected_at": "",
        "last_yielded_at": "",
    }


def _empty_keyword_cohort() -> dict[str, object]:
    return {
        "generated_keywords": 0,
        "claimed_keywords": 0,
        "claimed_rate": 0.0,
        "yield_attributed_admissions": 0,
        "admissions_per_claimed_keyword": 0.0,
        "mean_delight": 0.0,
        "distinct_topics": 0,
        "topic_diversity_per_100_admissions": 0.0,
        "claim_counts_by_day": {},
        "claim_counts_by_platform": {},
        "claim_counts_by_source_interest": {},
        "grounding_mix": {},
        "duplicate_rate_by_grounding_source": {},
    }


def _empty_interest_selection_report() -> dict[str, object]:
    return {
        "total_selected_interests": 0,
        "distinct_interests": 0,
        "by_source_interest": {},
        "by_query_kind": {},
        "last_selected_at": "",
    }


def _metric_int(value: object, default: int = 0) -> int:
    try:
        return int(cast("Any", value))
    except (TypeError, ValueError):
        return default


def _metric_float(value: object, default: float = 0.0) -> float:
    try:
        return float(cast("Any", value))
    except (TypeError, ValueError):
        return default


def _keyword_inspiration_gate(
    cohorts: dict[str, dict[str, object]],
    thresholds: Mapping[str, object],
    window_days: int,
) -> dict[str, object]:
    inspiration = cohorts.get("inspiration", {})
    merged = cohorts.get("merged", {})
    min_days = _metric_int(thresholds["min_window_days"])
    min_claimed = _metric_int(thresholds["min_inspiration_claimed_keywords"])
    claimed = _metric_int(inspiration.get("claimed_keywords", 0) or 0)
    checks = {
        "sample_floor": window_days >= min_days and claimed >= min_claimed,
        "admissions_per_claimed": False,
        "mean_delight": False,
        "topic_diversity": False,
    }
    if not checks["sample_floor"]:
        return {
            "verdict": "insufficient_sample",
            "checks": checks,
            "allowed_to_replace": False,
        }

    admission_ratio = _metric_float(thresholds["min_admissions_per_claimed_ratio"])
    delight_ratio = _metric_float(thresholds["min_mean_delight_ratio"])
    merged_admissions = _metric_float(merged.get("admissions_per_claimed_keyword", 0.0) or 0.0)
    merged_delight = _metric_float(merged.get("mean_delight", 0.0) or 0.0)
    merged_diversity = _metric_float(merged.get("topic_diversity_per_100_admissions", 0.0) or 0.0)
    inspiration_admissions = _metric_float(
        inspiration.get("admissions_per_claimed_keyword", 0.0) or 0.0
    )
    inspiration_delight = _metric_float(inspiration.get("mean_delight", 0.0) or 0.0)
    inspiration_diversity = _metric_float(
        inspiration.get("topic_diversity_per_100_admissions", 0.0) or 0.0
    )
    checks["admissions_per_claimed"] = inspiration_admissions >= merged_admissions * admission_ratio
    checks["mean_delight"] = inspiration_delight >= merged_delight * delight_ratio
    checks["topic_diversity"] = inspiration_diversity > merged_diversity
    allowed = all(bool(value) for value in checks.values())
    return {
        "verdict": "pass" if allowed else "fail",
        "checks": checks,
        "allowed_to_replace": allowed,
    }


def _metadata_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence):
        return ",".join(_unique_clean_strings(value))
    return str(value).strip()


def _normalized_keyword_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _display_interest_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _chunks(values: Sequence[str], size: int) -> list[list[str]]:
    chunk_size = max(1, int(size))
    return [list(values[index : index + chunk_size]) for index in range(0, len(values), chunk_size)]


# Mirrors recommendation.delight.DEFAULT_DELIGHT_THRESHOLD. Storage stays a
# leaf module (no openbiliclaw imports), so the value is duplicated here and
# pinned by tests/test_delight_scorer.py::test_delight_claim_threshold_floor_in_sync.
_DELIGHT_CLAIM_MIN_SCORE = 0.75
_DELIGHT_DYNAMIC_TOP_FRACTION = 0.10
_DELIGHT_DYNAMIC_MIN_SAMPLE_SIZE = 150
_DELIGHT_DYNAMIC_MIN_STDDEV = 0.08
_DELIGHT_SCORE_SYNC_EPSILON = 0.000001
_DEFAULT_ADMISSION_MIN_SCORE = DEFAULT_ADMISSION_MIN_SCORE


# Rows claimed by the surprise (delight) channel: already delivered as a
# delight, or currently delight-eligible (the pending-queue predicate). The
# regular feed's servable gate excludes them so the same content never shows
# up in both the recommendation list and the surprise tray.
def _delight_claim_guard_sql() -> str:
    return """
                  AND NOT (
                    COALESCE(delight_notified, 0) = 1
                    OR (
                      COALESCE(delight_score, 0.0) >= ?
                      AND COALESCE(delight_reason, '') != ''
                      AND COALESCE(delight_hook, '') != ''
                    )
                  )
"""


_LEGACY_STYLE_KEY_MAP: dict[str, str] = {
    "deep_dive": "deep_focus",
    "tech_analysis": "deep_focus",
    "music_analysis": "deep_focus",
    "news_brief": "quick_scan",
    "practical_guide": "hands_on",
    "tutorial_short": "hands_on",
    "game_strategy": "hands_on",
    "review_roundup": "decision_support",
    "unboxing_experience": "decision_support",
    "story_doc": "story_immersion",
    "emotional_narrative": "story_immersion",
    "true_crime": "story_immersion",
    "opinion_stand": "opinion_sparring",
    "light_chat": "social_chat",
    "lifestyle": "daily_wander",
    "fun_variety": "mood_release",
    "parody_remix": "mood_release",
    "visual_showcase": "aesthetic_browse",
    "audio_background": "ambient_companion",
    "music_live": "live_pulse",
    "live_moment": "live_pulse",
    "sports_highlight": "live_pulse",
    "sci_fact": "curiosity_spark",
}

_XHS_SOURCE_FAMILY = "xiaohongshu"
_XHS_SOURCE_PREFIXES = ("xhs-", "xhs_", "xiaohongshu")
_DOUYIN_SOURCE_FAMILY = "douyin"
_DOUYIN_SOURCE_PREFIXES = ("dy-", "dy_", "douyin")
_BILIBILI_SOURCE_FAMILY = "bilibili"
_BILIBILI_SOURCE_KEYS = ("search", "related_chain", "trending", "explore")
_YOUTUBE_SOURCE_FAMILY = "youtube"
_YOUTUBE_SOURCE_PREFIXES = ("yt-", "yt_", "youtube")
_TWITTER_SOURCE_FAMILY = "twitter"
_TWITTER_SOURCE_PREFIXES = ("x-", "x_", "twitter")
_REDDIT_SOURCE_FAMILY = "reddit"
_REDDIT_SOURCE_PREFIXES = ("reddit-", "reddit_")
_EXPLORE_HIGH_RISK_CLUSTERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "manufacturing",
        ("制造", "工艺", "工厂", "工业", "材料", "金属", "芯片", "显微", "纳米", "疲劳"),
    ),
    (
        "game_theory",
        ("博弈", "桌游", "纳什", "机制", "策略模型", "平衡性"),
    ),
)

# Schema version for migrations
_SCHEMA_VERSION = 2

_SCHEMA_SQL = """
-- Event log (behavioral data from browser extension)
CREATE TABLE IF NOT EXISTS events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type            TEXT NOT NULL,        -- click, search, scroll, comment, etc.
    url                   TEXT,
    title                 TEXT,
    context               TEXT,                 -- JSON: DOM snapshot reference, viewport, etc.
    metadata              TEXT,                 -- JSON: additional event-specific data
    -- v0.3.x event-satisfaction signal: deterministic classification
    -- written at insert time by ``classify_event_satisfaction``. NULL on
    -- pre-migration rows; consumers treat NULL as ``unknown``.
    inferred_satisfaction TEXT,                 -- "positive" | "neutral" | "negative" | "unknown"
    satisfaction_reason   TEXT,                 -- short snake_case reason; see event_format.py
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Content cache (discovered/evaluated content)
CREATE TABLE IF NOT EXISTS content_cache (
    bvid        TEXT PRIMARY KEY,
    item_key    TEXT NOT NULL DEFAULT '',
    title       TEXT,
    up_name     TEXT,
    up_mid      INTEGER,
    duration    INTEGER,
    tags        TEXT,                 -- JSON array
    topic_key   TEXT DEFAULT '',
    style_key   TEXT DEFAULT '',
    franchise_key TEXT DEFAULT '',  -- LLM IP/series; see _ensure_content_cache_topic_columns
    description TEXT,
    cover_url   TEXT,
    view_count  INTEGER DEFAULT 0,
    like_count  INTEGER DEFAULT 0,
    favorite_count INTEGER DEFAULT 0,
    collect_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    share_count INTEGER DEFAULT 0,
    danmaku_count INTEGER DEFAULT 0,
    reply_count INTEGER DEFAULT 0,
    retweet_count INTEGER DEFAULT 0,
    bookmark_count INTEGER DEFAULT 0,
    relevance_score REAL DEFAULT 0.0,
    relevance_reason TEXT DEFAULT '',
    pool_expression TEXT DEFAULT '',
    pool_topic_label TEXT DEFAULT '',
    candidate_tier TEXT DEFAULT 'primary',
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notification_sent INTEGER DEFAULT 0,
    notified_at TIMESTAMP,
    pool_status TEXT DEFAULT 'fresh',
    recommended_at TIMESTAMP,
    feedback_type TEXT,
    feedback_at TIMESTAMP,
    source      TEXT,                -- Which discovery strategy found it
    body_text   TEXT DEFAULT '',     -- Full text body for text-first sources (X tweet/thread)
    content_type TEXT DEFAULT 'video',  -- Content shape: "video"|"note"|"tweet"|"thread"
    -- P1.8 yield provenance: discovery_keywords.id that produced this row;
    -- NULL for legacy / non-search / flag-off content.
    source_keyword_id INTEGER
);

-- Unified raw discovery candidate queue.
-- Producers enqueue platform-specific raw content here; evaluators claim
-- mixed-source batches and only accepted items advance into content_cache.
CREATE TABLE IF NOT EXISTS discovery_candidates (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_key         TEXT NOT NULL UNIQUE,
    status                TEXT NOT NULL DEFAULT 'pending_eval',
    source_platform       TEXT NOT NULL DEFAULT '',
    source_strategy       TEXT NOT NULL DEFAULT '',
    source_context        TEXT NOT NULL DEFAULT '',
    content_type          TEXT NOT NULL DEFAULT 'video',
    body_text             TEXT NOT NULL DEFAULT '',
    bvid                  TEXT NOT NULL DEFAULT '',
    content_id            TEXT NOT NULL DEFAULT '',
    content_url           TEXT NOT NULL DEFAULT '',
    title                 TEXT NOT NULL DEFAULT '',
    author_name           TEXT NOT NULL DEFAULT '',
    up_name               TEXT NOT NULL DEFAULT '',
    up_mid                INTEGER NOT NULL DEFAULT 0,
    description           TEXT NOT NULL DEFAULT '',
    cover_url             TEXT NOT NULL DEFAULT '',
    duration              INTEGER NOT NULL DEFAULT 0,
    view_count            INTEGER NOT NULL DEFAULT 0,
    like_count            INTEGER NOT NULL DEFAULT 0,
    favorite_count        INTEGER NOT NULL DEFAULT 0,
    collect_count         INTEGER NOT NULL DEFAULT 0,
    comment_count         INTEGER NOT NULL DEFAULT 0,
    share_count           INTEGER NOT NULL DEFAULT 0,
    danmaku_count         INTEGER NOT NULL DEFAULT 0,
    reply_count           INTEGER NOT NULL DEFAULT 0,
    retweet_count         INTEGER NOT NULL DEFAULT 0,
    bookmark_count        INTEGER NOT NULL DEFAULT 0,
    tags                  TEXT NOT NULL DEFAULT '[]',
    candidate_tier        TEXT NOT NULL DEFAULT 'primary',
    score_threshold       REAL NOT NULL DEFAULT 0.0,
    raw_payload           TEXT NOT NULL DEFAULT '{}',
    source_keyword_id     INTEGER,
    topic_key             TEXT NOT NULL DEFAULT '',
    topic_group           TEXT NOT NULL DEFAULT '',
    style_key             TEXT NOT NULL DEFAULT '',
    franchise_key         TEXT NOT NULL DEFAULT '',
    relevance_score       REAL NOT NULL DEFAULT 0.0,
    relevance_reason      TEXT NOT NULL DEFAULT '',
    pool_expression       TEXT NOT NULL DEFAULT '',
    pool_topic_label      TEXT NOT NULL DEFAULT '',
    eval_error            TEXT NOT NULL DEFAULT '',
    eval_attempts         INTEGER NOT NULL DEFAULT 0,
    batch_eval_attempts   INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    claimed_at            TIMESTAMP,
    evaluated_at          TIMESTAMP,
    cached_at             TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_status_seen
    ON discovery_candidates(status, last_seen_at, id);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_source_status
    ON discovery_candidates(source_platform, status);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_content_id
    ON discovery_candidates(source_platform, content_id);

-- Recommendation history
CREATE TABLE IF NOT EXISTS recommendations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid        TEXT NOT NULL,
    item_key    TEXT NOT NULL DEFAULT '',
    expression  TEXT,                -- Friend-style recommendation text
    topic       TEXT,                -- Personal topic label
    confidence  REAL DEFAULT 0.0,
    presented   INTEGER DEFAULT 0,   -- Boolean
    feedback    TEXT,                -- User feedback (like/dislike/comment)
    feedback_type TEXT,
    feedback_note TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    presented_at TIMESTAMP,
    feedback_at TIMESTAMP,
    FOREIGN KEY (bvid) REFERENCES content_cache(bvid)
);

-- Durable popup chat turns.  These let the side panel recover in-flight
-- and completed replies after Chrome reloads or discards the panel page.
CREATE TABLE IF NOT EXISTS chat_turns (
    turn_id       TEXT PRIMARY KEY,
    session       TEXT NOT NULL DEFAULT 'popup',
    scope         TEXT NOT NULL DEFAULT 'chat',
    subject_id    TEXT NOT NULL DEFAULT '',
    subject_title TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    reply         TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session_created
    ON chat_turns(session, created_at, turn_id);
CREATE INDEX IF NOT EXISTS idx_chat_turns_scope_subject
    ON chat_turns(scope, subject_id, created_at);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- Per-call LLM usage ledger. Populated by ``UsageRecorder`` after every
-- successful provider response. Used by ``openbiliclaw cost`` to print
-- daily spend summaries and by future per-module attribution work.
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    provider TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    caller TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    -- v0.3.28+: portion of prompt_tokens served from provider-side
    -- prompt cache. Always <= prompt_tokens. 0 means cache miss / no
    -- caching. Used to compute cache hit rate per caller.
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_cny REAL NOT NULL DEFAULT 0.0,
    success INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_timestamp ON llm_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_usage_provider ON llm_usage(provider, model);
"""


def _pool_source_family(source: object, source_platform: object = "") -> str:
    """Return the source family key used by pool share accounting."""
    platform = str(source_platform or "").strip().lower()
    raw_source = str(source or "").strip()
    source_key = raw_source.lower()
    if platform in {_XHS_SOURCE_FAMILY, "xhs"} or source_key.startswith(_XHS_SOURCE_PREFIXES):
        return _XHS_SOURCE_FAMILY
    if platform in {_DOUYIN_SOURCE_FAMILY, "dy"} or source_key.startswith(_DOUYIN_SOURCE_PREFIXES):
        return _DOUYIN_SOURCE_FAMILY
    if platform in {_YOUTUBE_SOURCE_FAMILY, "yt"} or source_key.startswith(
        _YOUTUBE_SOURCE_PREFIXES
    ):
        return _YOUTUBE_SOURCE_FAMILY
    if platform in {_TWITTER_SOURCE_FAMILY, "x"} or source_key.startswith(_TWITTER_SOURCE_PREFIXES):
        return _TWITTER_SOURCE_FAMILY
    if platform in {_REDDIT_SOURCE_FAMILY, "rd"} or source_key.startswith(_REDDIT_SOURCE_PREFIXES):
        return _REDDIT_SOURCE_FAMILY
    if platform in {_BILIBILI_SOURCE_FAMILY, "bili"} or source_key in _BILIBILI_SOURCE_KEYS:
        return _BILIBILI_SOURCE_FAMILY
    return raw_source or "unknown"


def _normalize_source_platform_key(source_platform: object) -> str:
    """Return the canonical source key used in cross-source content IDs."""
    raw = str(source_platform or "").strip().lower()
    if raw in {_XHS_SOURCE_FAMILY, "xhs"}:
        return _XHS_SOURCE_FAMILY
    if raw in {_DOUYIN_SOURCE_FAMILY, "dy"}:
        return _DOUYIN_SOURCE_FAMILY
    if raw in {_YOUTUBE_SOURCE_FAMILY, "yt"}:
        return _YOUTUBE_SOURCE_FAMILY
    if raw in {_TWITTER_SOURCE_FAMILY, "x"}:
        return _TWITTER_SOURCE_FAMILY
    if raw in {_REDDIT_SOURCE_FAMILY, "rd"}:
        return _REDDIT_SOURCE_FAMILY
    if raw in {_BILIBILI_SOURCE_FAMILY, "bili"}:
        return _BILIBILI_SOURCE_FAMILY
    return raw


def _normalize_style_key_for_storage(value: object) -> str:
    """Canonicalize known style_key values while preserving unknown legacy rows."""
    token = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    if not token:
        return ""
    return _LEGACY_STYLE_KEY_MAP.get(token, token)


def _is_linkable_pool_source(
    source: object,
    source_platform: object,
    content_url: object,
) -> bool:
    """Return False for xhs rows that cannot be opened from recommendations."""
    if _pool_source_family(source, source_platform) != _XHS_SOURCE_FAMILY:
        return True
    return "xsec_token=" in str(content_url or "")


def _xhs_self_author_guard_sql(table_alias: str = "content_cache") -> str:
    """Return a SQL AND clause that excludes self-authored XHS rows.

    The clause takes 3 positional ``?`` parameters (all the same nickname
    string). When the nickname is empty the clause is a no-op.
    """
    prefix = f"{table_alias}." if table_alias else ""
    return (
        "AND ("
        "? = '' "
        f"OR COALESCE({prefix}source_platform, '') != 'xiaohongshu' "
        "OR ("
        f"LOWER(COALESCE({prefix}up_name, '')) != LOWER(?) "
        f"AND LOWER(COALESCE({prefix}author_name, '')) != LOWER(?)"
        ")"
        ")"
    )


def _xhs_self_author_guard_params(xhs_self_nickname: str | None) -> tuple[str, str, str]:
    """Return the 3 bind values for ``_xhs_self_author_guard_sql``."""
    nickname = str(xhs_self_nickname or "").strip()
    return (nickname, nickname, nickname)


def _validated_extension_native_save_uuid(value: object, field_name: str) -> str:
    raw_text = str(value or "")
    if any(unicodedata.category(character).startswith("C") for character in raw_text):
        raise ValueError(f"{field_name} must not contain control characters")
    text = raw_text.strip()
    try:
        parsed = UUID(text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a UUID") from exc
    if str(parsed) != text.lower():
        raise ValueError(f"{field_name} must be a canonical UUID")
    return text.lower()


def _validated_extension_native_save_text(
    value: object,
    field_name: str,
    *,
    max_length: int,
    allow_blank: bool = False,
) -> str:
    raw_text = str(value or "")
    if any(unicodedata.category(character).startswith("C") for character in raw_text):
        raise ValueError(f"{field_name} must not contain control characters")
    text = raw_text.strip()
    if not text and not allow_blank:
        raise ValueError(f"{field_name} must not be blank")
    if len(text) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    return text


def _canonical_extension_native_save_url(platform: str, value: object) -> str:
    raw_url = _validated_extension_native_save_text(value, "content_url", max_length=2048)
    try:
        parts = urlsplit(raw_url)
        port = parts.port
    except ValueError as exc:
        raise ValueError("content_url must use an allow-listed platform HTTPS host") from exc
    hostname = (parts.hostname or "").lower().rstrip(".")
    allowed_hosts = _EXTENSION_NATIVE_SAVE_HOSTS[platform]
    if (
        parts.scheme.lower() != "https"
        or not hostname
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or not any(hostname == host or hostname.endswith(f".{host}") for host in allowed_hosts)
    ):
        raise ValueError("content_url must use an allow-listed platform HTTPS host")
    retained_fields = _EXTENSION_NATIVE_SAVE_IDENTITY_QUERY_FIELDS.get(platform, frozenset())
    retained_query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key in retained_fields
    ]
    if platform == "youtube" and retained_query:
        videos = [item for key, item in retained_query if key == "v"]
        if len(videos) != 1 or not videos[0]:
            raise ValueError("content_url has an invalid YouTube navigation query")
    if platform == "xiaohongshu" and retained_query:
        seen_fields: set[str] = set()
        for key, item in retained_query:
            if key in seen_fields or not item:
                raise ValueError("content_url has an invalid Xiaohongshu navigation query")
            seen_fields.add(key)
        if "xsec_token" not in seen_fields:
            raise ValueError("content_url has an invalid Xiaohongshu navigation query")
    query = urlencode(retained_query)
    return urlunsplit(("https", hostname, parts.path or "/", query, ""))


def _validated_extension_native_save_job(
    job: ExtensionNativeSaveJob,
) -> dict[str, str]:
    job_id = _validated_extension_native_save_uuid(job.job_id, "job_id")
    platform = canonical_source_platform(job.platform)
    if platform != job.platform or platform not in _EXTENSION_NATIVE_SAVE_PLATFORM_SLUGS:
        raise ValueError("platform must be a supported canonical platform")
    platform_slug = _validated_extension_native_save_text(
        job.platform_slug, "platform_slug", max_length=16
    ).lower()
    if platform_slug != _EXTENSION_NATIVE_SAVE_PLATFORM_SLUGS[platform]:
        raise ValueError("platform_slug does not match platform")
    content_id = _validated_extension_native_save_text(job.content_id, "content_id", max_length=512)
    content_url = _canonical_extension_native_save_url(platform, job.content_url)
    item_key = _validated_extension_native_save_text(job.item_key, "item_key", max_length=768)
    if item_key != make_item_key(platform, content_id, content_url):
        raise ValueError("item_key does not match the canonical platform identity")
    content_type = _validated_extension_native_save_text(
        job.content_type, "content_type", max_length=128
    )
    requested_action = _validated_extension_native_save_text(
        job.requested_action, "requested_action", max_length=32
    )
    resolved_action = _validated_extension_native_save_text(
        job.resolved_action, "resolved_action", max_length=32
    )
    if requested_action not in _EXTENSION_NATIVE_SAVE_ACTIONS:
        raise ValueError("requested_action is invalid")
    if resolved_action not in _EXTENSION_NATIVE_SAVE_ACTIONS:
        raise ValueError("resolved_action is invalid")
    target_label = _validated_extension_native_save_text(
        job.target_label, "target_label", max_length=256
    )
    return {
        "job_id": job_id,
        "platform": platform,
        "platform_slug": platform_slug,
        "item_key": item_key,
        "content_id": content_id,
        "content_url": content_url,
        "content_type": content_type,
        "requested_action": requested_action,
        "resolved_action": resolved_action,
        "target_label": target_label,
    }


def _validated_extension_native_save_result(
    status: object, error_code: object, error_message: object
) -> tuple[str, str, str]:
    safe_status = _validated_extension_native_save_text(status, "status", max_length=32)
    if safe_status not in _EXTENSION_NATIVE_SAVE_RESULT_STATUSES:
        raise ValueError("status is invalid")
    safe_code = _validated_extension_native_save_text(
        error_code, "error_code", max_length=128, allow_blank=True
    )
    _validated_extension_native_save_text(
        error_message, "error_message", max_length=512, allow_blank=True
    )
    try:
        safe_message = _EXTENSION_NATIVE_SAVE_RESULT_MESSAGES[(safe_status, safe_code)]
    except KeyError as exc:
        raise ValueError("status and error_code combination is invalid") from exc
    return safe_status, safe_code, safe_message


def _normalize_admission_min_score(value: object) -> float:
    if isinstance(value, bool):
        return _DEFAULT_ADMISSION_MIN_SCORE
    if not isinstance(value, (int, float, str)):
        return _DEFAULT_ADMISSION_MIN_SCORE
    try:
        score = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_ADMISSION_MIN_SCORE
    if score <= 0.0 or score > 1.0:
        return _DEFAULT_ADMISSION_MIN_SCORE
    return score


class Database:
    """Lightweight SQLite wrapper for OpenBiliClaw.

    Manages the event log, content cache, and recommendation history.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._admission_min_score = _DEFAULT_ADMISSION_MIN_SCORE

    def set_admission_min_score(self, value: object) -> None:
        """Set the unified recommendation-pool admission floor."""
        self._admission_min_score = _normalize_admission_min_score(value)

    def initialize(self) -> None:
        """Initialize the database and run migrations if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.executescript(_SCHEMA_SQL)
        self._ensure_event_satisfaction_columns()
        self._ensure_recommendation_feedback_columns()
        self._ensure_content_cache_runtime_columns()
        self._ensure_content_cache_relevance_columns()
        self._ensure_content_cache_topic_columns()
        self._ensure_content_cache_pool_copy_columns()
        self._ensure_content_cache_delight_columns()
        self._ensure_content_cache_multisource_columns()
        self._ensure_content_identity_columns()
        self._ensure_recommendation_read_indexes()
        self._ensure_source_recipes_table()
        self._ensure_xhs_observed_urls_table()
        self._ensure_discovery_candidate_columns()
        self._normalize_legacy_style_keys()
        self._ensure_llm_usage_cache_columns()
        self._ensure_chat_turns_table()
        self._ensure_watch_later_table()
        self._ensure_discovery_keywords_table()
        self._ensure_favorites_table()
        self._ensure_saved_sync_tables()
        self._ensure_auth_state_table()
        self._ensure_init_runs_table()
        self.reset_stale_discovery_candidate_evaluations()
        self.suppress_low_score_pool_items()
        self.suppress_low_confidence_recommendations()

        # Set schema version
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (_SCHEMA_VERSION,),
        )
        self._conn.commit()
        logger.info("Database initialized at %s", self._db_path)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._conn

    def _pool_admission_min_score(self) -> float:
        return _normalize_admission_min_score(self._admission_min_score)

    def pool_admission_threshold(
        self,
        source_strategy: object,
        requested_threshold: object | None = None,
    ) -> float:
        """Return the shared effective admission floor for one source."""
        return effective_admission_threshold(
            source_strategy,
            self._pool_admission_min_score(),
            requested_threshold,
        )

    def _pool_admission_sql(
        self,
        *,
        score_expr: str = "COALESCE(relevance_score, 0.0)",
        source_expr: str = "source",
    ) -> tuple[str, tuple[Any, ...]]:
        """Return a SQL predicate and params for the shared admission policy."""
        predicate = f"""
            {score_expr} >= CASE
                WHEN LOWER(TRIM(COALESCE({source_expr}, ''))) = ? THEN ?
                ELSE ?
            END
        """
        return predicate, (
            EXPLORE_STRATEGY,
            EXPLORE_ADMISSION_MIN_SCORE,
            self._pool_admission_min_score(),
        )

    def open_connection(self) -> sqlite3.Connection:
        """Open a short-lived connection to the initialized database.

        Use this for explicit transactions that may run from FastAPI's
        threadpool. A separate connection lets SQLite serialize writers
        with ``busy_timeout`` instead of nesting transactions on the
        process-wide connection.
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _ensure_fresh_read(self) -> None:
        """Close any implicit transaction so the next SELECT sees the latest WAL state.

        When a CLI command (a separate process) writes to the same database,
        this server process may still hold a stale read snapshot inside an
        implicit transaction.  Committing closes that transaction so the next
        query starts a new one against the current WAL head.
        """
        if self.conn.in_transaction:
            self.conn.commit()

    def _execute_write(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] = (),
    ) -> sqlite3.Cursor:
        """Execute a write with short retry on transient SQLite locks."""
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.execute(sql, params)
                self.conn.commit()
                return cursor
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                logger.warning(
                    "SQLite write locked, retrying (%s attempts left): %s",
                    attempts,
                    sql.splitlines()[0].strip() if sql.strip() else "<empty-sql>",
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def _execute_many_write(
        self,
        sql: str,
        seq_of_params: Sequence[tuple[Any, ...] | list[Any]],
    ) -> sqlite3.Cursor:
        """Batch-execute a write with the same transient-lock retry as ``_execute_write``."""
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.executemany(sql, seq_of_params)
                self.conn.commit()
                return cursor
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                logger.warning(
                    "SQLite batch write locked, retrying (%s attempts left): %s",
                    attempts,
                    sql.splitlines()[0].strip() if sql.strip() else "<empty-sql>",
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def insert_event(self, event_type: str, **kwargs: Any) -> int:
        """Insert a behavioral event.

        v0.3.23+: ``context`` is now a natural-language string (from
        ``event_format.build_event()``). It's stored as raw text — no
        outer JSON wrapping — so consumers reading via SELECT get back
        the same string they put in. Pre-v0.3.22 callers that passed
        dict-shaped context still work: dicts / lists / other non-string
        values are JSON-encoded for storage so older code paths don't
        suddenly lose data.

        Args:
            event_type: Type of event.
            **kwargs: Additional event fields. ``context`` may be str,
                dict, list, or None.

        Returns:
            Inserted row ID.
        """
        import json

        from openbiliclaw.sources.event_format import classify_event_satisfaction

        raw_context = kwargs.get("context", "")
        if isinstance(raw_context, str):
            context_text = raw_context
        elif raw_context is None:
            context_text = ""
        else:
            # Legacy dict / list payload — JSON-encode for storage.
            context_text = json.dumps(raw_context, ensure_ascii=False)

        metadata_payload = kwargs.get("metadata", {})

        # Single classification owner. Reconstruct the event dict shape
        # the classifier expects (event_type + url + title + metadata).
        # API ingest may set dwell fields at the top level as well; pass
        # those through so the click rules read either location.
        classifier_event: dict[str, Any] = {
            "event_type": event_type,
            "url": kwargs.get("url", ""),
            "title": kwargs.get("title", ""),
            "metadata": metadata_payload if isinstance(metadata_payload, dict) else {},
        }
        for top_level_key in ("watch_seconds", "video_duration_seconds"):
            if top_level_key in kwargs and kwargs[top_level_key] is not None:
                classifier_event[top_level_key] = kwargs[top_level_key]
        inferred_satisfaction, satisfaction_reason = classify_event_satisfaction(classifier_event)

        cursor = self._execute_write(
            "INSERT INTO events "
            "(event_type, url, title, context, metadata, "
            " inferred_satisfaction, satisfaction_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_type,
                kwargs.get("url", ""),
                kwargs.get("title", ""),
                context_text,
                json.dumps(metadata_payload, ensure_ascii=False),
                inferred_satisfaction,
                satisfaction_reason,
            ),
        )
        return cursor.lastrowid or 0

    def get_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent events.

        Args:
            limit: Maximum number of events.

        Returns:
            List of event dicts.
        """
        cursor = self.conn.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Durable popup chat turns
    # ------------------------------------------------------------------

    def create_chat_turn(
        self,
        *,
        turn_id: str,
        message: str,
        session: str = "popup",
        scope: str = "chat",
        subject_id: str = "",
        subject_title: str = "",
    ) -> dict[str, Any]:
        """Create a pending popup chat turn if it does not already exist."""
        self._execute_write(
            """
            INSERT OR IGNORE INTO chat_turns (
                turn_id, session, scope, subject_id, subject_title, message, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                turn_id,
                session or "popup",
                scope or "chat",
                subject_id or "",
                subject_title or "",
                message,
            ),
        )
        row = self.get_chat_turn(turn_id)
        if row is None:
            raise RuntimeError(f"Failed to create chat turn {turn_id!r}")
        return row

    def complete_chat_turn(self, turn_id: str, *, reply: str) -> None:
        """Mark a pending popup chat turn as completed."""
        self._execute_write(
            """
            UPDATE chat_turns
            SET status = 'completed',
                reply = ?,
                error = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
            """,
            (reply, turn_id),
        )

    def fail_chat_turn(self, turn_id: str, *, error: str, reply: str = "") -> None:
        """Mark a popup chat turn as failed while preserving visible copy."""
        self._execute_write(
            """
            UPDATE chat_turns
            SET status = 'failed',
                reply = ?,
                error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
            """,
            (reply, error, turn_id),
        )

    def get_chat_turn(self, turn_id: str) -> dict[str, Any] | None:
        """Return one durable popup chat turn by id."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT turn_id, session, scope, subject_id, subject_title, message,
                   status, reply, error, created_at, updated_at
            FROM chat_turns
            WHERE turn_id = ?
            """,
            (turn_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_chat_turns(
        self,
        *,
        session: str = "popup",
        scope: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent popup chat turns in display order."""
        self._ensure_fresh_read()
        clauses = ["session = ?"]
        params: list[Any] = [session or "popup"]
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        params.append(max(1, int(limit)))
        cursor = self.conn.execute(
            f"""
            SELECT turn_id, session, scope, subject_id, subject_title, message,
                   status, reply, error, created_at, updated_at
            FROM (
                SELECT turn_id, session, scope, subject_id, subject_title, message,
                       status, reply, error, created_at, updated_at
                FROM chat_turns
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, turn_id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, turn_id ASC
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # LLM usage ledger
    # ------------------------------------------------------------------

    def insert_llm_usage(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost_cny: float,
        caller: str = "",
        success: bool = True,
        cached_input_tokens: int = 0,
    ) -> int:
        """Append one LLM-call usage record.

        ``cached_input_tokens`` (v0.3.28+) is the portion of
        ``prompt_tokens`` served from provider-side prompt cache —
        always ``<= prompt_tokens``. 0 means no cache use. Used by
        ``cost --by caller`` to compute hit rates and by
        ``estimate_cost`` to discount cached tokens correctly.
        """
        total = max(0, prompt_tokens) + max(0, completion_tokens)
        cursor = self._execute_write(
            """INSERT INTO llm_usage
               (provider, model, caller, prompt_tokens, completion_tokens,
                total_tokens, cached_input_tokens, estimated_cost_cny,
                success)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provider or "",
                model or "",
                caller or "",
                int(max(0, prompt_tokens)),
                int(max(0, completion_tokens)),
                int(total),
                int(max(0, cached_input_tokens)),
                float(estimated_cost_cny),
                1 if success else 0,
            ),
        )
        return cursor.lastrowid or 0

    def query_llm_usage_by_day(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-day aggregates for the last ``days`` days.

        Each row: {day, calls, prompt_tokens, completion_tokens,
        total_tokens, cost_cny}. Days with zero usage are omitted —
        the CLI fills gaps for display.
        """
        cursor = self.conn.execute(
            """
            SELECT date(timestamp, 'localtime') AS day,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY day
            ORDER BY day DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_by_provider(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-(provider, model) totals over the last ``days`` days."""
        cursor = self.conn.execute(
            """
            SELECT provider,
                   model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY provider, model
            ORDER BY cost_cny DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_by_caller(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-caller totals over the last ``days`` days.

        ``caller`` is a free-form string the LLM service tags into each
        row (e.g. ``discovery.evaluate`` / ``recommendation.write`` /
        ``soul.profile``). Untagged calls land under ``""`` which the
        CLI renders as ``(untagged)``. Result is sorted by cost so the
        first row is the most expensive caller.

        v0.3.28+ also returns ``cached_input_tokens`` so the CLI can
        compute and surface per-caller cache hit rates — a low rate
        (< 30%) signals prompt-prefix instability worth investigating.
        """
        cursor = self.conn.execute(
            """
            SELECT COALESCE(caller, '') AS caller,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY caller
            ORDER BY cost_cny DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_total(self, *, days: int = 7) -> dict[str, Any]:
        """Return a single-row total for the last ``days`` days."""
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            """,
            (max(1, int(days)),),
        )
        row = cursor.fetchone()
        return (
            dict(row)
            if row
            else {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_input_tokens": 0,
                "cost_cny": 0.0,
            }
        )

    def max_llm_usage_id(self) -> int:
        """Return the highest currently-stored ``llm_usage.id`` (0 if empty).

        Used as a checkpoint for "what's been billed since this point"
        queries — the init / discovery cycle wrappers snapshot it on
        entry and pass it to ``query_llm_usage_since_id`` on exit to
        scope the cost summary to that single phase.
        """
        cursor = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM llm_usage")
        row = cursor.fetchone()
        return int(row["m"]) if row else 0

    def query_llm_usage_since_id(self, *, since_id: int) -> dict[str, Any]:
        """Return per-caller breakdown + totals for rows ``id > since_id``.

        Output: ``{"total": {calls, prompt_tokens, completion_tokens,
        cost_cny}, "by_caller": [{caller, calls, ...}, ...]}``. Bound
        to a single phase by passing ``max_llm_usage_id()`` taken at
        the phase entry.
        """
        total_cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE id > ?
            """,
            (int(since_id),),
        )
        total_row = total_cursor.fetchone()
        total = (
            dict(total_row)
            if total_row
            else {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_input_tokens": 0,
                "cost_cny": 0.0,
            }
        )

        caller_cursor = self.conn.execute(
            """
            SELECT COALESCE(caller, '') AS caller,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE id > ?
            GROUP BY caller
            ORDER BY cost_cny DESC
            """,
            (int(since_id),),
        )
        return {
            "total": total,
            "by_caller": [dict(row) for row in caller_cursor.fetchall()],
        }

    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        keyword: str = "",
        limit: int = 100,
        satisfaction_modes: frozenset[str] | None = None,
        after_event_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query events with optional filters.

        ``satisfaction_modes`` filters by ``inferred_satisfaction``. When
        the set includes ``"unknown"``, rows with a NULL classification
        (pre-migration legacy rows) are also returned.

        ``after_event_id`` restricts to rows with ``id`` strictly greater
        than the given watermark — used by the cognition cycle to read only
        events not yet folded into awareness. Result order is unchanged
        (newest-first); callers that need chronological order reverse it.
        """
        sql = "SELECT * FROM events"
        clauses: list[str] = []
        params: list[Any] = []

        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)

        if after_event_id is not None:
            clauses.append("id > ?")
            params.append(after_event_id)

        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat(sep=" "))

        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat(sep=" "))

        if keyword:
            like = f"%{keyword}%"
            clauses.append("(url LIKE ? OR title LIKE ? OR metadata LIKE ?)")
            params.extend([like, like, like])

        if satisfaction_modes is not None:
            modes = list(satisfaction_modes)
            mode_clauses: list[str] = []
            if modes:
                placeholders = ", ".join("?" for _ in modes)
                mode_clauses.append(f"inferred_satisfaction IN ({placeholders})")
                params.extend(modes)
            if "unknown" in satisfaction_modes:
                mode_clauses.append("inferred_satisfaction IS NULL")
            if mode_clauses:
                clauses.append("(" + " OR ".join(mode_clauses) + ")")
            else:
                # Empty modes set explicitly requested → match nothing.
                clauses.append("1 = 0")

        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        sql = f"{sql} ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def count_events_by_type(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, int]:
        """Count events grouped by event type."""
        sql = "SELECT event_type, COUNT(*) AS count FROM events"
        clauses: list[str] = []
        params: list[Any] = []

        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat(sep=" "))

        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat(sep=" "))

        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        sql = f"{sql} GROUP BY event_type ORDER BY event_type ASC"
        cursor = self.conn.execute(sql, params)
        return {str(row["event_type"]): int(row["count"]) for row in cursor.fetchall()}

    def search_local_inspiration_evidence(
        self,
        query: str,
        *,
        limit: int = 10,
        lookback_days: int = 30,
    ) -> list[dict[str, object]]:
        """Return local content evidence for inspiration grounding."""

        clean_query = str(query or "").strip()
        if not clean_query:
            return []
        tokens = _local_evidence_tokens(clean_query)
        if not tokens:
            return []

        like_terms = [f"%{_escape_like_term(token)}%" for token in tokens[:12]]
        where = " OR ".join(
            "title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\'" for _ in like_terms
        )
        params: list[object] = []
        for term in like_terms:
            params.extend([term, term])
        params.append(f"-{max(1, int(lookback_days))} days")

        rows = self.conn.execute(
            f"""
            SELECT
                title,
                COALESCE(
                    NULLIF(content_url, ''),
                    CASE
                        WHEN COALESCE(bvid, '') != ''
                        THEN 'https://www.bilibili.com/video/' || bvid
                        ELSE ''
                    END
                ) AS url,
                description,
                source_platform,
                content_id,
                pool_topic_label AS topic_label,
                discovered_at AS created_at
            FROM content_cache
            WHERE ({where})
              AND COALESCE(pool_status, '') NOT IN ('purged_by_dislike')
              AND datetime(COALESCE(NULLIF(discovered_at, ''), '1970-01-01'))
                  >= datetime('now', ?)
            ORDER BY discovered_at DESC
            LIMIT 200
            """,
            params,
        ).fetchall()

        scored: list[tuple[int, str, dict[str, object]]] = []
        for row in rows:
            title = str(row["title"] or "").strip()
            url = str(row["url"] or "").strip()
            if not title or not url:
                continue
            description = str(row["description"] or "").strip()
            haystack = f"{title} {description}"
            match_count = sum(1 for token in tokens if token in haystack)
            if len(tokens) >= 2 and match_count < 2 and clean_query not in haystack:
                continue
            scored.append(
                (
                    match_count,
                    str(row["created_at"] or ""),
                    {
                        "title": title,
                        "url": url,
                        "highlights": [description] if description else [],
                        "source_table": "content_cache",
                        "source_platform": str(row["source_platform"] or ""),
                        "content_id": str(row["content_id"] or ""),
                        "topic_label": str(row["topic_label"] or ""),
                        "created_at": str(row["created_at"] or ""),
                    },
                )
            )
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [payload for _, _, payload in scored[: max(1, int(limit))]]

    def cache_content(self, bvid: str, **kwargs: Any) -> None:
        """Cache discovered content.

        Args:
            bvid: Video BV ID.
            **kwargs: Content fields.
        """
        import json

        source_platform = str(kwargs.get("source_platform", "bilibili") or "").strip()
        raw_content_id = str(kwargs.get("content_id", bvid) or "").strip()
        identity_content_id = raw_content_id if source_platform else bvid.strip()
        item_key = str(kwargs.get("item_key", "") or "").strip() or make_item_key(
            source_platform or "bilibili",
            identity_content_id,
            str(kwargs.get("content_url", "") or ""),
        )
        existing_identity_row = self.conn.execute(
            "SELECT bvid FROM content_cache WHERE item_key = ?",
            (item_key,),
        ).fetchone()
        if existing_identity_row is not None:
            bvid = str(existing_identity_row["bvid"])

        self._execute_write(
            """
            INSERT INTO content_cache (
                bvid,
                item_key,
                title,
                up_name,
                up_mid,
                duration,
                tags,
                topic_key,
                topic_group,
                style_key,
                franchise_key,
                description,
                cover_url,
                view_count,
                like_count,
                favorite_count,
                collect_count,
                comment_count,
                share_count,
                danmaku_count,
                reply_count,
                retweet_count,
                bookmark_count,
                relevance_score,
                relevance_reason,
                pool_expression,
                pool_topic_label,
                candidate_tier,
                last_scored_at,
                source,
                content_id,
                content_url,
                source_platform,
                author_name,
                body_text,
                content_type,
                source_keyword_id
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(bvid) DO UPDATE SET
                title = excluded.title,
                up_name = excluded.up_name,
                up_mid = excluded.up_mid,
                duration = excluded.duration,
                tags = excluded.tags,
                -- Preserve LLM-classified fields: when the incoming value
                -- is empty/zero, keep the existing DB value.  This prevents
                -- re-ingest from raw sources (e.g. xhs extension re-sending
                -- the same notes on every page load) from wiping out
                -- classifications that classify_pool_backlog has written.
                topic_key = COALESCE(
                    NULLIF(excluded.topic_key, ''),
                    content_cache.topic_key,
                    ''
                ),
                topic_group = COALESCE(
                    NULLIF(excluded.topic_group, ''),
                    content_cache.topic_group,
                    ''
                ),
                style_key = COALESCE(
                    NULLIF(excluded.style_key, ''),
                    content_cache.style_key,
                    ''
                ),
                franchise_key = COALESCE(
                    NULLIF(excluded.franchise_key, ''),
                    content_cache.franchise_key,
                    ''
                ),
                description = excluded.description,
                cover_url = COALESCE(
                    NULLIF(excluded.cover_url, ''),
                    content_cache.cover_url,
                    ''
                ),
                view_count = excluded.view_count,
                like_count = excluded.like_count,
                favorite_count = excluded.favorite_count,
                collect_count = excluded.collect_count,
                comment_count = excluded.comment_count,
                share_count = excluded.share_count,
                danmaku_count = excluded.danmaku_count,
                reply_count = excluded.reply_count,
                retweet_count = excluded.retweet_count,
                bookmark_count = excluded.bookmark_count,
                relevance_score = CASE
                    WHEN excluded.relevance_score > 0 THEN excluded.relevance_score
                    ELSE COALESCE(content_cache.relevance_score, 0)
                END,
                relevance_reason = COALESCE(
                    NULLIF(excluded.relevance_reason, ''),
                    content_cache.relevance_reason,
                    ''
                ),
                pool_expression = COALESCE(
                    NULLIF(excluded.pool_expression, ''),
                    content_cache.pool_expression,
                    ''
                ),
                pool_topic_label = COALESCE(
                    NULLIF(excluded.pool_topic_label, ''),
                    content_cache.pool_topic_label,
                    ''
                ),
                candidate_tier = excluded.candidate_tier,
                last_scored_at = CURRENT_TIMESTAMP,
                -- Re-fresh items previously trim-suppressed: 'suppressed' is
                -- an internal diversity decision (over-quota cuts, topic cap),
                -- not a user signal. When a discovery strategy re-finds the
                -- item it deserves another shot. Without this, B站 trending
                -- (which churns slowly) stays bottlenecked because most hot
                -- BVIDs are already cached as 'suppressed' from earlier
                -- trim cycles. User-driven states ('shown', 'feedbacked',
                -- 'purged_by_dislike') are preserved. Low-score suppressed
                -- rows only revive after a fresh/effective score meets the
                -- unified admission floor.
                pool_status = CASE
                    WHEN content_cache.pool_status = 'suppressed'
                         AND (
                            CASE
                                WHEN excluded.relevance_score > 0 THEN excluded.relevance_score
                                ELSE COALESCE(content_cache.relevance_score, 0)
                            END
                         ) >= CASE
                            WHEN LOWER(TRIM(COALESCE(excluded.source, ''))) = ? THEN ?
                            ELSE ?
                         END
                    THEN 'fresh'
                    ELSE content_cache.pool_status
                END,
                source = excluded.source,
                item_key = excluded.item_key,
                content_id = excluded.content_id,
                content_url = excluded.content_url,
                source_platform = excluded.source_platform,
                author_name = COALESCE(
                    NULLIF(excluded.author_name, ''),
                    content_cache.author_name,
                    ''
                ),
                body_text = COALESCE(
                    NULLIF(excluded.body_text, ''),
                    content_cache.body_text,
                    ''
                ),
                content_type = COALESCE(
                    NULLIF(excluded.content_type, ''),
                    content_cache.content_type,
                    'video'
                ),
                -- P1.8: keep the producing-keyword provenance once set; a later
                -- re-ingest from a source that doesn't carry the id (NULL) must
                -- not wipe it.
                source_keyword_id = COALESCE(
                    excluded.source_keyword_id,
                    content_cache.source_keyword_id
                )
            """,
            (
                bvid,
                item_key,
                kwargs.get("title", ""),
                kwargs.get("up_name", ""),
                kwargs.get("up_mid", 0),
                kwargs.get("duration", 0),
                json.dumps(kwargs.get("tags", []), ensure_ascii=False),
                kwargs.get("topic_key", ""),
                kwargs.get("topic_group", ""),
                _normalize_style_key_for_storage(kwargs.get("style_key", "")),
                kwargs.get("franchise_key", ""),
                kwargs.get("description", ""),
                kwargs.get("cover_url", ""),
                kwargs.get("view_count", 0),
                kwargs.get("like_count", 0),
                kwargs.get("favorite_count", 0),
                kwargs.get("collect_count", 0),
                kwargs.get("comment_count", 0),
                kwargs.get("share_count", 0),
                kwargs.get("danmaku_count", 0),
                kwargs.get("reply_count", 0),
                kwargs.get("retweet_count", 0),
                kwargs.get("bookmark_count", 0),
                kwargs.get("relevance_score", 0.0),
                kwargs.get("relevance_reason", ""),
                kwargs.get("pool_expression", ""),
                kwargs.get("pool_topic_label", ""),
                kwargs.get("candidate_tier", "primary"),
                kwargs.get("source", ""),
                kwargs.get("content_id", bvid),
                kwargs.get("content_url", ""),
                kwargs.get("source_platform", "bilibili"),
                kwargs.get("author_name", ""),
                kwargs.get("body_text", ""),
                kwargs.get("content_type", "video") or "video",
                self._coerce_source_keyword_id(kwargs.get("source_keyword_id")),
                EXPLORE_STRATEGY,
                EXPLORE_ADMISSION_MIN_SCORE,
                self._pool_admission_min_score(),
            ),
        )

    @staticmethod
    def _coerce_source_keyword_id(value: Any) -> int | None:
        """Normalize a ``source_keyword_id`` kwarg to ``int`` or ``None``.

        Tolerates the field being absent / blank / non-numeric so any caller
        that has not been threaded through the P1.8 provenance path stays a
        plain NULL write (no behavior change vs. the pre-P1.8 schema).
        """
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _candidate_value(candidate: object, key: str, default: Any = "") -> Any:
        if isinstance(candidate, Mapping):
            return candidate.get(key, default)
        return getattr(candidate, key, default)

    @staticmethod
    def _candidate_json_payload(value: object, *, default: object) -> str:
        if isinstance(value, str):
            try:
                json.loads(value)
            except json.JSONDecodeError:
                return json.dumps(default, ensure_ascii=False)
            return value
        try:
            return json.dumps(default if value is None else value, ensure_ascii=False)
        except TypeError:
            return json.dumps(default, ensure_ascii=False)

    def enqueue_discovery_candidates(
        self,
        candidates: Sequence[Any],
        *,
        max_pending_per_source: int | None = None,
    ) -> int:
        """Insert raw discovery candidates into the pending evaluation queue.

        Existing ``candidate_key`` rows are treated as rediscovery signals: the
        row is not duplicated, but ``last_seen_at`` is refreshed so active
        sources do not look stale.
        """

        inserted = 0
        touched_sources: set[str] = set()
        for candidate in candidates:
            candidate_key = str(self._candidate_value(candidate, "candidate_key", "") or "").strip()
            if not candidate_key:
                continue
            source_platform = str(self._candidate_value(candidate, "source_platform", "") or "")
            tags = self._candidate_json_payload(
                self._candidate_value(candidate, "tags", []),
                default=[],
            )
            raw_payload = self._candidate_json_payload(
                self._candidate_value(candidate, "raw_payload", {}),
                default={},
            )
            score_threshold = float(self._candidate_value(candidate, "score_threshold", 0.0) or 0.0)
            cursor = self._execute_write(
                """
                INSERT OR IGNORE INTO discovery_candidates (
                    candidate_key,
                    status,
                    source_platform,
                    source_strategy,
                    source_context,
                    content_type,
                    body_text,
                    bvid,
                    content_id,
                    content_url,
                    title,
                    author_name,
                    up_name,
                    up_mid,
                    description,
                    cover_url,
                    duration,
                    view_count,
                    like_count,
                    favorite_count,
                    collect_count,
                    comment_count,
                    share_count,
                    danmaku_count,
                    reply_count,
                    retweet_count,
                    bookmark_count,
                    tags,
                    candidate_tier,
                    score_threshold,
                    raw_payload,
                    source_keyword_id
                )
                VALUES (
                    ?, 'pending_eval', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    candidate_key,
                    source_platform,
                    str(self._candidate_value(candidate, "source_strategy", "") or ""),
                    str(self._candidate_value(candidate, "source_context", "") or ""),
                    str(self._candidate_value(candidate, "content_type", "video") or "video"),
                    str(self._candidate_value(candidate, "body_text", "") or ""),
                    str(self._candidate_value(candidate, "bvid", "") or ""),
                    str(self._candidate_value(candidate, "content_id", "") or ""),
                    str(self._candidate_value(candidate, "content_url", "") or ""),
                    str(self._candidate_value(candidate, "title", "") or ""),
                    str(self._candidate_value(candidate, "author_name", "") or ""),
                    str(self._candidate_value(candidate, "up_name", "") or ""),
                    int(self._candidate_value(candidate, "up_mid", 0) or 0),
                    str(self._candidate_value(candidate, "description", "") or ""),
                    str(self._candidate_value(candidate, "cover_url", "") or ""),
                    int(self._candidate_value(candidate, "duration", 0) or 0),
                    int(self._candidate_value(candidate, "view_count", 0) or 0),
                    int(self._candidate_value(candidate, "like_count", 0) or 0),
                    int(self._candidate_value(candidate, "favorite_count", 0) or 0),
                    int(self._candidate_value(candidate, "collect_count", 0) or 0),
                    int(self._candidate_value(candidate, "comment_count", 0) or 0),
                    int(self._candidate_value(candidate, "share_count", 0) or 0),
                    int(self._candidate_value(candidate, "danmaku_count", 0) or 0),
                    int(self._candidate_value(candidate, "reply_count", 0) or 0),
                    int(self._candidate_value(candidate, "retweet_count", 0) or 0),
                    int(self._candidate_value(candidate, "bookmark_count", 0) or 0),
                    tags,
                    str(self._candidate_value(candidate, "candidate_tier", "primary") or "primary"),
                    score_threshold,
                    raw_payload,
                    self._coerce_source_keyword_id(
                        self._candidate_value(candidate, "source_keyword_id", None)
                    ),
                ),
            )
            if source_platform:
                touched_sources.add(source_platform)
            if cursor.rowcount > 0:
                inserted += 1
                continue
            self._execute_write(
                """
                UPDATE discovery_candidates
                SET last_seen_at = CURRENT_TIMESTAMP
                WHERE candidate_key = ?
                """,
                (candidate_key,),
            )
        if max_pending_per_source is not None:
            max_pending = max(0, int(max_pending_per_source))
            if max_pending > 0:
                for source in touched_sources:
                    self.trim_discovery_candidates_for_source(
                        source_platform=source,
                        max_pending=max_pending,
                    )
        return inserted

    def trim_discovery_candidates_for_source(
        self,
        *,
        source_platform: str,
        max_pending: int,
    ) -> int:
        """Drop oldest candidate rows for one source over a queue cap.

        In-flight ``evaluating`` rows are never deleted. Terminal rows are
        trimmed before pending/evaluated rows so active raw material is kept
        whenever possible.
        """

        source = str(source_platform or "").strip()
        cap = max(0, int(max_pending))
        if not source or cap <= 0:
            return 0
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM discovery_candidates
            WHERE source_platform = ?
            """,
            (source,),
        ).fetchone()
        current = int(row["count"] if row else 0)
        excess = current - cap
        if excess <= 0:
            return 0
        cursor = self._execute_write(
            """
            DELETE FROM discovery_candidates
            WHERE id IN (
                SELECT id
                FROM discovery_candidates
                WHERE source_platform = ?
                  AND status != 'evaluating'
                ORDER BY
                    CASE
                        WHEN status IN (
                            'cached',
                            'rejected_low_score',
                            'rejected_duplicate',
                            'rejected_cache_admission',
                            'rejected_recently_viewed',
                            'rejected_franchise_quota',
                            'failed_eval'
                        ) THEN 0
                        ELSE 1
                    END ASC,
                    last_seen_at ASC,
                    id ASC
                LIMIT ?
            )
            """,
            (source, excess),
        )
        return int(cursor.rowcount)

    def reset_stale_discovery_candidate_evaluations(
        self,
        *,
        max_age_minutes: int = 30,
    ) -> int:
        """Release evaluator claims left behind by a crashed process.

        ``max_age_minutes=0`` releases EVERY ``evaluating`` row regardless of
        age — the startup case: the evaluator lives in-process, so any claim
        that survives a restart is orphaned by definition. Rows with a NULL
        ``claimed_at`` can never age out, so both modes include them.
        Without this a restart mid-batch starves the pool forever: stuck
        rows count toward the supply target but the drain only claims
        ``pending_eval`` (field log 2026-07-05: pool_available=0 with 40
        immortal ``evaluating`` rows).
        """

        minutes = max(0, int(max_age_minutes))
        if minutes == 0:
            cursor = self._execute_write(
                """
                UPDATE discovery_candidates
                SET status = 'pending_eval',
                    claimed_at = NULL,
                    eval_error = 'orphaned evaluating claim reset'
                WHERE status = 'evaluating'
                """
            )
            return int(cursor.rowcount)
        cursor = self._execute_write(
            """
            UPDATE discovery_candidates
            SET status = 'pending_eval',
                claimed_at = NULL,
                eval_error = 'stale evaluating claim reset'
            WHERE status = 'evaluating'
              AND (claimed_at IS NULL OR claimed_at < datetime('now', ?))
            """,
            (f"-{minutes} minutes",),
        )
        return int(cursor.rowcount)

    def claim_discovery_candidates_for_eval(self, *, limit: int) -> list[dict[str, Any]]:
        """Claim a mixed-source batch of pending candidates for evaluation."""

        claim_limit = max(0, int(limit))
        if claim_limit <= 0:
            return []
        self._ensure_fresh_read()
        # Peek a bounded window and round-robin in Python so one noisy source
        # cannot monopolize a mixed evaluator batch.
        cursor = self.conn.execute(
            """
            SELECT *
            FROM discovery_candidates
            WHERE status = 'pending_eval'
            ORDER BY last_seen_at ASC, id ASC
            LIMIT ?
            """,
            (max(claim_limit * 4, claim_limit),),
        )
        pending = [dict(row) for row in cursor.fetchall()]
        if not pending:
            return []

        source_order: list[str] = []
        by_source: dict[str, list[dict[str, Any]]] = {}
        for row in pending:
            source = str(row.get("source_platform") or "unknown")
            if source not in by_source:
                source_order.append(source)
                by_source[source] = []
            by_source[source].append(row)

        selected: list[dict[str, Any]] = []
        while len(selected) < claim_limit:
            added = False
            for source in source_order:
                rows = by_source[source]
                if not rows:
                    continue
                selected.append(rows.pop(0))
                added = True
                if len(selected) >= claim_limit:
                    break
            if not added:
                break

        ids = [int(row["id"]) for row in selected]
        placeholders = ", ".join("?" for _ in ids)
        self._execute_write(
            f"""
            UPDATE discovery_candidates
            SET status = 'evaluating',
                claimed_at = CURRENT_TIMESTAMP,
                eval_error = ''
            WHERE id IN ({placeholders})
              AND status = 'pending_eval'
            """,
            ids,
        )
        claimed_rows = self.conn.execute(
            f"""
            SELECT id
            FROM discovery_candidates
            WHERE id IN ({placeholders})
              AND status = 'evaluating'
            """,
            ids,
        ).fetchall()
        claimed_ids = {int(row["id"]) for row in claimed_rows}
        claimed = [row for row in selected if int(row["id"]) in claimed_ids]
        for row in claimed:
            row["status"] = "evaluating"
        return claimed

    def get_evaluated_discovery_candidates_for_admission(
        self,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return evaluated candidates still waiting for content-cache admission."""

        admission_limit = max(0, int(limit))
        if admission_limit <= 0:
            return []
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT *
            FROM discovery_candidates
            WHERE status = 'evaluated'
            ORDER BY evaluated_at ASC, last_seen_at ASC, id ASC
            LIMIT ?
            """,
            (admission_limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_discovery_candidate_evaluations(
        self,
        evaluations: Sequence[Mapping[str, Any]],
    ) -> int:
        """Persist evaluator output back onto claimed candidate rows."""

        updated = 0
        for evaluation in evaluations:
            candidate_id = int(evaluation.get("candidate_id") or evaluation.get("id") or 0)
            if candidate_id <= 0:
                continue
            cursor = self._execute_write(
                """
                UPDATE discovery_candidates
                SET status = ?,
                    topic_key = ?,
                    topic_group = ?,
                    style_key = ?,
                    franchise_key = ?,
                    relevance_score = ?,
                    relevance_reason = ?,
                    pool_expression = ?,
                    pool_topic_label = ?,
                    eval_error = ?,
                    eval_attempts = 0,
                    batch_eval_attempts = 0,
                    evaluated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'evaluating'
                """,
                (
                    str(evaluation.get("status") or "evaluated"),
                    str(evaluation.get("topic_key") or ""),
                    str(evaluation.get("topic_group") or ""),
                    _normalize_style_key_for_storage(evaluation.get("style_key")),
                    str(evaluation.get("franchise_key") or ""),
                    float(evaluation.get("relevance_score") or evaluation.get("score") or 0.0),
                    str(evaluation.get("relevance_reason") or evaluation.get("reason") or ""),
                    str(evaluation.get("pool_expression") or ""),
                    str(evaluation.get("pool_topic_label") or ""),
                    str(evaluation.get("eval_error") or ""),
                    candidate_id,
                ),
            )
            if cursor.rowcount > 0:
                updated += 1
        return updated

    def reset_discovery_candidates_to_pending(
        self,
        candidate_ids: Sequence[int],
        *,
        reason: str = "",
        max_attempts: int = 5,
        max_batch_attempts: int = 50,
        increment_attempts: bool = True,
    ) -> int:
        """Release claimed candidates after a transient evaluator failure."""

        ids = [int(candidate_id) for candidate_id in candidate_ids if int(candidate_id) > 0]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        if not increment_attempts:
            batch_attempts_limit = max(1, int(max_batch_attempts))
            cursor = self._execute_write(
                f"""
                UPDATE discovery_candidates
                SET batch_eval_attempts = batch_eval_attempts + 1,
                    status = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN 'failed_eval'
                        ELSE 'pending_eval'
                    END,
                    claimed_at = NULL,
                    eval_error = ?,
                    evaluated_at = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN CURRENT_TIMESTAMP
                        ELSE evaluated_at
                    END,
                    last_seen_at = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN last_seen_at
                        ELSE CURRENT_TIMESTAMP
                    END
                WHERE id IN ({placeholders})
                  AND status = 'evaluating'
                """,
                (
                    batch_attempts_limit,
                    str(reason),
                    batch_attempts_limit,
                    batch_attempts_limit,
                    *ids,
                ),
            )
            return int(cursor.rowcount)

        attempts_limit = max(1, int(max_attempts))
        cursor = self._execute_write(
            f"""
            UPDATE discovery_candidates
            SET eval_attempts = eval_attempts + 1,
                status = CASE
                    WHEN eval_attempts + 1 >= ? THEN 'failed_eval'
                    ELSE 'pending_eval'
                END,
                claimed_at = NULL,
                eval_error = ?,
                evaluated_at = CASE
                    WHEN eval_attempts + 1 >= ? THEN CURRENT_TIMESTAMP
                    ELSE evaluated_at
                END,
                last_seen_at = CASE
                    WHEN eval_attempts + 1 >= ? THEN last_seen_at
                    ELSE CURRENT_TIMESTAMP
                END
            WHERE id IN ({placeholders})
              AND status = 'evaluating'
            """,
            (attempts_limit, str(reason), attempts_limit, attempts_limit, *ids),
        )
        return int(cursor.rowcount)

    def mark_discovery_candidate_cached(self, candidate_id: int) -> None:
        """Mark an evaluated candidate as successfully inserted into content_cache."""

        self._execute_write(
            """
            UPDATE discovery_candidates
            SET status = 'cached',
                cached_at = CURRENT_TIMESTAMP,
                eval_error = '',
                eval_attempts = 0,
                batch_eval_attempts = 0
            WHERE id = ?
              AND status IN ('evaluating', 'evaluated')
            """,
            (int(candidate_id),),
        )

    def reject_discovery_candidate(
        self,
        candidate_id: int,
        *,
        status: str,
        reason: str = "",
    ) -> None:
        """Mark a candidate as rejected before it enters content_cache."""

        self._execute_write(
            """
            UPDATE discovery_candidates
            SET status = ?,
                eval_error = ?,
                evaluated_at = COALESCE(evaluated_at, CURRENT_TIMESTAMP)
            WHERE id = ?
              AND status IN ('evaluating', 'evaluated')
            """,
            (status, reason, int(candidate_id)),
        )

    def count_discovery_candidates_by_status(self) -> dict[str, int]:
        """Return candidate queue counts grouped by lifecycle status."""

        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM discovery_candidates
            GROUP BY status
            ORDER BY status ASC
            """
        )
        return {str(row["status"]): int(row["count"]) for row in cursor.fetchall()}

    def get_existing_discovery_candidate_keys(self, candidate_keys: Sequence[str]) -> set[str]:
        """Return candidate keys already present in the raw evaluation queue."""

        clean = _unique_clean_strings(candidate_keys)
        if not clean:
            return set()
        self._ensure_fresh_read()
        existing: set[str] = set()
        for chunk in _chunks(clean, 900):
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                SELECT candidate_key
                FROM discovery_candidates
                WHERE candidate_key IN ({placeholders})
                """,
                chunk,
            )
            existing.update(str(row["candidate_key"]) for row in cursor.fetchall())
        return existing

    def get_existing_content_cache_ids(self, content_ids: Sequence[str]) -> set[str]:
        """Return BVID/content ids that already exist in the evaluated content cache."""

        clean = _unique_clean_strings(content_ids)
        if not clean:
            return set()
        self._ensure_fresh_read()
        existing: set[str] = set()
        for chunk in _chunks(clean, 450):
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                SELECT bvid, content_id
                FROM content_cache
                WHERE bvid IN ({placeholders})
                   OR content_id IN ({placeholders})
                """,
                [*chunk, *chunk],
            )
            for row in cursor.fetchall():
                bvid = str(row["bvid"] or "").strip()
                content_id = str(row["content_id"] or "").strip()
                if bvid:
                    existing.add(bvid)
                if content_id:
                    existing.add(content_id)
        return existing

    def get_existing_content_cache_item_keys(self, item_keys: Sequence[str]) -> set[str]:
        """Return canonical identities already present in the evaluated content cache."""
        clean = _unique_clean_strings(item_keys)
        if not clean:
            return set()
        self._ensure_fresh_read()
        existing: set[str] = set()
        for chunk in _chunks(clean, 900):
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"SELECT item_key FROM content_cache WHERE item_key IN ({placeholders})",
                chunk,
            )
            existing.update(str(row["item_key"]) for row in cursor.fetchall())
        return existing

    def count_discovery_candidates_by_source_status(self) -> dict[str, dict[str, int]]:
        """Return candidate queue counts grouped by source and lifecycle status."""

        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT source_platform, status, COUNT(*) AS count
            FROM discovery_candidates
            GROUP BY source_platform, status
            ORDER BY source_platform ASC, status ASC
            """
        )
        counts: dict[str, dict[str, int]] = {}
        for row in cursor.fetchall():
            source = str(row["source_platform"] or "unknown")
            status = str(row["status"])
            counts.setdefault(source, {})[status] = int(row["count"])
        return counts

    def count_discovery_pending_raw_material_by_source(self) -> dict[str, int]:
        """Return not-yet-cached raw candidate counts grouped by source."""

        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT source_platform, COUNT(*) AS count
            FROM discovery_candidates
            WHERE status IN ('pending_eval', 'evaluating', 'evaluated')
            GROUP BY source_platform
            ORDER BY source_platform ASC
            """
        )
        return {str(row["source_platform"] or "unknown"): int(row["count"]) for row in cursor}

    def _count_pending_discovery_raw_material(self) -> int:
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM discovery_candidates
            WHERE status IN ('pending_eval', 'evaluating', 'evaluated')
            """
        )
        row = cursor.fetchone()
        return int(row["count"] if row else 0)

    def get_cached_content(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get cached discovered content ordered by basic quality signals."""
        cursor = self.conn.execute(
            """
            SELECT *
            FROM content_cache
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                view_count DESC,
                bvid ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_unrecommended_content(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get cached content that has not been recommended yet."""
        admission_sql, admission_params = self._pool_admission_sql(
            score_expr="COALESCE(c.relevance_score, 0.0)",
            source_expr="c.source",
        )
        cursor = self.conn.execute(
            f"""
            SELECT c.*
            FROM content_cache AS c
            WHERE {admission_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = c.bvid
            )
            ORDER BY
                CASE c.candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                c.relevance_score DESC,
                c.last_scored_at DESC,
                c.view_count DESC,
                c.bvid ASC
            LIMIT ?
            """,
            (*admission_params, max(limit * 5, 50)),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return self._balance_pool_rows(rows, limit=limit)

    def suppress_low_score_pool_items(self, min_score: float | None = None) -> int:
        """Suppress cached pool rows below the unified admission floor."""
        if min_score is None:
            admission_sql, admission_params = self._pool_admission_sql()
        else:
            admission_sql = "COALESCE(relevance_score, 0.0) >= ?"
            admission_params = (_normalize_admission_min_score(min_score),)
        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE NOT ({admission_sql})
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
            """,
            admission_params,
        )
        return int(cursor.rowcount or 0)

    def suppress_low_confidence_recommendations(self, min_score: float | None = None) -> int:
        """Mark old low-confidence recommendation rows as suppressed."""
        if min_score is None:
            admission_sql, admission_params = self._pool_admission_sql(
                score_expr="COALESCE(recommendations.confidence, 0.0)",
                source_expr=(
                    "(SELECT source FROM content_cache "
                    "WHERE content_cache.bvid = recommendations.bvid LIMIT 1)"
                ),
            )
        else:
            admission_sql = "COALESCE(recommendations.confidence, 0.0) >= ?"
            admission_params = (_normalize_admission_min_score(min_score),)
        cursor = self._execute_write(
            f"""
            UPDATE recommendations
            SET feedback_type = 'suppressed_low_score'
            WHERE NOT ({admission_sql})
              AND COALESCE(feedback_type, '') = ''
            """,
            admission_params,
        )
        return int(cursor.rowcount or 0)

    def get_pool_candidates(
        self,
        limit: int = 20,
        *,
        max_per_topic_group: int = 3,
        xhs_self_nickname: str = "",
    ) -> list[dict[str, Any]]:
        """Get fresh recommendation candidates directly from the discovery pool.

        ``max_per_topic_group`` caps how many items from any single
        ``topic_group`` enter the relevance-ordered head. Without this
        cap, a 600-item pool that contains 270 distinct topic_groups still
        produces a top-50 shortlist concentrated in ~10 head groups,
        because high-relevance candidates cluster around the user's
        primary interests; long-tail groups (197 with a single item each
        in the typical pool) never reach the candidate window. Cap of 3
        lets obvious favourites keep a strong presence while opening
        room for ~40+ different groups in the candidate window. Pass
        ``max_per_topic_group=0`` to restore the legacy unrestricted
        ordering for callers that need it (e.g. health checks).

        Rows claimed by the surprise (delight) channel are excluded via the
        delight claim guard — a delight that was delivered or is
        currently queue-eligible must never be duplicated by the regular
        feed. ``count_pool_candidates`` applies the same guard so the
        "还有 N 条" display stays in sync with what serve() can load.

        Notes:
            xhs rows without ``xsec_token`` in their ``content_url`` are
            excluded. Bare xhs URLs get rejected by xhs with error 300031
            when shared outbound, so surfacing them in recommendations
            would just mint dead links. Tokens get backfilled by the
            MAIN-world sniffer as the user browses xhs; bare rows become
            eligible again once ``_backfill_xhs_tokens`` upgrades them.
        """
        self._ensure_fresh_read()
        # Over-fetch widely so the per-group filter still leaves headroom
        # for the downstream balance pass.
        fetch_limit = max(limit * 8, 80)
        admission_sql, admission_params = self._pool_admission_sql()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        delight_threshold = self.dynamic_delight_threshold(
            default_threshold=_DELIGHT_CLAIM_MIN_SCORE
        )
        delight_guard_sql = _delight_claim_guard_sql()
        if max_per_topic_group <= 0:
            sql = f"""
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND {admission_sql}
                  AND COALESCE(pool_expression, '') != ''
                  AND COALESCE(pool_topic_label, '') != ''
                  AND COALESCE(style_key, '') != ''
                  AND COALESCE(topic_group, '') != ''
                  AND (
                    source_platform != 'xiaohongshu'
                    OR content_url LIKE '%xsec_token=%'
                  )
                  {guard_sql}
                  {delight_guard_sql}
                  AND NOT EXISTS (
                    SELECT 1
                    FROM recommendations AS r
                    WHERE r.bvid = content_cache.bvid
                  )
                ORDER BY
                    CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                    relevance_score DESC,
                    last_scored_at DESC,
                    view_count DESC,
                    bvid ASC
                LIMIT ?
            """
            params: tuple[Any, ...] = (
                *admission_params,
                *guard_params,
                delight_threshold,
                fetch_limit,
            )
        else:
            # Per-group rank via window function: keep the top-N classified
            # items of each topic_group, then order the remainder by relevance.
            sql = f"""
                WITH ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY topic_group
                               ORDER BY
                                   relevance_score DESC,
                                   last_scored_at DESC,
                                   view_count DESC,
                                   bvid ASC
                           ) AS group_rank
                    FROM content_cache
                    WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                      AND COALESCE(feedback_type, '') != 'dislike'
                      AND {admission_sql}
                      AND COALESCE(pool_expression, '') != ''
                      AND COALESCE(pool_topic_label, '') != ''
                      AND COALESCE(style_key, '') != ''
                      AND COALESCE(topic_group, '') != ''
                      AND (
                        source_platform != 'xiaohongshu'
                        OR content_url LIKE '%xsec_token=%'
                      )
                      {guard_sql}
                      {delight_guard_sql}
                      AND NOT EXISTS (
                        SELECT 1
                        FROM recommendations AS r
                        WHERE r.bvid = content_cache.bvid
                      )
                )
                SELECT * FROM ranked
                WHERE group_rank <= ?
                ORDER BY
                    CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                    relevance_score DESC,
                    last_scored_at DESC,
                    view_count DESC,
                    bvid ASC
                LIMIT ?
            """
            params = (
                *admission_params,
                *guard_params,
                delight_threshold,
                max_per_topic_group,
                fetch_limit,
            )
        cursor = self.conn.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return self._balance_pool_rows(rows, limit=limit)

    def _pool_servable_where_clause(self, xhs_self_nickname: str) -> tuple[str, tuple[Any, ...]]:
        """Shared WHERE fragment + params defining a ``serve()``-loadable row.

        Central definition of "servable right now", mirroring the gate baked
        into ``get_pool_candidates`` / ``_load_available_pool_candidate_rows``:
        fresh, not disliked, at/above the admission floor, fully classified
        (pool_expression / pool_topic_label / style_key / topic_group), xhs
        rows carrying an ``xsec_token``, not claimed by the delight channel,
        and not already recommended. Returns the fragment (no leading
        ``WHERE``, references the ``content_cache`` table) and its bind params.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        delight_threshold = self.dynamic_delight_threshold(
            default_threshold=_DELIGHT_CLAIM_MIN_SCORE
        )
        delight_guard_sql = _delight_claim_guard_sql()
        clause = f"""
            COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(pool_expression, '') != ''
              AND COALESCE(pool_topic_label, '') != ''
              AND COALESCE(style_key, '') != ''
              AND COALESCE(topic_group, '') != ''
              AND (
                source_platform != 'xiaohongshu'
                OR content_url LIKE '%xsec_token=%'
              )
              {guard_sql}
              {delight_guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
        """
        return clause, (*admission_params, *guard_params, delight_threshold)

    def get_pool_candidates_for_platform(
        self,
        platform: str,
        limit: int = 5,
        *,
        xhs_self_nickname: str = "",
    ) -> list[dict[str, Any]]:
        """Fetch up to ``limit`` servable pool rows for one platform token.

        Companion to ``get_pool_candidates`` powering the recommendation serve
        window's platform floor: when a relevance-ordered window happens to be
        all-bilibili, this back-fills a stocked non-bilibili platform so it
        can't be silently dropped for hours. Applies the exact servability
        guards and relevance ordering of ``get_pool_candidates`` plus a
        ``source_platform`` filter (``COALESCE(NULLIF(source_platform, ''),
        'bilibili')``), and drops recently-viewed / non-linkable rows so every
        returned row is one ``serve()`` can actually load.
        """
        token = str(platform or "").strip().lower() or "bilibili"
        fetch_limit = max(0, int(limit))
        if fetch_limit <= 0:
            return []
        self._ensure_fresh_read()
        where_clause, where_params = self._pool_servable_where_clause(xhs_self_nickname)
        sql = f"""
            SELECT *
            FROM content_cache
            WHERE {where_clause}
              AND LOWER(COALESCE(NULLIF(source_platform, ''), 'bilibili')) = ?
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                view_count DESC,
                bvid ASC
            LIMIT ?
        """
        # Over-fetch so viewed / non-linkable drops still leave up to `limit`.
        cursor = self.conn.execute(sql, (*where_params, token, fetch_limit * 4 + 8))
        viewed_content_keys = self.get_recent_viewed_content_keys()
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if not str(row_dict.get("bvid", "")).strip():
                continue
            if self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row_dict.get("source"),
                row_dict.get("source_platform"),
                row_dict.get("content_url"),
            ):
                continue
            rows.append(row_dict)
            if len(rows) >= fetch_limit:
                break
        return rows

    def list_servable_pool_platforms(self, *, xhs_self_nickname: str = "") -> list[str]:
        """Return the distinct platform tokens among currently-servable rows.

        Same servability gate as ``get_pool_candidates`` (via
        ``_load_available_pool_candidate_rows``, which also drops
        recently-viewed and non-linkable rows). Used by the serve window's
        platform floor to detect stocked platforms a single relevance-ordered
        window can silently drop. Tokens are lowercased and default to
        ``"bilibili"`` when ``source_platform`` is blank, matching
        ``RecommendationEngine._platform_token``.
        """
        rows = self._load_available_pool_candidate_rows(xhs_self_nickname=xhs_self_nickname)
        platforms: set[str] = set()
        for row in rows:
            token = str(row.get("source_platform", "") or "").strip().lower() or "bilibili"
            platforms.add(token)
        return sorted(platforms)

    def count_pool_candidates(
        self, *, max_per_topic_group: int = 3, xhs_self_nickname: str = ""
    ) -> int:
        """Return how many fresh candidates are immediately available for reshuffle.

        v0.3.57+: matches ``get_pool_candidates`` precompute gate — rows
        without ``pool_expression`` / ``pool_topic_label`` are excluded so
        the popup's "还有 N 条" never overstates what serve() can actually
        return.

        v0.3.66+: also requires ``style_key`` / ``topic_group`` — content
        must be classified before it can be served, regardless of source
        platform.

        v0.3.91+: applies the same ``max_per_topic_group`` window as
        ``get_pool_candidates`` so concentrated topic groups don't inflate
        the displayed count beyond what ``serve()`` can actually load.
        """
        return len(
            self._load_available_pool_candidate_rows(
                max_per_topic_group=max_per_topic_group,
                xhs_self_nickname=xhs_self_nickname,
            )
        )

    def _load_available_pool_candidate_rows(
        self, *, max_per_topic_group: int = 3, xhs_self_nickname: str = ""
    ) -> list[dict[str, Any]]:
        """Load rows counted by the frontend-visible pool availability gate.

        Applies the delight claim guard like ``get_pool_candidates`` so
        the availability count never includes surprise-channel rows serve()
        would refuse to load.
        """
        self._ensure_fresh_read()
        admission_sql, admission_params = self._pool_admission_sql()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        delight_threshold = self.dynamic_delight_threshold(
            default_threshold=_DELIGHT_CLAIM_MIN_SCORE
        )
        delight_guard_sql = _delight_claim_guard_sql()
        if max_per_topic_group > 0:
            cursor = self.conn.execute(
                f"""
                WITH ranked AS (
                    SELECT bvid, source, source_platform, content_url,
                           ROW_NUMBER() OVER (
                               PARTITION BY topic_group
                               ORDER BY
                                   relevance_score DESC,
                                   last_scored_at DESC,
                                   view_count DESC,
                                   bvid ASC
                           ) AS group_rank
                    FROM content_cache
                    WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                      AND COALESCE(feedback_type, '') != 'dislike'
                      AND {admission_sql}
                      AND COALESCE(pool_expression, '') != ''
                      AND COALESCE(pool_topic_label, '') != ''
                      AND COALESCE(style_key, '') != ''
                      AND COALESCE(topic_group, '') != ''
                      AND (
                        source_platform != 'xiaohongshu'
                        OR content_url LIKE '%xsec_token=%'
                      )
                      {guard_sql}
                      {delight_guard_sql}
                      AND NOT EXISTS (
                        SELECT 1
                        FROM recommendations AS r
                        WHERE r.bvid = content_cache.bvid
                      )
                )
                SELECT bvid, source, source_platform, content_url
                FROM ranked
                WHERE group_rank <= ?
                """,
                (*admission_params, *guard_params, delight_threshold, max_per_topic_group),
            )
        else:
            cursor = self.conn.execute(
                f"""
                SELECT bvid, source, source_platform, content_url
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND {admission_sql}
                  AND COALESCE(pool_expression, '') != ''
                  AND COALESCE(pool_topic_label, '') != ''
                  AND COALESCE(style_key, '') != ''
                  AND COALESCE(topic_group, '') != ''
                  AND (
                    source_platform != 'xiaohongshu'
                    OR content_url LIKE '%xsec_token=%'
                  )
                  {guard_sql}
                  {delight_guard_sql}
                  AND NOT EXISTS (
                    SELECT 1
                    FROM recommendations AS r
                    WHERE r.bvid = content_cache.bvid
                  )
                """,
                (*admission_params, *guard_params, delight_threshold),
            )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if not str(row_dict.get("bvid", "")).strip():
                continue
            if self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            rows.append(row_dict)
        return rows

    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 3, xhs_self_nickname: str = ""
    ) -> dict[str, int]:
        """Return frontend-visible pool availability grouped by source family."""
        rows = self._load_available_pool_candidate_rows(
            max_per_topic_group=max_per_topic_group,
            xhs_self_nickname=xhs_self_nickname,
        )
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        return dict(counts)

    def _load_pool_raw_material_rows(self) -> list[dict[str, Any]]:
        """Load raw fresh material rows governed by the raw ceiling."""
        self._ensure_fresh_read()
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT
                bvid,
                source,
                source_platform,
                content_url,
                relevance_score,
                last_scored_at,
                pool_expression,
                pool_topic_label,
                style_key,
                topic_group
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            admission_params,
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if not str(row_dict.get("bvid", "")).strip():
                continue
            if self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            rows.append(row_dict)
        return rows

    def count_pool_raw_material_candidates(self) -> int:
        """Return raw fresh material count used for raw-ceiling headroom."""
        return (
            len(self._load_pool_raw_material_rows()) + self._count_pending_discovery_raw_material()
        )

    def count_pool_raw_material_by_source(self) -> dict[str, int]:
        """Return raw fresh material grouped by source family.

        Unlike ``count_pool_candidates_by_source()``, this intentionally counts
        pending/unopenable rows such as XHS notes waiting for ``xsec_token``.
        """
        counts: dict[str, int] = defaultdict(int)
        for row in self._load_pool_raw_material_rows():
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        cursor = self.conn.execute(
            """
            SELECT source_platform, source_strategy, COUNT(*) AS count
            FROM discovery_candidates
            WHERE status IN ('pending_eval', 'evaluating', 'evaluated')
            GROUP BY source_platform, source_strategy
            """
        )
        for row in cursor.fetchall():
            source_family = _pool_source_family(row["source_strategy"], row["source_platform"])
            counts[source_family] += int(row["count"])
        return dict(counts)

    def count_pool_readiness(self, *, xhs_self_nickname: str = "") -> dict[str, int]:
        """Return pool inventory split by immediately servable and pending rows.

        ``available`` is the public "可换" count. ``raw`` is broad fresh
        material before readiness gates. ``pending`` is counted independently:
        recently viewed rows are unavailable, but they are not pending.
        """
        self._ensure_fresh_read()
        admission_sql, admission_params = self._pool_admission_sql()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        raw_cursor = self.conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              {guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            (*admission_params, *guard_params),
        )
        raw_count = int(raw_cursor.fetchone()["count"])
        pending_cursor = self.conn.execute(
            f"""
            SELECT
                bvid,
                content_id,
                source,
                source_platform,
                content_url,
                pool_expression,
                pool_topic_label,
                style_key,
                topic_group
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              {guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            (*admission_params, *guard_params),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        pending_count = 0
        for row in pending_cursor.fetchall():
            item = dict(row)
            if self._is_viewed_row(item, viewed_content_keys):
                continue
            if (
                not str(item.get("pool_expression") or "").strip()
                or not str(item.get("pool_topic_label") or "").strip()
                or not str(item.get("style_key") or "").strip()
                or not str(item.get("topic_group") or "").strip()
                or not _is_linkable_pool_source(
                    item.get("source"),
                    item.get("source_platform"),
                    item.get("content_url"),
                )
            ):
                pending_count += 1

        status_counts = self.count_discovery_candidates_by_status()
        pending_eval_count = int(status_counts.get("pending_eval", 0)) + int(
            status_counts.get("evaluating", 0)
        )
        evaluated_pending_count = int(status_counts.get("evaluated", 0))
        discovery_pending_count = pending_eval_count + evaluated_pending_count

        return {
            "available": self.count_pool_candidates(xhs_self_nickname=xhs_self_nickname),
            "raw": raw_count + discovery_pending_count,
            "pending": pending_count + discovery_pending_count,
            "pending_eval": pending_eval_count,
            "evaluated_pending": evaluated_pending_count,
        }

    def count_pool_candidates_by_source(self) -> dict[str, int]:
        """Return fresh pool counts grouped by discovery source family."""
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT bvid, source, source_platform, content_url
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            admission_params,
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        counts: dict[str, int] = defaultdict(int)
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        return dict(counts)

    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]:
        """Return fresh pool counts grouped by topic, style, and franchise."""
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT bvid, topic_group, style_key, franchise_key, source, source_platform, content_url
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(pool_expression, '') != ''
              AND COALESCE(pool_topic_label, '') != ''
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            admission_params,
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        counts: dict[str, dict[str, int]] = {
            "topic_group": defaultdict(int),
            "style_key": defaultdict(int),
            "franchise_key": defaultdict(int),
        }
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            for axis in ("topic_group", "style_key", "franchise_key"):
                value = str(row[axis] or "").strip()
                if value:
                    counts[axis][value] += 1
        return {axis: dict(axis_counts) for axis, axis_counts in counts.items()}

    def get_pool_topic_counts_by_platform(self) -> dict[str, dict[str, int]]:
        """Per-platform ``topic_group`` counts of fresh servable pool rows (P3.1).

        Same servable filter as :meth:`get_pool_distribution_counts`, but keyed by
        ``source_platform`` → ``{platform: {topic_group: count}}`` so the keyword
        planner can avoid topics saturated *on that platform* instead of pool-wide
        (a topic piled up on B站 may be absent on 小红书). Returns ``{}`` on error.
        """
        try:
            admission_sql, admission_params = self._pool_admission_sql()
            cursor = self.conn.execute(
                f"""
                SELECT bvid, topic_group, style_key, franchise_key,
                       source, source_platform, content_url
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND {admission_sql}
                  AND COALESCE(pool_expression, '') != ''
                  AND COALESCE(pool_topic_label, '') != ''
                  AND NOT EXISTS (
                    SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
                  )
                """,
                admission_params,
            )
            viewed_content_keys = self.get_recent_viewed_content_keys()
        except Exception:
            logger.debug("get_pool_topic_counts_by_platform query failed", exc_info=True)
            return {}
        counts: dict[str, dict[str, int]] = {}
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"], row["source_platform"], row["content_url"]
            ):
                continue
            platform = str(row["source_platform"] or "").strip()
            topic = str(row["topic_group"] or "").strip()
            if not platform or not topic:
                continue
            counts.setdefault(platform, defaultdict(int))[topic] += 1
        return {platform: dict(topics) for platform, topics in counts.items()}

    def get_admitted_topic_counts_by_platform(self) -> dict[str, dict[str, int]]:
        """Per-platform ``topic_group`` counts of ALL admitted content (P3.3).

        Where :meth:`get_pool_topic_counts_by_platform` counts the *current
        servable pool* (a saturation signal — too much right now), this counts
        every non-disliked, linkable row that ever made it into the cache from
        each platform, served or not — a *supply-advantage* signal: which topics
        each platform has actually delivered for this user. The keyword planner
        feeds the top topics back as a data-driven complement to the static
        ``<supply_advantage>`` table (after subtracting the platform's current
        avoid set). Returns ``{}`` on error.
        """
        try:
            admission_sql, admission_params = self._pool_admission_sql()
            cursor = self.conn.execute(
                f"""
                SELECT topic_group, source, source_platform, content_url
                FROM content_cache
                WHERE COALESCE(feedback_type, '') != 'dislike'
                  AND {admission_sql}
                  AND COALESCE(topic_group, '') != ''
                """,
                admission_params,
            )
        except Exception:
            logger.debug("get_admitted_topic_counts_by_platform query failed", exc_info=True)
            return {}
        counts: dict[str, dict[str, int]] = {}
        for row in cursor.fetchall():
            if not _is_linkable_pool_source(
                row["source"], row["source_platform"], row["content_url"]
            ):
                continue
            platform = str(row["source_platform"] or "").strip()
            topic = str(row["topic_group"] or "").strip()
            if not platform or not topic:
                continue
            counts.setdefault(platform, defaultdict(int))[topic] += 1
        return {platform: dict(topics) for platform, topics in counts.items()}

    def canonicalize_topic_groups(self, canonical_map: dict[str, str]) -> int:
        """Rewrite ``content_cache.topic_group`` to canonical form per map.

        v0.3.56+: ``canonical_map`` is built by
        ``RecommendationEngine.prewarm_supergroup_embeddings`` and maps
        normalized (lowered + stripped) topic_group → canonical form.
        Without applying it to the database rows, the merge only fires
        at serve time and downstream analytics (``get_topic_group_samples``,
        per-topic counts in popup status) see the un-merged labels.

        Returns the number of rows actually updated. Empty input or all-
        identity mappings short-circuit to 0.
        """
        if not canonical_map:
            return 0
        # Bulk update: one statement per (src → dst) pair. Pure SQL,
        # no row-level fetch. WAL-friendly because we batch in a single
        # transaction. Only rewrites rows whose lowercased+trimmed
        # topic_group exactly matches the source key — case-preserving
        # storage stays intact for non-matching rows.
        total = 0
        for src, dst in canonical_map.items():
            if src == dst or not src or not dst:
                continue
            cursor = self._execute_write(
                """
                UPDATE content_cache
                SET topic_group = ?
                WHERE LOWER(TRIM(COALESCE(topic_group, ''))) = ?
                  AND COALESCE(topic_group, '') != ?
                """,
                (dst, src, dst),
            )
            total += cursor.rowcount or 0
        return total

    def count_pool_by_franchise(self) -> dict[str, int]:
        """Return ``{franchise_key_lower: count}`` for fresh pool items.

        Used by discovery's pool-wide franchise quota check (v0.3.50+)
        so a franchise that already has many items in the pool can't
        keep accumulating across discovery rounds. Empty franchise_key
        is excluded — most generic content has no IP signal and the
        quota is only meaningful for series / IP / UP-driven groups.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT LOWER(TRIM(franchise_key)) AS fk, COUNT(*) AS n
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND franchise_key IS NOT NULL
              AND TRIM(franchise_key) != ''
            GROUP BY LOWER(TRIM(franchise_key))
            """,
            admission_params,
        )
        return {str(row["fk"]): int(row["n"]) for row in cursor.fetchall() if row["fk"]}

    def get_distinct_topic_groups(self) -> list[str]:
        """Return distinct non-empty ``topic_group`` values in the fresh pool.

        Used by recommendation pre-warming so the embedding cache is hot
        before the popup hits ``serve()``. Cheap GROUP BY on a small
        column with no JOIN.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT DISTINCT topic_group
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND {admission_sql}
              AND COALESCE(topic_group, '') != ''
            """,
            admission_params,
        )
        return [str(row[0]) for row in cursor.fetchall() if row and row[0]]

    def get_active_pool_topic_groups(
        self,
        *,
        limit: int = 30,
        min_count: int = 2,
    ) -> list[str]:
        """Return the top ``limit`` topic_group names currently in active pool.

        Used by ExploreStrategy to know which topics the pool already
        covers, so the LLM that generates explore domains can avoid
        re-proposing those (the v0.3.31 explore-blind-spot pattern).
        Filters to groups with at least ``min_count`` members so a
        single one-off item doesn't block exploration of an actually-
        empty area. Result is sorted by group size DESC.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT topic_group, COUNT(*) AS n
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND {admission_sql}
              AND COALESCE(topic_group, '') != ''
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            GROUP BY topic_group
            HAVING COUNT(*) >= ?
            ORDER BY n DESC, topic_group ASC
            LIMIT ?
            """,
            (*admission_params, max(1, int(min_count)), max(1, int(limit))),
        )
        return [str(row["topic_group"]) for row in cursor.fetchall()]

    def get_topic_group_samples(
        self,
        *,
        samples_per_group: int = 5,
        top_n_groups: int = 60,
    ) -> list[tuple[str, list[str]]]:
        """For each fresh-pool ``topic_group``, return up to N sample titles.

        Returns the top ``top_n_groups`` groups by member count (tie-break
        on highest in-group ``relevance_score``). Long-tail micro-topics
        (1-2 items) almost never show up together in a single 40-candidate
        recommendation batch, so investing API budget to merge-map them
        adds latency without affecting visible diversity.

        Used by the recommendation prewarmer to build an accurate
        supergroup-merge map: short Chinese labels (``赛博朋克``,
        ``动漫`` …) are catastrophically ambiguous in embedding space
        when embedded standalone — they need title-context disambiguation.
        Sample titles are picked top-by-``relevance_score`` within each
        group, so the input is reasonably stable while the pool is steady.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT topic_group, title, relevance_score
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND {admission_sql}
              AND COALESCE(topic_group, '') != ''
              AND COALESCE(title, '') != ''
            ORDER BY topic_group, relevance_score DESC, bvid
            """,
            admission_params,
        )
        by_group: dict[str, list[str]] = defaultdict(list)
        group_max_score: dict[str, float] = {}
        group_count: dict[str, int] = defaultdict(int)
        for row in cursor.fetchall():
            group = str(row["topic_group"]).strip()
            title = str(row["title"]).strip()
            if not group or not title:
                continue
            group_count[group] += 1
            score = float(row["relevance_score"] or 0.0)
            if score > group_max_score.get(group, -1.0):
                group_max_score[group] = score
            if len(by_group[group]) < samples_per_group:
                by_group[group].append(title)

        # Rank groups by member count desc, score desc, label asc (stable).
        ranked = sorted(
            by_group.keys(),
            key=lambda g: (-group_count[g], -group_max_score.get(g, 0.0), g),
        )
        return [(group, by_group[group]) for group in ranked[:top_n_groups]]

    def trim_explore_cluster_overflow(self, *, max_per_cluster: int = 3) -> int:
        """Suppress excess fresh explore items from high-risk topic clusters."""
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT bvid, title, topic_key, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(source, '') = 'explore'
            """,
            admission_params,
        )
        rows = [dict(row) for row in cursor.fetchall()]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            cluster = self._explore_risk_cluster(row)
            if not cluster:
                continue
            grouped[cluster].append(row)

        overflow_bvids: list[str] = []
        for items in grouped.values():
            ranked = sorted(
                items,
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    str(row.get("bvid", "")),
                ),
            )
            overflow_bvids.extend(
                str(row.get("bvid", "")).strip() for row in ranked[max(0, max_per_cluster) :]
            )

        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        return len(clean_bvids)

    def trim_topic_group_overflow(self, *, max_per_group: int) -> int:
        """Suppress fresh items where any single ``topic_group`` exceeds *max_per_group*.

        Generalises the source-and-keyword-specific
        :meth:`trim_explore_cluster_overflow` to a cross-source, dynamic cap on
        every populated ``topic_group`` value. Without this, a single topic
        (e.g. ``人工智能``) can accumulate hundreds of fresh candidates as
        related_chain/search/explore each keep returning the same coarse group
        across rounds — m118's per-call ``_compress_topic_repeats`` doesn't
        compose across rounds, and the explore-only cluster cap doesn't see
        related_chain or search.

        Items with empty ``topic_group`` are ignored. Within an over-cap
        group, the highest-scored / most-recently-scored items are kept;
        the rest get ``pool_status='suppressed'``.

        v0.3.31+: emits an INFO log when something gets dropped, naming
        the over-flowing groups + how many items each lost. Without this,
        the function ran silently — operators couldn't tell whether the
        diversity machinery was actually cutting anything or sleeping.
        """
        if max_per_group <= 0:
            return 0

        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT bvid, topic_group, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(topic_group, '') != ''
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            admission_params,
        )
        rows = [dict(row) for row in cursor.fetchall()]
        if not rows:
            return 0

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            group = str(row.get("topic_group", "") or "").strip().lower()
            if not group:
                continue
            grouped[group].append(row)

        overflow_bvids: list[str] = []
        # v0.3.31+: track per-group drop counts for the INFO log
        drops_per_group: dict[str, int] = {}
        for group_name, items in grouped.items():
            if len(items) <= max_per_group:
                continue
            ranked = sorted(
                items,
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    str(row.get("bvid", "")),
                ),
            )
            losers = ranked[max_per_group:]
            drops_per_group[group_name] = len(losers)
            overflow_bvids.extend(str(row.get("bvid", "")).strip() for row in losers)

        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )

        # Top 10 most-trimmed groups so the log line stays readable.
        # Demoted to DEBUG: this runs once per minute from the refresh
        # tick. When the pool is steady-state and a single group
        # consistently sits ~8 items over the cap, the same line gets
        # logged 1440x per day at INFO. Caller can lift to INFO when
        # the trim shape actually changes (see refresh.enforce_pool_cap).
        top = sorted(drops_per_group.items(), key=lambda kv: -kv[1])[:10]
        logger.debug(
            "[diversity] trim_topic_group_overflow: cap=%d, dropped=%d items "
            "across %d over-cap groups, top: %s",
            max_per_group,
            len(clean_bvids),
            len(drops_per_group),
            ", ".join(f"{g}:{c}" for g, c in top),
        )
        return len(clean_bvids)

    def trim_pool_to_target_count(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        """Suppress overflow fresh items so the pool does not exceed *target*.

        Ranking (what we keep): higher ``relevance_score`` > newer
        ``last_scored_at`` > non-``explore`` source > stable ``bvid``. Items
        already surfaced as recommendations are excluded from the count — the
        recommendation side treats the pool as a queue, so consumed rows are
        never trimmed here.

        When ``source_share_quotas`` is provided, the trim respects per-source-family
        share targets: items from source families already at or above their quota
        get suppressed *before* lower-scored items from under-quota sources.
        Without this, score-only trim systematically axes low-relevance
        sources (trending, explore) when high-relevance sources (search,
        related_chain) overflow — defeating the per-source diversity goal.
        Xiaohongshu extension channels (task/search/explore/profile) are
        collapsed under the single ``xiaohongshu`` family.
        """
        if target <= 0:
            return 0

        rows = self._load_pool_raw_material_rows()
        if len(rows) <= target:
            return 0

        ranked = sorted(
            rows,
            key=self._pool_trim_keep_key,
        )

        if source_share_quotas:
            # Three-tier protection so under-quota sources stay fully intact:
            #   protected: items from sources whose total ≤ quota, OR top-N
            #              items from sources whose total > quota (where N=quota)
            #   negotiable_tracked: bottom (total-quota) items from over-quota
            #              tracked sources
            #   negotiable_untracked: items from sources without a declared
            #              share — eligible to be cut before touching protected.
            # Order for the final keep walk: protected → negotiable_untracked
            # → negotiable_tracked.  This ensures trending (under quota) stays
            # 100% protected even when sum of in_quota > target due to
            # untracked sources eating slots.
            counts_per_source: dict[str, int] = defaultdict(int)
            for row in rows:
                source_family = _pool_source_family(
                    row.get("source", ""),
                    row.get("source_platform", ""),
                )
                counts_per_source[source_family] += 1

            protected: list[dict[str, Any]] = []
            negotiable_tracked: list[dict[str, Any]] = []
            negotiable_untracked: list[dict[str, Any]] = []
            seen: dict[str, int] = defaultdict(int)
            for row in ranked:
                source_family = _pool_source_family(
                    row.get("source", ""),
                    row.get("source_platform", ""),
                )
                quota = source_share_quotas.get(source_family)
                if quota is None:
                    negotiable_untracked.append(row)
                    continue
                if counts_per_source[source_family] <= quota:
                    # entire source under quota — every item protected
                    protected.append(row)
                else:
                    # over quota: top `quota` items protected, rest negotiable
                    if seen[source_family] < quota:
                        protected.append(row)
                        seen[source_family] += 1
                    else:
                        negotiable_tracked.append(row)
            ranked = protected + negotiable_untracked + negotiable_tracked

        overflow_rows = ranked[target:]
        overflow_bvids = [str(row.get("bvid", "")).strip() for row in overflow_rows]
        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        # v0.3.31+: log per-source breakdown so operators see whether the
        # quota guard is biting (e.g. "explore overflowing 80%" → fix the
        # discovery cycle, not the recommender).
        per_source: dict[str, int] = defaultdict(int)
        for row in overflow_rows:
            family = _pool_source_family(
                row.get("source", ""),
                row.get("source_platform", ""),
            )
            per_source[family] += 1
        breakdown = ", ".join(
            f"{src}:{cnt}" for src, cnt in sorted(per_source.items(), key=lambda kv: -kv[1])
        )
        logger.info(
            "[diversity] trim_pool_to_target_count: target=%d, before=%d, "
            "suppressed=%d, by-source: %s",
            target,
            len(rows),
            len(clean_bvids),
            breakdown or "(none)",
        )
        return len(clean_bvids)

    def trim_pool_source_overflow(self, *, source_share_quotas: dict[str, int]) -> int:
        """Suppress fresh rows that exceed platform-family pool quotas.

        ``trim_pool_to_target_count`` caps the total pool size. This pass caps
        each tracked platform family independently, so an over-filled family
        cannot occupy capacity reserved for another source while the total pool
        is still below target.
        """
        clean_quotas: dict[str, int] = {}
        for source_family, quota in source_share_quotas.items():
            try:
                clean_quotas[str(source_family)] = max(0, int(quota))
            except (TypeError, ValueError):
                continue
        if not clean_quotas:
            return 0

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._load_pool_raw_material_rows():
            source_family = _pool_source_family(row["source"], row["source_platform"])
            if source_family in clean_quotas:
                grouped[source_family].append(row)

        overflow_rows: list[dict[str, Any]] = []
        for source_family, rows in grouped.items():
            quota = clean_quotas[source_family]
            if len(rows) <= quota:
                continue
            ranked = sorted(
                rows,
                key=self._pool_trim_keep_key,
            )
            overflow_rows.extend(ranked[quota:])

        clean_bvids = [str(row.get("bvid", "")).strip() for row in overflow_rows]
        clean_bvids = [bvid for bvid in clean_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        per_source: dict[str, int] = defaultdict(int)
        for row in overflow_rows:
            family = _pool_source_family(
                row.get("source", ""),
                row.get("source_platform", ""),
            )
            per_source[family] += 1
        breakdown = ", ".join(
            f"{src}:{cnt}" for src, cnt in sorted(per_source.items(), key=lambda kv: -kv[1])
        )
        logger.info(
            "[diversity] trim_pool_source_overflow: suppressed=%d, by-source: %s",
            len(clean_bvids),
            breakdown or "(none)",
        )
        return len(clean_bvids)

    def reactivate_under_quota_pool_sources(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int],
        raw_source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        """Move suppressed candidates back to fresh for under-quota source families.

        This is a source-balance repair pass for pools that are already full but
        uneven. It only reactivates rows that are otherwise eligible for the
        recommendation pool. Reactivation is driven by frontend-available
        deficits, but bounded by raw-material headroom so pending rows already
        occupying a source's raw ceiling do not trigger more fresh inventory.
        """
        if target <= 0 or not source_share_quotas:
            return 0

        current_counts = self.count_pool_available_candidates_by_source()
        raw_counts = self.count_pool_raw_material_by_source()
        raw_quotas = raw_source_share_quotas or source_share_quotas
        deficits = {
            source_family: min(
                min(target, max(0, int(quota))) - int(current_counts.get(source_family, 0)),
                max(
                    0,
                    int(raw_quotas.get(source_family, quota))
                    - int(raw_counts.get(source_family, 0)),
                ),
            )
            for source_family, quota in source_share_quotas.items()
            if int(quota) > 0
        }
        deficits = {source: deficit for source, deficit in deficits.items() if deficit > 0}
        if not deficits:
            return 0

        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT bvid, source, source_platform, content_url, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'suppressed'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                bvid ASC
            """,
            admission_params,
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        selected_bvids: list[str] = []
        selected_counts: dict[str, int] = defaultdict(int)
        target_selection_count = sum(deficits.values())

        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            deficit = deficits.get(source_family, 0)
            if deficit <= 0 or selected_counts[source_family] >= deficit:
                continue
            selected_bvids.append(bvid)
            selected_counts[source_family] += 1
            if len(selected_bvids) >= target_selection_count:
                break

        if not selected_bvids:
            return 0

        placeholders = ", ".join("?" for _ in selected_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'fresh'
            WHERE bvid IN ({placeholders})
            """,
            selected_bvids,
        )
        return len(selected_bvids)

    @staticmethod
    def _balance_pool_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        """Round-robin sample from a relevance-ordered pool, balanced by content topic.

        Buckets by ``topic_group`` (with fallback to ``topic_key`` then a
        sentinel) so that one dominant topic in the relevance head can't
        crowd out the candidate window. Source/platform are intentionally
        ignored — content-side features drive richness, not provenance.

        The round-robin always runs (even when ``len(rows) <= limit``) so
        that the returned ordering is balanced for downstream callers
        that may sub-select; otherwise the SQL ordering can place several
        items of the same topic back-to-back at the top.
        """
        if limit <= 0 or len(rows) <= 1:
            return rows[:limit]

        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        topic_order: list[str] = []
        for row in rows:
            key = str(row.get("topic_group", "") or "").strip().lower()
            if not key:
                key = str(row.get("topic_key", "") or "").strip().lower()
            if not key:
                key = "unknown"
            if key not in buckets:
                topic_order.append(key)
            buckets[key].append(row)

        balanced: list[dict[str, Any]] = []
        while len(balanced) < limit:
            progressed = False
            for key in topic_order:
                bucket = buckets[key]
                if not bucket:
                    continue
                balanced.append(bucket.pop(0))
                progressed = True
                if len(balanced) >= limit:
                    break
            if not progressed:
                break
        return balanced[:limit]

    def get_recent_viewed_bvids(self, limit: int = 2000) -> set[str]:
        """Return recently viewed BVIDs from view events."""
        cursor = self.conn.execute(
            """
            SELECT url, metadata
            FROM events
            WHERE event_type = 'view'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        viewed_bvids: set[str] = set()
        for row in cursor.fetchall():
            bvid = self._extract_bvid_from_view_event(dict(row))
            if bvid:
                viewed_bvids.add(bvid)
        return viewed_bvids

    def get_recent_viewed_content_keys(self, limit: int = 2000) -> set[str]:
        """Return recently viewed content identities across supported sources.

        Keys are source-aware (``source_platform:content_id``) and include
        raw BVIDs for legacy Bilibili callers.
        """
        cursor = self.conn.execute(
            """
            SELECT url, metadata
            FROM events
            WHERE event_type = 'view'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        viewed_keys: set[str] = set()
        for row in cursor.fetchall():
            viewed_keys.update(self._extract_content_keys_from_view_event(dict(row)))
        return viewed_keys

    @staticmethod
    def _explore_risk_cluster(row: dict[str, Any]) -> str:
        haystack = " ".join(
            [
                str(row.get("topic_key", "") or ""),
                str(row.get("title", "") or ""),
            ]
        ).lower()
        if not haystack.strip():
            return ""
        compact = re.sub(r"\s+", "", haystack)
        for cluster, keywords in _EXPLORE_HIGH_RISK_CLUSTERS:
            if any(keyword in compact for keyword in keywords):
                return cluster
        return ""

    @staticmethod
    def _sort_timestamp_score(value: str) -> float:
        if not value:
            return 0.0
        normalized = value.replace(" ", "T")
        try:
            from datetime import datetime

            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return 0.0

    def _pool_trim_keep_key(self, row: dict[str, Any]) -> tuple[int, int, float, float, int, str]:
        """Sort fresh raw material from most worth keeping to least.

        Raw-ceiling trims include pending rows, so servability has to outrank
        relevance: never keep an unopenable row over an openable one from the
        same trim candidate set just because the pending row has a higher score.
        """
        linkable = _is_linkable_pool_source(
            row.get("source"),
            row.get("source_platform"),
            row.get("content_url"),
        )
        ready = all(
            str(row.get(field, "") or "").strip()
            for field in ("pool_expression", "pool_topic_label", "style_key", "topic_group")
        )
        return (
            0 if linkable else 1,
            0 if ready else 1,
            -float(row.get("relevance_score", 0.0) or 0.0),
            -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
            1 if str(row.get("source", "") or "") == "explore" else 0,
            str(row.get("bvid", "")),
        )

    def mark_pool_items_shown(self, bvids: list[str]) -> None:
        """Mark discovery-pool items as already shown in recommendations."""
        clean_bvids = [item for item in bvids if item]
        if not clean_bvids:
            return
        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'shown',
                recommended_at = CURRENT_TIMESTAMP
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )

    def evict_stale_pool_items(self, *, max_age_days: int = 14) -> int:
        """Mark pool items older than *max_age_days* as stale."""
        cursor = self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'stale'
            WHERE pool_status = 'fresh'
              AND discovered_at < datetime('now', '-' || ? || ' days')
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            (max_age_days,),
        )
        return cursor.rowcount

    def purge_pool_by_disliked_topics(self, topics: list[str]) -> int:
        """Mark fresh pool candidates matching new dislikes as purged.

        Matching strategy (all case-sensitive at the SQLite layer — Chinese
        text makes case folding moot and ASCII matching still works):
          1. Exact match on ``topic_key``, ``topic_group``, or ``pool_topic_label``
          2. Substring match on ``title`` or ``pool_topic_label``
             (catches "鬼畜合集" when the dislike is "鬼畜")

        Only candidates in ``pool_status = 'fresh'`` are affected — historical
        rows (``shown``, ``feedbacked``, ``stale``) are preserved for audit.
        Already-recommended items are skipped so the recommendation history
        remains intact.

        Args:
            topics: Newly added disliked topics (stripped, non-empty strings).

        Returns:
            Number of rows transitioned to ``pool_status = 'purged_by_dislike'``.
        """
        clean = [t.strip() for t in topics if t and t.strip()]
        if not clean:
            return 0

        # Build the match clause dynamically. Use parameterized queries
        # throughout — topic values may contain SQL metacharacters that must
        # not be interpolated into the query string.
        exact_placeholders = ", ".join("?" for _ in clean)
        like_conditions = " OR ".join("title LIKE ? OR pool_topic_label LIKE ?" for _ in clean)

        params: list[Any] = []
        params.extend(clean)  # topic_key IN (...)
        params.extend(clean)  # topic_group IN (...)
        params.extend(clean)  # pool_topic_label IN (...)
        for topic in clean:
            like = f"%{topic}%"
            params.append(like)  # title LIKE ?
            params.append(like)  # pool_topic_label LIKE ?

        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'purged_by_dislike'
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
              AND (
                topic_key IN ({exact_placeholders})
                OR topic_group IN ({exact_placeholders})
                OR pool_topic_label IN ({exact_placeholders})
                OR {like_conditions}
              )
            """,
            params,
        )
        return cursor.rowcount

    def get_fresh_pool_candidates_for_purge_scan(
        self,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return fresh, not-yet-recommended pool candidates for a semantic scan.

        Returns only the fields needed for embedding-based matching:
        bvid, title, topic_key, topic_group, pool_topic_label.
        """
        cursor = self.conn.execute(
            """
            SELECT bvid, title, topic_key, topic_group, pool_topic_label
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            ORDER BY discovered_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_pool_items_purged_by_dislike(self, bvids: list[str]) -> int:
        """Mark specified bvids as purged_by_dislike (only if currently fresh)."""
        clean = [b.strip() for b in bvids if b and b.strip()]
        if not clean:
            return 0
        placeholders = ", ".join("?" for _ in clean)
        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'purged_by_dislike'
            WHERE bvid IN ({placeholders})
              AND COALESCE(pool_status, 'fresh') = 'fresh'
            """,
            clean,
        )
        return cursor.rowcount

    def get_pool_candidates_needing_evaluation(
        self, limit: int = 20, *, xhs_self_nickname: str = ""
    ) -> list[dict[str, Any]]:
        """Return fresh pool candidates that lack LLM content classification.

        Targets items with empty ``style_key`` AND empty ``topic_group`` —
        typically content from non-bilibili sources (e.g. xiaohongshu) that
        was inserted directly into ``content_cache`` without passing through
        the discovery engine's ``evaluate_content`` pipeline.

        These items need LLM evaluation to receive ``style_key``,
        ``topic_group``, and ``relevance_score`` so the diversity mechanism
        in ``_select_diversified_batch`` can treat them equally alongside
        bilibili content.
        """
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(style_key, '') = ''
              AND COALESCE(topic_group, '') = ''
              AND COALESCE(relevance_score, 0) = 0
              {guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                last_scored_at DESC,
                bvid ASC
            LIMIT ?
            """,
            (*guard_params, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return rows[:limit]

    def get_pool_candidates_needing_copy(
        self, limit: int = 20, *, xhs_self_nickname: str = ""
    ) -> list[dict[str, Any]]:
        """Return fresh pool candidates missing precomputed popup copy.

        v0.3.66+: requires ``style_key`` / ``topic_group`` — content must
        be classified before expression generation.  This prevents
        unclassified items (e.g. raw XHS notes) from getting an expression
        and leaking through the serve gate without proper relevance scoring.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(style_key, '') != ''
              AND COALESCE(topic_group, '') != ''
              AND (
                COALESCE(pool_expression, '') = ''
                OR COALESCE(pool_topic_label, '') = ''
              )
              {guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                view_count DESC,
                bvid ASC
            LIMIT ?
            """,
            (*admission_params, *guard_params, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return rows[:limit]

    def update_pool_copy(
        self,
        bvid: str,
        *,
        expression: str,
        topic_label: str,
    ) -> None:
        """Persist precomputed popup copy for one pooled candidate."""
        self._execute_write(
            """
            UPDATE content_cache
            SET pool_expression = ?,
                pool_topic_label = ?
            WHERE bvid = ?
            """,
            (expression, topic_label, bvid),
        )

    def get_latest_event_id(self) -> int:
        """Return the latest event primary key."""
        cursor = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS latest_id FROM events")
        row = cursor.fetchone()
        return int(row["latest_id"]) if row is not None else 0

    def query_events_since(
        self,
        *,
        after_event_id: int,
        event_types: list[str],
    ) -> list[dict[str, Any]]:
        """Query events newer than a given id for selected event types."""
        if not event_types:
            return []
        placeholders = ", ".join("?" for _ in event_types)
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM events
            WHERE id > ? AND event_type IN ({placeholders})
            ORDER BY id ASC
            """,
            [after_event_id, *event_types],
        )
        return [dict(row) for row in cursor.fetchall()]

    def insert_recommendation(
        self,
        bvid: str,
        *,
        item_key: str = "",
        confidence: float,
        expression: str = "",
        topic: str = "",
        presented: int = 0,
    ) -> int:
        """Insert a recommendation history record."""
        cursor = self._execute_write(
            """
            INSERT INTO recommendations
                (bvid, item_key, expression, topic, confidence, presented)
            VALUES (
                ?,
                COALESCE(
                    NULLIF(?, ''),
                    (SELECT item_key FROM content_cache WHERE bvid = ?),
                    ?
                ),
                ?, ?, ?, ?
            )
            """,
            (
                bvid,
                item_key.strip(),
                bvid,
                self._fallback_recommendation_item_key(bvid),
                expression,
                topic,
                confidence,
                presented,
            ),
        )
        return cursor.lastrowid or 0

    @staticmethod
    def _fallback_recommendation_item_key(bvid: str) -> str:
        """Build a canonical fallback when no cache identity row is available."""
        storage_key = bvid.strip()
        if ":" in storage_key:
            return storage_key
        return make_item_key("bilibili", storage_key)

    def batch_insert_recommendations(
        self,
        items: list[dict[str, Any]],
    ) -> list[int]:
        """Insert N recommendation rows in one transaction; return row IDs in order.

        Single fsync replaces N (was 200-300ms each under discovery write
        contention → ~3s for the popup's 10-item batch). Returns
        ``lastrowid`` per item, computed from the auto-increment delta
        since this connection's last id.
        """
        return self.batch_insert_recommendations_and_mark_shown(items, [])

    def batch_insert_recommendations_and_mark_shown(
        self,
        items: list[dict[str, Any]],
        shown_bvids: list[str],
    ) -> list[int]:
        """Insert recommendations + mark pool items shown in **one transaction**.

        v0.3.45+: serve() used to fire two separate writes (insert recs,
        then UPDATE content_cache.pool_status='shown') and pay two
        fsyncs. Under refresh-tick write contention this stretched the
        tail to ~1s. One BEGIN IMMEDIATE / COMMIT pair gives the same
        atomic semantics with a single fsync, and the rare lost-write
        case (insert succeeds, mark fails) is now structurally
        impossible — both succeed or both rollback together.

        Returns ``lastrowid`` per item, in the same order as ``items``.
        """
        if not items and not shown_bvids:
            return []
        clean_bvids = [b for b in shown_bvids if b]
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                try:
                    ids: list[int] = []
                    for item in items:
                        cursor.execute(
                            """
                            INSERT INTO recommendations
                                (bvid, item_key, expression, topic, confidence, presented)
                            VALUES (
                                ?,
                                COALESCE(
                                    NULLIF(?, ''),
                                    (SELECT item_key FROM content_cache WHERE bvid = ?),
                                    ?
                                ),
                                ?, ?, ?, ?
                            )
                            """,
                            (
                                str(item.get("bvid", "")),
                                str(item.get("item_key", "")).strip(),
                                str(item.get("bvid", "")),
                                self._fallback_recommendation_item_key(str(item.get("bvid", ""))),
                                str(item.get("expression", "")),
                                str(item.get("topic", "")),
                                float(item.get("confidence", 0.0) or 0.0),
                                int(item.get("presented", 0) or 0),
                            ),
                        )
                        ids.append(cursor.lastrowid or 0)
                    if clean_bvids:
                        placeholders = ", ".join("?" for _ in clean_bvids)
                        cursor.execute(
                            f"""
                            UPDATE content_cache
                            SET pool_status = 'shown',
                                recommended_at = CURRENT_TIMESTAMP
                            WHERE bvid IN ({placeholders})
                            """,
                            clean_bvids,
                        )
                    self.conn.commit()
                    return ids
                except Exception:
                    self.conn.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempts <= 1:
                    raise
                attempts -= 1
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def get_recent_recommendation_signals(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Return recent recommendations with topic/source for scoring context.

        Includes both ``topic_key`` (fine, e.g. ``"洛克王国"``) and
        ``topic_group`` (coarse, e.g. ``"游戏"``) so the curator can fatigue
        on both axes. Without ``topic_group``, sibling fine-grained keys
        like ``动漫杂谈`` / ``动漫补番`` / ``动漫解说`` are independent and
        per-key fatigue never fires across them.
        """
        cursor = self.conn.execute(
            """
            SELECT r.bvid, c.topic_key, c.topic_group, c.source, r.created_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_recent_recommendation_signals_since(
        self,
        *,
        since: datetime,
    ) -> list[dict[str, Any]]:
        """Return recommendation topic/source rows shown since a timestamp."""
        self._ensure_fresh_read()
        since_text = since.isoformat(sep=" ")
        cursor = self.conn.execute(
            """
            SELECT r.bvid,
                   c.topic_key,
                   c.topic_group,
                   c.source,
                   r.created_at,
                   r.presented_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE COALESCE(r.presented_at, r.created_at) >= ?
            ORDER BY COALESCE(r.presented_at, r.created_at) DESC, r.id DESC
            """,
            (since_text,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_feedback_signals(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent feedback with UP/topic/franchise info for score
        adjustment.

        ``franchise_key`` is the LLM-tagged IP / series column (added in
        v0.3.18). Disliking one 原神 video used to only block its exact
        bvid; now the curator collects ``franchise_key`` across recent
        dislikes and down-ranks any candidate whose own ``franchise_key``
        matches — without relying on title-string heuristics.
        """
        cursor = self.conn.execute(
            """
            SELECT r.feedback_type, c.up_mid, c.up_name, c.topic_key,
                   c.source, c.title, c.franchise_key
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE r.feedback_type IS NOT NULL
            ORDER BY r.feedback_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_recommendations(
        self,
        limit: int = 100,
        *,
        exclude_processed: bool = False,
    ) -> list[dict[str, Any]]:
        """Get recommendation history ordered by newest first.

        xhs rows whose cached ``content_url`` is missing ``xsec_token``
        are filtered out — clicking them hits xhs's 300031 login wall.

        When *exclude_processed* is True, rows that have already been
        acted upon (liked / disliked / dismissed / commented) are
        omitted so the API only returns actionable items.

        ``franchise_key`` (v0.3.18) is exposed so /api/recommendations
        can apply a final per-IP cap before returning to the client —
        otherwise five 原神 / 提瓦特 items can land in one popup view.
        """
        self._ensure_fresh_read()
        admission_sql, admission_params = self._pool_admission_sql(
            score_expr="COALESCE(r.confidence, 0.0)",
            source_expr="c.source",
        )
        processed_clause = (
            "AND (r.feedback_type IS NULL OR r.feedback_type = '')" if exclude_processed else ""
        )
        cursor = self.conn.execute(
            f"""
            SELECT
                r.id,
                r.bvid,
                COALESCE(NULLIF(r.item_key, ''), c.item_key, '') AS item_key,
                r.expression,
                r.topic,
                r.confidence,
                r.presented,
                r.feedback,
                r.feedback_type,
                r.feedback_note,
                r.created_at,
                r.presented_at,
                r.feedback_at,
                COALESCE(c.title, '') AS title,
                COALESCE(c.up_name, '') AS up_name,
                COALESCE(c.cover_url, '') AS cover_url,
                COALESCE(c.content_id, r.bvid) AS content_id,
                COALESCE(c.content_url, '') AS content_url,
                COALESCE(c.source_platform, '') AS source_platform,
                COALESCE(c.content_type, 'video') AS content_type,
                COALESCE(c.body_text, '') AS body_text,
                COALESCE(c.franchise_key, '') AS franchise_key,
                COALESCE(c.duration, 0) AS duration,
                COALESCE(c.view_count, 0) AS view_count,
                COALESCE(c.like_count, 0) AS like_count,
                COALESCE(c.danmaku_count, 0) AS danmaku_count,
                COALESCE(c.favorite_count, 0) AS favorite_count,
                COALESCE(c.comment_count, 0) AS comment_count,
                COALESCE(c.up_mid, 0) AS up_mid
            FROM recommendations AS r
            LEFT JOIN content_cache AS c ON c.item_key = COALESCE(
                NULLIF(r.item_key, ''),
                (SELECT item_key FROM content_cache WHERE bvid = r.bvid),
                (
                    SELECT CASE WHEN COUNT(*) = 1 THEN MIN(item_key) END
                    FROM content_cache
                    WHERE content_id = r.bvid
                )
            )
            WHERE (
                COALESCE(c.source_platform, '') != 'xiaohongshu'
                OR COALESCE(c.content_url, '') LIKE '%xsec_token=%'
            )
            AND {admission_sql}
            {processed_clause}
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (*admission_params, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_recommendations(self) -> int:
        """Return the total number of stored recommendations."""
        self._ensure_fresh_read()
        cursor = self.conn.execute("SELECT COUNT(*) AS count FROM recommendations")
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def count_unread_recommendations(self) -> int:
        """Return the number of unpresented recommendations."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            "SELECT COUNT(*) AS count FROM recommendations WHERE presented = 0"
        )
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def get_notification_candidate(
        self,
        *,
        min_confidence: float = 0.82,
    ) -> dict[str, Any] | None:
        """Return one recommendation worth notifying the user about."""
        cursor = self.conn.execute(
            """
            SELECT
                r.id,
                r.bvid,
                r.expression,
                r.confidence,
                c.title,
                c.notification_sent,
                c.notified_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE r.presented = 0
              AND c.notification_sent = 0
              AND r.confidence >= ?
            ORDER BY r.confidence DESC, r.created_at DESC, r.id DESC
            LIMIT 1
            """,
            (min_confidence,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def mark_notification_sent(self, bvid: str) -> None:
        """Mark one cached item as already notified."""
        self._execute_write(
            """
            UPDATE content_cache
            SET notification_sent = 1,
                notified_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (bvid,),
        )

    def update_recommendation_content(
        self,
        recommendation_id: int,
        *,
        expression: str,
        topic: str,
    ) -> None:
        """Update the generated expression fields of a recommendation."""
        self._execute_write(
            """
            UPDATE recommendations
            SET expression = ?, topic = ?
            WHERE id = ?
            """,
            (expression, topic, recommendation_id),
        )

    def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, Any] | None:
        """Return a single recommendation row by primary key."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT
                r.*,
                r.topic AS topic_label,
                c.title AS title,
                c.up_name AS up_name,
                COALESCE(c.content_id, r.bvid) AS content_id,
                COALESCE(c.content_url, '') AS content_url,
                COALESCE(c.source_platform, '') AS source_platform
            FROM recommendations AS r
            LEFT JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE r.id = ?
            """,
            (recommendation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def update_recommendation_feedback(
        self,
        recommendation_id: int,
        *,
        feedback_type: str,
        feedback_note: str = "",
    ) -> None:
        """Update the current feedback state of a recommendation."""
        self._execute_write(
            """
            UPDATE recommendations
            SET feedback = ?,
                feedback_type = ?,
                feedback_note = ?,
                feedback_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (feedback_type, feedback_type, feedback_note, recommendation_id),
        )
        self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'feedbacked',
                feedback_type = ?,
                feedback_at = CURRENT_TIMESTAMP
            WHERE bvid = (
                SELECT bvid
                FROM recommendations
                WHERE id = ?
            )
            """,
            (feedback_type, recommendation_id),
        )

    def mark_recommendations_presented(self, recommendation_ids: list[int]) -> None:
        """Mark recommendations as presented and set their presented timestamp."""
        if not recommendation_ids:
            return
        placeholders = ", ".join("?" for _ in recommendation_ids)
        self._execute_write(
            f"""
            UPDATE recommendations
            SET presented = 1,
                presented_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            recommendation_ids,
        )

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_llm_usage_cache_columns(self) -> None:
        """Backfill v0.3.28+ prompt-cache columns on existing llm_usage tables."""
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(llm_usage)").fetchall()
        }
        required_columns = {
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE llm_usage ADD COLUMN {column_name} {column_type}")

    def _ensure_event_satisfaction_columns(self) -> None:
        """Backfill v0.3.x event-satisfaction columns for pre-migration DBs.

        Existing rows keep ``NULL`` in both columns; consumers treat NULL
        as ``unknown`` so the upgrade is non-blocking.
        """
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        required_columns = {
            "inferred_satisfaction": "TEXT",
            "satisfaction_reason": "TEXT",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE events ADD COLUMN {column_name} {column_type}")

    def _ensure_recommendation_feedback_columns(self) -> None:
        """Backfill recommendation feedback columns for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(recommendations)").fetchall()
        }
        required_columns = {
            "feedback_type": "TEXT",
            "feedback_note": "TEXT",
            "feedback_at": "TIMESTAMP",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE recommendations ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_runtime_columns(self) -> None:
        """Backfill content-cache runtime columns for continuous refresh."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "last_scored_at": "TIMESTAMP",
            "notification_sent": "INTEGER DEFAULT 0",
            "notified_at": "TIMESTAMP",
            "pool_status": "TEXT DEFAULT 'fresh'",
            "recommended_at": "TIMESTAMP",
            "feedback_type": "TEXT",
            "feedback_at": "TIMESTAMP",
            "source": "TEXT",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_relevance_columns(self) -> None:
        """Backfill relevance fields for existing content-cache rows."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "relevance_score": "REAL DEFAULT 0.0",
            "relevance_reason": "TEXT DEFAULT ''",
            "candidate_tier": "TEXT DEFAULT 'primary'",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_topic_columns(self) -> None:
        """Backfill topic bucketing fields for existing content-cache rows."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        if "topic_key" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN topic_key TEXT DEFAULT ''")
        if "topic_group" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN topic_group TEXT DEFAULT ''")
        if "style_key" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN style_key TEXT DEFAULT ''")
        if "franchise_key" not in existing_columns:
            # v0.3.18: LLM-tagged IP / franchise / series. Empty string for
            # general-interest content; non-empty rows let the curator
            # propagate dislikes within an IP and let
            # /api/recommendations cap how many same-franchise items
            # appear in a single response window — without relying on
            # any title-string heuristic or hardcoded alias list.
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN franchise_key TEXT DEFAULT ''")

    def _ensure_content_cache_pool_copy_columns(self) -> None:
        """Backfill precomputed pool-copy fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "pool_expression": "TEXT DEFAULT ''",
            "pool_topic_label": "TEXT DEFAULT ''",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_delight_columns(self) -> None:
        """Backfill proactive delight scoring fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "delight_score": "REAL DEFAULT 0.0",
            "delight_reason": "TEXT DEFAULT ''",
            "delight_hook": "TEXT DEFAULT ''",
            "delight_notified": "INTEGER DEFAULT 0",
            "delight_notified_at": "TIMESTAMP",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_multisource_columns(self) -> None:
        """Add multi-source content identity fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "content_id": "TEXT DEFAULT ''",
            "content_url": "TEXT DEFAULT ''",
            "source_platform": "TEXT DEFAULT 'bilibili'",
            "author_name": "TEXT DEFAULT ''",
            "body_text": "TEXT DEFAULT ''",
            "content_type": "TEXT DEFAULT 'video'",
            "favorite_count": "INTEGER DEFAULT 0",
            "collect_count": "INTEGER DEFAULT 0",
            "comment_count": "INTEGER DEFAULT 0",
            "share_count": "INTEGER DEFAULT 0",
            "danmaku_count": "INTEGER DEFAULT 0",
            "reply_count": "INTEGER DEFAULT 0",
            "retweet_count": "INTEGER DEFAULT 0",
            "bookmark_count": "INTEGER DEFAULT 0",
            # P1.8 yield provenance: the discovery_keywords.id that produced this
            # row (NULL for legacy / non-search / flag-off). Nullable, additive.
            "source_keyword_id": "INTEGER",
        }
        added = False
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")
            added = True
        if added:
            self.conn.execute("UPDATE content_cache SET content_id = bvid WHERE content_id = ''")

    def _ensure_content_identity_columns(self) -> None:
        """Backfill canonical identity columns for cache and recommendation rows."""
        cache_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        if "item_key" not in cache_columns:
            self.conn.execute(
                "ALTER TABLE content_cache ADD COLUMN item_key TEXT NOT NULL DEFAULT ''"
            )

        cache_rows = self.conn.execute(
            """
            SELECT bvid, item_key, source_platform, content_id, content_url
            FROM content_cache
            WHERE item_key = ''
            """
        ).fetchall()
        for row in cache_rows:
            storage_key = str(row["bvid"] or "").strip()
            source_platform = str(row["source_platform"] or "").strip()
            platform = canonical_source_platform(source_platform or "bilibili")
            content_id = str(row["content_id"] or "").strip()
            if not source_platform:
                content_id = storage_key
            item_key = make_item_key(
                platform,
                content_id or storage_key,
                str(row["content_url"] or ""),
            )
            self.conn.execute(
                "UPDATE content_cache SET item_key = ? WHERE bvid = ?",
                (item_key, storage_key),
            )

        recommendation_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(recommendations)").fetchall()
        }
        if "item_key" not in recommendation_columns:
            self.conn.execute(
                "ALTER TABLE recommendations ADD COLUMN item_key TEXT NOT NULL DEFAULT ''"
            )
        self._consolidate_content_identity_duplicates()
        self.conn.execute(
            """
            UPDATE recommendations AS r
            SET item_key = COALESCE(
                (SELECT c.item_key FROM content_cache AS c WHERE c.bvid = r.bvid),
                ''
            )
            WHERE r.item_key = ''
            """
        )

    @staticmethod
    def _content_identity_metadata_missing(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() in {"", "[]", "{}"}
        if isinstance(value, (int, float)):
            return value == 0
        return False

    def _consolidate_content_identity_duplicates(self) -> None:
        """Merge legacy cache rows that normalize to one canonical identity."""
        duplicate_keys = self.conn.execute(
            """
            SELECT item_key
            FROM content_cache
            WHERE item_key != ''
            GROUP BY item_key
            HAVING COUNT(*) > 1
            ORDER BY item_key
            """
        ).fetchall()
        if not duplicate_keys:
            return

        columns = [
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        ]
        merge_columns = [column for column in columns if column not in {"bvid", "item_key"}]
        for duplicate in duplicate_keys:
            item_key = str(duplicate["item_key"])
            members = [
                dict(row)
                for row in self.conn.execute(
                    "SELECT * FROM content_cache WHERE item_key = ? ORDER BY bvid",
                    (item_key,),
                ).fetchall()
            ]
            canonical_members: list[dict[str, Any]] = []
            for member in members:
                if str(member["bvid"]) == item_key:
                    canonical_members.append(member)
                    continue
                platform = canonical_source_platform(
                    str(member.get("source_platform") or "bilibili")
                )
                content_id = str(member.get("content_id") or member.get("bvid") or "")
                try:
                    expected_storage_key = content_storage_key(
                        platform,
                        content_id,
                        str(member.get("content_url") or ""),
                    )
                except ValueError:
                    continue
                if str(member["bvid"]) == expected_storage_key:
                    canonical_members.append(member)
            keeper = min(canonical_members or members, key=lambda row: str(row["bvid"]))
            keeper_bvid = str(keeper["bvid"])
            merged = dict(keeper)
            for member in members:
                for column in merge_columns:
                    if self._content_identity_metadata_missing(
                        merged.get(column)
                    ) and not self._content_identity_metadata_missing(member.get(column)):
                        merged[column] = member[column]

            changed_columns = [
                column for column in merge_columns if merged.get(column) != keeper.get(column)
            ]
            if changed_columns:
                assignments = ", ".join(f"{column} = ?" for column in changed_columns)
                self.conn.execute(
                    f"UPDATE content_cache SET {assignments} WHERE bvid = ?",
                    [*(merged[column] for column in changed_columns), keeper_bvid],
                )

            member_bvids = [str(member["bvid"]) for member in members]
            self._redirect_legacy_saved_identity(member_bvids, item_key)
            placeholders = ", ".join("?" for _ in member_bvids)
            self.conn.execute(
                f"""
                UPDATE recommendations
                SET bvid = ?, item_key = ?
                WHERE bvid IN ({placeholders}) OR item_key = ?
                """,
                [keeper_bvid, item_key, *member_bvids, item_key],
            )
            removed_bvids = [bvid for bvid in member_bvids if bvid != keeper_bvid]
            if removed_bvids:
                removed_placeholders = ", ".join("?" for _ in removed_bvids)
                self.conn.execute(
                    f"DELETE FROM content_cache WHERE bvid IN ({removed_placeholders})",
                    removed_bvids,
                )

    def _redirect_legacy_saved_identity(self, storage_keys: list[str], item_key: str) -> None:
        """Preserve legacy saved-list identity before duplicate cache rows are removed."""
        if not storage_keys:
            return
        placeholders = ", ".join("?" for _ in storage_keys)
        for table_name in ("watch_later", "favorites"):
            exists = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            if exists is None:
                continue
            self._ensure_legacy_saved_item_key_column(table_name)
            self.conn.execute(
                f"UPDATE {table_name} SET item_key = ? WHERE bvid IN ({placeholders})",
                [item_key, *storage_keys],
            )

    def _ensure_discovery_candidate_columns(self) -> None:
        """Backfill discovery-candidate lifecycle columns for existing databases."""

        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(discovery_candidates)").fetchall()
        }
        required_columns = {
            "score_threshold": "REAL NOT NULL DEFAULT 0.0",
            "eval_attempts": "INTEGER NOT NULL DEFAULT 0",
            "batch_eval_attempts": "INTEGER NOT NULL DEFAULT 0",
            "body_text": "TEXT NOT NULL DEFAULT ''",
            "favorite_count": "INTEGER NOT NULL DEFAULT 0",
            "collect_count": "INTEGER NOT NULL DEFAULT 0",
            "comment_count": "INTEGER NOT NULL DEFAULT 0",
            "share_count": "INTEGER NOT NULL DEFAULT 0",
            "danmaku_count": "INTEGER NOT NULL DEFAULT 0",
            "reply_count": "INTEGER NOT NULL DEFAULT 0",
            "retweet_count": "INTEGER NOT NULL DEFAULT 0",
            "bookmark_count": "INTEGER NOT NULL DEFAULT 0",
            # P1.8 yield provenance: nullable, additive (existing rows stay NULL).
            "source_keyword_id": "INTEGER",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(
                f"ALTER TABLE discovery_candidates ADD COLUMN {column_name} {column_type}"
            )

    def _normalize_legacy_style_keys(self) -> None:
        """Rewrite known legacy content-form style keys to viewing-mode keys."""

        targets = (
            ("content_cache", "style_key"),
            ("discovery_candidates", "style_key"),
        )
        for table_name, column_name in targets:
            existing_columns = {
                str(row["name"])
                for row in self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            if column_name not in existing_columns:
                continue
            for legacy_key, style_key in _LEGACY_STYLE_KEY_MAP.items():
                self.conn.execute(
                    f"UPDATE {table_name} SET {column_name} = ? WHERE {column_name} = ?",
                    (style_key, legacy_key),
                )

    def _ensure_recommendation_read_indexes(self) -> None:
        """Create indexes used by recommendation and activity-feed reads."""
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_recommendations_created_id
                ON recommendations (created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_content_cache_content_id
                ON content_cache (content_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_content_cache_item_key
                ON content_cache (item_key);
            CREATE INDEX IF NOT EXISTS idx_recommendations_item_key
                ON recommendations (item_key);
        """)

    def _ensure_source_recipes_table(self) -> None:
        """Create the source_recipes table if it does not exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_recipes (
                id            TEXT PRIMARY KEY,
                source_type   TEXT NOT NULL,
                name          TEXT NOT NULL,
                strategy      TEXT NOT NULL,
                config        TEXT DEFAULT '{}',
                target_share  INTEGER DEFAULT 4,
                enabled       INTEGER DEFAULT 1,
                created_by    TEXT DEFAULT 'system',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_fetched_at TIMESTAMP
            );
        """)

    def _ensure_xhs_observed_urls_table(self) -> None:
        """Create the xhs_observed_urls table if it does not exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS xhs_observed_urls (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT NOT NULL,
                page_type   TEXT NOT NULL DEFAULT 'other',
                observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                enriched    INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_xhs_observed_urls_url
                ON xhs_observed_urls (url);
        """)

    def _ensure_chat_turns_table(self) -> None:
        """Create durable popup chat-turn storage for existing databases."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_turns (
                turn_id       TEXT PRIMARY KEY,
                session       TEXT NOT NULL DEFAULT 'popup',
                scope         TEXT NOT NULL DEFAULT 'chat',
                subject_id    TEXT NOT NULL DEFAULT '',
                subject_title TEXT NOT NULL DEFAULT '',
                message       TEXT NOT NULL DEFAULT '',
                status        TEXT NOT NULL DEFAULT 'pending',
                reply         TEXT NOT NULL DEFAULT '',
                error         TEXT NOT NULL DEFAULT '',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_chat_turns_session_created
                ON chat_turns(session, created_at, turn_id);
            CREATE INDEX IF NOT EXISTS idx_chat_turns_scope_subject
                ON chat_turns(scope, subject_id, created_at);
        """)

    def _ensure_watch_later_table(self) -> None:
        """Create the watch_later bookmarks table for existing databases."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS watch_later (
                bvid     TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note     TEXT DEFAULT '',
                item_key TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_watch_later_added
                ON watch_later(added_at DESC);
        """)
        self._ensure_legacy_saved_item_key_column("watch_later")

    def _ensure_discovery_keywords_table(self) -> None:
        """Create the unified search-keyword store + planner single-flight lock.

        ``discovery_keywords`` is the generation-side cache/history/yield
        ledger for the unified keyword planner (Discover backpressure
        refactor, P1). It carries the same atomic-claim + lease-reclaim
        semantics as the ``xhs_tasks`` / ``dy_tasks`` execution queues
        (``BEGIN IMMEDIATE`` claim, ``pending → claimed`` transition,
        ``claimed_at`` lease), but tracks *which words to search* rather
        than *which tabs to open*.

        The uniqueness constraint is **partial** — it only covers the
        in-flight states (``pending`` / ``claimed`` / ``executing``) so a
        word that has already been ``used`` (or ``expired``) does not block
        the planner from re-generating the same word on a later cycle once
        it has rolled out of the dedup window.

        ``discovery_planner_lock`` is a tiny CAS row used to single-flight
        the planner across loops / restarts. It is held only for *short*
        transactions (acquire → commit → run LLM unlocked → reacquire to
        write), never across the LLM call, so it cannot block other
        SQLite writers.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS discovery_keywords (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                platform          TEXT NOT NULL,
                keyword           TEXT NOT NULL,
                keyword_kind      TEXT NOT NULL DEFAULT 'regular',
                profile_kw_digest TEXT NOT NULL DEFAULT '',
                aspect_id         TEXT NOT NULL DEFAULT '',
                inspiration_backend TEXT NOT NULL DEFAULT '',
                inspiration_id    TEXT NOT NULL DEFAULT '',
                inspiration_terms TEXT NOT NULL DEFAULT '',
                expansion_id      TEXT NOT NULL DEFAULT '',
                expansion_label   TEXT NOT NULL DEFAULT '',
                angle_id          TEXT NOT NULL DEFAULT '',
                angle_label       TEXT NOT NULL DEFAULT '',
                query_kind        TEXT NOT NULL DEFAULT '',
                source_domain     TEXT NOT NULL DEFAULT '',
                source_interest   TEXT NOT NULL DEFAULT '',
                generation_reason TEXT NOT NULL DEFAULT '',
                normalized_keyword TEXT NOT NULL DEFAULT '',
                status            TEXT NOT NULL DEFAULT 'pending',
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                claimed_at        TIMESTAMP,
                executing_at      TIMESTAMP,
                used_at           TIMESTAMP,
                attempts          INTEGER NOT NULL DEFAULT 0,
                yield_count       INTEGER NOT NULL DEFAULT 0
            );
        """)
        columns = self.conn.execute("PRAGMA table_info(discovery_keywords)").fetchall()
        column_names = {str(row[1]) for row in columns}
        if "keyword_kind" not in column_names:
            self.conn.execute(
                "ALTER TABLE discovery_keywords "
                "ADD COLUMN keyword_kind TEXT NOT NULL DEFAULT 'regular'"
            )
        for name, definition in _DISCOVERY_KEYWORD_METADATA_COLUMNS.items():
            if name not in column_names:
                self.conn.execute(f"ALTER TABLE discovery_keywords ADD COLUMN {name} {definition}")
        self.conn.executescript("""
            -- Partial uniqueness: only the in-flight triplet is unique, so
            -- used/expired history never blocks re-generating the same word.
            DROP INDEX IF EXISTS uq_discovery_keywords_inflight;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_discovery_keywords_inflight
                ON discovery_keywords (platform, keyword, profile_kw_digest, keyword_kind)
                WHERE status IN ('pending', 'claimed', 'executing');
            CREATE INDEX IF NOT EXISTS idx_discovery_keywords_status_digest
                ON discovery_keywords (platform, keyword_kind, status, profile_kw_digest);
            CREATE INDEX IF NOT EXISTS idx_discovery_keywords_status_used
                ON discovery_keywords (platform, keyword_kind, status, used_at);

            CREATE TABLE IF NOT EXISTS discovery_planner_lock (
                lock_name    TEXT PRIMARY KEY,
                owner        TEXT NOT NULL DEFAULT '',
                locked_until TIMESTAMP NOT NULL,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- P1.8 yield ledger. One row per (keyword, admitted content) the
            -- keyword produced. The composite primary key makes the yield
            -- backfill idempotent: a retried / out-of-order / duplicate admit
            -- of the SAME (keyword, content) is an INSERT-OR-IGNORE no-op, so
            -- ``discovery_keywords.yield_count`` is only ever bumped once per
            -- distinct produced content. Decoupled from ``used`` (P1.7).
            CREATE TABLE IF NOT EXISTS discovery_keyword_yield (
                keyword_id  INTEGER NOT NULL,
                content_id  TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (keyword_id, content_id)
            );

            CREATE TABLE IF NOT EXISTS discovery_inspiration_probe_cache (
                platform            TEXT NOT NULL,
                profile_kw_digest   TEXT NOT NULL,
                aspect_id           TEXT NOT NULL,
                query_kind          TEXT NOT NULL,
                probe_backend       TEXT NOT NULL DEFAULT 'exa',
                freshness_digest    TEXT NOT NULL DEFAULT '',
                seed_query          TEXT NOT NULL,
                domain_filters_json TEXT NOT NULL DEFAULT '[]',
                inspiration_id      TEXT NOT NULL,
                source_domains_json TEXT NOT NULL DEFAULT '[]',
                source_terms_json   TEXT NOT NULL DEFAULT '[]',
                evidence_titles_json TEXT NOT NULL DEFAULT '[]',
                evidence_urls_json  TEXT NOT NULL DEFAULT '[]',
                reason              TEXT NOT NULL DEFAULT '',
                risk_flags_json     TEXT NOT NULL DEFAULT '[]',
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at          TIMESTAMP,
                selected_count      INTEGER NOT NULL DEFAULT 0,
                yielded_count       INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (
                    platform, profile_kw_digest, aspect_id, query_kind, probe_backend,
                    freshness_digest, seed_query, inspiration_id
                )
            );
            CREATE INDEX IF NOT EXISTS idx_discovery_inspiration_probe_lookup
                ON discovery_inspiration_probe_cache (
                    platform, profile_kw_digest, aspect_id, query_kind, created_at
                );

            CREATE TABLE IF NOT EXISTS discovery_inspiration_expansion_cache (
                platform            TEXT NOT NULL,
                profile_kw_digest   TEXT NOT NULL,
                aspect_id           TEXT NOT NULL,
                query_kind          TEXT NOT NULL,
                inspiration_id      TEXT NOT NULL,
                parent_expansion_id TEXT NOT NULL DEFAULT '',
                expansion_id        TEXT NOT NULL,
                hop                 INTEGER NOT NULL DEFAULT 1,
                relation            TEXT NOT NULL DEFAULT '',
                text                TEXT NOT NULL DEFAULT '',
                detail_axes_json    TEXT NOT NULL DEFAULT '[]',
                source_terms_json   TEXT NOT NULL DEFAULT '[]',
                curator_decision    TEXT NOT NULL DEFAULT '',
                curator_score       REAL NOT NULL DEFAULT 0.0,
                curator_reason      TEXT NOT NULL DEFAULT '',
                curator_feedback    TEXT NOT NULL DEFAULT '',
                risk_flags_json     TEXT NOT NULL DEFAULT '[]',
                status              TEXT NOT NULL DEFAULT 'new',
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at          TIMESTAMP,
                last_selected_at    TIMESTAMP,
                selected_count      INTEGER NOT NULL DEFAULT 0,
                realized_count      INTEGER NOT NULL DEFAULT 0,
                yielded_count       INTEGER NOT NULL DEFAULT 0,
                failed_count        INTEGER NOT NULL DEFAULT 0,
                cooldown_until      TIMESTAMP,
                PRIMARY KEY (
                    platform, profile_kw_digest, aspect_id, query_kind,
                    inspiration_id, expansion_id
                )
            );
            CREATE INDEX IF NOT EXISTS idx_discovery_inspiration_expansion_lookup
                ON discovery_inspiration_expansion_cache (
                    platform, profile_kw_digest, aspect_id, inspiration_id, status
                );

            CREATE TABLE IF NOT EXISTS discovery_inspiration_axis (
                axis_id             TEXT PRIMARY KEY,
                interest_label      TEXT NOT NULL,
                interest_id         TEXT,
                axis_label          TEXT NOT NULL,
                axis_kind           TEXT NOT NULL,
                example_terms       TEXT,
                evidence_refs       TEXT,
                source              TEXT NOT NULL,
                time_sensitive      INTEGER NOT NULL DEFAULT 0,
                freshness_ttl_days  INTEGER,
                yield_score         REAL NOT NULL DEFAULT 0.0,
                admissions          INTEGER NOT NULL DEFAULT 0,
                use_count           INTEGER NOT NULL DEFAULT 0,
                status              TEXT NOT NULL DEFAULT 'active',
                created_at          TEXT NOT NULL,
                last_used_at        TEXT,
                last_refreshed_at   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_discovery_inspiration_axis_interest
                ON discovery_inspiration_axis (interest_label, status);

            CREATE TABLE IF NOT EXISTS discovery_interest_selection_ledger (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                source_interest     TEXT NOT NULL,
                normalized_interest TEXT NOT NULL,
                query_kind          TEXT NOT NULL DEFAULT '',
                selection_scope     TEXT NOT NULL DEFAULT 'production',
                profile_kw_digest   TEXT NOT NULL DEFAULT '',
                selected_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_discovery_interest_selection_lookup
                ON discovery_interest_selection_ledger (
                    selection_scope, query_kind, normalized_interest, selected_at
                );
        """)
        axis_columns = {
            str(row[1])
            for row in self.conn.execute("PRAGMA table_info(discovery_inspiration_axis)").fetchall()
        }
        for name, definition in _DISCOVERY_INSPIRATION_AXIS_YIELD_COLUMNS.items():
            if name not in axis_columns:
                self.conn.execute(
                    f"ALTER TABLE discovery_inspiration_axis ADD COLUMN {name} {definition}"
                )

    # ── Discovery keyword store (unified search-keyword planner) ──
    #
    # Status machine:
    #   pending → claimed → (inline:    used / failed)
    #                     → (async: executing → used / failed)
    #   any in-flight state → pending (lease reclaim / budget rollback)
    #   pending (stale digest) → expired
    # ``used`` only ever lands at the terminal (never at enqueue time); the
    # word stays "in flight" until its fetch actually completes. yield_count
    # is backfilled later (P1.8) at admission time; P1.1 only stores the column.

    def insert_pending_keywords(
        self,
        platform: str,
        keywords: Sequence[str],
        profile_kw_digest: str,
        *,
        keyword_kind: str = "regular",
        metadata_by_keyword: Mapping[str, Mapping[str, object]] | None = None,
    ) -> int:
        """Batch-insert ``pending`` keywords, ignoring in-flight duplicates.

        The partial unique index ``uq_discovery_keywords_inflight`` means a
        word already ``pending`` / ``claimed`` / ``executing`` for the same
        ``(platform, profile_kw_digest, keyword_kind)`` is silently skipped
        (``OR IGNORE``);
        a word that is only present as ``used`` / ``expired`` history does
        **not** conflict, so the same word can be regenerated. Blank /
        duplicate words within ``keywords`` are de-duplicated up front.

        Returns the number of rows actually inserted.
        """
        platform_key = platform.strip()
        digest = profile_kw_digest.strip()
        kind = _normalize_keyword_kind(keyword_kind)
        seen: set[str] = set()
        metadata_lookup = {
            str(key).strip(): value for key, value in (metadata_by_keyword or {}).items()
        }
        rows: list[tuple[Any, ...]] = []
        for raw in keywords:
            word = str(raw).strip()
            if not word or word in seen:
                continue
            seen.add(word)
            metadata = metadata_lookup.get(word, {})
            rows.append(
                (
                    platform_key,
                    word,
                    kind,
                    digest,
                    _metadata_text(metadata.get("aspect_id")),
                    _metadata_text(metadata.get("inspiration_backend")),
                    _metadata_text(metadata.get("inspiration_id")),
                    _metadata_text(metadata.get("inspiration_terms")),
                    _metadata_text(metadata.get("expansion_id")),
                    _metadata_text(metadata.get("expansion_label")),
                    _metadata_text(metadata.get("angle_id")),
                    _metadata_text(metadata.get("angle_label")),
                    _metadata_text(metadata.get("query_kind") or kind),
                    _metadata_text(metadata.get("source_domain")),
                    _metadata_text(metadata.get("source_interest")),
                    _metadata_text(metadata.get("generation_reason")),
                    _metadata_text(
                        metadata.get("normalized_keyword") or _normalized_keyword_text(word)
                    ),
                    _metadata_text(metadata.get("grounding_source")),
                )
            )
        if not rows:
            return 0
        before = self.conn.total_changes
        self._execute_many_write(
            """
            INSERT OR IGNORE INTO discovery_keywords
                (
                    platform, keyword, keyword_kind, profile_kw_digest,
                    aspect_id, inspiration_backend, inspiration_id, inspiration_terms,
                    expansion_id, expansion_label, angle_id, angle_label, query_kind,
                    source_domain, source_interest, generation_reason, normalized_keyword,
                    grounding_source, status
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            rows,
        )
        return self.conn.total_changes - before

    def count_pending_keywords(
        self,
        platform: str,
        profile_kw_digest: str,
        *,
        keyword_kind: str = "regular",
    ) -> int:
        """Return how many ``pending`` keywords exist for this digest."""
        kind = _normalize_keyword_kind(keyword_kind)
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM discovery_keywords
            WHERE platform = ?
              AND keyword_kind = ?
              AND status = 'pending'
              AND profile_kw_digest = ?
            """,
            (platform.strip(), kind, profile_kw_digest.strip()),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def claim_keywords(
        self,
        platform: str,
        n: int,
        *,
        keyword_kind: str = "regular",
    ) -> list[dict[str, Any]]:
        """Atomically claim up to ``n`` ``pending`` keywords for a platform.

        Uses a short-lived connection + ``BEGIN IMMEDIATE`` so two concurrent
        callers serialize and never receive overlapping rows: the second
        writer blocks until the first commits, after which the just-claimed
        rows are no longer ``pending`` and cannot be re-selected. Mirrors the
        ``xhs_tasks`` / ``dy_tasks`` ``next_pending`` claim, generalized to a
        batch. Returns the claimed rows (``status='claimed'``), oldest first.
        """
        claim_n = max(0, int(n))
        if claim_n <= 0:
            return []
        kind = _normalize_keyword_kind(keyword_kind)
        self._ensure_fresh_read()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                """
                SELECT id
                FROM discovery_keywords
                WHERE platform = ?
                  AND keyword_kind = ?
                  AND status = 'pending'
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (platform.strip(), kind, claim_n),
            ).fetchall()
            if not pending:
                conn.commit()
                return []
            ids = [int(row["id"]) for row in pending]
            placeholders = ", ".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE discovery_keywords
                SET status = 'claimed', claimed_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders}) AND status = 'pending'
                """,
                ids,
            )
            claimed = conn.execute(
                f"""
                SELECT *
                FROM discovery_keywords
                WHERE id IN ({placeholders}) AND status = 'claimed'
                ORDER BY claimed_at ASC, id ASC
                """,
                ids,
            ).fetchall()
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return [dict(row) for row in claimed]

    def mark_keyword_executing(self, keyword_id: int) -> None:
        """Move a ``claimed`` keyword to ``executing`` (async fetch enqueued)."""
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'executing', executing_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )

    def mark_keyword_used(self, keyword_id: int) -> None:
        """Mark a keyword ``used`` (terminal — its fetch has completed)."""
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'used', used_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )

    def mark_keyword_failed(self, keyword_id: int) -> int:
        """Mark a keyword ``failed`` and bump ``attempts``.

        Returns the new ``attempts`` count so the caller can decide whether
        to retry (re-pend) or treat the word as terminally failed.
        """
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'failed',
                attempts = attempts + 1
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )
        row = self.conn.execute(
            "SELECT attempts FROM discovery_keywords WHERE id = ?",
            (int(keyword_id),),
        ).fetchone()
        return int(row["attempts"]) if row is not None else 0

    def rollback_keyword_to_pending(self, keyword_id: int) -> None:
        """Return a ``claimed`` keyword to ``pending`` (budget-rejection rollback).

        Used when a claim succeeded but the downstream enqueue was rejected
        (e.g. daily budget exhausted) so no fetch ever ran — the word must go
        back into the pool rather than be burned. Only ``claimed`` rolls back;
        ``executing`` rows already have an in-flight task and are left alone.
        """
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'pending', claimed_at = NULL
            WHERE id = ? AND status = 'claimed'
            """,
            (int(keyword_id),),
        )

    def reclaim_leased_keywords(
        self,
        claim_lease_minutes: float,
        executing_timeout_minutes: float,
    ) -> int:
        """Reclaim leaked in-flight keywords back to ``pending``.

        ``claimed`` rows whose ``claimed_at`` is older than
        ``claim_lease_minutes`` (a loop crashed between claim and fetch) and
        ``executing`` rows whose ``executing_at`` is older than
        ``executing_timeout_minutes`` (an async task never reported back) are
        returned to ``pending`` so the word is not lost. Returns the number
        of rows reclaimed.
        """
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        claimed_cutoff = (now - timedelta(minutes=max(0.0, claim_lease_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        executing_cutoff = (now - timedelta(minutes=max(0.0, executing_timeout_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'pending', claimed_at = NULL, executing_at = NULL
            WHERE (status = 'claimed' AND claimed_at IS NOT NULL AND claimed_at <= ?)
               OR (status = 'executing' AND executing_at IS NOT NULL AND executing_at <= ?)
            """,
            (claimed_cutoff, executing_cutoff),
        )
        return int(cursor.rowcount or 0)

    def history_keywords(
        self,
        platform: str,
        window_size: int,
        window_hours: float,
        *,
        keyword_kind: str = "regular",
    ) -> list[str]:
        """Return recent in-flight + used keywords for dedup, newest first.

        Includes ``claimed`` / ``executing`` (in-flight, so the planner does
        not regenerate a word a fetch is about to consume) and ``used``
        (recently searched) within the rolling window. Capped at
        ``window_size`` and bounded to the last ``window_hours``. History is
        scoped by keyword pool so regular search and planner-backed explore do
        not suppress or recycle each other's queries.
        """
        from datetime import UTC, datetime, timedelta

        cap = max(0, int(window_size))
        if cap <= 0:
            return []
        kind = _normalize_keyword_kind(keyword_kind)
        self._ensure_fresh_read()
        cutoff = (datetime.now(UTC) - timedelta(hours=max(0.0, window_hours))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        rows = self.conn.execute(
            """
            SELECT keyword
            FROM discovery_keywords
            WHERE platform = ?
              AND keyword_kind = ?
              AND status IN ('claimed', 'executing', 'used')
              AND COALESCE(used_at, executing_at, claimed_at, created_at) >= ?
            ORDER BY COALESCE(used_at, executing_at, claimed_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (platform.strip(), kind, cutoff, cap),
        ).fetchall()
        return [str(row["keyword"]) for row in rows]

    def recycle_oldest_used(
        self,
        platform: str,
        n: int,
        profile_kw_digest: str,
        *,
        keyword_kind: str = "regular",
    ) -> int:
        """Recycle the oldest ``used`` keywords back to ``pending``.

        Sparse-profile safety valve: when generation can only produce words
        already in history, the planner recycles the least-recently-used words
        so the cache does not starve. Recycled rows are re-stamped with the
        current ``profile_kw_digest`` and become ``pending`` again. Rows that
        would collide with an existing in-flight row (same word already
        pending/claimed/executing for this digest) are skipped to respect the
        partial unique index. Returns the number of rows recycled.
        """
        recycle_n = max(0, int(n))
        if recycle_n <= 0:
            return 0
        digest = profile_kw_digest.strip()
        kind = _normalize_keyword_kind(keyword_kind)
        self._ensure_fresh_read()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            candidates = conn.execute(
                """
                SELECT id, keyword
                FROM discovery_keywords
                WHERE platform = ?
                  AND keyword_kind = ?
                  AND status = 'used'
                ORDER BY used_at ASC, id ASC
                """,
                (platform.strip(), kind),
            ).fetchall()
            recycled = 0
            for row in candidates:
                if recycled >= recycle_n:
                    break
                clash = conn.execute(
                    """
                    SELECT 1
                    FROM discovery_keywords
                    WHERE platform = ?
                      AND keyword = ?
                      AND profile_kw_digest = ?
                      AND keyword_kind = ?
                      AND status IN ('pending', 'claimed', 'executing')
                    LIMIT 1
                    """,
                    (platform.strip(), str(row["keyword"]), digest, kind),
                ).fetchone()
                if clash is not None:
                    continue
                conn.execute(
                    """
                    UPDATE discovery_keywords
                    SET status = 'pending',
                        profile_kw_digest = ?,
                        claimed_at = NULL,
                        executing_at = NULL,
                        used_at = NULL
                    WHERE id = ? AND status = 'used'
                    """,
                    (digest, int(row["id"])),
                )
                recycled += 1
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return recycled

    def expire_pending_by_digest(self, platform: str, current_digest: str) -> int:
        """Expire ``pending`` keywords generated under a stale profile digest.

        When the profile changes the planner expires any ``pending`` word from
        an older digest so the next generation uses the fresh profile.
        ``used`` / ``claimed`` / ``executing`` rows are left untouched
        (dedup history + in-flight work are preserved). Returns the count
        expired.
        """
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'expired'
            WHERE platform = ? AND status = 'pending' AND profile_kw_digest != ?
            """,
            (platform.strip(), current_digest.strip()),
        )
        return int(cursor.rowcount or 0)

    def purge_archived_keywords(
        self,
        retention_hours: float,
        *,
        platform: str | None = None,
    ) -> int:
        """Delete archived (``used`` / ``expired`` / ``failed``) rows past retention.

        Cleanup for rows that have left the dedup window and are no longer
        needed for yield accounting. Only terminal-archive states are purged;
        in-flight rows are never deleted. Returns the number of rows removed.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(hours=max(0.0, retention_hours))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        params: list[Any] = [cutoff]
        platform_clause = ""
        if platform is not None:
            platform_clause = " AND platform = ?"
            params.append(platform.strip())
        cursor = self._execute_write(
            f"""
            DELETE FROM discovery_keywords
            WHERE status IN ('used', 'expired', 'failed')
              AND COALESCE(used_at, executing_at, claimed_at, created_at) < ?
              {platform_clause}
            """,
            params,
        )
        return int(cursor.rowcount or 0)

    def record_keyword_interest_selection(
        self,
        source_interests: Sequence[str],
        *,
        query_kind: str = "regular",
        selection_scope: str = "production",
        profile_kw_digest: str = "",
        retention_days: int = 30,
    ) -> int:
        """Record secondary interests sampled for inspiration planning."""

        rows: list[tuple[str, str, str, str, str]] = []
        seen: set[str] = set()
        normalized_query_kind = str(query_kind or "").strip() or "regular"
        normalized_scope = str(selection_scope or "").strip() or "production"
        digest = str(profile_kw_digest or "").strip()
        for raw_label in source_interests:
            label = _display_interest_label(raw_label)
            norm = _normalize_match_text(label)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            rows.append((label, norm, normalized_query_kind, normalized_scope, digest))
        if not rows:
            return 0
        self._execute_many_write(
            """
            INSERT INTO discovery_interest_selection_ledger (
                source_interest, normalized_interest, query_kind,
                selection_scope, profile_kw_digest
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._execute_write(
            """
            DELETE FROM discovery_interest_selection_ledger
            WHERE selected_at < datetime('now', ?)
            """,
            (f"-{max(1, int(retention_days))} days",),
        )
        return len(rows)

    def get_keyword_interest_coverage_snapshot(
        self,
        *,
        limit: int = 200,
        selection_scope: str = "production",
        selection_window_days: int = 14,
    ) -> dict[str, dict[str, object]]:
        """Return coverage counters keyed by keyword ``source_interest``.

        The snapshot intentionally combines generation-side keyword history and
        admitted-pool distribution. Keyword history catches repeated search
        generation even before yield is known; admitted-pool counts cool down
        interests that already dominate the candidate pool. The selection
        ledger cools down interests as soon as they are sampled, even if the
        later search/curation stage produces no keyword rows.
        """

        cap = max(1, int(limit))
        scope = str(selection_scope or "").strip() or "production"
        window_days = max(1, int(selection_window_days))
        self._ensure_fresh_read()
        snapshot: dict[str, dict[str, object]] = defaultdict(_empty_interest_coverage)
        display_by_norm: dict[str, str] = {}

        def bucket_for(raw_label: object) -> tuple[str, dict[str, object]] | None:
            label = _display_interest_label(raw_label)
            norm = _normalize_match_text(label)
            if not norm:
                return None
            display = display_by_norm.setdefault(norm, label)
            return display, snapshot[display]

        rows = self.conn.execute(
            """
            SELECT source_interest,
                   COUNT(*) AS generated_keyword_count,
                   SUM(CASE WHEN status IN ('claimed', 'executing', 'used') THEN 1 ELSE 0 END)
                       AS selected_keyword_count,
                   SUM(COALESCE(yield_count, 0)) AS yield_count,
                   MAX(COALESCE(used_at, executing_at, claimed_at, created_at)) AS last_selected_at
            FROM discovery_keywords
            WHERE COALESCE(source_interest, '') != ''
            GROUP BY source_interest
            ORDER BY generated_keyword_count DESC, source_interest ASC
            LIMIT ?
            """,
            (cap,),
        ).fetchall()
        for row in rows:
            bucket_record = bucket_for(row["source_interest"])
            if bucket_record is None:
                continue
            _label, bucket = bucket_record
            bucket["generated_keyword_count"] = _metric_int(
                bucket.get("generated_keyword_count", 0) or 0
            ) + _metric_int(row["generated_keyword_count"] or 0)
            bucket["selected_keyword_count"] = _metric_int(
                bucket.get("selected_keyword_count", 0) or 0
            ) + _metric_int(row["selected_keyword_count"] or 0)
            bucket["yield_count"] = _metric_int(bucket.get("yield_count", 0) or 0) + _metric_int(
                row["yield_count"] or 0
            )
            bucket["last_selected_at"] = str(row["last_selected_at"] or "")

        selection_rows = self.conn.execute(
            """
            SELECT source_interest,
                   normalized_interest,
                   COUNT(*) AS interest_selection_count,
                   MAX(selected_at) AS last_interest_selected_at
            FROM discovery_interest_selection_ledger
            WHERE selection_scope = ?
              AND selected_at >= datetime('now', ?)
            GROUP BY normalized_interest
            ORDER BY interest_selection_count DESC, source_interest ASC
            LIMIT ?
            """,
            (scope, f"-{window_days} days", cap),
        ).fetchall()
        for row in selection_rows:
            bucket_record = bucket_for(row["source_interest"])
            if bucket_record is None:
                continue
            _label, bucket = bucket_record
            bucket["interest_selection_count"] = _metric_int(
                bucket.get("interest_selection_count", 0) or 0
            ) + _metric_int(row["interest_selection_count"] or 0)
            bucket["last_interest_selected_at"] = str(row["last_interest_selected_at"] or "")

        pool_rows = self.conn.execute(
            """
            SELECT COALESCE(NULLIF(pool_topic_label, ''), NULLIF(topic_group, '')) AS interest,
                   COALESCE(content_type, 'video') AS content_type,
                   COUNT(*) AS n
            FROM content_cache
            WHERE COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(pool_status, 'fresh') != 'purged_by_dislike'
              AND COALESCE(NULLIF(pool_topic_label, ''), NULLIF(topic_group, '')) IS NOT NULL
            GROUP BY interest, content_type
            ORDER BY n DESC, interest ASC
            LIMIT ?
            """,
            (cap,),
        ).fetchall()
        total_admitted = sum(int(row["n"] or 0) for row in pool_rows)
        content_type_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in pool_rows:
            bucket_record = bucket_for(row["interest"])
            if bucket_record is None:
                continue
            label, bucket = bucket_record
            count = int(row["n"] or 0)
            content_type = str(row["content_type"] or "video").strip() or "video"
            bucket["admitted_count"] = int(str(bucket.get("admitted_count") or 0)) + count
            content_type_counts[label][content_type] += count
        for label, counts in content_type_counts.items():
            bucket = snapshot[label]
            admitted_count = int(str(bucket.get("admitted_count") or 0))
            bucket["admitted_share"] = (
                float(admitted_count) / float(total_admitted) if total_admitted > 0 else 0.0
            )
            if counts:
                dominant_type, dominant_count = max(counts.items(), key=lambda item: item[1])
                bucket["dominant_content_type"] = dominant_type
                bucket["dominant_content_type_share"] = (
                    float(dominant_count) / float(admitted_count) if admitted_count > 0 else 0.0
                )
        candidate_rows = self.conn.execute(
            """
            SELECT dc.raw_payload,
                   dc.pool_topic_label,
                   dc.topic_group,
                   dc.topic_key,
                   dc.source_platform,
                   dc.content_type,
                   dk.source_interest AS keyword_source_interest
            FROM discovery_candidates dc
            LEFT JOIN discovery_keywords dk ON dk.id = dc.source_keyword_id
            WHERE COALESCE(dc.status, '') NOT IN ('rejected_duplicate')
            ORDER BY dc.last_seen_at DESC, dc.id DESC
            LIMIT ?
            """,
            (cap * 20,),
        ).fetchall()
        candidate_counts: dict[str, int] = defaultdict(int)
        candidate_platform_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        candidate_type_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in candidate_rows:
            label = str(row["keyword_source_interest"] or "").strip()
            raw_payload = str(row["raw_payload"] or "{}")
            if not label:
                try:
                    payload = json.loads(raw_payload)
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, Mapping):
                    label = str(payload.get("source_interest") or "").strip()
                    nested_metadata = payload.get("metadata")
                    if not label and isinstance(nested_metadata, Mapping):
                        label = str(nested_metadata.get("source_interest") or "").strip()
                    if not label:
                        label = str(payload.get("pool_topic_label") or "").strip()
                    if not label:
                        label = str(payload.get("topic_group") or "").strip()
            if not label:
                label = str(row["pool_topic_label"] or "").strip()
            if not label:
                label = str(row["topic_group"] or "").strip()
            if not label:
                label = str(row["topic_key"] or "").strip()
            bucket_record = bucket_for(label)
            if bucket_record is None:
                continue
            label, _bucket = bucket_record
            platform = str(row["source_platform"] or "").strip() or "unknown"
            content_type = str(row["content_type"] or "").strip() or "unknown"
            candidate_counts[label] += 1
            candidate_platform_counts[label][platform] += 1
            candidate_type_counts[label][content_type] += 1

        total_candidates = sum(candidate_counts.values())
        for label, count in candidate_counts.items():
            bucket = snapshot[label]
            bucket["candidate_count"] = count
            bucket["candidate_share"] = (
                float(count) / float(total_candidates) if total_candidates > 0 else 0.0
            )
            platform_counts = candidate_platform_counts[label]
            if platform_counts:
                dominant_platform, dominant_count = max(
                    platform_counts.items(),
                    key=lambda item: (item[1], item[0]),
                )
                bucket["dominant_candidate_platform"] = dominant_platform
                bucket["dominant_candidate_platform_share"] = (
                    float(dominant_count) / float(count) if count > 0 else 0.0
                )
            type_counts = candidate_type_counts[label]
            if type_counts:
                dominant_type, dominant_count = max(
                    type_counts.items(),
                    key=lambda item: (item[1], item[0]),
                )
                bucket["dominant_candidate_content_type"] = dominant_type
                bucket["dominant_candidate_content_type_share"] = (
                    float(dominant_count) / float(count) if count > 0 else 0.0
                )
        return {label: dict(values) for label, values in snapshot.items()}

    def migrate_keyword_interest_labels(self, mapping: Mapping[str, str]) -> int:
        """Rewrite keyword ``source_interest`` labels after profile consolidation."""

        normalized_mapping: dict[str, str] = {}
        for old, new in mapping.items():
            old_norm = _normalize_match_text(old)
            new_label = _display_interest_label(new)
            if not old_norm or not new_label or old_norm == _normalize_match_text(new_label):
                continue
            normalized_mapping[old_norm] = new_label
        if not normalized_mapping:
            return 0
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT id, source_interest
            FROM discovery_keywords
            WHERE COALESCE(source_interest, '') != ''
            """
        ).fetchall()
        updates: list[tuple[str, int]] = []
        for row in rows:
            current = str(row["source_interest"] or "")
            target = normalized_mapping.get(_normalize_match_text(current), "")
            if target and _display_interest_label(current) != target:
                updates.append((target, int(row["id"])))
        ledger_rows = self.conn.execute(
            """
            SELECT id, source_interest
            FROM discovery_interest_selection_ledger
            WHERE COALESCE(source_interest, '') != ''
            """
        ).fetchall()
        ledger_updates: list[tuple[str, str, int]] = []
        for row in ledger_rows:
            current = str(row["source_interest"] or "")
            target = normalized_mapping.get(_normalize_match_text(current), "")
            if target and _display_interest_label(current) != target:
                ledger_updates.append((target, _normalize_match_text(target), int(row["id"])))
        if updates:
            self._execute_many_write(
                "UPDATE discovery_keywords SET source_interest = ? WHERE id = ?",
                updates,
            )
        if ledger_updates:
            self._execute_many_write(
                """
                UPDATE discovery_interest_selection_ledger
                SET source_interest = ?, normalized_interest = ?
                WHERE id = ?
                """,
                ledger_updates,
            )
        return len(updates) + len(ledger_updates)

    def get_keyword_cohort_stats(self, *, window_days: int = 14) -> dict[str, object]:
        """Compare inspiration and merged keyword cohorts for enablement gating."""

        days = max(1, int(window_days))
        since_modifier = f"-{days} days"
        thresholds = {
            "min_window_days": 14,
            "min_inspiration_claimed_keywords": 200,
            "min_admissions_per_claimed_ratio": 0.8,
            "min_mean_delight_ratio": 0.95,
        }
        cohorts: dict[str, dict[str, object]] = {
            "inspiration": _empty_keyword_cohort(),
            "merged": _empty_keyword_cohort(),
        }
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT CASE WHEN COALESCE(inspiration_id, '') != ''
                        THEN 'inspiration' ELSE 'merged' END AS cohort,
                   COUNT(*) AS generated_keywords,
                   SUM(
                       CASE
                           WHEN status IN ('claimed', 'executing', 'used', 'failed')
                                OR claimed_at IS NOT NULL
                                OR executing_at IS NOT NULL
                                OR used_at IS NOT NULL
                           THEN 1 ELSE 0
                       END
                   ) AS claimed_keywords
            FROM discovery_keywords
            WHERE created_at >= datetime('now', ?)
            GROUP BY cohort
            """,
            (since_modifier,),
        ).fetchall()
        for row in rows:
            cohort = str(row["cohort"] or "")
            if cohort not in cohorts:
                continue
            bucket = cohorts[cohort]
            generated = int(row["generated_keywords"] or 0)
            claimed = int(row["claimed_keywords"] or 0)
            bucket["generated_keywords"] = generated
            bucket["claimed_keywords"] = claimed
            bucket["claimed_rate"] = float(claimed) / float(generated) if generated > 0 else 0.0

        yield_rows = self.conn.execute(
            """
            SELECT CASE WHEN COALESCE(dk.inspiration_id, '') != ''
                        THEN 'inspiration' ELSE 'merged' END AS cohort,
                   COUNT(DISTINCT y.content_id) AS admissions,
                   AVG(COALESCE(c.delight_score, 0.0)) AS mean_delight,
                   COUNT(
                       DISTINCT COALESCE(NULLIF(c.pool_topic_label, ''), NULLIF(c.topic_group, ''))
                   ) AS distinct_topics
            FROM discovery_keyword_yield y
            JOIN discovery_keywords dk ON dk.id = y.keyword_id
            LEFT JOIN content_cache c
              ON c.bvid = y.content_id OR c.content_id = y.content_id
            WHERE y.created_at >= datetime('now', ?)
            GROUP BY cohort
            """,
            (since_modifier,),
        ).fetchall()
        for row in yield_rows:
            cohort = str(row["cohort"] or "")
            if cohort not in cohorts:
                continue
            bucket = cohorts[cohort]
            admissions = int(row["admissions"] or 0)
            claimed = _metric_int(bucket.get("claimed_keywords", 0) or 0)
            distinct_topics = int(row["distinct_topics"] or 0)
            bucket["yield_attributed_admissions"] = admissions
            bucket["admissions_per_claimed_keyword"] = (
                float(admissions) / float(claimed) if claimed > 0 else 0.0
            )
            bucket["mean_delight"] = float(row["mean_delight"] or 0.0)
            bucket["distinct_topics"] = distinct_topics
            bucket["topic_diversity_per_100_admissions"] = (
                float(distinct_topics) * 100.0 / float(admissions) if admissions > 0 else 0.0
            )
        interest_selection: dict[str, dict[str, object]] = {
            "production": _empty_interest_selection_report(),
            "preview": _empty_interest_selection_report(),
        }
        selection_rows = self.conn.execute(
            """
            SELECT selection_scope,
                   source_interest,
                   query_kind,
                   COUNT(*) AS selected_count,
                   MAX(selected_at) AS last_selected_at
            FROM discovery_interest_selection_ledger
            WHERE selected_at >= datetime('now', ?)
            GROUP BY selection_scope, source_interest, query_kind
            ORDER BY selection_scope ASC, selected_count DESC, source_interest ASC
            """,
            (since_modifier,),
        ).fetchall()
        for row in selection_rows:
            scope = str(row["selection_scope"] or "").strip() or "production"
            bucket = interest_selection.setdefault(scope, _empty_interest_selection_report())
            label = _display_interest_label(row["source_interest"])
            query_kind = str(row["query_kind"] or "").strip() or "regular"
            count = _metric_int(row["selected_count"] or 0)
            by_interest = cast("dict[str, int]", bucket["by_source_interest"])
            by_query_kind = cast("dict[str, int]", bucket["by_query_kind"])
            by_interest[label] = by_interest.get(label, 0) + count
            by_query_kind[query_kind] = by_query_kind.get(query_kind, 0) + count
            bucket["total_selected_interests"] = (
                _metric_int(bucket.get("total_selected_interests", 0) or 0) + count
            )
            bucket["distinct_interests"] = len(by_interest)
            current_last = str(bucket.get("last_selected_at") or "")
            row_last = str(row["last_selected_at"] or "")
            if row_last > current_last:
                bucket["last_selected_at"] = row_last
        return {
            "window_days": days,
            "thresholds": thresholds,
            "cohorts": cohorts,
            "interest_selection": interest_selection,
            "gate": _keyword_inspiration_gate(cohorts, thresholds, days),
        }

    # ── Discovery keyword yield (P1.8 admit-time backfill) ───────

    def increment_keyword_yield(self, keyword_id: int, content_id: str) -> bool:
        """Idempotently credit one admitted content to the keyword that produced it.

        Called at admission (the single ``_cache_results`` convergence) for every
        pool item whose ``source_keyword_id`` is set. Idempotency is keyed on
        ``(keyword_id, content_id)`` via the ``discovery_keyword_yield`` ledger:
        the ledger ``INSERT OR IGNORE`` only fires once per distinct produced
        content, so a retried / partial / out-of-order admit of the same item
        does **not** double-count. ``yield_count`` is bumped only on a genuinely
        new ledger row. Decoupled from ``used`` (P1.7) — a word can be ``used``
        and still accrue yield later.

        Returns True if this call recorded a new yield (counter bumped), False
        if it was a duplicate / invalid no-op.
        """
        kid = int(keyword_id)
        cid = str(content_id or "").strip()
        if kid <= 0 or not cid:
            return False
        before = self.conn.total_changes
        self._execute_write(
            """
            INSERT OR IGNORE INTO discovery_keyword_yield (keyword_id, content_id)
            VALUES (?, ?)
            """,
            (kid, cid),
        )
        if self.conn.total_changes == before:
            # Ledger row already existed → this (keyword, content) was already
            # credited. Do not touch the counter.
            return False
        self._execute_write(
            "UPDATE discovery_keywords SET yield_count = yield_count + 1 WHERE id = ?",
            (kid,),
        )
        self._increment_inspiration_yield_for_keyword(kid)
        return True

    def keyword_yield_count(self, keyword_id: int) -> int:
        """Return the stored ``yield_count`` for a keyword (0 if unknown)."""
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT yield_count FROM discovery_keywords WHERE id = ?",
            (int(keyword_id),),
        ).fetchone()
        return int(row["yield_count"]) if row is not None else 0

    def _increment_inspiration_yield_for_keyword(self, keyword_id: int) -> None:
        """Best-effort provenance backfill from keyword yield to inspiration yield."""

        try:
            row = self.conn.execute(
                """
                SELECT platform, profile_kw_digest, keyword_kind, aspect_id, query_kind,
                       inspiration_backend, inspiration_id, expansion_id
                FROM discovery_keywords
                WHERE id = ?
                """,
                (int(keyword_id),),
            ).fetchone()
        except Exception:
            logger.debug("keyword inspiration provenance lookup failed", exc_info=True)
            return
        if row is None:
            return
        platform = str(row["platform"] or "").strip()
        digest = str(row["profile_kw_digest"] or "").strip()
        aspect_id = str(row["aspect_id"] or "").strip()
        query_kind = str(row["query_kind"] or row["keyword_kind"] or "regular").strip()
        backend = str(row["inspiration_backend"] or "exa").strip() or "exa"
        inspiration_id = str(row["inspiration_id"] or "").strip()
        expansion_id = str(row["expansion_id"] or "").strip()
        if not platform or not digest or not aspect_id or not inspiration_id:
            return
        try:
            self._execute_write(
                """
                UPDATE discovery_inspiration_probe_cache
                SET yielded_count = yielded_count + 1
                WHERE platform = ?
                  AND profile_kw_digest = ?
                  AND aspect_id = ?
                  AND query_kind = ?
                  AND probe_backend = ?
                  AND inspiration_id = ?
                """,
                (
                    platform,
                    digest,
                    aspect_id,
                    _normalize_keyword_kind(query_kind),
                    backend,
                    inspiration_id,
                ),
            )
            if expansion_id:
                self._execute_write(
                    """
                    UPDATE discovery_inspiration_expansion_cache
                    SET yielded_count = yielded_count + 1
                    WHERE platform = ?
                      AND profile_kw_digest = ?
                      AND aspect_id = ?
                      AND query_kind = ?
                      AND inspiration_id = ?
                      AND expansion_id = ?
                    """,
                    (
                        platform,
                        digest,
                        aspect_id,
                        _normalize_keyword_kind(query_kind),
                        inspiration_id,
                        expansion_id,
                    ),
                )
        except Exception:
            logger.debug("keyword inspiration yield backfill failed", exc_info=True)

    def keyword_yield_total(self, platform: str) -> int:
        """Return the platform-wide sum of ``yield_count`` across all keywords.

        Cheap single aggregate (the ``(platform, status, …)`` index already
        covers the scan) used only for the planner's per-cycle observability
        ledger (P1.9): the merged LLM call is one ``discovery.keyword_planner``
        caller (token cost can't be split per platform), so the ledger surfaces
        per-platform keyword *production* (generated) + cumulative *yield* so
        operators can still see which platform's search words actually land
        content. Counts every row's stored ``yield_count`` (used / expired
        history included) — it is a running production total, not a live-pool
        gauge. Returns 0 on any error so it never breaks a generation pass.
        """
        try:
            self._ensure_fresh_read()
            row = self.conn.execute(
                "SELECT COALESCE(SUM(yield_count), 0) AS total "
                "FROM discovery_keywords WHERE platform = ?",
                (platform.strip(),),
            ).fetchone()
        except Exception:
            logger.debug("keyword_yield_total failed for %s", platform, exc_info=True)
            return 0
        return int(row["total"]) if row is not None else 0

    # ── Discovery inspiration probe + lateral expansion cache ─────

    def upsert_inspiration_axes(
        self,
        axes: Sequence[AxisRow],
        *,
        bump_usage: bool = True,
    ) -> None:
        """Insert or merge reusable keyword-inspiration axes."""

        affected_interests: set[str] = set()
        for axis in axes:
            if not axis.axis_id or not axis.interest_label or not axis.axis_label:
                continue
            existing = self.conn.execute(
                "SELECT * FROM discovery_inspiration_axis WHERE axis_id = ?",
                (axis.axis_id,),
            ).fetchone()
            last_refreshed_at = axis.last_refreshed_at or axis.created_at
            if existing is None:
                self.conn.execute(
                    """
                    INSERT INTO discovery_inspiration_axis (
                        axis_id, interest_label, interest_id, axis_label, axis_kind,
                        example_terms, evidence_refs, source, time_sensitive,
                        freshness_ttl_days, yield_score, admissions, use_count, status,
                        created_at, last_used_at, last_refreshed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        axis.axis_id,
                        axis.interest_label,
                        axis.interest_id,
                        axis.axis_label,
                        axis.axis_kind,
                        _json_array(axis.example_terms),
                        _json_array(axis.evidence_refs),
                        axis.source,
                        int(axis.time_sensitive),
                        axis.freshness_ttl_days,
                        axis.yield_score,
                        axis.admissions,
                        axis.use_count + (1 if bump_usage else 0),
                        axis.status,
                        axis.created_at,
                        axis.last_used_at,
                        last_refreshed_at,
                    ),
                )
            else:
                # No resurrection: a retired axis keeps merging evidence but its
                # status never flips back — a proven-bad axis must not return via
                # the LLM re-proposing the same label. ``stale`` MAY be revived by
                # a fresh upsert (deliberate asymmetry: a topic can come back).
                existing_status = str(existing["status"] or "active")
                next_status = (
                    existing_status
                    if existing_status == "retired"
                    else (axis.status or existing_status)
                )
                use_count = _metric_int(existing["use_count"]) + (1 if bump_usage else 0)
                last_used_at = (
                    axis.last_used_at or _optional_text(existing["last_used_at"])
                    if bump_usage
                    else _optional_text(existing["last_used_at"])
                )
                self.conn.execute(
                    """
                    UPDATE discovery_inspiration_axis
                    SET interest_label = ?,
                        interest_id = ?,
                        axis_label = ?,
                        axis_kind = ?,
                        example_terms = ?,
                        evidence_refs = ?,
                        source = ?,
                        time_sensitive = ?,
                        freshness_ttl_days = ?,
                        yield_score = ?,
                        admissions = ?,
                        use_count = ?,
                        status = ?,
                        last_used_at = ?,
                        last_refreshed_at = ?
                    WHERE axis_id = ?
                    """,
                    (
                        axis.interest_label,
                        axis.interest_id or str(existing["interest_id"] or ""),
                        axis.axis_label,
                        axis.axis_kind,
                        _json_array_union(existing["example_terms"], axis.example_terms),
                        _json_array_union(existing["evidence_refs"], axis.evidence_refs),
                        axis.source or str(existing["source"] or ""),
                        int(axis.time_sensitive),
                        axis.freshness_ttl_days,
                        max(_metric_float(existing["yield_score"]), axis.yield_score),
                        max(_metric_int(existing["admissions"]), axis.admissions),
                        use_count,
                        next_status,
                        last_used_at,
                        last_refreshed_at,
                        axis.axis_id,
                    ),
                )
            affected_interests.add(axis.interest_label)
        self.conn.commit()
        for interest_label in sorted(affected_interests):
            self._enforce_inspiration_axis_active_cap(interest_label)

    def backfill_inspiration_axis_yield(
        self,
        *,
        window_days: int = 30,
        now: datetime,
    ) -> dict[str, int]:
        """Recompute per-axis yield stats over a trailing window (SET, not add).

        This is a full recompute with SET semantics, so it is idempotent by
        construction: the same input rows always produce the same table state,
        no watermark / dedup bookkeeping. Old admissions decay naturally as they
        slide out of the trailing window (successes must stay fresh — a feature).

        Attribution rides the persisted ``angle_id`` / ``angle_label`` columns of
        ``discovery_keywords`` (no keyword-schema change): a row is credited to an
        axis via ``angle_id`` ONLY when that id is a real axis; otherwise the id
        is re-derived from ``source_interest`` + ``angle_label`` — the same stable
        hash the axis stores. The existence check prevents a legacy row whose
        label happens to start with ``axis:`` from being mistaken for a real id.

        For every axis the yield fields are SET (even to zero) so an axis with no
        window rows lands at ``window_uses=0`` / ``admissions=0`` /
        ``yield_score = prior`` — smooth and continuous with the prior floor.
        """

        from datetime import timedelta

        window = max(1, int(window_days))
        now_utc = _axis_now_utc(now)
        since = (now_utc - timedelta(days=window)).strftime("%Y-%m-%d %H:%M:%S")
        backfilled_at = now_utc.isoformat()
        prior = _INSPIRATION_AXIS_EXPLORATION_PRIOR

        axis_rows = self.conn.execute("SELECT axis_id FROM discovery_inspiration_axis").fetchall()
        known_axis_ids = {str(row["axis_id"]) for row in axis_rows}

        uses: dict[str, int] = {}
        admissions: dict[str, int] = {}
        keyword_rows = self.conn.execute(
            """
            SELECT angle_id, angle_label, source_interest, status,
                   COALESCE(yield_count, 0) AS yield_count
            FROM discovery_keywords
            WHERE created_at >= ?
              AND (COALESCE(angle_id, '') != '' OR COALESCE(angle_label, '') != '')
            """,
            (since,),
        ).fetchall()
        for row in keyword_rows:
            axis_id = _attribute_inspiration_axis_id(
                angle_id=str(row["angle_id"] or ""),
                source_interest=str(row["source_interest"] or ""),
                angle_label=str(row["angle_label"] or ""),
                known_axis_ids=known_axis_ids,
            )
            if axis_id is None or axis_id not in known_axis_ids:
                continue
            admissions[axis_id] = admissions.get(axis_id, 0) + _metric_int(row["yield_count"])
            if str(row["status"] or "") in _INSPIRATION_CONSUMED_KEYWORD_STATUSES:
                uses[axis_id] = uses.get(axis_id, 0) + 1

        for row in axis_rows:
            axis_id = str(row["axis_id"])
            window_uses = uses.get(axis_id, 0)
            axis_admissions = admissions.get(axis_id, 0)
            yield_score = (axis_admissions + prior) / (window_uses + 1.0)
            self.conn.execute(
                """
                UPDATE discovery_inspiration_axis
                SET window_uses = ?,
                    admissions = ?,
                    yield_score = ?,
                    yield_backfilled_at = ?
                WHERE axis_id = ?
                """,
                (window_uses, axis_admissions, yield_score, backfilled_at, axis_id),
            )
        self.conn.commit()
        return {
            "axes": len(axis_rows),
            "attributed_axes": len(set(uses) | set(admissions)),
            "window_days": window,
        }

    def apply_inspiration_axis_lifecycle(self, *, now: datetime) -> dict[str, int]:
        """Apply the deterministic axis lifecycle transitions (post-backfill).

        Three transitions, in order, all keyed on the injected ``now``:

        1. ``time_sensitive`` axes past their ``freshness_ttl_days`` →
           persisted ``status='stale'`` (Phase 1 only filtered them at read
           time).
        2. Active axes given ≥ ``_INSPIRATION_AXIS_RETIRE_MIN_WINDOW_USES``
           consumption chances whose post-backfill ``yield_score`` stays below
           ``_INSPIRATION_AXIS_RETIRE_YIELD_SCORE`` → ``status='retired'``.
           Retired axes never re-enter selection and are not resurrected by
           upsert.
        3. Stale/retired rows whose ``last_refreshed_at`` is older than
           ``_INSPIRATION_AXIS_PURGE_AFTER_DAYS`` days → physical DELETE.

        Returns a ``{"staled": n, "retired": n, "purged": n}`` transition
        summary for stage telemetry.
        """

        from datetime import timedelta

        now_utc = _axis_now_utc(now)
        purge_cutoff = now_utc - timedelta(days=_INSPIRATION_AXIS_PURGE_AFTER_DAYS)

        staled_ids: list[str] = []
        active_rows = self.conn.execute(
            "SELECT * FROM discovery_inspiration_axis WHERE status = 'active'"
        ).fetchall()
        for row in active_rows:
            if _axis_is_time_expired(row, now_utc):
                staled_ids.append(str(row["axis_id"]))
        if staled_ids:
            self.conn.executemany(
                "UPDATE discovery_inspiration_axis SET status = 'stale' WHERE axis_id = ?",
                [(axis_id,) for axis_id in staled_ids],
            )

        retired = self.conn.execute(
            """
            UPDATE discovery_inspiration_axis
            SET status = 'retired'
            WHERE status = 'active'
              AND window_uses >= ?
              AND yield_score < ?
            """,
            (
                _INSPIRATION_AXIS_RETIRE_MIN_WINDOW_USES,
                _INSPIRATION_AXIS_RETIRE_YIELD_SCORE,
            ),
        ).rowcount

        purged_ids: list[str] = []
        inactive_rows = self.conn.execute(
            "SELECT axis_id, last_refreshed_at FROM discovery_inspiration_axis "
            "WHERE status IN ('stale', 'retired')"
        ).fetchall()
        for row in inactive_rows:
            refreshed_at = _parse_axis_datetime(row["last_refreshed_at"])
            if refreshed_at is not None and refreshed_at < purge_cutoff:
                purged_ids.append(str(row["axis_id"]))
        if purged_ids:
            self.conn.executemany(
                "DELETE FROM discovery_inspiration_axis WHERE axis_id = ?",
                [(axis_id,) for axis_id in purged_ids],
            )

        self.conn.commit()
        return {
            "staled": len(staled_ids),
            "retired": max(0, int(retired)),
            "purged": len(purged_ids),
        }

    def list_inspiration_axes(
        self,
        interest_labels: Sequence[str],
        *,
        limit: int,
        now: datetime,
    ) -> list[AxisRow]:
        """Return active reusable inspiration axes, ranked with a zero-yield prior."""

        labels = _unique_clean_strings(interest_labels)
        per_interest_limit = max(0, int(limit))
        if not labels or per_interest_limit <= 0:
            return []
        placeholders = ", ".join("?" for _ in labels)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM discovery_inspiration_axis
            WHERE status = 'active'
              AND interest_label IN ({placeholders})
            """,
            tuple(labels),
        ).fetchall()
        by_interest: dict[str, list[sqlite3.Row]] = {label: [] for label in labels}
        for row in rows:
            if _axis_is_time_expired(row, now):
                continue
            by_interest.setdefault(str(row["interest_label"]), []).append(row)

        result: list[AxisRow] = []
        for label in labels:
            ranked = sorted(
                by_interest.get(label, []), key=lambda row: _axis_list_sort_key(row, now)
            )
            result.extend(
                self._row_to_discovery_inspiration_axis(row) for row in ranked[:per_interest_limit]
            )
        return result

    def list_inspiration_axes_by_source(
        self,
        source: str,
        *,
        min_yield: float = 0.0,
        limit: int,
        now: datetime,
    ) -> list[AxisRow]:
        """Return active axes filtered by ``source`` (Phase 2.3, E5).

        Explore axes carry cross-domain ``interest_label``s that never match a
        selected like interest, so :meth:`list_inspiration_axes` (interest-keyed)
        cannot surface them. This mirrors that DAO's ``status='active'`` filter,
        the SAME ``_axis_is_time_expired`` time-sensitive suppression, and the
        SAME Phase-2 ``_axis_list_sort_key`` ordering (freshness × conditional
        prior floor), but keys on ``source`` and applies a raw ``yield_score >=
        min_yield`` floor — letting the explore stage reuse its own high-yield
        cross-domain axes. ``limit`` is a global (not per-interest) bound.
        """

        source_key = str(source or "").strip()
        bounded_limit = max(0, int(limit))
        if not source_key or bounded_limit <= 0:
            return []
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT *
            FROM discovery_inspiration_axis
            WHERE status = 'active'
              AND source = ?
              AND yield_score >= ?
            """,
            (source_key, float(min_yield)),
        ).fetchall()
        ranked = sorted(
            (row for row in rows if not _axis_is_time_expired(row, now)),
            key=lambda row: _axis_list_sort_key(row, now),
        )
        return [self._row_to_discovery_inspiration_axis(row) for row in ranked[:bounded_limit]]

    def _enforce_inspiration_axis_active_cap(self, interest_label: str) -> None:
        rows = self.conn.execute(
            """
            SELECT *
            FROM discovery_inspiration_axis
            WHERE interest_label = ?
              AND status = 'active'
            """,
            (interest_label,),
        ).fetchall()
        if len(rows) <= _INSPIRATION_AXIS_ACTIVE_CAP:
            return
        ranked = sorted(rows, key=_axis_cap_sort_key)
        stale_ids = [str(row["axis_id"]) for row in ranked[_INSPIRATION_AXIS_ACTIVE_CAP:]]
        self.conn.executemany(
            "UPDATE discovery_inspiration_axis SET status = 'stale' WHERE axis_id = ?",
            [(axis_id,) for axis_id in stale_ids],
        )
        self.conn.commit()

    def upsert_discovery_inspiration_seed(
        self,
        *,
        platform: str,
        profile_kw_digest: str,
        aspect_id: str,
        query_kind: str,
        seed_query: str,
        inspiration_id: str,
        source_terms: Sequence[object] | None = None,
        evidence_titles: Sequence[object] | None = None,
        evidence_urls: Sequence[object] | None = None,
        reason: str = "",
        risk_flags: Sequence[object] | None = None,
        probe_backend: str = "exa",
        freshness_digest: str = "",
        domain_filters: Sequence[object] | None = None,
        source_domains: Sequence[object] | None = None,
        expires_at: str | None = None,
    ) -> None:
        """Insert or refresh one search-derived inspiration seed."""

        self._execute_write(
            """
            INSERT INTO discovery_inspiration_probe_cache (
                platform, profile_kw_digest, aspect_id, query_kind, probe_backend,
                freshness_digest, seed_query, domain_filters_json, inspiration_id,
                source_domains_json, source_terms_json, evidence_titles_json,
                evidence_urls_json, reason, risk_flags_json, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                platform, profile_kw_digest, aspect_id, query_kind, probe_backend,
                freshness_digest, seed_query, inspiration_id
            ) DO UPDATE SET
                domain_filters_json = excluded.domain_filters_json,
                source_domains_json = excluded.source_domains_json,
                source_terms_json = excluded.source_terms_json,
                evidence_titles_json = excluded.evidence_titles_json,
                evidence_urls_json = excluded.evidence_urls_json,
                reason = excluded.reason,
                risk_flags_json = excluded.risk_flags_json,
                expires_at = excluded.expires_at
            """,
            (
                platform.strip(),
                profile_kw_digest.strip(),
                aspect_id.strip(),
                _normalize_keyword_kind(query_kind),
                probe_backend.strip() or "exa",
                freshness_digest.strip(),
                seed_query.strip(),
                _json_array(domain_filters),
                inspiration_id.strip(),
                _json_array(source_domains),
                _json_array(source_terms),
                _json_array(evidence_titles),
                _json_array(evidence_urls),
                reason.strip(),
                _json_array(risk_flags),
                expires_at,
            ),
        )

    def list_discovery_inspiration_seeds(
        self,
        platform: str,
        profile_kw_digest: str,
        *,
        aspect_id: str | None = None,
        query_kind: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return cached inspiration seeds for a profile digest."""

        clauses = ["platform = ?", "profile_kw_digest = ?"]
        params: list[Any] = [platform.strip(), profile_kw_digest.strip()]
        if aspect_id is not None:
            clauses.append("aspect_id = ?")
            params.append(aspect_id.strip())
        if query_kind is not None:
            clauses.append("query_kind = ?")
            params.append(_normalize_keyword_kind(query_kind))
        sql = f"""
            SELECT *
            FROM discovery_inspiration_probe_cache
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at ASC, seed_query ASC, inspiration_id ASC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        self._ensure_fresh_read()
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_discovery_inspiration_seed(row) for row in rows]

    def increment_discovery_inspiration_yield(
        self,
        platform: str,
        profile_kw_digest: str,
        *,
        aspect_id: str,
        query_kind: str,
        seed_query: str,
        inspiration_id: str,
        probe_backend: str = "exa",
        freshness_digest: str = "",
        source_terms: Sequence[object] | None = None,
    ) -> bool:
        """Bump the yield counter for one cached inspiration seed."""

        _ = source_terms
        cursor = self._execute_write(
            """
            UPDATE discovery_inspiration_probe_cache
            SET yielded_count = yielded_count + 1
            WHERE platform = ?
              AND profile_kw_digest = ?
              AND aspect_id = ?
              AND query_kind = ?
              AND probe_backend = ?
              AND freshness_digest = ?
              AND seed_query = ?
              AND inspiration_id = ?
            """,
            (
                platform.strip(),
                profile_kw_digest.strip(),
                aspect_id.strip(),
                _normalize_keyword_kind(query_kind),
                probe_backend.strip() or "exa",
                freshness_digest.strip(),
                seed_query.strip(),
                inspiration_id.strip(),
            ),
        )
        return int(cursor.rowcount or 0) > 0

    def upsert_discovery_inspiration_expansion(
        self,
        *,
        platform: str,
        profile_kw_digest: str,
        aspect_id: str,
        query_kind: str,
        inspiration_id: str,
        expansion_id: str,
        parent_expansion_id: str = "",
        hop: int = 1,
        relation: str = "",
        text: str = "",
        detail_axes: Sequence[object] | None = None,
        source_terms: Sequence[object] | None = None,
        curator_decision: str = "",
        curator_score: float = 0.0,
        curator_reason: str = "",
        curator_feedback: str = "",
        risk_flags: Sequence[object] | None = None,
        status: str = "new",
        expires_at: str | None = None,
        cooldown_until: str | None = None,
    ) -> None:
        """Insert or refresh one lateral expansion under an inspiration seed."""

        self._execute_write(
            """
            INSERT INTO discovery_inspiration_expansion_cache (
                platform, profile_kw_digest, aspect_id, query_kind, inspiration_id,
                parent_expansion_id, expansion_id, hop, relation, text,
                detail_axes_json, source_terms_json, curator_decision, curator_score,
                curator_reason, curator_feedback, risk_flags_json, status, expires_at,
                cooldown_until
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                platform, profile_kw_digest, aspect_id, query_kind,
                inspiration_id, expansion_id
            ) DO UPDATE SET
                parent_expansion_id = excluded.parent_expansion_id,
                hop = excluded.hop,
                relation = excluded.relation,
                text = excluded.text,
                detail_axes_json = excluded.detail_axes_json,
                source_terms_json = excluded.source_terms_json,
                curator_decision = excluded.curator_decision,
                curator_score = excluded.curator_score,
                curator_reason = excluded.curator_reason,
                curator_feedback = excluded.curator_feedback,
                risk_flags_json = excluded.risk_flags_json,
                status = excluded.status,
                expires_at = excluded.expires_at,
                cooldown_until = excluded.cooldown_until
            """,
            (
                platform.strip(),
                profile_kw_digest.strip(),
                aspect_id.strip(),
                _normalize_keyword_kind(query_kind),
                inspiration_id.strip(),
                parent_expansion_id.strip(),
                expansion_id.strip(),
                max(1, int(hop)),
                relation.strip(),
                text.strip(),
                _json_array(detail_axes),
                _json_array(source_terms),
                curator_decision.strip(),
                float(curator_score),
                curator_reason.strip(),
                curator_feedback.strip(),
                _json_array(risk_flags),
                status.strip() or "new",
                expires_at,
                cooldown_until,
            ),
        )

    def list_discovery_inspiration_expansions(
        self,
        platform: str,
        profile_kw_digest: str,
        *,
        aspect_id: str | None = None,
        inspiration_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return cached lateral expansions for a profile digest."""

        clauses = ["platform = ?", "profile_kw_digest = ?"]
        params: list[Any] = [platform.strip(), profile_kw_digest.strip()]
        if aspect_id is not None:
            clauses.append("aspect_id = ?")
            params.append(aspect_id.strip())
        if inspiration_id is not None:
            clauses.append("inspiration_id = ?")
            params.append(inspiration_id.strip())
        if status is not None:
            clauses.append("status = ?")
            params.append(status.strip())
        sql = f"""
            SELECT *
            FROM discovery_inspiration_expansion_cache
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at ASC, inspiration_id ASC, expansion_id ASC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        self._ensure_fresh_read()
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_discovery_inspiration_expansion(row) for row in rows]

    def increment_discovery_inspiration_expansion_yield(
        self,
        platform: str,
        profile_kw_digest: str,
        *,
        aspect_id: str,
        query_kind: str,
        inspiration_id: str,
        expansion_id: str,
    ) -> bool:
        """Bump the yield counter for one cached lateral expansion."""

        cursor = self._execute_write(
            """
            UPDATE discovery_inspiration_expansion_cache
            SET yielded_count = yielded_count + 1
            WHERE platform = ?
              AND profile_kw_digest = ?
              AND aspect_id = ?
              AND query_kind = ?
              AND inspiration_id = ?
              AND expansion_id = ?
            """,
            (
                platform.strip(),
                profile_kw_digest.strip(),
                aspect_id.strip(),
                _normalize_keyword_kind(query_kind),
                inspiration_id.strip(),
                expansion_id.strip(),
            ),
        )
        return int(cursor.rowcount or 0) > 0

    @staticmethod
    def _row_to_discovery_inspiration_axis(row: sqlite3.Row) -> AxisRow:
        ttl_value = row["freshness_ttl_days"]
        return AxisRow(
            axis_id=str(row["axis_id"]),
            interest_label=str(row["interest_label"]),
            interest_id=str(row["interest_id"] or ""),
            axis_label=str(row["axis_label"]),
            axis_kind=str(row["axis_kind"]),
            example_terms=tuple(_load_json_array(row["example_terms"])),
            evidence_refs=tuple(_load_json_array(row["evidence_refs"])),
            source=str(row["source"]),
            time_sensitive=bool(_metric_int(row["time_sensitive"])),
            freshness_ttl_days=None if ttl_value is None else _metric_int(ttl_value),
            yield_score=_metric_float(row["yield_score"]),
            admissions=_metric_int(row["admissions"]),
            use_count=_metric_int(row["use_count"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            last_used_at=_optional_text(row["last_used_at"]),
            last_refreshed_at=_optional_text(row["last_refreshed_at"]),
        )

    @staticmethod
    def _row_to_discovery_inspiration_seed(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "platform": str(row["platform"]),
            "profile_kw_digest": str(row["profile_kw_digest"]),
            "aspect_id": str(row["aspect_id"]),
            "query_kind": str(row["query_kind"]),
            "probe_backend": str(row["probe_backend"]),
            "freshness_digest": str(row["freshness_digest"]),
            "seed_query": str(row["seed_query"]),
            "domain_filters": _load_json_array(row["domain_filters_json"]),
            "inspiration_id": str(row["inspiration_id"]),
            "source_domains": _load_json_array(row["source_domains_json"]),
            "source_terms": _load_json_array(row["source_terms_json"]),
            "evidence_titles": _load_json_array(row["evidence_titles_json"]),
            "evidence_urls": _load_json_array(row["evidence_urls_json"]),
            "reason": str(row["reason"]),
            "risk_flags": _load_json_array(row["risk_flags_json"]),
            "selected_count": int(row["selected_count"]),
            "yielded_count": int(row["yielded_count"]),
        }

    @staticmethod
    def _row_to_discovery_inspiration_expansion(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "platform": str(row["platform"]),
            "profile_kw_digest": str(row["profile_kw_digest"]),
            "aspect_id": str(row["aspect_id"]),
            "query_kind": str(row["query_kind"]),
            "inspiration_id": str(row["inspiration_id"]),
            "parent_expansion_id": str(row["parent_expansion_id"]),
            "expansion_id": str(row["expansion_id"]),
            "hop": int(row["hop"]),
            "relation": str(row["relation"]),
            "text": str(row["text"]),
            "detail_axes": _load_json_array(row["detail_axes_json"]),
            "source_terms": _load_json_array(row["source_terms_json"]),
            "curator_decision": str(row["curator_decision"]),
            "curator_score": float(row["curator_score"]),
            "curator_reason": str(row["curator_reason"]),
            "curator_feedback": str(row["curator_feedback"]),
            "risk_flags": _load_json_array(row["risk_flags_json"]),
            "status": str(row["status"]),
            "selected_count": int(row["selected_count"]),
            "realized_count": int(row["realized_count"]),
            "yielded_count": int(row["yielded_count"]),
            "failed_count": int(row["failed_count"]),
        }

    def used_keyword_count(self, platform: str) -> int:
        """Count ``used`` keywords for a platform (P3.2 dynamic-cap denominator).

        Paired with :meth:`keyword_yield_total` to derive the platform's observed
        average yield-per-keyword (total yield / used count). Cheap single
        aggregate; returns 0 on any error so it never breaks a generation pass.
        """
        try:
            self._ensure_fresh_read()
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM discovery_keywords "
                "WHERE platform = ? AND status = 'used'",
                (platform.strip(),),
            ).fetchone()
        except Exception:
            logger.debug("used_keyword_count failed for %s", platform, exc_info=True)
            return 0
        return int(row["n"]) if row is not None else 0

    def retire_zero_yield_keywords(
        self,
        platform: str,
        *,
        min_age_minutes: float = 60.0,
    ) -> int:
        """Retire ``used`` words that have produced nothing, conservatively.

        A word that has been ``used`` for at least ``min_age_minutes`` and still
        has ``yield_count == 0`` is moved to ``expired`` so the recycler does not
        keep re-pending a search term that demonstrably never lands content.

        The age floor is the safety valve against retiring a *freshly* used word
        whose admit is still pending: inline-admit credits yield synchronously,
        but fetch-only (X / YouTube) and async (XHS) words are marked ``used`` at
        handoff and only accrue yield once the shared pipeline admits — minutes
        later. ``min_age_minutes`` must comfortably exceed that admit latency.
        Only ``used`` rows are touched; in-flight / pending / already-expired
        rows are left alone. Returns the number of rows retired.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(minutes=max(0.0, min_age_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'expired'
            WHERE platform = ?
              AND status = 'used'
              AND yield_count = 0
              AND used_at IS NOT NULL
              AND used_at <= ?
            """,
            (platform.strip(), cutoff),
        )
        return int(cursor.rowcount or 0)

    # ── Discovery keyword planner single-flight lock ─────────────

    def acquire_planner_lock(self, owner: str, lease_seconds: float) -> bool:
        """Try to acquire the planner single-flight lock via CAS.

        ``BEGIN IMMEDIATE`` serializes the check-and-set: the lock is granted
        if it is unheld, already owned by ``owner``, or its ``locked_until``
        has elapsed (the previous holder crashed). On success ``locked_until``
        is extended by ``lease_seconds`` and the row's ``owner`` is set.
        **Short transaction only** — acquire, commit, then run the LLM call
        *without* holding any DB lock; reacquire/``renew`` to write results.
        Returns True if the lock is now held by ``owner``.
        """
        from datetime import UTC, datetime, timedelta

        lock_name = "keyword_planner"
        now = datetime.now(UTC)
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")
        new_until = (now + timedelta(seconds=max(0.0, lease_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, locked_until FROM discovery_planner_lock WHERE lock_name = ?",
                (lock_name,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO discovery_planner_lock
                        (lock_name, owner, locked_until, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (lock_name, owner, new_until),
                )
                conn.commit()
                return True
            held_by = str(row["owner"] or "")
            locked_until = str(row["locked_until"] or "")
            if held_by and held_by != owner and locked_until > now_text:
                # Still validly held by someone else.
                conn.commit()
                return False
            conn.execute(
                """
                UPDATE discovery_planner_lock
                SET owner = ?, locked_until = ?, updated_at = CURRENT_TIMESTAMP
                WHERE lock_name = ?
                """,
                (owner, new_until, lock_name),
            )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return True

    def renew_planner_lock(self, owner: str, lease_seconds: float) -> bool:
        """Extend the planner lock lease if still owned by ``owner``.

        Returns True if the lease was extended, False if the lock has been
        taken over by another owner in the meantime.
        """
        from datetime import UTC, datetime, timedelta

        new_until = (datetime.now(UTC) + timedelta(seconds=max(0.0, lease_seconds))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self._execute_write(
            """
            UPDATE discovery_planner_lock
            SET locked_until = ?, updated_at = CURRENT_TIMESTAMP
            WHERE lock_name = 'keyword_planner' AND owner = ?
            """,
            (new_until, owner),
        )
        return int(cursor.rowcount or 0) > 0

    def release_planner_lock(self, owner: str) -> bool:
        """Release the planner lock if still owned by ``owner``.

        Clears the owner and expires ``locked_until`` so the next acquirer
        can take it immediately. Returns True if a row was released.
        """
        from datetime import UTC, datetime

        now_text = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        cursor = self._execute_write(
            """
            UPDATE discovery_planner_lock
            SET owner = '', locked_until = ?, updated_at = CURRENT_TIMESTAMP
            WHERE lock_name = 'keyword_planner' AND owner = ?
            """,
            (now_text, owner),
        )
        return int(cursor.rowcount or 0) > 0

    # ── Watch-later CRUD ─────────────────────────────────────────

    def add_to_watch_later(self, bvid: str, note: str = "") -> bool:
        """Bookmark a video. Returns True if newly inserted, False if updated."""
        item = self._bilibili_saved_item_input(bvid)
        self.upsert_saved_membership("watch_later", item, note)
        self._execute_write(
            """
            INSERT INTO watch_later (bvid, note, item_key)
            VALUES (?, ?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                added_at = CURRENT_TIMESTAMP,
                note = excluded.note,
                item_key = excluded.item_key
            """,
            (bvid.strip(), note, item.item_key),
        )
        return self.conn.total_changes > 0

    def remove_from_watch_later(self, bvid: str) -> bool:
        """Remove a bookmark. Returns True if a row was deleted."""
        item_key = self._resolve_legacy_saved_item_key("watch_later", bvid)
        if item_key is None:
            return False
        return self.remove_saved_membership("watch_later", item_key)

    def is_in_watch_later(self, bvid: str) -> bool:
        """Check whether a video is bookmarked."""
        item_key = self._resolve_legacy_saved_item_key("watch_later", bvid)
        return (
            item_key is not None and self.get_saved_membership("watch_later", item_key) is not None
        )

    def count_watch_later(self) -> int:
        """Return total number of bookmarked videos."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM saved_memberships WHERE list_kind = ?",
            ("watch_later",),
        ).fetchone()
        return int(row[0]) if row else 0

    def list_watch_later(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return bookmarked videos with content_cache metadata, newest first."""
        return [
            {
                "bvid": row["content_id"],
                "item_key": row["item_key"],
                "content_id": row["content_id"],
                "added_at": row["added_at"],
                "note": row["note"],
                "title": row["title"],
                "up_name": row["author_name"],
                "cover_url": row["cover_url"],
                "content_url": row["content_url"],
                "source_platform": row["source_platform"],
                "content_type": row["content_type"],
            }
            for row in self.list_saved_memberships("watch_later", limit, offset)
        ]

    def _ensure_favorites_table(self) -> None:
        """Create the favorites (收藏夹) table for existing databases.

        Favorites are a permanent, curated keep — distinct from the
        ephemeral ``watch_later`` queue. The two tables are independent so
        a video can be in one, both, or neither.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS favorites (
                bvid     TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note     TEXT DEFAULT '',
                item_key TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_favorites_added
                ON favorites(added_at DESC);
        """)
        self._ensure_legacy_saved_item_key_column("favorites")

    def _ensure_legacy_saved_item_key_column(self, table_name: str) -> None:
        """Add the stable normalized identity link to a trusted legacy saved table."""
        if table_name not in {"watch_later", "favorites"}:
            raise ValueError(f"unsupported legacy saved table: {table_name}")
        columns = {
            str(row["name"])
            for row in self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if "item_key" not in columns:
            self.conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN item_key TEXT NOT NULL DEFAULT ''"
            )

    def _ensure_saved_sync_tables(self) -> None:
        """Create normalized saved-content tables and import legacy saved rows once."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS saved_items (
                item_key        TEXT PRIMARY KEY,
                source_platform TEXT NOT NULL,
                content_id      TEXT NOT NULL,
                content_url     TEXT NOT NULL DEFAULT '',
                content_type    TEXT NOT NULL DEFAULT 'video',
                title           TEXT NOT NULL DEFAULT '',
                author_name     TEXT NOT NULL DEFAULT '',
                cover_url       TEXT NOT NULL DEFAULT '',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS saved_memberships (
                list_kind TEXT NOT NULL CHECK (list_kind IN ('favorite', 'watch_later')),
                item_key  TEXT NOT NULL REFERENCES saved_items(item_key) ON DELETE CASCADE,
                note      TEXT NOT NULL DEFAULT '',
                added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (list_kind, item_key)
            );
            CREATE INDEX IF NOT EXISTS idx_saved_memberships_item_key
                ON saved_memberships(item_key);
            CREATE TABLE IF NOT EXISTS native_save_states (
                list_kind          TEXT NOT NULL,
                item_key           TEXT NOT NULL,
                requested_action   TEXT NOT NULL,
                resolved_action    TEXT NOT NULL DEFAULT '',
                resolved_target    TEXT NOT NULL DEFAULT '',
                status             TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'syncing', 'synced', 'already_synced',
                        'login_required', 'unsupported', 'rate_limited',
                        'extension_required', 'failed'
                    )),
                task_id            TEXT NOT NULL DEFAULT '',
                execution_id       TEXT NOT NULL DEFAULT '',
                task_claimed_at    TIMESTAMP,
                task_started_at    TIMESTAMP,
                task_heartbeat_at  TIMESTAMP,
                task_runner_id     TEXT NOT NULL DEFAULT '',
                last_error_code    TEXT NOT NULL DEFAULT '',
                last_error_message TEXT NOT NULL DEFAULT '',
                last_attempt_at    TIMESTAMP,
                synced_at          TIMESTAMP,
                PRIMARY KEY (list_kind, item_key),
                FOREIGN KEY (list_kind, item_key)
                    REFERENCES saved_memberships(list_kind, item_key) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS native_save_tasks (
                task_id     TEXT PRIMARY KEY,
                list_kind   TEXT NOT NULL CHECK (list_kind IN ('favorite', 'watch_later')),
                trigger     TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS native_save_task_items (
                task_id            TEXT NOT NULL
                    REFERENCES native_save_tasks(task_id) ON DELETE CASCADE,
                item_key           TEXT NOT NULL,
                ordinal            INTEGER NOT NULL,
                requested_action   TEXT NOT NULL,
                resolved_action    TEXT NOT NULL DEFAULT '',
                resolved_target    TEXT NOT NULL DEFAULT '',
                status             TEXT NOT NULL
                    CHECK (status IN (
                        'pending', 'syncing', 'synced', 'already_synced',
                        'login_required', 'unsupported', 'rate_limited',
                        'extension_required', 'failed'
                    )),
                is_live            INTEGER NOT NULL DEFAULT 0 CHECK (is_live IN (0, 1)),
                last_error_code    TEXT NOT NULL DEFAULT '',
                last_error_message TEXT NOT NULL DEFAULT '',
                updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (task_id, item_key)
            );
            CREATE INDEX IF NOT EXISTS idx_native_save_task_items_order
                ON native_save_task_items(task_id, ordinal, item_key);
            CREATE TABLE IF NOT EXISTS extension_native_save_jobs (
                job_id             TEXT PRIMARY KEY,
                platform           TEXT NOT NULL,
                platform_slug      TEXT NOT NULL,
                item_key           TEXT NOT NULL,
                content_id         TEXT NOT NULL,
                content_url        TEXT NOT NULL,
                content_type       TEXT NOT NULL,
                requested_action   TEXT NOT NULL
                    CHECK(requested_action IN ('favorite', 'watch_later')),
                resolved_action    TEXT NOT NULL
                    CHECK(resolved_action IN ('favorite', 'watch_later')),
                target_label       TEXT NOT NULL,
                status             TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN (
                        'pending', 'in_progress', 'synced', 'already_synced',
                        'login_required', 'rate_limited', 'unsupported', 'failed',
                        'extension_required', 'cancelled'
                    )),
                claimed_at         TIMESTAMP,
                completed_at       TIMESTAMP,
                last_error_code    TEXT NOT NULL DEFAULT '',
                last_error_message TEXT NOT NULL DEFAULT '',
                created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_extension_native_save_jobs_claim
                ON extension_native_save_jobs(platform_slug, status, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_extension_native_save_jobs_active_item
                ON extension_native_save_jobs(platform, item_key, requested_action)
                WHERE status IN ('pending', 'in_progress');
            CREATE TABLE IF NOT EXISTS saved_sync_migrations (
                name       TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        native_state_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(native_save_states)").fetchall()
        }
        if "execution_id" not in native_state_columns:
            self.conn.execute(
                "ALTER TABLE native_save_states ADD COLUMN execution_id TEXT NOT NULL DEFAULT ''"
            )
        if "task_runner_id" not in native_state_columns:
            self.conn.execute(
                "ALTER TABLE native_save_states ADD COLUMN task_runner_id TEXT NOT NULL DEFAULT ''"
            )
        for column_name in ("task_claimed_at", "task_started_at", "task_heartbeat_at"):
            if column_name not in native_state_columns:
                self.conn.execute(
                    f"ALTER TABLE native_save_states ADD COLUMN {column_name} TIMESTAMP"
                )
        self.conn.execute(
            """
            UPDATE native_save_states
            SET task_claimed_at = CURRENT_TIMESTAMP
            WHERE status = 'pending' AND task_id != '' AND task_claimed_at IS NULL
            """
        )
        self.conn.execute(
            """
            UPDATE native_save_states
            SET task_heartbeat_at = CURRENT_TIMESTAMP
            WHERE status IN ('pending', 'syncing')
              AND task_id != '' AND task_started_at IS NOT NULL
              AND task_heartbeat_at IS NULL
            """
        )
        self.conn.execute(
            """
            UPDATE native_save_states
            SET task_runner_id = ?
            WHERE status IN ('pending', 'syncing')
              AND task_id != '' AND task_started_at IS NOT NULL
              AND task_runner_id = ''
              AND task_heartbeat_at IS NOT NULL
            """,
            (_LEGACY_NATIVE_SAVE_RUNNER_ID,),
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO native_save_tasks (task_id, list_kind, trigger, created_at)
            SELECT task_id, MIN(list_kind), 'legacy',
                   COALESCE(MIN(task_claimed_at), MIN(last_attempt_at), CURRENT_TIMESTAMP)
            FROM native_save_states
            WHERE task_id != ''
            GROUP BY task_id
            """
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO native_save_task_items (
                task_id, item_key, ordinal, requested_action, resolved_action,
                resolved_target, status, is_live, last_error_code, last_error_message,
                updated_at
            )
            SELECT
                task_id,
                item_key,
                ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY item_key) - 1,
                requested_action,
                resolved_action,
                resolved_target,
                status,
                CASE WHEN status IN ('pending', 'syncing') THEN 1 ELSE 0 END,
                last_error_code,
                last_error_message,
                COALESCE(last_attempt_at, task_claimed_at, CURRENT_TIMESTAMP)
            FROM native_save_states
            WHERE task_id != ''
            """
        )
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            migrated = self.conn.execute(
                "SELECT 1 FROM saved_sync_migrations WHERE name = ?",
                ("legacy_saved_tables_v1",),
            ).fetchone()
            if migrated is None:
                self._migrate_legacy_saved_list("watch_later", "watch_later")
                self._migrate_legacy_saved_list("favorites", "favorite")
                self.conn.execute(
                    "INSERT INTO saved_sync_migrations (name) VALUES (?)",
                    ("legacy_saved_tables_v1",),
                )
            stable_links = self.conn.execute(
                "SELECT 1 FROM saved_sync_migrations WHERE name = ?",
                ("legacy_saved_item_keys_v2",),
            ).fetchone()
            if stable_links is None:
                self._backfill_legacy_saved_item_keys("watch_later", "watch_later")
                self._backfill_legacy_saved_item_keys("favorites", "favorite")
                self.conn.execute(
                    "INSERT INTO saved_sync_migrations (name) VALUES (?)",
                    ("legacy_saved_item_keys_v2",),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self.migrate_legacy_native_save_unsupported()

    def migrate_legacy_native_save_unsupported(self) -> int:
        """Mark only pre-adapter unsupported rows for the six extension sources."""
        migration_name = "extension_adapter_missing_unsupported_v1"
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            migrated = conn.execute(
                "SELECT 1 FROM saved_sync_migrations WHERE name = ?",
                (migration_name,),
            ).fetchone()
            if migrated is not None:
                conn.commit()
                return 0
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET last_error_code = 'unsupported_adapter_missing',
                    last_error_message =
                        'Native save adapter was unavailable; retry is now supported'
                WHERE status = 'unsupported'
                  AND last_error_code IN ('', 'unsupported')
                  AND item_key IN (
                      SELECT item_key
                      FROM saved_items
                      WHERE source_platform IN (
                          'youtube', 'xiaohongshu', 'douyin',
                          'twitter', 'zhihu', 'reddit'
                      )
                  )
                """
            )
            conn.execute(
                "INSERT INTO saved_sync_migrations (name) VALUES (?)",
                (migration_name,),
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_or_reuse_extension_native_save_job(
        self, job: ExtensionNativeSaveJob
    ) -> dict[str, Any]:
        """Atomically persist or reuse one active extension native-save job."""
        payload = _validated_extension_native_save_job(job)
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT * FROM extension_native_save_jobs
                WHERE platform = ? AND item_key = ? AND requested_action = ?
                  AND status IN ('pending', 'in_progress')
                """,
                (
                    payload["platform"],
                    payload["item_key"],
                    payload["requested_action"],
                ),
            ).fetchone()
            if active is None:
                conn.execute(
                    """
                    INSERT INTO extension_native_save_jobs (
                        job_id, platform, platform_slug, item_key, content_id,
                        content_url, content_type, requested_action, resolved_action,
                        target_label
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["job_id"],
                        payload["platform"],
                        payload["platform_slug"],
                        payload["item_key"],
                        payload["content_id"],
                        payload["content_url"],
                        payload["content_type"],
                        payload["requested_action"],
                        payload["resolved_action"],
                        payload["target_label"],
                    ),
                )
                active = conn.execute(
                    "SELECT * FROM extension_native_save_jobs WHERE job_id = ?",
                    (payload["job_id"],),
                ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if active is None:
            raise RuntimeError("extension native-save job insert did not persist")
        return dict(active)

    def claim_extension_native_save_job(
        self, platform_slug: str, lease_seconds: float
    ) -> dict[str, Any] | None:
        """Claim the oldest pending platform job after expiring uncertain stale claims."""
        slug = self._validated_extension_native_save_slug(platform_slug)
        lease = self._validated_extension_native_save_lease(lease_seconds)
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._expire_stale_extension_native_save_jobs_in_transaction(conn, slug, lease)
            pending = conn.execute(
                """
                SELECT job_id FROM extension_native_save_jobs
                WHERE platform_slug = ? AND status = 'pending'
                ORDER BY created_at, job_id
                LIMIT 1
                """,
                (slug,),
            ).fetchone()
            claimed = None
            if pending is not None:
                job_id = str(pending["job_id"])
                cursor = conn.execute(
                    """
                    UPDATE extension_native_save_jobs
                    SET status = 'in_progress',
                        claimed_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now'),
                        updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now')
                    WHERE job_id = ? AND status = 'pending'
                    """,
                    (job_id,),
                )
                if cursor.rowcount == 1:
                    claimed = conn.execute(
                        "SELECT * FROM extension_native_save_jobs WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return dict(claimed) if claimed is not None else None

    def complete_extension_native_save_job(
        self,
        job_id: str,
        platform_slug: str,
        item_key: str,
        status: str,
        error_code: str,
        error_message: str,
    ) -> bool:
        """Complete one claimed job when slug, UUID, and item identity match."""
        safe_job_id = _validated_extension_native_save_uuid(job_id, "job_id")
        safe_slug = self._validated_extension_native_save_slug(platform_slug)
        safe_item_key = _validated_extension_native_save_text(item_key, "item_key", max_length=768)
        safe_status, safe_code, safe_message = _validated_extension_native_save_result(
            status, error_code, error_message
        )
        conn = self.open_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE extension_native_save_jobs
                SET status = ?, last_error_code = ?, last_error_message = ?,
                    completed_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now'),
                    updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now')
                WHERE job_id = ? AND platform_slug = ? AND item_key = ?
                  AND status = 'in_progress'
                """,
                (safe_status, safe_code, safe_message, safe_job_id, safe_slug, safe_item_key),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def cancel_unclaimed_extension_native_save_job(self, job_id: str) -> bool:
        """Cancel a pending job without touching a possibly state-changing claimed job."""
        safe_job_id = _validated_extension_native_save_uuid(job_id, "job_id")
        conn = self.open_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE extension_native_save_jobs
                SET status = 'cancelled',
                    completed_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now'),
                    updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now')
                WHERE job_id = ? AND status = 'pending'
                """,
                (safe_job_id,),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def mark_unclaimed_extension_native_save_job_extension_required(self, job_id: str) -> bool:
        """Durably mark a still-pending job when no extension claims it in time."""
        safe_job_id = _validated_extension_native_save_uuid(job_id, "job_id")
        conn = self.open_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE extension_native_save_jobs
                SET status = 'extension_required',
                    last_error_code = 'extension_unavailable',
                    last_error_message = 'OpenBiliClaw extension is unavailable',
                    completed_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now'),
                    updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now')
                WHERE job_id = ? AND status = 'pending'
                """,
                (safe_job_id,),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def get_extension_native_save_job(self, job_id: str) -> dict[str, Any] | None:
        """Return a copied durable extension job row by canonical UUID."""
        safe_job_id = _validated_extension_native_save_uuid(job_id, "job_id")
        conn = self.open_connection()
        try:
            row = conn.execute(
                "SELECT * FROM extension_native_save_jobs WHERE job_id = ?", (safe_job_id,)
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def owns_extension_native_save_job(self, job_id: str, platform_slug: str | None = None) -> bool:
        """Return global job ownership, optionally restricted to one exact slug."""
        safe_job_id = _validated_extension_native_save_uuid(job_id, "job_id")
        conn = self.open_connection()
        if platform_slug is None:
            try:
                row = conn.execute(
                    "SELECT 1 FROM extension_native_save_jobs WHERE job_id = ?",
                    (safe_job_id,),
                ).fetchone()
                return row is not None
            finally:
                conn.close()
        try:
            safe_slug = self._validated_extension_native_save_slug(platform_slug)
            row = conn.execute(
                "SELECT 1 FROM extension_native_save_jobs WHERE job_id = ? AND platform_slug = ?",
                (safe_job_id, safe_slug),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def expire_stale_extension_native_save_jobs(
        self, platform_slug: str, lease_seconds: float
    ) -> int:
        """Fail stale claimed writes without returning them to the pending queue."""
        slug = self._validated_extension_native_save_slug(platform_slug)
        lease = self._validated_extension_native_save_lease(lease_seconds)
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            count = self._expire_stale_extension_native_save_jobs_in_transaction(conn, slug, lease)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return count

    def _expire_stale_extension_native_save_jobs_in_transaction(
        self, conn: sqlite3.Connection, platform_slug: str, lease_seconds: float
    ) -> int:
        cursor = conn.execute(
            """
            UPDATE extension_native_save_jobs
            SET status = 'failed',
                last_error_code = 'extension_task_timeout',
                last_error_message = 'Extension native-save task timed out',
                completed_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now'),
                updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now')
            WHERE platform_slug = ? AND status = 'in_progress'
              AND claimed_at IS NOT NULL
              AND (JULIANDAY('now') - JULIANDAY(claimed_at)) * 86400.0 >= ?
            """,
            (platform_slug, lease_seconds),
        )
        return cursor.rowcount

    @staticmethod
    def _validated_extension_native_save_slug(platform_slug: object) -> str:
        slug = _validated_extension_native_save_text(
            platform_slug, "platform_slug", max_length=16
        ).lower()
        if slug not in _EXTENSION_NATIVE_SAVE_SLUGS:
            raise ValueError("platform_slug is invalid")
        return slug

    @staticmethod
    def _validated_extension_native_save_lease(lease_seconds: object) -> float:
        if isinstance(lease_seconds, bool):
            raise ValueError("lease_seconds must be a positive finite number")
        try:
            lease = float(cast("Any", lease_seconds))
        except (TypeError, ValueError) as exc:
            raise ValueError("lease_seconds must be a positive finite number") from exc
        if not math.isfinite(lease) or lease <= 0:
            raise ValueError("lease_seconds must be a positive finite number")
        return lease

    def _migrate_legacy_saved_list(self, table_name: str, list_kind: SavedListKind) -> None:
        """Copy one trusted legacy saved table into normalized storage."""
        if table_name not in {"watch_later", "favorites"}:
            raise ValueError(f"unsupported legacy saved table: {table_name}")
        platform_sql, content_id_sql, item_key_sql = self._legacy_saved_identity_sql()
        cache_join_sql = """
            c.bvid = COALESCE(
                (SELECT exact.bvid FROM content_cache AS exact WHERE exact.bvid = legacy.bvid),
                (
                    SELECT linked.bvid
                    FROM content_cache AS linked
                    WHERE linked.item_key = NULLIF(TRIM(legacy.item_key), '')
                )
            )
        """

        self.conn.execute(
            f"""
            INSERT OR IGNORE INTO saved_items (
                item_key, source_platform, content_id, content_url, content_type,
                title, author_name, cover_url, created_at, updated_at
            )
            SELECT
                {item_key_sql},
                {platform_sql},
                {content_id_sql},
                COALESCE(c.content_url, ''),
                COALESCE(NULLIF(c.content_type, ''), 'video'),
                COALESCE(c.title, ''),
                COALESCE(NULLIF(c.author_name, ''), c.up_name, ''),
                COALESCE(c.cover_url, ''),
                legacy.added_at,
                legacy.added_at
            FROM {table_name} AS legacy
            LEFT JOIN content_cache AS c ON {cache_join_sql}
            """
        )
        self.conn.execute(
            f"""
            INSERT OR IGNORE INTO saved_memberships (list_kind, item_key, note, added_at)
            SELECT ?, {item_key_sql}, COALESCE(legacy.note, ''), legacy.added_at
            FROM {table_name} AS legacy
            LEFT JOIN content_cache AS c ON {cache_join_sql}
            """,
            (list_kind,),
        )
        self._backfill_legacy_saved_item_keys(table_name, list_kind)

    @staticmethod
    def _legacy_saved_identity_sql() -> tuple[str, str, str]:
        """Return shared SQL expressions for canonicalizing one joined legacy saved row."""
        complete_identity_sql = """
            NULLIF(TRIM(c.source_platform), '') IS NOT NULL
            AND NULLIF(TRIM(c.content_id), '') IS NOT NULL
        """
        platform_sql = f"""
            CASE WHEN {complete_identity_sql} THEN
                CASE LOWER(TRIM(c.source_platform))
                    WHEN 'bili' THEN 'bilibili'
                    WHEN 'xhs' THEN 'xiaohongshu'
                    WHEN 'dy' THEN 'douyin'
                    WHEN 'yt' THEN 'youtube'
                    WHEN 'x' THEN 'twitter'
                    WHEN 'zh' THEN 'zhihu'
                    WHEN 'rd' THEN 'reddit'
                    ELSE LOWER(TRIM(c.source_platform))
                END
            ELSE 'bilibili'
            END
        """
        content_id_sql = (
            f"CASE WHEN {complete_identity_sql} THEN TRIM(c.content_id) ELSE legacy.bvid END"
        )
        item_key_sql = f"({platform_sql}) || ':' || ({content_id_sql})"
        return platform_sql, content_id_sql, item_key_sql

    def _backfill_legacy_saved_item_keys(
        self,
        table_name: str,
        list_kind: SavedListKind,
    ) -> None:
        """Persist the migration-time identity link without creating memberships."""
        if table_name not in {"watch_later", "favorites"}:
            raise ValueError(f"unsupported legacy saved table: {table_name}")
        _, _, item_key_sql = self._legacy_saved_identity_sql()
        self.conn.execute(
            f"""
            UPDATE {table_name} AS legacy
            SET item_key = COALESCE(
                (
                    SELECT {item_key_sql}
                    FROM content_cache AS c
                    JOIN saved_memberships AS m
                      ON m.list_kind = ? AND m.item_key = ({item_key_sql})
                    WHERE c.bvid = legacy.bvid
                    LIMIT 1
                ),
                CASE WHEN EXISTS (
                    SELECT 1
                    FROM saved_memberships AS m
                    WHERE m.list_kind = ?
                      AND m.item_key = 'bilibili:' || legacy.bvid
                ) THEN 'bilibili:' || legacy.bvid ELSE '' END
            )
            WHERE item_key = ''
            """,
            (list_kind, list_kind),
        )

    @staticmethod
    def _saved_list_kind(value: str) -> SavedListKind:
        list_kind = value.strip()
        if list_kind not in {"favorite", "watch_later"}:
            raise ValueError("list_kind must be 'favorite' or 'watch_later'")
        return cast("SavedListKind", list_kind)

    @staticmethod
    def _native_task_id(value: str) -> str:
        task_id = value.strip()
        if not task_id:
            raise ValueError("task_id must not be blank")
        return task_id

    @staticmethod
    def _native_execution_id(value: str) -> str:
        execution_id = value.strip()
        if not execution_id:
            raise ValueError("execution_id must not be blank")
        return execution_id

    @staticmethod
    def _native_runner_id(value: str) -> str:
        runner_id = value.strip()
        if not runner_id:
            raise ValueError("runner_id must not be blank")
        if runner_id.startswith(_NATIVE_INTERNAL_RUNNER_PREFIX):
            raise ValueError("runner_id uses a reserved internal prefix")
        return runner_id

    def _bilibili_saved_item_input(self, bvid: str) -> SavedItemInput:
        """Build a Bilibili compatibility input with any cached metadata snapshot."""
        content_id = bvid.strip()
        row = self.conn.execute(
            """
            SELECT title, up_name, author_name, cover_url, content_url, content_type
            FROM content_cache
            WHERE bvid = ?
            """,
            (content_id,),
        ).fetchone()
        if row is None:
            return SavedItemInput(source_platform="bilibili", content_id=content_id)
        return SavedItemInput(
            source_platform="bilibili",
            content_id=content_id,
            content_url=str(row["content_url"] or ""),
            content_type=str(row["content_type"] or "video"),
            title=str(row["title"] or ""),
            author_name=str(row["author_name"] or row["up_name"] or ""),
            cover_url=str(row["cover_url"] or ""),
        )

    def _resolve_legacy_saved_item_key(self, list_kind: str, content_id: str) -> str | None:
        """Resolve a legacy raw-ID removal to one unambiguous normalized membership."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_content_id = content_id.strip()
        bilibili_key = f"bilibili:{normalized_content_id}"
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT m.item_key
            FROM saved_memberships AS m
            JOIN saved_items AS i ON i.item_key = m.item_key
            WHERE m.list_kind = ?
              AND (m.item_key = ? OR i.content_id = ?)
            ORDER BY CASE WHEN m.item_key = ? THEN 0 ELSE 1 END, m.item_key
            """,
            (normalized_kind, bilibili_key, normalized_content_id, bilibili_key),
        ).fetchall()
        if rows and str(rows[0]["item_key"]) == bilibili_key:
            return bilibili_key
        if len(rows) == 1:
            return str(rows[0]["item_key"])
        if len(rows) > 1:
            return None
        return bilibili_key

    @staticmethod
    def _saved_membership_select() -> str:
        return """
            SELECT
                m.list_kind,
                i.item_key,
                i.source_platform,
                i.content_id,
                i.content_url,
                i.content_type,
                i.title,
                i.author_name,
                i.cover_url,
                i.created_at,
                i.updated_at,
                m.note,
                m.added_at,
                COALESCE(n.requested_action, '') AS requested_action,
                COALESCE(n.resolved_action, '') AS resolved_action,
                COALESCE(n.resolved_target, '') AS resolved_target,
                COALESCE(n.status, 'pending') AS sync_status,
                COALESCE(n.task_id, '') AS sync_task_id,
                COALESCE(n.last_error_code, '') AS last_error_code,
                COALESCE(n.last_error_message, '') AS last_error_message,
                n.last_attempt_at,
                n.synced_at
            FROM saved_memberships AS m
            JOIN saved_items AS i ON i.item_key = m.item_key
            LEFT JOIN native_save_states AS n
                ON n.list_kind = m.list_kind AND n.item_key = m.item_key
        """

    def upsert_saved_membership(
        self,
        list_kind: str,
        item: SavedItemInput,
        note: str = "",
    ) -> dict[str, Any]:
        """Atomically upsert an item snapshot and its local list membership."""
        normalized_kind = self._saved_list_kind(list_kind)
        item_key = item.item_key
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO saved_items (
                    item_key, source_platform, content_id, content_url, content_type,
                    title, author_name, cover_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    source_platform = excluded.source_platform,
                    content_id = excluded.content_id,
                    content_url = excluded.content_url,
                    content_type = excluded.content_type,
                    title = excluded.title,
                    author_name = excluded.author_name,
                    cover_url = excluded.cover_url,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item_key,
                    item.platform,
                    item.content_id.strip(),
                    item.content_url.strip(),
                    item.content_type.strip() or "video",
                    item.title.strip(),
                    item.author_name.strip(),
                    item.cover_url.strip(),
                ),
            )
            conn.execute(
                """
                INSERT INTO saved_memberships (list_kind, item_key, note)
                VALUES (?, ?, ?)
                ON CONFLICT(list_kind, item_key) DO UPDATE SET
                    note = excluded.note,
                    added_at = CURRENT_TIMESTAMP
                """,
                (normalized_kind, item_key, note),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        row = self.get_saved_membership(normalized_kind, item_key)
        if row is None:  # pragma: no cover - transaction succeeded but row vanished externally
            raise RuntimeError("saved membership disappeared after upsert")
        return row

    def remove_saved_membership(self, list_kind: str, item_key: str) -> bool:
        """Remove a normalized membership and any matching legacy compatibility row."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        legacy_table = "favorites" if normalized_kind == "favorite" else "watch_later"
        legacy_bvid = (
            normalized_key.removeprefix("bilibili:")
            if normalized_key.startswith("bilibili:")
            else None
        )
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active_state = conn.execute(
                """
                SELECT task_id
                FROM native_save_states
                WHERE list_kind = ? AND item_key = ?
                  AND status IN ('pending', 'syncing') AND task_id != ''
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            if active_state is not None:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'not_saved_locally',
                        last_error_message = 'Item is not saved locally',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                      AND status IN ('pending', 'syncing')
                    """,
                    (str(active_state["task_id"]), normalized_key),
                )
            cursor = conn.execute(
                "DELETE FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (normalized_kind, normalized_key),
            )
            removed = int(cursor.rowcount or 0) > 0
            direct_bilibili_clause = "bvid = ? OR" if legacy_bvid is not None else ""
            legacy_params = (legacy_bvid, normalized_key) if legacy_bvid else (normalized_key,)
            legacy_cursor = conn.execute(
                f"""
                DELETE FROM {legacy_table}
                WHERE {direct_bilibili_clause} item_key = ?
                """,
                legacy_params,
            )
            removed = removed or int(legacy_cursor.rowcount or 0) > 0
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()
        return removed

    def get_saved_membership(self, list_kind: str, item_key: str) -> dict[str, Any] | None:
        """Return one normalized membership with its current native-sync state."""
        normalized_kind = self._saved_list_kind(list_kind)
        self._ensure_fresh_read()
        row = self.conn.execute(
            self._saved_membership_select() + " WHERE m.list_kind = ? AND m.item_key = ?",
            (normalized_kind, item_key.strip()),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_saved_memberships(
        self,
        list_kind: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List normalized memberships newest first with native-sync state."""
        normalized_kind = self._saved_list_kind(list_kind)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            self._saved_membership_select()
            + " WHERE m.list_kind = ? ORDER BY m.added_at DESC, m.item_key ASC LIMIT ? OFFSET ?",
            (normalized_kind, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_native_save_state(
        self,
        list_kind: str,
        item_key: str,
        requested_action: str,
        resolved_action: str = "",
        resolved_target: str = "",
        status: str = "pending",
        task_id: str = "",
        execution_id: str = "",
        last_error_code: str = "",
        last_error_message: str = "",
    ) -> None:
        """Persist the latest native-save routing and execution state for one item."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        normalized_task_id = task_id.strip()
        if not isinstance(status, str) or status not in NATIVE_SAVE_STATUSES:
            raise ValueError("invalid native save status")
        if task_id and not normalized_task_id:
            raise ValueError("task_id must not be blank")
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            membership = conn.execute(
                "SELECT 1 FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (normalized_kind, normalized_key),
            ).fetchone()
            if membership is None:
                raise ValueError(
                    f"saved membership does not exist: {normalized_kind}/{normalized_key}"
                )
            if execution_id or status == "syncing" or (status == "pending" and normalized_task_id):
                raise ValueError("active task ownership must use the atomic claim APIs")
            current = conn.execute(
                """
                SELECT status, task_id
                FROM native_save_states
                WHERE list_kind = ? AND item_key = ?
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            if (
                current is not None
                and str(current["status"]) in {"pending", "syncing"}
                and str(current["task_id"])
            ):
                raise ValueError("active task ownership must use the atomic claim APIs")
            if current is not None and status == "pending" and str(current["status"]) != "pending":
                raise ValueError("invalid native save status transition to pending")
            conn.execute(
                """
                INSERT INTO native_save_states (
                    list_kind, item_key, requested_action, resolved_action, resolved_target,
                    status, task_id, execution_id, last_error_code, last_error_message,
                    last_attempt_at, synced_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    CASE WHEN ? = 'pending' THEN NULL ELSE CURRENT_TIMESTAMP END,
                    CASE WHEN ? IN ('synced', 'already_synced')
                        THEN CURRENT_TIMESTAMP ELSE NULL END
                )
                ON CONFLICT(list_kind, item_key) DO UPDATE SET
                    requested_action = excluded.requested_action,
                    resolved_action = excluded.resolved_action,
                    resolved_target = excluded.resolved_target,
                    status = excluded.status,
                    task_id = excluded.task_id,
                    execution_id = excluded.execution_id,
                    last_error_code = excluded.last_error_code,
                    last_error_message = excluded.last_error_message,
                    last_attempt_at = CASE
                        WHEN excluded.status = 'pending' THEN native_save_states.last_attempt_at
                        ELSE CURRENT_TIMESTAMP
                    END,
                    synced_at = CASE
                        WHEN excluded.status IN ('synced', 'already_synced')
                            THEN CURRENT_TIMESTAMP
                        ELSE native_save_states.synced_at
                    END
                """,
                (
                    normalized_kind,
                    normalized_key,
                    requested_action,
                    resolved_action,
                    resolved_target,
                    status,
                    normalized_task_id,
                    "",
                    last_error_code,
                    last_error_message,
                    status,
                    status,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_native_save_state(
        self,
        list_kind: str,
        item_key: str,
        requested_action: str,
    ) -> dict[str, Any]:
        """Insert a pending state only when absent and return the effective state.

        Existing active, claimed, syncing, retryable, and terminal rows are never
        modified. This closes the local-save/task-claim read-then-write race.
        """
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            membership = conn.execute(
                "SELECT 1 FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (normalized_kind, normalized_key),
            ).fetchone()
            if membership is None:
                raise ValueError(
                    f"saved membership does not exist: {normalized_kind}/{normalized_key}"
                )
            conn.execute(
                """
                INSERT OR IGNORE INTO native_save_states (
                    list_kind, item_key, requested_action, status
                ) VALUES (?, ?, ?, 'pending')
                """,
                (normalized_kind, normalized_key, requested_action),
            )
            row = conn.execute(
                """
                SELECT requested_action, resolved_action, resolved_target, status,
                       task_id, execution_id, last_error_code, last_error_message,
                       last_attempt_at, synced_at, task_claimed_at, task_started_at,
                       task_heartbeat_at, task_runner_id
                FROM native_save_states
                WHERE list_kind = ? AND item_key = ?
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if row is None:  # pragma: no cover - insert/select share one write transaction
            raise RuntimeError("native save state disappeared during ensure")
        return dict(row)

    def claim_native_sync_task(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None,
        task_id: str,
    ) -> list[str]:
        """Atomically assign eligible memberships to one durable pending task.

        A pending row with a non-empty task owner is deliberately ineligible so
        duplicate/manual task creation cannot steal it and invalidate polling for
        the original task ID.
        """
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        raw_keys = list(item_keys or ())
        cleaned_keys = list(dict.fromkeys(key.strip() for key in raw_keys if key.strip()))
        if raw_keys and not cleaned_keys:
            raise ValueError("item_keys must contain at least one non-blank key")
        params: list[Any] = [normalized_kind]
        item_filter = ""
        if cleaned_keys:
            placeholders = ", ".join("?" for _ in cleaned_keys)
            item_filter = f" AND m.item_key IN ({placeholders})"
            params.extend(cleaned_keys)

        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT m.item_key
                FROM saved_memberships AS m
                LEFT JOIN native_save_states AS n
                  ON n.list_kind = m.list_kind AND n.item_key = m.item_key
                WHERE m.list_kind = ?
                  AND (
                      n.status IS NULL
                      OR (n.status = 'pending' AND n.task_id = '')
                      OR n.status IN (
                          'login_required', 'rate_limited',
                          'extension_required', 'failed'
                      )
                  )
                """
                + item_filter
                + " ORDER BY m.added_at DESC, m.item_key ASC",
                params,
            ).fetchall()
            claimed_keys = [str(row["item_key"]) for row in rows]
            for item_key in claimed_keys:
                conn.execute(
                    """
                    INSERT INTO native_save_states (
                        list_kind, item_key, requested_action, status, task_id,
                        execution_id, resolved_action, resolved_target,
                        last_error_code, last_error_message, last_attempt_at,
                        task_claimed_at, task_started_at
                    )
                    VALUES (?, ?, ?, 'pending', ?, '', '', '', '', '', NULL,
                            CURRENT_TIMESTAMP, NULL)
                    ON CONFLICT(list_kind, item_key) DO UPDATE SET
                        requested_action = excluded.requested_action,
                        status = 'pending',
                        task_id = excluded.task_id,
                        execution_id = '',
                        resolved_action = '',
                        resolved_target = '',
                        last_error_code = '',
                        last_error_message = '',
                        task_claimed_at = CURRENT_TIMESTAMP,
                        task_started_at = NULL,
                        task_heartbeat_at = NULL,
                        task_runner_id = ''
                    """,
                    (normalized_kind, item_key, normalized_kind, normalized_task_id),
                )
            conn.commit()
            return claimed_keys
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_native_sync_task_snapshot(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None,
        task_id: str,
        trigger: str,
    ) -> list[dict[str, Any]]:
        """Create one durable task ledger and atomically claim its live items.

        ``item_keys is None`` selects every currently eligible membership. An
        explicit selection snapshots every requested key in caller order:
        missing keys become terminal ``not_saved_locally`` failures, terminal
        native states become terminal no-ops, and rows already owned by another
        task become terminal ``sync_already_in_progress`` no-ops.
        """
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        normalized_trigger = trigger.strip()
        if not normalized_trigger:
            raise ValueError("trigger must not be blank")
        explicit_selection = item_keys is not None
        raw_keys = list(item_keys or ())
        cleaned_keys = list(dict.fromkeys(key.strip() for key in raw_keys if key.strip()))
        if (
            raw_keys
            and len(cleaned_keys) != len(dict.fromkeys(raw_keys))
            and any(not key.strip() for key in raw_keys)
        ):
            raise ValueError("item_keys must not contain blank keys")

        retryable_statuses = {
            "login_required",
            "rate_limited",
            "extension_required",
            "failed",
        }
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO native_save_tasks (task_id, list_kind, trigger)
                VALUES (?, ?, ?)
                """,
                (normalized_task_id, normalized_kind, normalized_trigger),
            )
            if explicit_selection:
                selected_keys = cleaned_keys
            else:
                rows = conn.execute(
                    """
                    SELECT m.item_key
                    FROM saved_memberships AS m
                    LEFT JOIN native_save_states AS n
                      ON n.list_kind = m.list_kind AND n.item_key = m.item_key
                    WHERE m.list_kind = ?
                      AND (
                          n.status IS NULL
                          OR (n.status = 'pending' AND n.task_id = '')
                          OR n.status IN (
                              'login_required', 'rate_limited',
                              'extension_required', 'failed'
                          )
                          OR (
                              n.status = 'unsupported'
                              AND n.last_error_code = 'unsupported_adapter_missing'
                          )
                      )
                    ORDER BY m.added_at DESC, m.item_key ASC
                    """,
                    (normalized_kind,),
                ).fetchall()
                selected_keys = [str(row["item_key"]) for row in rows]

            for ordinal, item_key in enumerate(selected_keys):
                row = conn.execute(
                    """
                    SELECT
                        m.item_key,
                        n.requested_action,
                        n.resolved_action,
                        n.resolved_target,
                        n.status,
                        n.task_id,
                        n.last_error_code,
                        n.last_error_message
                    FROM saved_memberships AS m
                    LEFT JOIN native_save_states AS n
                      ON n.list_kind = m.list_kind AND n.item_key = m.item_key
                    WHERE m.list_kind = ? AND m.item_key = ?
                    """,
                    (normalized_kind, item_key),
                ).fetchone()
                requested_action: str = normalized_kind
                resolved_action: str = normalized_kind
                resolved_target = ""
                status: str = "failed"
                is_live = 0
                error_code = "not_saved_locally"
                error_message = "Item is not saved locally"

                if row is not None:
                    current_status = str(row["status"] or "pending")
                    current_task_id = str(row["task_id"] or "")
                    requested_action = str(row["requested_action"] or normalized_kind)
                    resolved_action = str(row["resolved_action"] or normalized_kind)
                    resolved_target = str(row["resolved_target"] or "")
                    error_code = str(row["last_error_code"] or "")
                    error_message = str(row["last_error_message"] or "")
                    eligible = (
                        row["status"] is None
                        or (current_status == "pending" and not current_task_id)
                        or current_status in retryable_statuses
                        or (
                            current_status == "unsupported"
                            and error_code == "unsupported_adapter_missing"
                        )
                    )
                    if eligible:
                        status = "pending"
                        is_live = 1
                        resolved_action = normalized_kind
                        resolved_target = ""
                        error_code = ""
                        error_message = ""
                        conn.execute(
                            """
                            INSERT INTO native_save_states (
                                list_kind, item_key, requested_action, status, task_id,
                                execution_id, resolved_action, resolved_target,
                                last_error_code, last_error_message, last_attempt_at,
                                task_claimed_at, task_started_at
                            )
                            VALUES (?, ?, ?, 'pending', ?, '', '', '', '', '', NULL,
                                    CURRENT_TIMESTAMP, NULL)
                            ON CONFLICT(list_kind, item_key) DO UPDATE SET
                                requested_action = excluded.requested_action,
                                status = 'pending',
                                task_id = excluded.task_id,
                                execution_id = '',
                                resolved_action = '',
                                resolved_target = '',
                                last_error_code = '',
                                last_error_message = '',
                                task_claimed_at = CURRENT_TIMESTAMP,
                                task_started_at = NULL,
                                task_heartbeat_at = NULL,
                                task_runner_id = ''
                            """,
                            (
                                normalized_kind,
                                item_key,
                                normalized_kind,
                                normalized_task_id,
                            ),
                        )
                    elif current_status in NATIVE_SAVE_TERMINAL_STATUSES:
                        status = current_status
                    else:
                        status = "failed"
                        error_code = "sync_already_in_progress"
                        error_message = "Item already belongs to an active sync task"

                conn.execute(
                    """
                    INSERT INTO native_save_task_items (
                        task_id, item_key, ordinal, requested_action, resolved_action,
                        resolved_target, status, is_live, last_error_code,
                        last_error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_task_id,
                        item_key,
                        ordinal,
                        requested_action,
                        resolved_action,
                        resolved_target,
                        status,
                        is_live,
                        error_code,
                        error_message,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.list_native_sync_task_items(normalized_task_id)

    def native_sync_task_exists(self, task_id: str) -> bool:
        """Return whether a durable task ledger exists, including empty tasks."""
        normalized_task_id = self._native_task_id(task_id)
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT 1 FROM native_save_tasks WHERE task_id = ?",
            (normalized_task_id,),
        ).fetchone()
        return row is not None

    def list_native_sync_task_items(self, task_id: str) -> list[dict[str, Any]]:
        """Return immutable task membership with its task-scoped result snapshot."""
        normalized_task_id = self._native_task_id(task_id)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT
                t.list_kind,
                i.task_id,
                i.item_key,
                i.ordinal,
                i.requested_action,
                i.resolved_action,
                i.resolved_target,
                i.status,
                i.is_live,
                i.last_error_code,
                i.last_error_message,
                i.updated_at
            FROM native_save_task_items AS i
            JOIN native_save_tasks AS t ON t.task_id = i.task_id
            WHERE i.task_id = ?
            ORDER BY i.ordinal ASC, i.item_key ASC
            """,
            (normalized_task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def discard_native_sync_task(self, task_id: str) -> bool:
        """Delete an unreturned task ledger after task-starter registration fails."""
        normalized_task_id = self._native_task_id(task_id)
        cursor = self._execute_write(
            "DELETE FROM native_save_tasks WHERE task_id = ?",
            (normalized_task_id,),
        )
        return int(cursor.rowcount or 0) > 0

    def release_native_sync_task(self, task_id: str) -> int:
        """Release pending ownership when a task could not be registered."""
        normalized_task_id = self._native_task_id(task_id)
        cursor = self._execute_write(
            """
            UPDATE native_save_states
            SET task_id = '', execution_id = '', task_claimed_at = NULL,
                task_started_at = NULL, task_heartbeat_at = NULL, task_runner_id = ''
            WHERE task_id = ? AND status = 'pending' AND execution_id = ''
            """,
            (normalized_task_id,),
        )
        return int(cursor.rowcount or 0)

    def release_stale_pending_native_sync_tasks(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None = None,
        *,
        stale_after_seconds: int = 300,
    ) -> int:
        """Release pending owners whose task never started or lost its heartbeat."""
        normalized_kind = self._saved_list_kind(list_kind)
        raw_keys = list(item_keys or ())
        cleaned_keys = list(dict.fromkeys(key.strip() for key in raw_keys if key.strip()))
        if raw_keys and not cleaned_keys:
            raise ValueError("item_keys must contain at least one non-blank key")
        item_filter = ""
        params: list[Any] = [normalized_kind]
        if cleaned_keys:
            placeholders = ", ".join("?" for _ in cleaned_keys)
            item_filter = f" AND item_key IN ({placeholders})"
            params.extend(cleaned_keys)
        age = max(0, int(stale_after_seconds))
        cutoff = f"-{age} seconds"
        params.extend((cutoff, cutoff))
        where_sql = (
            """
            WHERE list_kind = ? AND status = 'pending' AND task_id != ''
            """
            + item_filter
            + """
              AND (
                  (task_started_at IS NULL AND task_claimed_at IS NOT NULL
                   AND task_claimed_at <= datetime('now', ?))
                  OR
                  (task_started_at IS NOT NULL
                   AND (task_heartbeat_at IS NULL
                        OR task_heartbeat_at <= datetime('now', ?)))
              )
            """
        )
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT task_id, item_key FROM native_save_states " + where_sql,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'interrupted',
                        last_error_message = 'Native save was interrupted',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                    """,
                    (str(row["task_id"]), str(row["item_key"])),
                )
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET task_id = '', execution_id = '', task_claimed_at = NULL,
                    task_started_at = NULL, task_heartbeat_at = NULL, task_runner_id = ''
                """
                + where_sql,
                params,
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def release_stale_pending_native_sync_task(
        self,
        task_id: str,
        *,
        stale_after_seconds: int = 300,
    ) -> int:
        """Release stale pending rows while polling one known task."""
        normalized_task_id = self._native_task_id(task_id)
        age = max(0, int(stale_after_seconds))
        cutoff = f"-{age} seconds"
        params = (normalized_task_id, cutoff, cutoff)
        where_sql = """
            WHERE task_id = ? AND status = 'pending'
              AND (
                  (task_started_at IS NULL AND task_claimed_at IS NOT NULL
                   AND task_claimed_at <= datetime('now', ?))
                  OR
                  (task_started_at IS NOT NULL
                   AND (task_heartbeat_at IS NULL
                        OR task_heartbeat_at <= datetime('now', ?)))
              )
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT item_key FROM native_save_states " + where_sql,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'interrupted',
                        last_error_message = 'Native save was interrupted',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                    """,
                    (normalized_task_id, str(row["item_key"])),
                )
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET task_id = '', execution_id = '', task_claimed_at = NULL,
                    task_started_at = NULL, task_heartbeat_at = NULL, task_runner_id = ''
                """
                + where_sql,
                params,
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def claim_native_sync_task_runner(
        self,
        task_id: str,
        runner_id: str,
        *,
        stale_after_seconds: int = 300,
    ) -> bool:
        """Atomically acquire the single batch-runner lease for a task."""
        normalized_task_id = self._native_task_id(task_id)
        normalized_runner_id = self._native_runner_id(runner_id)
        age = max(0, int(stale_after_seconds))
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT 1
                FROM native_save_states
                WHERE task_id = ? AND status IN ('pending', 'syncing')
                LIMIT 1
                """,
                (normalized_task_id,),
            ).fetchone()
            if active is None:
                conn.commit()
                return True
            conflicting = conn.execute(
                """
                SELECT 1
                FROM native_save_states
                WHERE task_id = ? AND status IN ('pending', 'syncing')
                  AND task_runner_id NOT IN ('', ?)
                  AND task_heartbeat_at IS NOT NULL
                  AND task_heartbeat_at > datetime('now', ?)
                LIMIT 1
                """,
                (normalized_task_id, normalized_runner_id, f"-{age} seconds"),
            ).fetchone()
            if conflicting is not None:
                conn.commit()
                return False
            conn.execute(
                """
                UPDATE native_save_states
                SET task_runner_id = ?,
                    task_started_at = COALESCE(task_started_at, CURRENT_TIMESTAMP),
                    task_heartbeat_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND status IN ('pending', 'syncing')
                """,
                (normalized_runner_id, normalized_task_id),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def heartbeat_native_sync_task(self, task_id: str, runner_id: str) -> int:
        """Refresh the task lease protecting all remaining batch rows."""
        normalized_task_id = self._native_task_id(task_id)
        normalized_runner_id = self._native_runner_id(runner_id)
        cursor = self._execute_write(
            """
            UPDATE native_save_states
            SET task_heartbeat_at = CURRENT_TIMESTAMP
            WHERE task_id = ? AND task_runner_id = ? AND task_started_at IS NOT NULL
              AND status IN ('pending', 'syncing')
            """,
            (normalized_task_id, normalized_runner_id),
        )
        return int(cursor.rowcount or 0)

    def release_pending_native_sync_task(self, task_id: str, runner_id: str) -> int:
        """Release unclaimed pending rows when a runner exits normally or by cancellation."""
        normalized_task_id = self._native_task_id(task_id)
        normalized_runner_id = self._native_runner_id(runner_id)
        params = (normalized_task_id, normalized_runner_id)
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT item_key
                FROM native_save_states
                WHERE task_id = ? AND task_runner_id = ?
                  AND status = 'pending' AND execution_id = ''
                """,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'interrupted',
                        last_error_message = 'Native save was interrupted',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                    """,
                    (normalized_task_id, str(row["item_key"])),
                )
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET task_id = '', execution_id = '', task_claimed_at = NULL,
                    task_started_at = NULL, task_heartbeat_at = NULL, task_runner_id = ''
                WHERE task_id = ? AND task_runner_id = ?
                  AND status = 'pending' AND execution_id = ''
                """,
                params,
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def claim_native_save_item(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        runner_id: str,
        execution_id: str,
    ) -> bool:
        """Atomically claim one pending task item for adapter execution."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        normalized_runner_id = self._native_runner_id(runner_id)
        normalized_execution_id = self._native_execution_id(execution_id)
        normalized_key = item_key.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET status = 'syncing', execution_id = ?, last_attempt_at = CURRENT_TIMESTAMP
                WHERE list_kind = ? AND item_key = ? AND task_id = ? AND task_runner_id = ?
                  AND status = 'pending' AND execution_id = ''
                """,
                (
                    normalized_execution_id,
                    normalized_kind,
                    normalized_key,
                    normalized_task_id,
                    normalized_runner_id,
                ),
            )
            claimed = int(cursor.rowcount or 0) > 0
            if claimed:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'syncing', updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                      AND status = 'pending'
                    """,
                    (normalized_task_id, normalized_key),
                )
            conn.commit()
            return claimed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_native_save_claim_route(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        execution_id: str,
        resolved_action: str,
        resolved_target: str,
    ) -> bool:
        """Persist the router-owned destination for a live execution claim."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        normalized_execution_id = self._native_execution_id(execution_id)
        normalized_key = item_key.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET resolved_action = ?, resolved_target = ?
                WHERE list_kind = ? AND item_key = ? AND task_id = ?
                  AND status = 'syncing' AND execution_id = ?
                """,
                (
                    resolved_action,
                    resolved_target,
                    normalized_kind,
                    normalized_key,
                    normalized_task_id,
                    normalized_execution_id,
                ),
            )
            updated = int(cursor.rowcount or 0) > 0
            if updated:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET resolved_action = ?, resolved_target = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                      AND status = 'syncing'
                    """,
                    (
                        resolved_action,
                        resolved_target,
                        normalized_task_id,
                        normalized_key,
                    ),
                )
            conn.commit()
            return updated
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def heartbeat_native_save_claim(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        execution_id: str,
    ) -> bool:
        """Refresh a live adapter lease only while the execution owner matches."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        normalized_execution_id = self._native_execution_id(execution_id)
        cursor = self._execute_write(
            """
            UPDATE native_save_states
            SET last_attempt_at = CURRENT_TIMESTAMP
            WHERE list_kind = ? AND item_key = ? AND task_id = ?
              AND status = 'syncing' AND execution_id = ?
            """,
            (normalized_kind, item_key.strip(), normalized_task_id, normalized_execution_id),
        )
        return int(cursor.rowcount or 0) > 0

    def complete_native_save_claim(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        execution_id: str,
        *,
        requested_action: str,
        resolved_action: str,
        resolved_target: str,
        status: str,
        last_error_code: str = "",
        last_error_message: str = "",
    ) -> bool:
        """Complete one item only when the caller still owns its execution claim."""
        if not isinstance(status, str) or status not in NATIVE_SAVE_TERMINAL_STATUSES:
            raise ValueError("completion requires a terminal status")
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        normalized_execution_id = self._native_execution_id(execution_id)
        normalized_key = item_key.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET requested_action = ?, resolved_action = ?, resolved_target = ?,
                    status = ?, execution_id = '', task_runner_id = '', last_error_code = ?,
                    last_error_message = ?,
                    synced_at = CASE WHEN ? IN ('synced', 'already_synced')
                        THEN CURRENT_TIMESTAMP ELSE synced_at END
                WHERE list_kind = ? AND item_key = ? AND task_id = ?
                  AND status = 'syncing' AND execution_id = ?
                """,
                (
                    requested_action,
                    resolved_action,
                    resolved_target,
                    status,
                    last_error_code,
                    last_error_message,
                    status,
                    normalized_kind,
                    normalized_key,
                    normalized_task_id,
                    normalized_execution_id,
                ),
            )
            completed = int(cursor.rowcount or 0) > 0
            if completed:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET requested_action = ?, resolved_action = ?, resolved_target = ?,
                        status = ?, is_live = 0, last_error_code = ?,
                        last_error_message = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                      AND status = 'syncing'
                    """,
                    (
                        requested_action,
                        resolved_action,
                        resolved_target,
                        status,
                        last_error_code,
                        last_error_message,
                        normalized_task_id,
                        normalized_key,
                    ),
                )
            conn.commit()
            return completed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reconcile_stale_native_save_claims(
        self,
        task_id: str,
        *,
        stale_after_seconds: int = 300,
    ) -> int:
        """Turn abandoned syncing leases into explicit retryable failures."""
        normalized_task_id = self._native_task_id(task_id)
        age = max(0, int(stale_after_seconds))
        params = (normalized_task_id, f"-{age} seconds")
        where_sql = """
            WHERE task_id = ? AND status = 'syncing'
              AND last_attempt_at IS NOT NULL
              AND last_attempt_at <= datetime('now', ?)
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT item_key FROM native_save_states " + where_sql,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'interrupted',
                        last_error_message = 'Native save was interrupted',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                    """,
                    (normalized_task_id, str(row["item_key"])),
                )
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET status = 'failed', execution_id = '', task_runner_id = '',
                    last_error_code = 'interrupted',
                    last_error_message = 'Native save was interrupted'
                """
                + where_sql,
                params,
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reconcile_stale_native_save_claims_for_list(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None = None,
        *,
        stale_after_seconds: int = 300,
    ) -> int:
        """Recover stale syncing rows selected by a normal manual-list action."""
        normalized_kind = self._saved_list_kind(list_kind)
        raw_keys = list(item_keys or ())
        cleaned_keys = list(dict.fromkeys(key.strip() for key in raw_keys if key.strip()))
        if raw_keys and not cleaned_keys:
            raise ValueError("item_keys must contain at least one non-blank key")
        item_filter = ""
        params: list[Any] = [normalized_kind]
        if cleaned_keys:
            placeholders = ", ".join("?" for _ in cleaned_keys)
            item_filter = f" AND item_key IN ({placeholders})"
            params.extend(cleaned_keys)
        age = max(0, int(stale_after_seconds))
        params.append(f"-{age} seconds")
        where_sql = (
            """
            WHERE list_kind = ? AND status = 'syncing'
            """
            + item_filter
            + """
              AND last_attempt_at IS NOT NULL
              AND last_attempt_at <= datetime('now', ?)
            """
        )
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT task_id, item_key FROM native_save_states " + where_sql,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'interrupted',
                        last_error_message = 'Native save was interrupted',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                    """,
                    (str(row["task_id"]), str(row["item_key"])),
                )
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET status = 'failed', execution_id = '', task_runner_id = '',
                    last_error_code = 'interrupted',
                    last_error_message = 'Native save was interrupted'
                """
                + where_sql,
                params,
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_native_sync_eligible(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List memberships eligible for an initial sync or a manual retry."""
        normalized_kind = self._saved_list_kind(list_kind)
        params: list[Any] = [normalized_kind]
        item_filter = ""
        if item_keys:
            cleaned_keys = [item_key.strip() for item_key in item_keys]
            placeholders = ", ".join("?" for _ in cleaned_keys)
            item_filter = f" AND m.item_key IN ({placeholders})"
            params.extend(cleaned_keys)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            self._saved_membership_select()
            + """
            WHERE m.list_kind = ?
              AND (
                  n.status IS NULL
                  OR (n.status = 'pending' AND n.task_id = '')
                  OR n.status IN (
                      'login_required', 'rate_limited',
                      'extension_required', 'failed'
                  )
                  OR (
                      n.status = 'unsupported'
                      AND n.last_error_code = 'unsupported_adapter_missing'
                  )
              )
            """
            + item_filter
            + " ORDER BY m.added_at DESC, m.item_key ASC",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def list_native_save_states_by_task(self, task_id: str) -> list[dict[str, Any]]:
        """Return all persisted item results for a native-save task."""
        normalized_task_id = self._native_task_id(task_id)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT
                n.list_kind,
                n.item_key,
                i.source_platform,
                i.content_id,
                i.content_url,
                i.content_type,
                i.title,
                i.author_name,
                i.cover_url,
                m.note,
                m.added_at,
                n.requested_action,
                n.resolved_action,
                n.resolved_target,
                n.status,
                n.task_id,
                n.execution_id,
                n.task_claimed_at,
                n.task_started_at,
                n.task_heartbeat_at,
                n.task_runner_id,
                n.last_error_code,
                n.last_error_message,
                n.last_attempt_at,
                n.synced_at
            FROM native_save_states AS n
            JOIN saved_memberships AS m
                ON m.list_kind = n.list_kind AND m.item_key = n.item_key
            JOIN saved_items AS i ON i.item_key = n.item_key
            WHERE n.task_id = ?
            ORDER BY m.added_at DESC, n.item_key ASC
            """,
            (normalized_task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── Auth state (password gate revocation epoch) ──────────────

    def _ensure_auth_state_table(self) -> None:
        """Create the auth_state key/value table.

        Holds the global revocation epoch (``auth_epoch``) and the password
        fingerprint, kept out of ``config.toml`` so that revocation is a
        cross-process atomic counter rather than a whole-file rewrite. See
        ``docs/plans/2026-05-30-web-password-auth-design.md`` §4.7.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS auth_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

    def _ensure_init_runs_table(self) -> None:
        """Create the init_runs table backing guided (GUI) initialization.

        One row per guided-init run; the latest row is the authoritative
        progress source for ``GET /api/init-status`` (docs/specs/gui-init.md
        §5a). State survives restarts so a crashed / hot-reloaded run is
        reconciled to ``failed`` on boot rather than leaving a stuck
        ``running`` flag.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS init_runs (
                run_id          TEXT PRIMARY KEY,
                -- status: idle|starting|running|completed|failed|cancelled
                status          TEXT NOT NULL,
                stage           INTEGER NOT NULL DEFAULT 0,  -- 0..4
                stages_json     TEXT,  -- JSON: per-stage [{n,status,reason}]
                partial_success INTEGER NOT NULL DEFAULT 0,
                error_reason    TEXT,
                -- Human-readable failure specifics (exception summary /
                -- GuidedInitError message) surfaced by /api/init-status so
                -- an internal_error is diagnosable without server logs.
                error_detail    TEXT,
                sequence        INTEGER NOT NULL DEFAULT 0,
                started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at     TIMESTAMP
            );
        """)
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(init_runs)").fetchall()
        }
        if "error_detail" not in existing_columns:
            self.conn.execute("ALTER TABLE init_runs ADD COLUMN error_detail TEXT")

    def get_latest_init_run(self) -> dict[str, Any] | None:
        """Return the most recent init run as a dict, or None if none exist.

        Reads fresh WAL state so a run written by the background task / another
        process is visible immediately.
        """
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT * FROM init_runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else None

    def try_reserve_init_starting(self, run_id: str) -> bool:
        """Atomically reserve a new init run in ``starting`` state.

        Single-flight via ``BEGIN IMMEDIATE`` CAS (like ``bump_auth_epoch``):
        succeeds only when no run is currently ``starting``/``running``.
        Returns False when an init is already active, so concurrent
        ``POST /api/init`` callers cannot double-start (spec §5b TOCTOU).
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT 1 FROM init_runs WHERE status IN ('starting','running') LIMIT 1"
            ).fetchone()
            if active is not None:
                conn.rollback()
                return False
            conn.execute(
                """
                INSERT INTO init_runs (run_id, status, stage, sequence, started_at, updated_at)
                VALUES (?, 'starting', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(run_id) DO UPDATE SET
                    status='starting', stage=0, sequence=0, partial_success=0,
                    error_reason=NULL, error_detail=NULL, finished_at=NULL,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (run_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def update_init_run(self, run_id: str, **fields: Any) -> None:
        """Update mutable columns of an init run (the single status writer).

        Only whitelisted columns are accepted and ``updated_at`` is always
        bumped; unknown keys raise so a typo cannot silently no-op.
        """
        allowed = {
            "status",
            "stage",
            "stages_json",
            "partial_success",
            "error_reason",
            "error_detail",
            "sequence",
            "finished_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"update_init_run: unknown columns {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{col} = ?" for col in fields)
        params = [*fields.values(), run_id]
        self._execute_write(
            f"UPDATE init_runs SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
            params,
        )

    def reconcile_init_runs_on_boot(self) -> int:
        """Fail any run left ``starting``/``running`` by a crash/restart.

        No init task survives a process restart, so a persisted active status
        is necessarily stale. Returns the number of rows reconciled (spec §5a).
        """
        cursor = self._execute_write(
            """
            UPDATE init_runs
               SET status = 'failed', error_reason = 'interrupted',
                   finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
             WHERE status IN ('starting','running')
            """
        )
        return cursor.rowcount

    def get_auth_epoch(self) -> int:
        """Return the current revocation epoch. Reads fresh WAL state.

        A missing row means "never bumped" → 0. A present-but-corrupt value
        RAISES (never silently 0) so the auth gate fails closed instead of
        resurrecting tokens minted before a prior revocation. See §4.7.
        """
        self._ensure_fresh_read()
        row = self.conn.execute("SELECT value FROM auth_state WHERE key = 'auth_epoch'").fetchone()
        if row is None:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"corrupt auth_epoch value: {row[0]!r}") from exc

    def set_xhs_login_state(self, logged_in: bool, when_iso: str | None = None) -> None:
        """Persist the latest browser-observed xhs login state.

        The browser extension deliberately sends only this boolean, never the
        ``web_session`` cookie value, because xhs fetching remains client-side.
        """
        if not isinstance(logged_in, bool):
            raise TypeError("logged_in must be bool")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        self._execute_many_write(
            "INSERT OR REPLACE INTO auth_state (key, value) VALUES (?, ?)",
            [
                ("xhs_login_state", "1" if logged_in else "0"),
                ("xhs_login_state_at", str(when_iso)),
            ],
        )

    def get_xhs_login_state(self) -> tuple[bool, str]:
        """Return ``(logged_in, iso_timestamp)`` for xhs, or ``(False, "")``."""
        self._ensure_fresh_read()
        rows = self.conn.execute(
            "SELECT key, value FROM auth_state WHERE key IN (?, ?)",
            ("xhs_login_state", "xhs_login_state_at"),
        ).fetchall()
        values = {str(row["key"]): str(row["value"]) for row in rows}
        state = values.get("xhs_login_state")
        when_iso = values.get("xhs_login_state_at", "").strip()
        if state not in {"0", "1"} or not when_iso:
            return False, ""
        return state == "1", when_iso

    def set_zhihu_login_state(self, logged_in: bool, when_iso: str | None = None) -> None:
        """Persist the latest browser-observed Zhihu login state.

        The browser extension sends only whether ``z_c0`` is present and
        non-empty; it never sends the cookie value.
        """
        if not isinstance(logged_in, bool):
            raise TypeError("logged_in must be bool")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        self._execute_many_write(
            "INSERT OR REPLACE INTO auth_state (key, value) VALUES (?, ?)",
            [
                ("zhihu_login_state", "1" if logged_in else "0"),
                ("zhihu_login_state_at", str(when_iso)),
            ],
        )

    def get_zhihu_login_state(self) -> tuple[bool, str]:
        """Return ``(logged_in, iso_timestamp)`` for Zhihu, or ``(False, "")``."""
        self._ensure_fresh_read()
        rows = self.conn.execute(
            "SELECT key, value FROM auth_state WHERE key IN (?, ?)",
            ("zhihu_login_state", "zhihu_login_state_at"),
        ).fetchall()
        values = {str(row["key"]): str(row["value"]) for row in rows}
        state = values.get("zhihu_login_state")
        when_iso = values.get("zhihu_login_state_at", "").strip()
        if state not in {"0", "1"} or not when_iso:
            return False, ""
        return state == "1", when_iso

    def bump_auth_epoch(self) -> int:
        """Atomically increment and return the revocation epoch.

        Uses a short-lived connection with ``BEGIN IMMEDIATE`` so concurrent
        bumps (or another process) cannot lose an increment.
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM auth_state WHERE key = 'auth_epoch'").fetchone()
            # Missing → 0; corrupt → raise (never reset a damaged epoch downward).
            current = 0 if row is None else int(row[0])
            new_value = current + 1
            conn.execute(
                """
                INSERT INTO auth_state (key, value) VALUES ('auth_epoch', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(new_value),),
            )
            conn.commit()
            return new_value
        finally:
            conn.close()

    def reconcile_password_fingerprint(self, fingerprint: str) -> bool:
        """Detect a password change and bump the epoch if needed.

        Compares ``fingerprint`` (derived from stable credential material, see
        ``auth_core.password_fingerprint``) against the stored value, inside a
        single ``BEGIN IMMEDIATE`` transaction (CAS). Returns ``True`` when the
        epoch was bumped. First enable (no prior fingerprint) records it WITHOUT
        bumping. See §4.7.
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM auth_state WHERE key = 'password_fingerprint'"
            ).fetchone()
            stored = row[0] if row is not None else None
            bumped = False
            if stored is None:
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES "
                    "('password_fingerprint', ?)",
                    (fingerprint,),
                )
            elif stored != fingerprint:
                epoch_row = conn.execute(
                    "SELECT value FROM auth_state WHERE key = 'auth_epoch'"
                ).fetchone()
                # Missing → 0; corrupt → raise (the caller fails closed).
                current = 0 if epoch_row is None else int(epoch_row[0])
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('auth_epoch', ?)",
                    (str(current + 1),),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES "
                    "('password_fingerprint', ?)",
                    (fingerprint,),
                )
                bumped = True
            conn.commit()
            return bumped
        finally:
            conn.close()

    def set_password_fingerprint(self, fingerprint: str) -> None:
        """Overwrite the stored fingerprint without touching the epoch.

        Used after ``--rotate-secret`` re-bases the fingerprint under a new
        signing secret, so the next reconcile does not double-bump.
        """
        self._execute_write(
            "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('password_fingerprint', ?)",
            (fingerprint,),
        )

    def revoke_and_set_fingerprint(self, fingerprint: str | None, *, force_bump: bool) -> None:
        """Atomically (single ``BEGIN IMMEDIATE``) set the fingerprint, bumping the
        epoch when the credential changed or ``force_bump`` is set.

        Used by the local admin endpoint so a password change's revocation
        (epoch bump) and fingerprint update commit together — never a half state
        where the new password is live but old sessions survive (review r1#2).

        The bump decision is made INSIDE the transaction by comparing ``fingerprint``
        to the stored one (CAS), mirroring ``reconcile_password_fingerprint``: a
        first-ever set (no stored fingerprint) never bumps, but any *change* from an
        existing fingerprint always does — even when the caller's ``force_bump`` is
        false. This catches an effective credential change the caller can't see in
        its request, e.g. admin hot-publishing a ``password_hash`` that drifted on
        disk via an out-of-band ``set-password`` (review r4#2). ``force_bump`` adds a
        revoke for enabled on/off toggles, which carry no fingerprint change.

        Raises on a corrupt epoch (caller fails closed). The caller persists the new
        config FIRST (rolling it back if this raises) and publishes to the live gate
        only AFTER this commits, so a failure here leaves the durable DB state
        untouched and the persisted/live auth on the old password; a crash between
        the config write and this call is healed by the startup fingerprint
        reconcile (review r2#1).
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            stored_row = conn.execute(
                "SELECT value FROM auth_state WHERE key = 'password_fingerprint'"
            ).fetchone()
            stored = stored_row[0] if stored_row is not None else None
            credential_changed = (
                fingerprint is not None and stored is not None and stored != fingerprint
            )
            if force_bump or credential_changed:
                row = conn.execute(
                    "SELECT value FROM auth_state WHERE key = 'auth_epoch'"
                ).fetchone()
                current = 0 if row is None else int(row[0])  # corrupt → raise
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('auth_epoch', ?)",
                    (str(current + 1),),
                )
            if fingerprint is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES "
                    "('password_fingerprint', ?)",
                    (fingerprint,),
                )
            conn.commit()
        finally:
            conn.close()

    # ── Favorites CRUD ───────────────────────────────────────────

    def add_to_favorites(self, bvid: str, note: str = "") -> bool:
        """Save a video to favorites. Returns True if newly inserted."""
        item = self._bilibili_saved_item_input(bvid)
        self.upsert_saved_membership("favorite", item, note)
        self._execute_write(
            """
            INSERT INTO favorites (bvid, note, item_key)
            VALUES (?, ?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                added_at = CURRENT_TIMESTAMP,
                note = excluded.note,
                item_key = excluded.item_key
            """,
            (bvid.strip(), note, item.item_key),
        )
        return self.conn.total_changes > 0

    def remove_from_favorites(self, bvid: str) -> bool:
        """Remove a favorite. Returns True if a row was deleted."""
        item_key = self._resolve_legacy_saved_item_key("favorite", bvid)
        if item_key is None:
            return False
        return self.remove_saved_membership("favorite", item_key)

    def is_in_favorites(self, bvid: str) -> bool:
        """Check whether a video is favorited."""
        item_key = self._resolve_legacy_saved_item_key("favorite", bvid)
        return item_key is not None and self.get_saved_membership("favorite", item_key) is not None

    def count_favorites(self) -> int:
        """Return total number of favorited videos."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM saved_memberships WHERE list_kind = ?",
            ("favorite",),
        ).fetchone()
        return int(row[0]) if row else 0

    def list_favorites(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return favorited videos with content_cache metadata, newest first."""
        return [
            {
                "bvid": row["content_id"],
                "item_key": row["item_key"],
                "content_id": row["content_id"],
                "added_at": row["added_at"],
                "note": row["note"],
                "title": row["title"],
                "up_name": row["author_name"],
                "cover_url": row["cover_url"],
                "content_url": row["content_url"],
                "source_platform": row["source_platform"],
                "content_type": row["content_type"],
            }
            for row in self.list_saved_memberships("favorite", limit, offset)
        ]

    def iter_cover_lifecycle(self) -> list[tuple[str, str, bool]]:
        """Return ``(cover_url, pool_status, is_saved)`` for every cached-cover candidate.

        ``is_saved`` is True when the canonical item key has a normalized saved
        membership, with legacy Bilibili tables retained as a compatibility fallback.
        Consumed by the image-cache cleanup (:mod:`openbiliclaw.runtime.image_cache`)
        to decide which cached cover files are safe to evict: covers of saved or
        still-pending content are kept; covers of consumed, unsaved content are
        eligible for removal.
        """
        cursor = self.conn.execute(
            """
            SELECT
                COALESCE(cc.cover_url, '') AS cover_url,
                COALESCE(cc.pool_status, 'fresh') AS pool_status,
                CASE WHEN EXISTS (
                    SELECT 1
                    FROM saved_memberships AS m
                    WHERE m.item_key = cc.item_key
                ) OR f.bvid IS NOT NULL OR w.bvid IS NOT NULL THEN 1 ELSE 0 END AS is_saved
            FROM content_cache AS cc
            LEFT JOIN favorites AS f ON f.bvid = cc.bvid
            LEFT JOIN watch_later AS w ON w.bvid = cc.bvid
            WHERE COALESCE(cc.cover_url, '') <> ''
            """
        )
        return [
            (str(row["cover_url"]), str(row["pool_status"]), bool(row["is_saved"]))
            for row in cursor.fetchall()
        ]

    def iter_servable_cover_urls(self, *, recent_hours: int = 12, limit: int = 300) -> list[str]:
        """Recent, still-servable cover URLs (newest first) for discovery-time prefetch.

        Returns covers of content that may still be shown — ``pool_status`` in
        ``fresh / shown / suppressed``, or saved (favorites / watch_later) — limited
        to the last ``recent_hours`` of discoveries and ordered newest-first, so the
        prefetch sweep (:mod:`openbiliclaw.runtime.image_cache`) caches the freshest
        CDN tokens (notably XHS) before they expire. The recency window also keeps the
        sweep from endlessly retrying old content whose signed token is already dead.
        """
        cursor = self.conn.execute(
            """
            SELECT cc.cover_url
            FROM content_cache AS cc
            LEFT JOIN favorites AS f ON f.bvid = cc.bvid
            LEFT JOIN watch_later AS w ON w.bvid = cc.bvid
            WHERE COALESCE(cc.cover_url, '') <> ''
              AND cc.discovered_at >= datetime('now', ?)
              AND (
                COALESCE(cc.pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
                OR EXISTS (
                    SELECT 1
                    FROM saved_memberships AS m
                    WHERE m.item_key = cc.item_key
                )
                OR f.bvid IS NOT NULL
                OR w.bvid IS NOT NULL
              )
            ORDER BY cc.discovered_at DESC
            LIMIT ?
            """,
            (f"-{int(recent_hours)} hours", limit),
        )
        return [str(row["cover_url"]) for row in cursor.fetchall()]

    # ── XHS observed URL ingest ───────────────────────────────────

    def save_xhs_observed_urls(self, urls: list[str], page_type: str) -> int:
        """Insert observed xhs URLs, skipping duplicates. Returns count inserted."""
        inserted = 0
        for url in urls:
            # Skip if we've already seen this URL
            existing = self.conn.execute(
                "SELECT 1 FROM xhs_observed_urls WHERE url = ?", (url,)
            ).fetchone()
            if existing:
                continue
            self._execute_write(
                "INSERT INTO xhs_observed_urls (url, page_type) VALUES (?, ?)",
                (url, page_type),
            )
            inserted += 1
        return inserted

    # ── Source recipe CRUD ──────────────────────────────────────────

    def save_source_recipe(self, recipe: dict[str, Any]) -> None:
        """Insert or update a source recipe."""
        import json as _json

        self._execute_write(
            """
            INSERT INTO source_recipes (id, source_type, name, strategy, config,
                                        target_share, enabled, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                strategy = excluded.strategy,
                config = excluded.config,
                target_share = excluded.target_share,
                enabled = excluded.enabled
            """,
            (
                str(recipe["id"]),
                str(recipe["source_type"]),
                str(recipe["name"]),
                str(recipe["strategy"]),
                _json.dumps(recipe.get("config", {}), ensure_ascii=False),
                int(recipe.get("target_share", 4)),
                int(recipe.get("enabled", True)),
                str(recipe.get("created_by", "system")),
                recipe.get("created_at") or None,
            ),
        )

    def get_all_recipes(self) -> list[dict[str, Any]]:
        """Return all source recipes."""
        self._ensure_fresh_read()
        rows = self.conn.execute("SELECT * FROM source_recipes ORDER BY created_at").fetchall()
        return [self._row_to_recipe(row) for row in rows]

    def get_enabled_recipes(self) -> list[dict[str, Any]]:
        """Return only enabled source recipes."""
        self._ensure_fresh_read()
        rows = self.conn.execute(
            "SELECT * FROM source_recipes WHERE enabled = 1 ORDER BY created_at"
        ).fetchall()
        return [self._row_to_recipe(row) for row in rows]

    def update_recipe(self, recipe_id: str, **fields: Any) -> bool:
        """Update specific fields of a recipe. Returns True if a row was updated."""
        import json as _json

        allowed = {"name", "strategy", "config", "target_share", "enabled", "last_fetched_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        if "config" in updates and not isinstance(updates["config"], str):
            updates["config"] = _json.dumps(updates["config"], ensure_ascii=False)
        if "enabled" in updates:
            updates["enabled"] = int(updates["enabled"])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [recipe_id]
        cursor = self._execute_write(
            f"UPDATE source_recipes SET {set_clause} WHERE id = ?",
            tuple(values),
        )
        return cursor.rowcount > 0

    def delete_recipe(self, recipe_id: str) -> bool:
        """Delete a recipe by id. Returns True if a row was deleted."""
        cursor = self._execute_write(
            "DELETE FROM source_recipes WHERE id = ?",
            (recipe_id,),
        )
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_recipe(row: Any) -> dict[str, Any]:
        import json as _json

        config_raw = row["config"] if row["config"] else "{}"
        try:
            config = _json.loads(config_raw)
        except (ValueError, TypeError):
            config = {}
        return {
            "id": str(row["id"]),
            "source_type": str(row["source_type"]),
            "name": str(row["name"]),
            "strategy": str(row["strategy"]),
            "config": config,
            "target_share": int(row["target_share"]),
            "enabled": bool(row["enabled"]),
            "created_by": str(row["created_by"]),
            "created_at": str(row["created_at"] or ""),
            "last_fetched_at": str(row["last_fetched_at"] or ""),
        }

    def dynamic_delight_threshold(
        self,
        *,
        default_threshold: float = _DELIGHT_CLAIM_MIN_SCORE,
    ) -> float:
        """Return the profile floor raised to the delight pool Top 10% boundary.

        The dynamic component uses the current formal candidate pool, not raw
        ``discovery_candidates``. The percentile is computed over rows that
        already have ``delight_score``. When the scored pool is too small or
        too homogeneous for a meaningful percentile, the caller-provided
        default is returned unchanged.
        """
        try:
            floor = float(default_threshold)
        except (TypeError, ValueError):
            floor = _DELIGHT_CLAIM_MIN_SCORE
        floor = min(1.0, max(0.0, floor))

        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT COALESCE(delight_score, 0.0) AS score
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'shown')
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(delight_score, 0.0) > 0.0
            ORDER BY score DESC
            """
        )
        scores = [float(row["score"]) for row in cursor.fetchall()]
        if len(scores) < _DELIGHT_DYNAMIC_MIN_SAMPLE_SIZE:
            return floor
        if statistics.pstdev(scores) < _DELIGHT_DYNAMIC_MIN_STDDEV:
            return floor

        top_count = max(1, math.ceil(len(scores) * _DELIGHT_DYNAMIC_TOP_FRACTION))
        boundary = min(1.0, max(0.0, scores[top_count - 1]))
        return max(floor, boundary)

    def get_delight_candidate(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 1,
    ) -> dict[str, Any] | None:
        """Return one un-notified pool item with the highest delight_score.

        Backwards-compatible: ``limit=1`` returns a single dict (or None);
        callers that want multiple candidates (for example to filter
        disliked topics in Python) should call
        ``get_delight_candidates`` instead.
        """
        rows = self.get_delight_candidates(
            min_delight_score=min_delight_score,
            limit=max(1, int(limit)),
        )
        return rows[0] if rows else None

    def get_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 20,
        include_liked: bool = False,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` un-notified delight candidates ordered by score.

        Restricts to ``pool_status IN ('fresh', 'shown')`` —  ``suppressed``
        items have been trimmed out of the active pool by topic-group cap
        or source-share quota and shouldn't reappear as delights. Without
        this guard, popup re-hydration would pull historical delight
        scores baked under earlier (looser) calibrations from the
        suppressed graveyard and surface 20 stale "surprises" on every
        extension reload (observed 2026-05-04: 562 suppressed items
        carried delight metadata vs 2 in fresh).

        ``include_liked`` keeps ``feedback_type='like'`` rows in the result.
        Queue re-hydration (``/api/delight/pending-batch``) passes True so a
        liked delight stays visible until the user explicitly dismisses it —
        positive feedback must not remove the card (v0.3.63 contract). New
        delivery paths (WS push, counts, CLI) keep the default False so an
        already-liked item is never re-pushed as a fresh surprise.
        """
        feedback_clause = (
            "COALESCE(feedback_type, '') IN ('', 'like')"
            if include_liked
            else "COALESCE(feedback_type, '') = ''"
        )
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM content_cache
            WHERE COALESCE(delight_score, 0.0) >= ?
              AND {admission_sql}
              AND COALESCE(delight_notified, 0) = 0
              AND COALESCE(delight_reason, '') != ''
              AND COALESCE(delight_hook, '') != ''
              AND {feedback_clause}
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown')
            ORDER BY delight_score DESC, relevance_score DESC, discovered_at DESC
            LIMIT ?
            """,
            (min_delight_score, *admission_params, max(1, int(limit))),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_delight_notified(self, bvid: str) -> None:
        """Mark one content item as delight-notified."""
        self._execute_write(
            """
            UPDATE content_cache
            SET delight_notified = 1,
                delight_notified_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (bvid,),
        )

    def update_delight_score(
        self,
        bvid: str,
        *,
        delight_score: float,
        delight_reason: str,
        delight_hook: str = "",
    ) -> None:
        """Persist the computed delight score and explanation for a pool item."""
        self._execute_write(
            """
            UPDATE content_cache
            SET delight_score = ?,
                delight_reason = ?,
                delight_hook = ?
            WHERE bvid = ?
            """,
            (delight_score, delight_reason, delight_hook, bvid),
        )

    def count_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
    ) -> int:
        """Return the number of un-notified delight candidates."""
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM content_cache
            WHERE COALESCE(delight_score, 0.0) >= ?
              AND {admission_sql}
              AND COALESCE(delight_notified, 0) = 0
              AND COALESCE(delight_reason, '') != ''
              AND COALESCE(delight_hook, '') != ''
              AND COALESCE(feedback_type, '') = ''
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
            """,
            (min_delight_score, *admission_params),
        )
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def get_pool_candidates_needing_delight_score(
        self,
        limit: int = 30,
        *,
        min_delight_score_for_reason: float | None = None,
        min_relevance_score: float = 0.55,
        xhs_self_nickname: str = "",
    ) -> list[dict[str, Any]]:
        """Return pool candidates that still need delight backfill or copy.

        Two-stage retrieval: ``relevance_score >= min_relevance_score``
        is the cheap pre-filter (the discovery LLM already judged user-
        content fit during ``evaluate_batch``), then the caller reuses that
        Evo relevance result to populate delight fields only on this
        shortlist.

        Default 0.55 is calibrated to the discovery rubric:
          0.6+ strong fit, 0.5-0.6 moderate, <0.5 weak fit.
        Items below ``min_relevance_score`` skip delight backfill
        entirely — they're not going to delight anyone they don't
        already half-fit.
        """
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        effective_min_relevance_score = _normalize_admission_min_score(min_relevance_score)
        if min_delight_score_for_reason is None:
            cursor = self.conn.execute(
                f"""
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(delight_score, 0.0) = 0.0
                  AND COALESCE(relevance_score, 0.0) >= ?
                  {guard_sql}
                ORDER BY relevance_score DESC, discovered_at DESC
                LIMIT ?
                """,
                (
                    effective_min_relevance_score,
                    *guard_params,
                    limit,
                ),
            )
        else:
            cursor = self.conn.execute(
                f"""
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND (
                    COALESCE(delight_score, 0.0) = 0.0
                    OR ABS(
                      COALESCE(delight_score, 0.0) - COALESCE(relevance_score, 0.0)
                    ) > ?
                    OR (
                      COALESCE(delight_score, 0.0) >= ?
                      AND (
                        COALESCE(delight_reason, '') = ''
                        OR COALESCE(delight_hook, '') = ''
                      )
                    )
                  )
                  {guard_sql}
                ORDER BY
                    relevance_score DESC,
                    delight_score DESC,
                    discovered_at DESC
                LIMIT ?
                """,
                (
                    effective_min_relevance_score,
                    _DELIGHT_SCORE_SYNC_EPSILON,
                    min_delight_score_for_reason,
                    *guard_params,
                    limit,
                ),
            )
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _decode_event_metadata(row: dict[str, Any]) -> dict[str, Any]:
        metadata_raw = row.get("metadata", "")
        if isinstance(metadata_raw, str) and metadata_raw:
            try:
                metadata = json.loads(metadata_raw)
            except json.JSONDecodeError:
                metadata = {}
            if isinstance(metadata, dict):
                return metadata
        if isinstance(metadata_raw, dict):
            return metadata_raw
        return {}

    @classmethod
    def _extract_content_keys_from_view_event(cls, row: dict[str, Any]) -> set[str]:
        metadata = cls._decode_event_metadata(row)
        url = str(row.get("url", "")).strip()

        platform = _normalize_source_platform_key(metadata.get("source_platform", ""))
        if not platform:
            platform = cls._infer_source_platform_from_url(url)

        content_ids: set[str] = set()
        for key in _VIEW_CONTENT_ID_METADATA_KEYS:
            raw_value = metadata.get(key, "")
            if isinstance(raw_value, (str, int)):
                value = str(raw_value).strip()
                if value:
                    content_ids.add(value)
                    if (
                        platform == _REDDIT_SOURCE_FAMILY
                        and not value.startswith("t3_")
                        and re.fullmatch(r"[A-Za-z0-9_]+", value)
                    ):
                        content_ids.add(f"t3_{value}")

        url_content_id = cls._extract_content_id_from_url(platform, url)
        if url_content_id:
            content_ids.add(url_content_id)

        bvid = cls._extract_bvid_from_view_event(row)
        if bvid:
            content_ids.add(bvid)
            platform = platform or _BILIBILI_SOURCE_FAMILY

        keys: set[str] = set()
        for content_id in content_ids:
            if content_id.startswith("BV"):
                keys.add(content_id)
            if platform:
                keys.add(f"{platform}:{content_id}")
        return keys

    @staticmethod
    def _infer_source_platform_from_url(url: str) -> str:
        if not url:
            return ""
        host = urlparse(url).netloc.lower()
        if "bilibili.com" in host or host == "b23.tv":
            return _BILIBILI_SOURCE_FAMILY
        if "xiaohongshu.com" in host or "xhslink.com" in host:
            return _XHS_SOURCE_FAMILY
        if "douyin.com" in host:
            return _DOUYIN_SOURCE_FAMILY
        if "youtube.com" in host or host == "youtu.be":
            return _YOUTUBE_SOURCE_FAMILY
        if (
            host == "x.com"
            or host.endswith(".x.com")
            or host == "twitter.com"
            or host.endswith(".twitter.com")
        ):
            return _TWITTER_SOURCE_FAMILY
        if host == "reddit.com" or host.endswith(".reddit.com") or host == "redd.it":
            return _REDDIT_SOURCE_FAMILY
        return ""

    @staticmethod
    def _extract_content_id_from_url(platform: str, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if platform == _XHS_SOURCE_FAMILY:
            if len(path_parts) >= 2 and path_parts[0] == "explore":
                return path_parts[1]
            if len(path_parts) >= 3 and path_parts[:2] == ["discovery", "item"]:
                return path_parts[2]
        if platform == _DOUYIN_SOURCE_FAMILY and "video" in path_parts:
            video_index = path_parts.index("video")
            if len(path_parts) > video_index + 1:
                return path_parts[video_index + 1]
        if platform == _YOUTUBE_SOURCE_FAMILY:
            query_video_id = parse_qs(parsed.query).get("v", [""])[0].strip()
            if query_video_id:
                return query_video_id
            if parsed.netloc.lower() == "youtu.be" and path_parts:
                return path_parts[0]
            for prefix in ("shorts", "embed", "live"):
                if prefix in path_parts:
                    prefix_index = path_parts.index(prefix)
                    if len(path_parts) > prefix_index + 1:
                        return path_parts[prefix_index + 1]
        if platform == _BILIBILI_SOURCE_FAMILY:
            match = _BVID_PATTERN.search(url)
            if match:
                return match.group(1)
        if platform == _REDDIT_SOURCE_FAMILY:
            host = parsed.netloc.lower()
            if host == "redd.it" and path_parts:
                return f"t3_{path_parts[0]}"
            if len(path_parts) >= 4 and path_parts[0] == "r" and path_parts[2] == "comments":
                return f"t3_{path_parts[3]}"
        return ""

    @staticmethod
    def _extract_bvid_from_view_event(row: dict[str, Any]) -> str:
        metadata = Database._decode_event_metadata(row)
        bvid = str(metadata.get("bvid", "")).strip()
        if bvid:
            return bvid

        url = str(row.get("url", "")).strip()
        match = _BVID_PATTERN.search(url)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _content_row_view_keys(row: dict[str, Any]) -> set[str]:
        platform = _normalize_source_platform_key(row.get("source_platform", ""))
        if not platform:
            platform = _pool_source_family(row.get("source", ""), row.get("source_platform", ""))
            if platform == "unknown":
                platform = ""

        keys: set[str] = set()
        raw_bvid = str(row.get("bvid", "") or "").strip()
        content_id = str(row.get("content_id", "") or "").strip() or raw_bvid
        for value in {raw_bvid, content_id}:
            if not value:
                continue
            if value.startswith("BV"):
                keys.add(value)
            if platform:
                keys.add(f"{platform}:{value}")
        return keys

    @staticmethod
    def _is_viewed_row(row: dict[str, Any], viewed_content_keys: set[str]) -> bool:
        if not viewed_content_keys:
            return False
        return bool(Database._content_row_view_keys(row) & viewed_content_keys)

    @staticmethod
    def _exclude_viewed_rows(
        rows: list[dict[str, Any]],
        viewed_content_keys: set[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not viewed_content_keys:
            return rows[:limit]
        filtered = [row for row in rows if not Database._is_viewed_row(row, viewed_content_keys)]
        return filtered[:limit]
