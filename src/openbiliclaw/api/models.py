"""Pydantic models for the local backend API."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from openbiliclaw.saved_sync.identity import canonical_source_platform, make_item_key

NativeSaveStatusOut = Literal[
    "pending",
    "syncing",
    "synced",
    "already_synced",
    "login_required",
    "unsupported",
    "rate_limited",
    "extension_required",
    "failed",
]
NativeSaveActionOut = Literal["favorite", "watch_later"]
_SAVED_PLATFORM_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_URL_FALLBACK_ID_RE = re.compile(r"[0-9a-f]{24}")
_ZHIHU_TYPED_CONTENT_ID_RE = re.compile(r"(?:question|answer|article):[0-9]+")


def _has_unicode_control(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _has_identity_whitespace(value: str) -> bool:
    return any(character.isspace() for character in value)


def _validate_http_url(value: str) -> str:
    if _has_identity_whitespace(value) or _has_unicode_control(value):
        raise ValueError("URL fields must not contain whitespace or control characters")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError("URL fields must use a valid absolute HTTP(S) URL") from exc
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or hostname is None:
        raise ValueError("URL fields must use a valid absolute HTTP(S) URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL fields must not contain credentials")
    if port is not None and port <= 0:
        raise ValueError("URL fields must use a valid TCP port")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("URL fields must contain a valid hostname") from exc
        labels = ascii_hostname.removesuffix(".").split(".")
        if (
            not ascii_hostname
            or len(ascii_hostname.removesuffix(".")) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or re.fullmatch(r"[A-Za-z0-9-]+", label) is None
                for label in labels
            )
        ):
            raise ValueError("URL fields must contain a valid hostname") from None
    return value


class BehaviorEventIn(BaseModel):
    """One behavior event reported by the extension."""

    type: str
    url: str = ""
    title: str = ""
    timestamp: int
    source_platform: str = "bilibili"
    context: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    # v0.3.x event-satisfaction signal: dwell on video-page exit. Either
    # top-level or `metadata.watch_seconds` is accepted; the endpoint
    # folds top-level into metadata before persistence so the storage
    # classifier reads from a single canonical location.
    watch_seconds: float | None = None
    video_duration_seconds: float | None = None


class BehaviorEventBatchIn(BaseModel):
    """Batch payload used by the service worker."""

    events: list[BehaviorEventIn]


class HealthResponse(BaseModel):
    """Health-check response."""

    status: str
    service: str
    profile_ready: bool | None = None
    lan_ip: str | None = None
    # v0.3.95+: surfaces whether the embedding service built successfully.
    # ``False`` means semantic dedup / diversity is degraded (recommendations
    # may repeat near-identical content under different ids) — the popup
    # turns this into a one-click "enable local Ollama" banner.
    embedding_ready: bool | None = None


class InitStageProgressOut(BaseModel):
    """Fine-grained progress inside a running stage (init-progress spec).

    Additive/optional: only present while a stage exposes sub-progress (e.g.
    stage 2 chunked preference analysis). Absent on stages with no natural
    progress points or on a status produced by an older backend.
    """

    done: int = 0
    total: int = 0
    note: str | None = None


class InitStageOut(BaseModel):
    """One stage of guided init (gui-init spec API shape)."""

    n: int
    label: str
    status: str  # pending | running | ok | warning | failed
    reason: str | None = None
    # Optional (backward-compatible): intra-stage sub-progress + typical
    # duration hint. Old stages_json rows lack these; both default to None.
    progress: InitStageProgressOut | None = None
    eta_seconds: int | None = None


class InitPrerequisitesOut(BaseModel):
    """Pre-init checklist surfaced to the UI."""

    bilibili_logged_in: bool = False
    bilibili_check: str = "checking"  # ok | failed | checking
    bilibili_detail: str = ""  # why the last probe failed ("" when ok)
    llm_ready: bool = False
    embedding_ready: bool = False
    # Classified cause when embedding_ready is False, so the UI can say
    # WHY instead of a dead retry (v0.3.155+): ok | disabled | misconfigured |
    # not_running | model_missing | model_broken | model_path_encoding |
    # disk_full | network | model_oom | provider_error | error.
    embedding_check: str = "ok"
    embedding_detail: str = ""  # human-readable hint ("" when ok/disabled)
    # Live pull progress while a one-click repair is downloading the model
    # (embedding_check == "repairing"), so init pages can render a real
    # progress indicator instead of an opaque wait. total may be 0 while
    # Ollama resolves the manifest.
    embedding_repair_running: bool = False
    embedding_repair_completed: int = 0
    embedding_repair_total: int = 0
    ollama_phase: str = "ready"
    embedding_pull_status: str = ""
    embedding_required: bool = False
    enabled_platforms: list[str] = Field(default_factory=list)


class InitStatusOut(BaseModel):
    """Authoritative guided-init status / progress (gui-init spec API shape)."""

    initialized: bool = False
    running: bool = False
    run_id: str | None = None
    sequence: int = 0
    current_stage: int = 0
    total_stages: int = 4
    stages: list[InitStageOut] = Field(default_factory=list)
    partial_success: bool = False
    can_start: bool = False
    can_manage: bool = False
    # How this caller can start initialization. ``cli_only`` is used by
    # container runtimes; ``local_only`` means the page is being viewed from a
    # remote/LAN client and initialization must be started on the host.
    start_mode: Literal["web", "cli_only", "local_only"] = "web"
    prerequisites: InitPrerequisitesOut = Field(default_factory=InitPrerequisitesOut)
    reason: str = "none"
    detail: str = ""
    # Capability (reason/detail) and the most recent terminal failure are
    # independent. Keeping both prevents Docker's ``unsupported_runtime`` from
    # hiding a real CLI/background analysis failure that the user must fix.
    last_failure_reason: str = ""
    last_failure_detail: str = ""
    # Wall-clock (server tz) of the most recent status write for the current
    # run — advanced by every stage/progress/heartbeat write. "" when idle.
    # The GUI derives a "still working / stalled" indicator from now minus this.
    last_activity: str = ""


class RecommendationOut(BaseModel):
    """Recommendation payload exposed to the popup."""

    id: int
    bvid: str
    item_key: str = ""
    title: str = ""
    up_name: str = ""
    cover_url: str = ""
    expression: str = ""
    topic_label: str = ""
    presented: bool = False
    feedback_type: str = ""
    # Multi-source fields (additive, backward-compatible)
    content_id: str = ""
    content_url: str = ""
    source_platform: str = ""
    published_at: str = ""
    published_label: str = ""
    # Text-first sources (X tweet/thread): the popup renders a no-cover
    # text card from body_text/title when content_type is tweet/thread or
    # cover_url is empty.
    content_type: str = "video"
    body_text: str = ""
    # Desktop card metadata (additive for issue #75; extension popup ignores unknown keys).
    duration: int = 0
    view_count: int = 0
    like_count: int = 0
    danmaku_count: int = 0
    # Cross-platform engagement counts (issue #79): text-first sources like
    # Zhihu have no view/danmaku but do carry favorites/comments — surface them
    # so the card stats row is not left with a lone like count.
    favorite_count: int = 0
    comment_count: int = 0
    up_mid: int = 0


class RecommendationListResponse(BaseModel):
    """Wrapper response for recommendation lists."""

    items: list[RecommendationOut]


class RecommendationReshuffleResponse(BaseModel):
    """Immediate recommendation reshuffle result."""

    items: list[RecommendationOut]


class RecommendationReshuffleIn(BaseModel):
    """Optional visible-card exclusions for a reshuffle request."""

    excluded_bvids: list[str] = Field(default_factory=list)


class RecommendationAppendIn(BaseModel):
    """Request payload for appending another recommendation page."""

    excluded_bvids: list[str] = Field(default_factory=list)


class RecommendationRefreshResponse(BaseModel):
    """Result of one explicit recommendation refresh request."""

    ok: bool
    accepted: bool
    state: str = "idle"
    reason: str = ""


class RuntimeStatusResponse(BaseModel):
    """Runtime summary for popup and background status checks."""

    initialized: bool
    recommendation_count: int
    pending_signal_events: int
    last_refresh_at: str = ""
    last_notification_at: str = ""
    unread_count: int
    pool_available_count: int = 0
    pool_raw_count: int = 0
    pool_pending_count: int = 0
    pool_pending_eval_count: int = 0
    pool_evaluated_pending_count: int = 0
    pool_target_count: int = 0
    candidate_eval_state: str = "idle"
    candidate_eval_workers: int = 0
    candidate_eval_in_flight: int = 0
    candidate_eval_pending: int = 0
    candidate_eval_backoff_until: float = 0.0
    candidate_eval_last_error: str = ""
    candidate_eval_last_batch_seconds: float = 0.0
    candidate_eval_last_cached: int = 0
    candidate_eval_last_rejected: int = 0
    expression_pending_count: int = 0
    expression_batch_state: str = "idle"
    expression_batch_deadline: float = 0.0
    expression_last_completed: int = 0
    expression_last_error: str = ""
    llm_total_concurrency: int = 0
    llm_background_concurrency: int = 0
    llm_total_active: int = 0
    llm_total_waiting: int = 0
    llm_background_active: int = 0
    llm_background_waiting: int = 0
    llm_refill_active: int = 0
    llm_refill_waiting: int = 0
    llm_maintenance_active: int = 0
    llm_maintenance_waiting: int = 0
    llm_refill_priority_active: bool = False
    inventory_priority_state: str = "healthy"
    last_discovered_count: int = 0
    last_replenished_count: int = 0
    recent_pool_topics: list[str] = Field(default_factory=list)
    manual_refresh_state: str = "idle"
    manual_refresh_message: str = ""
    last_account_sync_at: str = ""
    last_account_sync_error: str = ""
    auto_update_enabled: bool = False
    install_mode: str = ""
    current_version: str = ""
    latest_remote_version: str = ""
    last_update_check_at: str = ""
    last_update_error: str = ""
    backend_update_state: str = "unknown"
    backend_update_reason: str = "none"


class ActivityFeedItemOut(BaseModel):
    """One recent user-visible activity item for the popup."""

    id: str
    kind: str
    summary: str
    detail: str = ""
    created_at: str = ""
    tone: str = "info"


class ActivityFeedResponse(BaseModel):
    """Aggregated activity feed for the popup activity card."""

    live_summary: str = ""
    headline: str = ""
    items: list[ActivityFeedItemOut] = Field(default_factory=list)
    has_more: bool = False
    next_cursor: str = ""


class PendingNotificationOut(BaseModel):
    """One notification-worthy recommendation."""

    recommendation_id: int
    bvid: str
    title: str = ""
    reason: str = ""


class PendingNotificationResponse(BaseModel):
    """Wrapper for a pending notification candidate."""

    item: PendingNotificationOut | None = None


class PendingCognitionUpdateOut(BaseModel):
    """One cognition update worthy of notifying in the extension."""

    id: str
    kind: str
    summary: str


class PendingCognitionUpdateResponse(BaseModel):
    """Wrapper for a pending cognition update."""

    item: PendingCognitionUpdateOut | None = None


class PendingDelightOut(BaseModel):
    """One proactive delight recommendation."""

    bvid: str
    item_key: str = ""
    content_id: str = ""
    title: str = ""
    delight_reason: str = ""
    delight_score: float = 0.0
    delight_hook: str = ""
    cover_url: str = ""
    content_url: str = ""
    source_platform: str = ""
    published_at: str = ""
    published_label: str = ""
    content_type: str = "video"
    body_text: str = ""
    # Engagement stats (from content_cache), so the delight card can show the
    # same ▶ / 👍 / 💬 metadata row as the recommendation grid. 0 = unknown /
    # not fetched (platforms that don't populate a metric render nothing).
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    danmaku_count: int = 0
    favorite_count: int = 0


class PendingDelightResponse(BaseModel):
    """Wrapper for a pending delight candidate."""

    item: PendingDelightOut | None = None


class DelightAckIn(BaseModel):
    """Acknowledge delivery of a delight notification."""

    bvid: str


class DelightAckResponse(BaseModel):
    """Response after marking a delight notification as delivered."""

    ok: bool
    bvid: str


class BilibiliCookieIn(BaseModel):
    """Cookie sync payload from the browser extension.

    Lets the extension push the user's live bilibili.com session cookies
    to the backend (writes to data/bilibili_cookie.json + config.toml's
    [bilibili].cookie). Replaces the manual F12 → copy → paste flow.
    """

    cookie: str = Field(
        ...,
        description="Cookie header string ('SESSDATA=...; bili_jct=...; ...').",
        min_length=1,
    )
    source: str = Field(
        default="extension",
        description="Where the cookie came from. Used for telemetry only.",
    )
    validate_with_bilibili: bool = Field(
        default=True,
        description="If true, hit the Bilibili nav endpoint before saving "
        "to confirm the cookie is actually authenticated.",
    )


class BilibiliCookieResponse(BaseModel):
    """Result of a cookie-sync attempt.

    ``error_code`` lets the extension pick a smart retry cadence
    (network errors → quick retry, expired cookie → wait for next
    login). Empty when ``ok=True``.
    """

    ok: bool
    authenticated: bool
    username: str = ""
    user_id: int = 0
    message: str = ""
    # v0.3.42+ machine-readable code for the extension to branch retry
    # logic on. One of:
    #   ""                       — success
    #   "empty_cookie"           — payload was empty
    #   "cookie_invalid"         — Bilibili says cookie is bad / expired
    #   "validation_network"     — backend couldn't reach api.bilibili.com
    error_code: str = ""


class DouyinCookieIn(BaseModel):
    """Cookie sync payload for Douyin direct-cookie discovery."""

    cookie: str = Field(
        ...,
        description="Cookie header string from douyin.com.",
        min_length=1,
    )
    source: str = Field(
        default="extension",
        description="Where the cookie came from. Used for telemetry only.",
    )


class DouyinCookieResponse(BaseModel):
    """Result of syncing a Douyin Cookie header."""

    ok: bool
    has_cookie: bool
    cookie_names: list[str] = Field(default_factory=list)
    message: str = ""
    error_code: str = ""


class XCookieIn(BaseModel):
    """Cookie sync payload for X (Twitter) server-side cookie-replay discovery."""

    cookie: str = Field(
        ...,
        description="Cookie header string from x.com.",
        min_length=1,
    )
    source: str = Field(
        default="extension",
        description="Where the cookie came from. Used for telemetry only.",
    )


class XCookieResponse(BaseModel):
    """Result of syncing an X (Twitter) Cookie header.

    ``has_cookie`` is true only when BOTH ``auth_token`` and ``ct0`` are
    present — twitter-cli needs both to authenticate.
    """

    ok: bool
    has_cookie: bool
    cookie_names: list[str] = Field(default_factory=list)
    message: str = ""
    error_code: str = ""


class RedditCookieIn(BaseModel):
    """Cookie sync payload for Reddit rdt-cli discovery."""

    cookie: str = Field(
        ...,
        description="Cookie header string from reddit.com.",
        min_length=1,
    )
    source: str = Field(
        default="extension",
        description="Where the cookie came from. Used for telemetry only.",
    )


class RedditCookieResponse(BaseModel):
    """Result of syncing Reddit cookies into the rdt-cli credential store."""

    ok: bool
    has_cookie: bool
    cookie_names: list[str] = Field(default_factory=list)
    credential_file: str = ""
    message: str = ""
    error_code: str = ""


class XhsLoginStateIn(BaseModel):
    """Privacy-preserving xhs login state reported by the browser extension."""

    logged_in: StrictBool


class XhsLoginStateResponse(BaseModel):
    """Result of persisting the browser-observed xhs login state."""

    ok: bool = True
    logged_in: bool
    updated_at: str = ""


class ZhihuLoginStateIn(BaseModel):
    """Privacy-preserving Zhihu login state reported by the browser extension."""

    logged_in: StrictBool


class ZhihuLoginStateResponse(BaseModel):
    """Result of persisting the browser-observed Zhihu login state."""

    ok: bool = True
    logged_in: bool
    updated_at: str = ""


class XStatusResponse(BaseModel):
    """Current X (Twitter) source health (spec §7).

    ``state`` is one of ``ok`` / ``missing_cookie`` / ``expired_cookie`` /
    ``rate_limited`` / ``blocked``. ``feed_paused`` is true when repeated
    For-You failures have auto-paused the high-visibility home-timeline fetch.
    """

    state: str = "ok"
    consecutive_failures: int = 0
    feed_paused: bool = False
    cooldown_until: str = ""
    detail: str = ""
    updated_at: str = ""


class SourceStatusItem(BaseModel):
    """Unified per-source login / cookie readiness (settings pages).

    ``state`` is a coarse, source-agnostic status so every platform can render
    the same chip:

    - ``ok``         — credential present AND live-validated (X only, from the
      health store).
    - ``ready``      — credential present and structurally valid, but not
      live-validated (B站 cookie with login fields, or a fresh browser login
      state recently synced).
    - ``partial``    — credential present but structurally incomplete, likely
      broken (B站 cookie missing some of the core login fields).
    - ``stale``      — credential synced before but not recently, likely
      expired.
    - ``missing``    — source enabled but no usable credential.
    - ``unverified`` — a credential or source is configured, but local state
      does not prove that it currently works.
    - ``login_required`` / ``error`` — a local command credential is missing,
      or its saved credential file is invalid.
    - ``expired`` / ``rate_limited`` / ``blocked`` — X live-health states.
    - ``no_auth``    — source needs no login (YouTube, public).

    ``logged_in`` is a convenience flag (``state in {ok, ready, no_auth}``) so
    the UI can pick a dot colour without re-deriving the rule.
    """

    enabled: bool = False
    state: str = "missing"
    detail: str = ""
    logged_in: bool = False
    feed_paused: bool = False


class SourcesStatusResponse(BaseModel):
    """Login / cookie readiness for every content source, keyed by platform.

    Backs the unified status chip shown on both the desktop-Web and the
    extension settings pages. Derived entirely from local signals (config
    cookie fields, the X health store, the Douyin cookie file/env, and the
    privacy-preserving 小红书 browser login-state flag) — no outbound platform calls.
    """

    bilibili: SourceStatusItem = Field(default_factory=SourceStatusItem)
    xiaohongshu: SourceStatusItem = Field(default_factory=SourceStatusItem)
    douyin: SourceStatusItem = Field(default_factory=SourceStatusItem)
    youtube: SourceStatusItem = Field(default_factory=SourceStatusItem)
    twitter: SourceStatusItem = Field(default_factory=SourceStatusItem)
    zhihu: SourceStatusItem = Field(default_factory=SourceStatusItem)
    reddit: SourceStatusItem = Field(default_factory=SourceStatusItem)


class SourceCredentialItem(BaseModel):
    """Current local credential snapshot for a source settings page."""

    label: str = "Cookie"
    value: str = ""
    available: bool = False
    detail: str = ""


class SourcesCredentialsResponse(BaseModel):
    """Current local Cookie / token values for source settings pages."""

    bilibili: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    xiaohongshu: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    douyin: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    youtube: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    twitter: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    zhihu: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    reddit: SourceCredentialItem = Field(default_factory=SourceCredentialItem)


class NotificationAckIn(BaseModel):
    """Acknowledge one browser notification delivery."""

    bvid: str


class NotificationAckResponse(BaseModel):
    """Response after marking a notification as delivered."""

    ok: bool
    bvid: str


class CognitionUpdateSeenIn(BaseModel):
    """Acknowledge one cognition update as seen/notified."""

    id: str


class CognitionUpdateSeenResponse(BaseModel):
    """Response after marking a cognition update as seen."""

    ok: bool
    id: str


class CognitionUpdateSummary(BaseModel):
    """Structured cognition card shown in the popup profile tab."""

    summary: str
    context_line: str = ""
    impact: str = ""
    reasoning: str = ""
    evidence: str = ""
    source: str = ""
    source_label: str = ""
    expand_hint: str = "summary_only"
    created_at: str = ""


class SpeculativeSpecificOut(BaseModel):
    """A narrow topic within a speculative domain."""

    name: str = ""
    confirmation_count: int = 0


class SpeculativeInterestOut(BaseModel):
    """A speculated interest direction with two-level structure."""

    domain: str = ""
    reason: str = ""
    confidence: float = 0.0
    probe_mode: str = "near"
    challenge: bool = False
    confirmation_count: int = 0
    confirmation_threshold: int = 3
    status: str = "active"
    specifics: list[SpeculativeSpecificOut] = Field(default_factory=list)


class SpeculativeAvoidanceOut(BaseModel):
    """A speculated avoidance direction with two-level structure."""

    domain: str = ""
    reason: str = ""
    confidence: float = 0.0
    source_mode: str = ""
    source_signal: str = ""
    confirmation_count: int = 0
    confirmation_threshold: int = 3
    status: str = "active"
    specifics: list[SpeculativeSpecificOut] = Field(default_factory=list)


class MBTIDimensionOut(BaseModel):
    """A single MBTI dimension pole with strength."""

    pole: str = ""
    strength: float = 0.5


class MBTIOut(BaseModel):
    """MBTI personality type with dimensional breakdown."""

    type: str = ""
    dimensions: dict[str, MBTIDimensionOut] = Field(default_factory=dict)
    confidence: float = 0.0


class InterestSpecificOut(BaseModel):
    """A narrow interest within a domain."""

    name: str = ""
    weight: float = 0.5


class InterestDomainOut(BaseModel):
    """A broad interest domain with optional specific sub-interests."""

    domain: str = ""
    weight: float = 0.5
    specifics: list[InterestSpecificOut] = Field(default_factory=list)


class StylePreferenceOut(BaseModel):
    """Content style preferences."""

    preferred_duration: str = ""
    preferred_pace: str = ""
    quality_sensitivity: float = 0.5
    humor_preference: float = 0.5
    depth_preference: float = 0.5


class ContextModeOut(BaseModel):
    """Contextual usage patterns."""

    weekday_patterns: str = ""
    weekend_patterns: str = ""
    time_of_day_patterns: str = ""
    session_type: str = ""


class AwarenessNoteOut(BaseModel):
    """A single awareness observation from the soul layer."""

    date: str = ""
    observation: str = ""
    trend: str = ""
    emotion_guess: str = ""


class InsightHypothesisOut(BaseModel):
    """An active insight or hypothesis about the user."""

    hypothesis: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    validated: bool = False
    created_at: str = ""


class ProfileSummaryResponse(BaseModel):
    """Full soul profile exposed to the popup — all five Onion layers."""

    initialized: bool
    personality_portrait: str = ""
    # Core layer
    core_traits: list[str] = Field(default_factory=list)
    deep_needs: list[str] = Field(default_factory=list)
    mbti: MBTIOut = Field(default_factory=MBTIOut)
    # Values layer
    values: list[str] = Field(default_factory=list)
    motivational_drivers: list[str] = Field(default_factory=list)
    # Interest layer
    likes: list[InterestDomainOut] = Field(default_factory=list)
    dislikes: list[InterestDomainOut] = Field(default_factory=list)
    favorite_up_users: list[str] = Field(default_factory=list)
    # Role layer
    life_stage: str = ""
    current_phase: str = ""
    # Surface layer
    cognitive_style: list[str] = Field(default_factory=list)
    style: StylePreferenceOut = Field(default_factory=StylePreferenceOut)
    context: ContextModeOut = Field(default_factory=ContextModeOut)
    exploration_openness: float = 0.5
    # Cross-cutting
    speculative_interests: list[SpeculativeInterestOut] = Field(default_factory=list)
    speculative_avoidances: list[SpeculativeAvoidanceOut] = Field(default_factory=list)
    recent_cognition_updates: list[CognitionUpdateSummary] = Field(default_factory=list)
    has_more_cognition_updates: bool = False
    next_cognition_cursor: str = ""
    active_insights: list[InsightHypothesisOut] = Field(default_factory=list)
    recent_awareness: list[AwarenessNoteOut] = Field(default_factory=list)
    # User-authored overrides (ProfileOverrides.to_dict()), so the display UI
    # can badge edited/pinned fields. Empty when the user has made no edits.
    overrides: dict[str, object] = Field(default_factory=dict)


class EventRejectedOut(BaseModel):
    """One event skipped during batch ingest."""

    index: int
    type: str
    reason: str


class EventIngestResponse(BaseModel):
    """Response after accepting a batch of events."""

    accepted: int
    rejected: list[EventRejectedOut] = Field(default_factory=list)


ExtensionE2EPlatform = Literal["douyin", "xiaohongshu", "twitter", "reddit"]
ExtensionE2EAction = Literal[
    "snapshot",
    "scroll",
    "click",
    "like",
    "favorite",
    "share",
    "follow",
    "repost",
    "bookmark",
]
ExtensionE2EActionList = Annotated[list[ExtensionE2EAction], Field(min_length=1)]
ExtensionE2EActionStatus = Literal["ok", "skipped", "failed"]
ExtensionE2ERunStatus = Literal["ok", "partial", "failed", "timeout"]
ExtensionNativeSaveE2EPlatform = Literal[
    "youtube",
    "xiaohongshu",
    "douyin",
    "twitter",
    "zhihu",
    "reddit",
]
_EXTENSION_NATIVE_SAVE_E2E_TARGETS: dict[str, dict[NativeSaveActionOut, str]] = {
    "youtube": {"favorite": "OpenBiliClaw", "watch_later": "YouTube Watch Later"},
    "xiaohongshu": {"favorite": "小红书收藏", "watch_later": "小红书收藏"},
    "douyin": {"favorite": "抖音收藏", "watch_later": "抖音收藏"},
    "twitter": {"favorite": "X Bookmarks", "watch_later": "X Bookmarks"},
    "zhihu": {"favorite": "OpenBiliClaw", "watch_later": "OpenBiliClaw"},
    "reddit": {"favorite": "Reddit Saved", "watch_later": "Reddit Saved"},
}
_EXTENSION_NATIVE_SAVE_E2E_CONTENT_IDS: dict[str, re.Pattern[str]] = {
    "youtube": re.compile(r"[A-Za-z0-9_-]{11}"),
    "xiaohongshu": re.compile(r"[0-9a-f]{24}"),
    "douyin": re.compile(r"[0-9]{5,30}"),
    "twitter": re.compile(r"[0-9]{5,30}"),
    "zhihu": re.compile(r"(?:question|answer|article):[0-9]+"),
    "reddit": re.compile(r"t[13]_[a-z0-9]+"),
}
_EXTENSION_NATIVE_SAVE_E2E_ERROR_CODES: dict[NativeSaveStatusOut, frozenset[str]] = {
    "pending": frozenset({""}),
    "syncing": frozenset({""}),
    "synced": frozenset({""}),
    "already_synced": frozenset({""}),
    "login_required": frozenset({""}),
    "rate_limited": frozenset({""}),
    "unsupported": frozenset({"unsupported_content_type"}),
    "extension_required": frozenset({"extension_unavailable"}),
    "failed": frozenset(
        {
            "adapter_exception",
            "adapter_timeout",
            "extension_task_timeout",
            "interrupted",
            "invalid_adapter_result",
            "item_heartbeat_failed",
            "native_confirmation_not_observed",
            "native_content_not_ready",
            "native_control_not_found",
            "native_dialog_not_opened",
            "native_request_rejected",
            "native_save_failed",
            "native_save_timeout",
            "native_target_not_found",
            "not_saved_locally",
            "sync_already_in_progress",
        }
    ),
}


def _default_extension_e2e_platforms() -> list[ExtensionE2EPlatform]:
    return ["douyin", "xiaohongshu", "twitter", "reddit"]


class ExtensionNativeSaveE2EAuthorizationIn(BaseModel):
    """Exact, non-secret authorization for one named native-save mutation."""

    model_config = ConfigDict(extra="forbid")

    allow_state_changing: StrictBool
    platform: ExtensionNativeSaveE2EPlatform
    action: NativeSaveActionOut
    content_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    expected_target: Annotated[StrictStr, Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def _validate_exact_mapping(self) -> Self:
        if self.allow_state_changing is not True:
            raise ValueError("allow_state_changing must be true")
        if _EXTENSION_NATIVE_SAVE_E2E_CONTENT_IDS[self.platform].fullmatch(self.content_id) is None:
            raise ValueError("content_id is not an allowed public content identity")
        if self.expected_target != _EXTENSION_NATIVE_SAVE_E2E_TARGETS[self.platform][self.action]:
            raise ValueError("expected_target does not match platform action")
        return self


class ExtensionE2ERunIn(BaseModel):
    """Request to run a local browser-extension E2E simulation."""

    model_config = ConfigDict(extra="forbid")

    platforms: list[ExtensionE2EPlatform] = Field(
        default_factory=_default_extension_e2e_platforms,
        min_length=1,
    )
    actions: dict[ExtensionE2EPlatform, ExtensionE2EActionList] = Field(default_factory=dict)
    allow_state_changing: bool = False
    timeout_seconds: int = Field(default=45, ge=5, le=180)
    native_save_authorization: ExtensionNativeSaveE2EAuthorizationIn | None = None

    @model_validator(mode="after")
    def _validate_native_save_mode(self) -> Self:
        if self.native_save_authorization is None:
            return self
        if self.allow_state_changing is not True:
            raise ValueError("allow_state_changing must be true for native save")
        if self.actions:
            raise ValueError("native-save E2E cannot include generic actions")
        return self


class ExtensionE2EActionResultIn(BaseModel):
    """One action result reported by the extension E2E runner."""

    action: ExtensionE2EAction
    status: ExtensionE2EActionStatus
    detail: str = ""


class ExtensionE2EPlatformResultIn(BaseModel):
    """Per-platform action results reported by the extension."""

    platform: ExtensionE2EPlatform
    actions: list[ExtensionE2EActionResultIn] = Field(default_factory=list)
    detail: str = ""


class ExtensionNativeSaveE2EResultIn(BaseModel):
    """Only fields allowed in a native-save E2E result record."""

    model_config = ConfigDict(extra="forbid")

    platform: ExtensionNativeSaveE2EPlatform
    action: NativeSaveActionOut
    content_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    expected_target: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    task_status: NativeSaveStatusOut
    error_code: Annotated[StrictStr, Field(max_length=64)] = ""

    @model_validator(mode="after")
    def _validate_safe_pair(self) -> Self:
        authorization = ExtensionNativeSaveE2EAuthorizationIn(
            allow_state_changing=True,
            platform=self.platform,
            action=self.action,
            content_id=self.content_id,
            expected_target=self.expected_target,
        )
        del authorization
        if self.error_code not in _EXTENSION_NATIVE_SAVE_E2E_ERROR_CODES[self.task_status]:
            raise ValueError("task_status and error_code combination is not allowed")
        return self


class ExtensionE2EResultIn(BaseModel):
    """Signed extension callback payload for a local E2E run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    token: str
    platforms: list[ExtensionE2EPlatformResultIn] = Field(default_factory=list)
    error: str = ""
    native_save_result: ExtensionNativeSaveE2EResultIn | None = None

    @model_validator(mode="after")
    def _validate_result_mode(self) -> Self:
        if self.native_save_result is not None and (self.platforms or self.error):
            raise ValueError("native-save result cannot include generic result fields")
        return self


class ExtensionE2EEventMatchOut(BaseModel):
    """Natural backend event matched to a requested extension action."""

    event_id: int
    event_type: str
    url: str = ""
    title: str = ""


class ExtensionE2EActionReportOut(BaseModel):
    """Final report for one requested action."""

    action: ExtensionE2EAction
    extension_status: ExtensionE2EActionStatus = "skipped"
    extension_executed: bool = False
    extension_detail: str = ""
    backend_event_matched: bool = False
    backend_event: ExtensionE2EEventMatchOut | None = None


class ExtensionE2EPlatformReportOut(BaseModel):
    """Final report for one requested platform."""

    platform: ExtensionE2EPlatform
    actions: list[ExtensionE2EActionReportOut] = Field(default_factory=list)
    detail: str = ""


class ExtensionE2ERunOut(BaseModel):
    """Final local E2E run report."""

    run_id: str
    status: ExtensionE2ERunStatus
    platforms: list[ExtensionE2EPlatformReportOut] = Field(default_factory=list)
    error: str = ""
    timeout_seconds: int
    native_save_result: ExtensionNativeSaveE2EResultIn | None = None


class FeedbackIn(BaseModel):
    """Feedback payload submitted from CLI-compatible clients."""

    recommendation_id: int
    feedback_type: str
    note: str = ""


class FeedbackResponse(BaseModel):
    """Response after accepting recommendation feedback."""

    ok: bool
    recommendation_id: int
    feedback_type: str


class InsightFeedbackIn(BaseModel):
    """User confirm/reject on a specific insight hypothesis (insight cards)."""

    hypothesis: str
    signal: str  # confirm/like/support (positive) or reject/dislike/deny


class InsightFeedbackResponse(BaseModel):
    """Result of calibrating an insight hypothesis from user feedback."""

    ok: bool
    matched: bool
    hypothesis: str = ""
    signal: str = ""
    validated: bool = False
    confidence: float = 0.0


class ProfileEditIn(BaseModel):
    """One user edit to the AI-generated profile overlay.

    ``target`` is an onion field path (e.g. ``core.core_traits``) or an
    interest polarity (``likes`` / ``dislikes``). ``op`` ∈
    {set, add, remove, reset}. ``parent`` targets a specific under an
    interest domain; ``weight`` pins an interest domain's weight.
    """

    target: str
    op: str
    value: str | float | None = None
    parent: str = ""
    weight: float | None = None


class WatchLaterAddIn(BaseModel):
    """Payload to bookmark a video."""

    bvid: str
    note: str = ""


class WatchLaterStateResponse(BaseModel):
    """Whether a single video is bookmarked, plus the total count."""

    saved: bool
    total: int
    item_key: str = ""
    sync_status: NativeSaveStatusOut | None = None
    sync_task_id: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class WatchLaterItem(BaseModel):
    """One item in the watch-later list."""

    bvid: str
    item_key: str = ""
    content_id: str = ""
    title: str = ""
    up_name: str = ""
    cover_url: str = ""
    content_url: str = ""
    source_platform: str = ""
    content_type: str = "video"
    added_at: str = ""
    sync_status: NativeSaveStatusOut = "pending"
    sync_task_id: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class WatchLaterListResponse(BaseModel):
    """Paginated watch-later list."""

    items: list[WatchLaterItem]
    total: int


class FavoriteAddIn(BaseModel):
    """Payload to favorite (收藏) a video."""

    bvid: str
    note: str = ""


class FavoriteStateResponse(BaseModel):
    """Whether a single video is favorited, plus the total count."""

    saved: bool
    total: int
    item_key: str = ""
    sync_status: NativeSaveStatusOut | None = None
    sync_task_id: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class FavoriteItem(BaseModel):
    """One item in the favorites list."""

    bvid: str
    item_key: str = ""
    content_id: str = ""
    title: str = ""
    up_name: str = ""
    cover_url: str = ""
    content_url: str = ""
    source_platform: str = ""
    content_type: str = "video"
    added_at: str = ""
    sync_status: NativeSaveStatusOut = "pending"
    sync_task_id: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class FavoriteListResponse(BaseModel):
    """Paginated favorites list."""

    items: list[FavoriteItem]
    total: int


_SavedIdentityString = Annotated[StrictStr, Field(max_length=2048)]


def validate_saved_item_key(value: str) -> str:
    """Validate a canonical item key without guessing or alias resolution."""
    if not isinstance(value, str):
        raise ValueError("item_key must be a string")
    item_key = value.strip()
    if (
        not item_key
        or item_key != value
        or len(item_key) > 2048
        or _has_identity_whitespace(item_key)
        or _has_unicode_control(item_key)
    ):
        raise ValueError("item_key must be a non-blank canonical key")
    parts = item_key.split(":")
    platform = parts[0]
    stable_key = len(parts) == 2 and bool(parts[1])
    zhihu_typed_key = (
        len(parts) == 3
        and platform == "zhihu"
        and _ZHIHU_TYPED_CONTENT_ID_RE.fullmatch(":".join(parts[1:])) is not None
    )
    url_fallback_key = (
        len(parts) == 3
        and parts[1] == "url"
        and _URL_FALLBACK_ID_RE.fullmatch(parts[2]) is not None
    )
    if (
        not platform
        or not (stable_key or zhihu_typed_key or url_fallback_key)
        or canonical_source_platform(platform) != platform
        or _SAVED_PLATFORM_RE.fullmatch(platform) is None
    ):
        raise ValueError("item_key must be a canonical platform:content identity")
    return item_key


class SavedItemIn(BaseModel):
    """Canonical local-save input for every supported source platform."""

    model_config = ConfigDict(extra="forbid")

    source_platform: _SavedIdentityString
    content_id: _SavedIdentityString = ""
    content_url: _SavedIdentityString = ""
    content_type: Annotated[StrictStr, Field(min_length=1, max_length=128)] = "video"
    title: _SavedIdentityString = ""
    author_name: _SavedIdentityString = ""
    cover_url: _SavedIdentityString = ""
    note: _SavedIdentityString = ""

    @field_validator(
        "source_platform",
        "content_id",
        "content_url",
        "content_type",
        "title",
        "author_name",
        "cover_url",
        "note",
    )
    @classmethod
    def _strip_safe_text(cls, value: str, info: ValidationInfo) -> str:
        if _has_unicode_control(value):
            raise ValueError("saved item fields must not contain Unicode control characters")
        if (
            info.field_name
            in {
                "source_platform",
                "content_id",
                "content_url",
                "cover_url",
            }
            and value != value.strip()
        ):
            raise ValueError("saved identity and URL fields must not have surrounding whitespace")
        normalized = value.strip()
        return normalized

    @field_validator("source_platform")
    @classmethod
    def _canonicalize_platform(cls, value: str) -> str:
        platform = canonical_source_platform(value)
        if not platform:
            raise ValueError("source_platform is required")
        if _SAVED_PLATFORM_RE.fullmatch(platform) is None:
            raise ValueError("source_platform must be a canonical platform slug")
        return platform

    @field_validator("content_type")
    @classmethod
    def _require_content_type(cls, value: str) -> str:
        if not value:
            raise ValueError("content_type is required")
        return value

    @field_validator("content_url", "cover_url")
    @classmethod
    def _validate_optional_http_url(cls, value: str) -> str:
        if not value:
            return value
        return _validate_http_url(value)

    @field_validator("content_id")
    @classmethod
    def _validate_content_id(cls, value: str, info: ValidationInfo) -> str:
        platform = str(info.data.get("source_platform", ""))
        typed_zhihu_id = (
            platform == "zhihu" and _ZHIHU_TYPED_CONTENT_ID_RE.fullmatch(value) is not None
        )
        if (
            (":" in value and not typed_zhihu_id)
            or _has_identity_whitespace(value)
            or _has_unicode_control(value)
        ):
            raise ValueError("content_id must be one non-blank stable identity segment")
        return value

    def model_post_init(self, __context: object) -> None:
        del __context
        validate_saved_item_key(
            make_item_key(self.source_platform, self.content_id, self.content_url)
        )


class SavedItemKeyIn(BaseModel):
    """Exact local membership identity used for removal."""

    model_config = ConfigDict(extra="forbid")

    item_key: _SavedIdentityString

    @field_validator("item_key")
    @classmethod
    def _validate_item_key(cls, value: str) -> str:
        return validate_saved_item_key(value)


class SavedSyncRequest(BaseModel):
    """Explicit manual-sync selection; an empty list means all eligible rows."""

    model_config = ConfigDict(extra="forbid")

    item_keys: Annotated[list[StrictStr], Field(max_length=500)] = Field(default_factory=list)

    @field_validator("item_keys")
    @classmethod
    def _validate_item_keys(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(validate_saved_item_key(value) for value in values))


class ExtensionNativeSaveResultIn(BaseModel):
    """Strict extension callback for one durable native-save job."""

    model_config = ConfigDict(extra="forbid")

    task_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    item_key: Annotated[StrictStr, Field(min_length=1, max_length=768)]
    status: Literal[
        "synced",
        "already_synced",
        "login_required",
        "rate_limited",
        "unsupported",
        "failed",
    ]
    error_code: Annotated[StrictStr, Field(max_length=128)] = ""
    error_message: Annotated[StrictStr, Field(max_length=512)] = ""


class SavedItemStateResponse(BaseModel):
    """Local membership plus its latest native-sync state."""

    saved: bool
    item_key: str
    sync_status: NativeSaveStatusOut | None = None
    sync_task_id: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class SavedListItem(BaseModel):
    """One platform-neutral saved membership and sync snapshot."""

    item_key: str
    source_platform: str
    content_id: str
    content_url: str = ""
    content_type: str = "video"
    title: str = ""
    author_name: str = ""
    cover_url: str = ""
    note: str = ""
    added_at: str = ""
    sync_status: NativeSaveStatusOut = "pending"
    sync_task_id: str = ""
    requested_action: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class SavedListResponse(BaseModel):
    """Paginated platform-neutral saved memberships."""

    items: list[SavedListItem]
    total: int


class SavedSyncItemResponse(BaseModel):
    """One truthful item result in a native-save task."""

    item_key: str
    status: NativeSaveStatusOut
    resolved_action: NativeSaveActionOut
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class SavedSyncBatchResponse(BaseModel):
    """Durable native-save batch state returned at creation and polling."""

    task_id: str
    items: list[SavedSyncItemResponse]


class RecommendationClickIn(BaseModel):
    """Payload for a recommendation click-through from the extension popup."""

    recommendation_id: int | None = None
    bvid: str = ""
    content_id: str = ""
    content_url: str = ""
    source_platform: str = ""
    title: str = ""
    topic_label: str = ""
    up_name: str = ""
    # v0.3.x event-satisfaction signal: optional dwell on the
    # recommendation click-through. When present, these flow into the
    # persisted click event's metadata so storage classification can
    # tell meaningful_dwell vs quick_exit on recommended content.
    watch_seconds: float | None = None
    video_duration_seconds: float | None = None


class RecommendationClickResponse(BaseModel):
    """Response after ingesting a recommendation click-through."""

    ok: bool
    bvid: str
    layers_updated: list[str]


class ChatIn(BaseModel):
    """Popup chat request."""

    message: str


class ChatResponse(BaseModel):
    """Popup chat response."""

    reply: str


class ChatTurnIn(BaseModel):
    """Durable popup chat turn request.

    The popup uses this endpoint for lifecycle-safe chat.  The POST
    returns quickly with a pending turn; the backend completes it in the
    background and the popup polls by ``turn_id`` after reloads.
    """

    message: str
    turn_id: str = ""
    session: str = "popup"
    scope: str = "chat"
    subject_id: str = ""
    subject_title: str = ""


class ChatTurnOut(BaseModel):
    """One durable popup chat turn."""

    turn_id: str
    session: str = "popup"
    scope: str = "chat"
    subject_id: str = ""
    subject_title: str = ""
    message: str = ""
    reply: str = ""
    status: str = "pending"
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


class ChatTurnListResponse(BaseModel):
    """Durable popup chat history."""

    items: list[ChatTurnOut]


# --- Configuration API models ---


class LLMProviderConfigOut(BaseModel):
    """LLM provider configuration (keys masked by default)."""

    api_key: str = ""
    model: str = ""
    base_url: str = ""
    auth_mode: str = ""
    api_flavor: str = ""
    http_referer: str = ""
    x_title: str = ""
    reasoning_effort: str = ""


class EmbeddingConfigOut(BaseModel):
    provider: str = ""
    model: str = ""
    # v0.3.32+ embedding owns its own credentials; api_key is masked.
    api_key: str = ""
    base_url: str = ""
    output_dimensionality: int = 1024
    similarity_threshold: float = 0.82
    fallback_enabled: bool = False
    fallback_provider: str = ""
    # Optional cover image-only embedding (needs a multimodal embedding model
    # such as gemini-embedding-2 or dashscope qwen3-vl-embedding). Default off.
    multimodal_enabled: bool = False


class ModuleLLMConfigOut(BaseModel):
    provider: str = ""
    model: str = ""


class LLMConfigOut(BaseModel):
    # One-release compatibility projection. The dedicated /api/model-config
    # endpoint is authoritative and is the only model writer.
    authoritative: bool = False
    read_only: bool = True
    projection: str = "primary_and_first_fallback"
    default_provider: str = "deepseek"
    concurrency: int = 4
    timeout: int = 300
    # Non-empty fallback_provider = chat fallback on (the legacy
    # fallback_enabled bool was never consulted and is no longer echoed;
    # old clients still sending it are ignored on PUT).
    fallback_provider: str = ""
    openai: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    claude: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    gemini: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    deepseek: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    ollama: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    openrouter: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    # v0.3.32+ — generic OpenAI-protocol-compatible provider.
    openai_compatible: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    embedding: EmbeddingConfigOut = Field(default_factory=EmbeddingConfigOut)
    soul: ModuleLLMConfigOut = Field(default_factory=ModuleLLMConfigOut)
    discovery: ModuleLLMConfigOut = Field(default_factory=ModuleLLMConfigOut)
    recommendation: ModuleLLMConfigOut = Field(default_factory=ModuleLLMConfigOut)
    evaluation: ModuleLLMConfigOut = Field(default_factory=ModuleLLMConfigOut)


class BilibiliConfigOut(BaseModel):
    auth_method: str = "cookie"
    cookie: str = ""
    browser_executable: str = ""
    browser_headed: bool = False


class NetworkConfigOut(BaseModel):
    """Overseas routing policy. Any proxy URL userinfo is masked in responses."""

    mode: str = "direct"
    proxy: str = ""


class SourcesBrowserConfigOut(BaseModel):
    cdp_url: str = ""
    headed: bool = False


class BilibiliSourceConfigOut(BaseModel):
    enabled: bool = True


class XiaohongshuSourceConfigOut(BaseModel):
    enabled: bool = False
    daily_search_budget: int = 0
    daily_creator_budget: int = 0
    task_interval_seconds: int = 45


class DouyinSourceConfigOut(BaseModel):
    enabled: bool = False
    mode: str = "direct"
    # Resolved Cookie header (env override, else data/douyin_cookie.json).
    # Read-only mirror for the settings pages — masked unless reveal_keys.
    # PUT routes a non-empty value to DouyinCookieManager, never config.toml.
    cookie: str = ""
    cookie_env: str = "OPENBILICLAW_DOUYIN_COOKIE"
    daily_search_budget: int = 0
    daily_hot_budget: int = 0
    daily_feed_budget: int = 0
    request_interval_seconds: int = 2


class YoutubeSourceConfigOut(BaseModel):
    enabled: bool = False
    daily_search_budget: int = 0
    daily_trending_budget: int = 0
    daily_channel_budget: int = 0
    request_interval_seconds: int = 2
    min_interval_minutes: int = 60


class TwitterSourceConfigOut(BaseModel):
    enabled: bool = False
    mode: str = "cookie"
    # Resolved Cookie header (env override, else data/x_cookie.json).
    # Read-only mirror for the settings pages — masked unless reveal_keys.
    # PUT routes a non-empty value to XCookieManager, never config.toml.
    cookie: str = ""
    cookie_env: str = "OPENBILICLAW_X_COOKIE"
    daily_search_budget: int = 0
    daily_feed_budget: int = 0
    daily_creator_budget: int = 0
    request_interval_seconds: int = 3
    min_interval_minutes: int = 60


class ZhihuSourceConfigOut(BaseModel):
    enabled: bool = False
    source_modes: list[str] = Field(
        default_factory=lambda: ["search", "hot", "feed", "creator", "related"]
    )
    daily_search_budget: int = 0
    daily_hot_budget: int = 0
    daily_feed_budget: int = 0
    daily_creator_budget: int = 0
    daily_related_budget: int = 0
    request_interval_seconds: int = 3
    min_interval_minutes: int = 60


class RedditSourceConfigOut(BaseModel):
    enabled: bool = False
    backend: str = "rdt"
    source_modes: list[str] = Field(
        default_factory=lambda: ["search", "hot", "subreddit", "related"]
    )
    daily_search_budget: int = 300
    daily_hot_budget: int = 300
    daily_subreddit_budget: int = 300
    daily_related_budget: int = 300
    request_interval_seconds: int = 3
    min_interval_minutes: int = 60


class SourcesConfigOut(BaseModel):
    browser: SourcesBrowserConfigOut = Field(default_factory=SourcesBrowserConfigOut)
    bilibili: BilibiliSourceConfigOut = Field(default_factory=BilibiliSourceConfigOut)
    xiaohongshu: XiaohongshuSourceConfigOut = Field(default_factory=XiaohongshuSourceConfigOut)
    douyin: DouyinSourceConfigOut = Field(default_factory=DouyinSourceConfigOut)
    youtube: YoutubeSourceConfigOut = Field(default_factory=YoutubeSourceConfigOut)
    twitter: TwitterSourceConfigOut = Field(default_factory=TwitterSourceConfigOut)
    zhihu: ZhihuSourceConfigOut = Field(default_factory=ZhihuSourceConfigOut)
    reddit: RedditSourceConfigOut = Field(default_factory=RedditSourceConfigOut)


class SchedulerConfigOut(BaseModel):
    enabled: bool = True
    pause_on_extension_disconnect: bool = False
    extension_disconnect_grace_seconds: int = 90
    discovery_cron: str = "0 */8 * * *"
    pool_target_count: int = 300
    pool_source_shares: dict[str, int] = Field(default_factory=dict)
    account_sync_interval_hours: int = 6
    refresh_check_interval_seconds: int = 60
    signal_event_threshold: int = 6
    feedback_batch_threshold: int = 3
    trending_refresh_hours: int = 3
    explore_refresh_hours: int = 12
    discovery_limit: int = 30
    delight_queue_limit: int = 20
    proactive_push_interval_seconds: int = 120
    speculator_idle_interval_minutes: int = 30
    speculation_interval_minutes: int = 10
    speculation_ttl_days: int = 3
    speculation_cooldown_days: int = 7
    speculation_confirmation_threshold: int = 3
    speculation_max_active: int = 5
    speculation_max_primary_interests: int = 15
    speculation_max_secondary_interests: int = 60
    avoidance_speculation_interval_minutes: int = 10
    avoidance_speculation_ttl_days: int = 3
    avoidance_speculation_cooldown_days: int = 7
    avoidance_speculation_confirmation_threshold: int = 3
    avoidance_speculation_max_active: int = 5
    auto_update_enabled: bool = False
    auto_update_check_interval_hours: int = 6
    auto_update_allow_prerelease: bool = False
    auto_update_allowed_remotes: list[str] = Field(default_factory=list)


class DiscoveryConfigOut(BaseModel):
    unified_keyword_planner_enabled: bool = True
    kw_cache_high: int = 30
    kw_cache_low: int = 10
    gen_batch: int = 30
    fetch_batch: int = 5
    history_window_size: int = 150
    history_window_hours: int = 48
    claim_lease_minutes: int = 10
    planner_poll_seconds: int = 120
    plan_ttl_hours: int = 12
    admission_min_score: float = 0.60
    candidate_eval_concurrency: int = Field(default=3, ge=1, le=3)
    multimodal_evaluation_enabled: bool = False
    multimodal_batch_size: int = 8
    multimodal_image_max_px: int = 384
    multimodal_image_quality: int = 72
    multimodal_image_timeout_seconds: int = 6
    # Read-only UI/API-derived enum over the two canonical DiscoveryConfig
    # booleans (inspiration_search_enabled / inspiration_replace_merged_keywords).
    # Not a config.toml field — the two booleans stay the single source of truth.
    keyword_generation_mode: Literal["legacy", "hybrid", "inspiration"] = "legacy"


class BackendUpdateStatusOut(BaseModel):
    state: str = "unknown"
    auto_update_enabled: bool = False
    install_mode: str = ""
    current_version: str = ""
    latest_version: str = ""
    latest_tag: str = ""
    last_check_at: str = ""
    last_error: str = ""
    reason: str = "none"


class UpdateStatusResponse(BaseModel):
    backend: BackendUpdateStatusOut


class UpdateCheckIn(BaseModel):
    include_backend: bool = True


class UpdateApplyIn(BaseModel):
    target: Literal["backend"]
    tag: str = ""


class UpdateApplyResponse(BaseModel):
    target: str = "backend"
    state: str
    reason: str = "none"
    accepted: bool
    observe_via: str = "runtime-stream"


class StorageConfigOut(BaseModel):
    db_path: str = "data/openbiliclaw.db"


class LoggingConfigOut(BaseModel):
    level: str = "INFO"
    file_level: str = "DEBUG"
    directory: str = "logs"
    filename: str = "openbiliclaw.log"
    file_path: str = "logs/openbiliclaw.log"
    max_file_size_mb: int = 100
    backup_count: int = 1
    aggregate_budget_mb: int = 500
    unmanaged_truncate_mb: int = 200
    unmanaged_max_age_days: int = 30


class AutostartConfigOut(BaseModel):
    enabled: bool = False
    manage_ollama: bool = True


class SavedSyncConfigOut(BaseModel):
    auto_sync_enabled: bool = False


class SavedSyncConfigUpdateIn(BaseModel):
    auto_sync_enabled: StrictBool | None = None

    @field_validator("auto_sync_enabled", mode="before")
    @classmethod
    def reject_explicit_null_auto_sync(cls, value: object) -> object:
        if value is None:
            raise ValueError("saved_sync.auto_sync_enabled must be a boolean")
        return value


class AutostartStatusOut(BaseModel):
    supported: bool
    enabled: bool
    registered: bool
    can_manage: bool
    platform: str
    mechanism: str
    manage_ollama: bool
    ollama_required: bool
    reason: str = "none"
    detail: str = ""


class AutostartApplyIn(BaseModel):
    enabled: bool


class ConfigIssueOut(BaseModel):
    field: str
    message: str
    severity: str = "warning"


class ConfigResponse(BaseModel):
    """Full configuration response."""

    language: str = "zh"
    data_dir: str = "data"
    degraded: bool = False
    degraded_reason: str = ""
    llm: LLMConfigOut = Field(default_factory=LLMConfigOut)
    bilibili: BilibiliConfigOut = Field(default_factory=BilibiliConfigOut)
    network: NetworkConfigOut = Field(default_factory=NetworkConfigOut)
    sources: SourcesConfigOut = Field(default_factory=SourcesConfigOut)
    scheduler: SchedulerConfigOut = Field(default_factory=SchedulerConfigOut)
    discovery: DiscoveryConfigOut = Field(default_factory=DiscoveryConfigOut)
    autostart: AutostartConfigOut = Field(default_factory=AutostartConfigOut)
    saved_sync: SavedSyncConfigOut = Field(default_factory=SavedSyncConfigOut)
    storage: StorageConfigOut = Field(default_factory=StorageConfigOut)
    logging: LoggingConfigOut = Field(default_factory=LoggingConfigOut)
    issues: list[ConfigIssueOut] = Field(default_factory=list)


class ConfigUpdateIn(BaseModel):
    """Partial config update. Only provided fields are updated."""

    language: str | None = None
    data_dir: str | None = None
    reset_fields: list[str] | None = None
    suppress_background_llm_work: bool | None = None
    llm: dict[str, object] | None = None
    bilibili: dict[str, object] | None = None
    network: dict[str, object] | None = None
    sources: dict[str, object] | None = None
    scheduler: dict[str, object] | None = None
    discovery: dict[str, object] | None = None
    saved_sync: SavedSyncConfigUpdateIn | None = None
    storage: dict[str, object] | None = None
    logging: dict[str, object] | None = None

    @field_validator("saved_sync", mode="before")
    @classmethod
    def reject_explicit_null_saved_sync(cls, value: object) -> object:
        if value is None:
            raise ValueError("saved_sync must be an object")
        return value


class ConfigServiceProbeIn(BaseModel):
    """Legacy no-write request retained only for outbound-network policy."""

    kind: Literal["network_proxy"]
    config: dict[str, object] = Field(default_factory=dict)


class ConfigServiceProbeResponse(BaseModel):
    """Result of a legacy outbound-network connectivity probe."""

    ok: bool
    kind: Literal["network_proxy"]
    provider: str = ""
    model: str = ""
    message: str = ""
    error: str = ""
    latency_ms: int = 0


class SourceShareSuggestionIn(BaseModel):
    """Optional overrides from a settings form that has not been saved yet."""

    enabled_sources: dict[str, bool] | None = None
    configured_shares: dict[str, int] | None = None


class ConfigUpdateResponse(BaseModel):
    """Response after config save."""

    ok: bool = True
    config: ConfigResponse
    message: str = ""
    reloaded: bool = False
    rollback_applied: bool = False
    restart_required: bool = False
    warnings: list[str] = Field(default_factory=list)


class SourceShareSuggestionResponse(BaseModel):
    """Suggested source shares based on observed source event counts."""

    event_counts: dict[str, int] = Field(default_factory=dict)
    enabled_sources: dict[str, bool] = Field(default_factory=dict)
    suggested_shares: dict[str, int] = Field(default_factory=dict)
