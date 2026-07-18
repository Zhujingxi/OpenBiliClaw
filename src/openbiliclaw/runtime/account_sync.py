"""Periodic account-side sync for long-term Bilibili signals."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast

from openbiliclaw.bilibili.api import BilibiliAuthExpiredError
from openbiliclaw.llm.base import classify_llm_unavailability, safe_llm_failure_message

# Cross-source identity-key helpers live in the shared ``sources.identity_keys``
# module (promoted in event-capture-completion Phase 0 so retraction discounting
# can key on the same normalization). Aliased to the historic private names to
# keep this module's call sites unchanged.
from openbiliclaw.sources.identity_keys import dedup_key as _dedup_key
from openbiliclaw.sources.identity_keys import tweet_id_from_url as _tweet_id_from_url

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_ANALYSIS_TIMEOUT_SECONDS = 360.0
_PROFILE_ANALYSIS_TIMEOUT_MESSAGE = (
    "AI 偏好分析等待模型服务超过 6 分钟仍未返回结果，已自动停止，避免继续卡住。"
    "常见原因是 Base URL、模型名或代理配置错误，网络无法访问模型服务，"
    "或模型服务响应过慢。请到模型设置测试 AI 服务，修正后重试。"
)

# Cross-source dedup lookback. 48h > 2× the 6h sync interval (one full missed
# cycle) while comfortably shorter than typical organic re-watch gaps, so a
# genuine re-watch after the window still produces a fresh signal.
_CROSS_SOURCE_DEDUP_WINDOW_HOURS = 48

# X (Twitter) scheduled incremental sync bounds — mirror init's read limits.
_X_FETCH_LIMIT = 200
# Bound the per-account seen-tweet-id state so it can't grow without limit.
_X_ID_CAP = 2000
# First-sync seeding reads *all* persisted X events (init may be months old), so
# the lookback is effectively unwindowed rather than the 48h dedup window.
_X_SEED_WINDOW_HOURS = 24 * 365 * 10


class SupportsRecentEventUrls(Protocol):
    def recent_event_urls(
        self,
        event_types: list[str],
        *,
        within_hours: int,
        exclude_source: str | None = ...,
        limit: int = ...,
    ) -> set[str]: ...


class SupportsXClient(Protocol):
    async def likes(self, *, limit: int) -> list[dict[str, Any]]: ...
    async def bookmarks(self, *, limit: int) -> list[dict[str, Any]]: ...


class SupportsAccountSyncState(Protocol):
    def load_account_sync_state(self) -> dict[str, object]: ...
    def save_account_sync_state(self, state: dict[str, object]) -> None: ...
    async def propagate_event(self, event: dict[str, Any]) -> None: ...


class SupportsAccountClient(Protocol):
    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]: ...
    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
        max_total_items: int | None = None,
    ) -> list[Any]: ...
    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[Any]: ...


def _client_is_authenticated(client: Any) -> bool:
    """True when the client either has no auth concept or reports authed.

    Tests pass plain stubs that don't expose ``is_authenticated``; for
    those, we conservatively assume "authenticated" so behavior matches
    pre-v0.3.57. Production ``BilibiliAPIClient`` exposes the real flag,
    which is what gates the cookie-race short-circuit.
    """
    if not hasattr(client, "is_authenticated"):
        return True
    return bool(client.is_authenticated)


class SupportsSoulAnalyzer(Protocol):
    async def analyze_events(self, events: list[dict[str, Any]]) -> None: ...


@dataclass
class AccountSyncService:
    """Incrementally import account-side history, favorites, and following."""

    memory_manager: SupportsAccountSyncState
    bilibili_client: SupportsAccountClient
    soul_engine: SupportsSoulAnalyzer
    sync_interval_hours: int = 6
    history_max_items: int = 200
    # v0.3.x: favorites budget parity with ``openbiliclaw init``. Folders 11+
    # and follows 101+ used to silently never sync. ``max_total_items`` bounds
    # the worst case at ~1 + ceil(500/20) requests instead of 200×3.
    max_folders: int = 200
    max_items_per_folder: int = 50
    max_total_items: int = 500
    following_page_size: int = 100
    following_max_pages: int = 5
    check_interval_seconds: int = 300
    llm_work_allowed: Callable[[], bool] | None = None
    database: SupportsRecentEventUrls | None = None
    x_client: SupportsXClient | None = None
    profile_analysis_timeout_seconds: float = DEFAULT_PROFILE_ANALYSIS_TIMEOUT_SECONDS
    _auto_bootstrap_attempted: bool = False
    # v0.3.57+: tracks the cookie-not-ready → ready transition so
    # ``sync_if_due`` only emits the "auth ready" INFO log once per
    # session. Reset path is via fresh AccountSyncService instance,
    # which is what ``rebuild_from_config`` already produces.
    _last_seen_authenticated: bool = False

    async def sync_if_due(self) -> dict[str, object]:
        """Run one account sync only when the configured interval has elapsed."""
        # v0.3.57+: skip the throttle check entirely while the cookie
        # hasn't arrived. ``sync_now`` will short-circuit too — checking
        # here just keeps the no-auth signal visible in run_forever logs
        # without "not_due" noise on every tick of the 5-min poll loop.
        authed = _client_is_authenticated(self.bilibili_client)
        if not authed:
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "no_auth",
            }
        # Keep the first authenticated tick untouched while guided init owns
        # the bootstrap path (or before the first complete profile exists).
        # Besides avoiding the fetch, this must run before the transition log
        # and flag update so a paused tick cannot falsely announce that the
        # first history fetch is about to start.
        if self.llm_work_allowed is not None and not self.llm_work_allowed():
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "llm_paused",
            }
        if not self._last_seen_authenticated:
            self._last_seen_authenticated = True
            logger.info(
                "account_sync: bilibili cookie now ready — first history "
                "fetch will run on this tick"
            )
        state = self.memory_manager.load_account_sync_state()
        if not self._is_due(str(state.get("last_account_sync_at", ""))):
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "not_due",
            }
        return await self.sync_now()

    async def sync_now(self) -> dict[str, object]:
        """Run one immediate incremental account sync."""
        # v0.3.57+: cookie race short-circuit. Daemon often starts before
        # the extension cookie sync arrives; without this gate, the first
        # tick fetches with empty cookies, gets 0 items, stamps
        # last_account_sync_at, and locks the next attempt out for
        # ``sync_interval_hours`` (default 6h). Bail out before touching
        # the network OR the timestamp so the next ``sync_if_due`` tick
        # (5 min) still re-tries.
        if not _client_is_authenticated(self.bilibili_client):
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "no_auth",
            }
        state = self.memory_manager.load_account_sync_state()
        events: list[dict[str, Any]] = []
        errors: list[str] = []
        error_kind = ""

        try:
            history = await self.bilibili_client.get_user_history(max_items=self.history_max_items)
            previous_history_view_at = self._to_int(state.get("last_history_view_at", 0))
            previous_history_bvids = self._string_set(
                state.get("history_bvids_at_last_view_at", [])
            )
            new_history, last_view_at, last_bvid = self._filter_new_history(
                history,
                last_view_at=previous_history_view_at,
                last_bvid=str(state.get("last_history_bvid", "")),
                seen_bvids_at_last_view_at=previous_history_bvids,
            )
            events.extend(self._history_events(new_history))
            state["last_history_view_at"] = last_view_at
            state["last_history_bvid"] = last_bvid
            state["history_bvids_at_last_view_at"] = self._history_cursor_bvids(
                history,
                last_view_at,
                fallback_bvid=last_bvid,
                previous_seen=(
                    previous_history_bvids if last_view_at == previous_history_view_at else set()
                ),
            )
        except Exception as exc:
            errors.append(str(exc))
            error_kind = self._record_stage_error("history", exc, error_kind)

        try:
            favorites = await self.bilibili_client.get_all_favorites(
                max_folders=self.max_folders,
                max_items_per_folder=self.max_items_per_folder,
                max_total_items=self.max_total_items,
            )
            current_signature = self._favorite_signature(favorites)
            previous_signature = str(state.get("favorite_signature", ""))
            previous_bvids = self._favorite_bvids_from_state(state)
            if current_signature and current_signature != previous_signature:
                new_favorites = self._filter_favorite_folders(favorites, previous_bvids)
                events.extend(self._favorite_events(new_favorites))
                state["favorite_signature"] = current_signature
                state["favorite_bvids"] = self._favorite_bvids(favorites)
                state["last_favorites_sync_at"] = self._now().isoformat()
            elif current_signature and not state.get("favorite_bvids"):
                state["favorite_bvids"] = self._favorite_bvids(favorites)
        except Exception as exc:
            errors.append(str(exc))
            error_kind = self._record_stage_error("favorites", exc, error_kind)

        # Following is paginated (page_size=100, hard cap following_max_pages)
        # so follows past position 100 finally sync. On a mid-loop page failure
        # the pages fetched so far are still ingested; the ``following_mids``
        # set-diff makes the next sync re-cover anything missed.
        following, following_error = await self._collect_following()
        current_signature = self._following_signature(following)
        previous_signature = str(state.get("following_signature", ""))
        previous_mids = self._following_mids_from_state(state)
        if current_signature and current_signature != previous_signature:
            new_following = self._filter_following(following, previous_mids)
            events.extend(self._following_events(new_following))
            state["following_signature"] = current_signature
            state["following_mids"] = self._following_mids(following)
            state["last_following_sync_at"] = self._now().isoformat()
        elif current_signature and not state.get("following_mids"):
            state["following_mids"] = self._following_mids(following)
        if following_error is not None:
            errors.append(str(following_error))
            error_kind = self._record_stage_error("following", following_error, error_kind)

        if self.x_client is not None:
            error_kind = await self._sync_x(state, events, errors, error_kind)

        if events:
            events = self._dedup_cross_source(events)

        if events:
            for event in events:
                await self.memory_manager.propagate_event(event)
            try:
                timeout = (
                    self.profile_analysis_timeout_seconds
                    if self.profile_analysis_timeout_seconds > 0
                    else None
                )
                async with asyncio.timeout(timeout):
                    await self._apply_profile_update(events)
            except TimeoutError as exc:
                timeout_error = TimeoutError(_PROFILE_ANALYSIS_TIMEOUT_MESSAGE)
                logger.warning(
                    "Profile analysis timed out during account sync after %.1fs",
                    self.profile_analysis_timeout_seconds,
                )
                self._persist_profile_analysis_error(str(timeout_error))
                raise timeout_error from exc
            except Exception as exc:
                # A profile-analysis failure here is almost always the chat
                # LLM being unavailable (a local model never pulled → 404, a
                # gateway rejecting auth → 401, or a timeout). Historically
                # this was a bare await: the exception bubbled straight up,
                # run_forever logged one backend line, but the user-visible
                # last_sync_error was never written — so guided init just
                # showed an endless wait with nothing to diagnose (CLAUDE.md
                # pitfall #7: failures must be diagnosable). Record the reason
                # in last_sync_error WITHOUT advancing any sync cursor (the
                # whole tick still rolls back and retries next cycle), then
                # re-raise so run_forever still classifies/logs it. CancelledError
                # subclasses BaseException, so hot-reload/restart cancellation
                # is not swallowed here.
                logger.warning(
                    "Profile analysis failed during account sync: %s", exc, exc_info=True
                )
                self._persist_profile_analysis_error(safe_llm_failure_message(exc))
                raise

        state["last_account_sync_at"] = self._now().isoformat()
        state["last_sync_error"] = self._merge_stage_errors(errors)
        state["last_sync_error_kind"] = error_kind
        self.memory_manager.save_account_sync_state(state)
        return {
            "synced": bool(events),
            "new_event_count": len(events),
            "errors": errors,
        }

    @staticmethod
    def _merge_stage_errors(errors: list[str]) -> str:
        """Join stage errors, dropping repeats of the same message.

        One expired cookie fails several stages: the history stage hits
        /x/web-interface/history/cursor, while favorites and following each
        call get_nav_info() for the mid. All three raised the same -101, so
        the joined string repeated one cause three times.
        """
        merged: list[str] = []
        for error in errors:
            message = error.strip()
            if message and message not in merged:
                merged.append(message)
        return " | ".join(merged)

    @staticmethod
    def _record_stage_error(stage: str, exc: Exception, current_kind: str) -> str:
        """Log a swallowed fetch-stage error and classify it.

        ``auth_expired`` (expired/logged-out cookie) always wins over a generic
        ``error`` so the UI can surface a "re-login needed" state even when
        another stage also failed for an unrelated reason.
        """
        logger.warning("account_sync: %s fetch failed: %s", stage, exc)
        if isinstance(exc, BilibiliAuthExpiredError):
            return "auth_expired"
        if current_kind == "auth_expired":
            return current_kind
        return "error"

    async def _apply_profile_update(self, events: list[dict[str, Any]]) -> None:
        """Route pulled events through the same profile machinery as live events.

        Fallback matrix (default degrades to the legacy path, never to nothing):

        * profile ready + pipeline present + ``ingest_batch`` succeeds → pipeline
          only (no ``analyze_events``, no bootstrap);
        * ready but pipeline missing / ``ingest_batch`` absent / raises → WARN
          and fall back to ``analyze_events`` (no bootstrap — a profile exists);
        * not ready → legacy ``analyze_events`` + ``_auto_bootstrap_soul_profile``;
        * ``is_profile_ready`` missing or raising → treated as not ready
          (conservative legacy path).

        ``propagate_event`` persistence has already run unconditionally in the
        caller, so events are never lost to a profile-path failure here.
        """
        # Single readiness probe per sync serves both routing and the
        # bootstrap gate, so the pre-profile bootstrap fires at most once.
        readiness = self._profile_readiness()

        if readiness is True:
            if await self._try_pipeline_ingest(events):
                return
            logger.warning(
                "account_sync: profile pipeline unavailable/failed; falling back to analyze_events"
            )
            await self.soul_engine.analyze_events(events)
            return

        # Not ready (or readiness unknown) → legacy analyze_events. Bootstrap
        # only when readiness is *definitively* False (a profile can still be
        # built); unknown readiness stays conservative and skips the bootstrap.
        await self.soul_engine.analyze_events(events)
        if readiness is False:
            await self._auto_bootstrap_soul_profile(len(events))

    def _profile_readiness(self) -> bool | None:
        """Return profile readiness, or ``None`` when it cannot be determined.

        ``None`` (``is_profile_ready`` missing or raising) is treated as *not
        ready* for routing but skips the first-profile bootstrap, preserving
        the original conservative behavior.
        """
        is_ready = getattr(self.soul_engine, "is_profile_ready", None)
        if not callable(is_ready):
            return None
        try:
            return bool(is_ready())
        except Exception:
            logger.debug("account_sync: is_profile_ready check failed", exc_info=True)
            return None

    async def _try_pipeline_ingest(self, events: list[dict[str, Any]]) -> bool:
        """Feed events into ``soul_engine.pipeline.ingest_batch``.

        Returns ``True`` only when the batch was ingested. Any missing handle
        or raised exception returns ``False`` so the caller can fall back.
        """
        pipeline = getattr(self.soul_engine, "pipeline", None)
        if pipeline is None:
            return False
        ingest_batch = getattr(pipeline, "ingest_batch", None)
        if not callable(ingest_batch):
            return False
        try:
            from openbiliclaw.soul.pipeline import signals_from_events

            signals = signals_from_events(events)
            await ingest_batch(signals)
            return True
        except Exception:
            logger.warning("account_sync: profile pipeline ingest failed", exc_info=True)
            return False

    def _dedup_cross_source(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop events whose identity key already appears in the events table.

        Guards against double-counting an observation that the browser
        extension already recorded (extension ``view`` + history ``view`` for
        the same bvid, etc.). Watermark/cursor state is advanced by the caller
        before this runs, so a deduped item never stalls the cursor. Own prior
        rows are excluded via ``exclude_source="account_sync"`` so a re-watch
        seen only via the pulled API still flows.
        """
        if self.database is None:
            return events

        recent_keys_by_type: dict[str, set[str]] = {}
        for event_type in {str(event.get("event_type", "")) for event in events}:
            if not event_type:
                continue
            try:
                recent_urls = self.database.recent_event_urls(
                    [event_type],
                    within_hours=_CROSS_SOURCE_DEDUP_WINDOW_HOURS,
                    exclude_source="account_sync",
                )
            except Exception as exc:
                logger.warning(
                    "account_sync: cross-source dedup lookup for %s failed: %s",
                    event_type,
                    exc,
                )
                recent_urls = set()
            recent_keys_by_type[event_type] = {
                key for url in recent_urls if (key := _dedup_key(url))
            }

        kept: list[dict[str, Any]] = []
        dropped_counts: dict[str, int] = {}
        for event in events:
            event_type = str(event.get("event_type", ""))
            key = _dedup_key(str(event.get("url", "")))
            if key and key in recent_keys_by_type.get(event_type, set()):
                dropped_counts[event_type] = dropped_counts.get(event_type, 0) + 1
                continue
            kept.append(event)

        if dropped_counts:
            summary = " / ".join(
                f"{count} {event_type}" for event_type, count in sorted(dropped_counts.items())
            )
            logger.info("account_sync: deduped %s events already observed by extension", summary)
        return kept

    async def _sync_x(
        self,
        state: dict[str, Any],
        events: list[dict[str, Any]],
        errors: list[str],
        error_kind: str,
    ) -> str:
        """Fetch X likes/bookmarks server-side and append incremental events.

        Read-only, one fetch pair per cycle, set-based dedup on normalized tweet
        ids. First sync (empty state sets) seeds from tweet ids already persisted
        in the events table so a like made between init and the first cycle is
        NOT silently swallowed. Failures feed the shared error list + WARN log
        (Task 4) and never block the bilibili sync.
        """
        if self.x_client is None:
            return error_kind

        like_ids = self._string_list(state.get("x_like_ids", []))
        bookmark_ids = self._string_list(state.get("x_bookmark_ids", []))
        if not like_ids and not bookmark_ids:
            seed = self._seed_x_ids_from_events()
            like_ids = list(seed)
            bookmark_ids = list(seed)

        try:
            likes = await self.x_client.likes(limit=_X_FETCH_LIMIT)
            like_events, like_ids = self._x_ingest(likes, event_type="like", seen_ids=like_ids)
            events.extend(like_events)
            state["x_like_ids"] = like_ids
        except Exception as exc:
            errors.append(str(exc))
            error_kind = self._record_stage_error("x likes", exc, error_kind)

        try:
            bookmarks = await self.x_client.bookmarks(limit=_X_FETCH_LIMIT)
            bookmark_events, bookmark_ids = self._x_ingest(
                bookmarks, event_type="favorite", seen_ids=bookmark_ids
            )
            events.extend(bookmark_events)
            state["x_bookmark_ids"] = bookmark_ids
        except Exception as exc:
            errors.append(str(exc))
            error_kind = self._record_stage_error("x bookmarks", exc, error_kind)

        return error_kind

    def _seed_x_ids_from_events(self) -> list[str]:
        """Seed the seen-tweet-id set from persisted X ``like``/``favorite`` events."""
        if self.database is None:
            return []
        try:
            urls = self.database.recent_event_urls(
                ["like", "favorite"],
                within_hours=_X_SEED_WINDOW_HOURS,
                exclude_source=None,
                limit=_X_ID_CAP,
            )
        except Exception as exc:
            logger.warning("account_sync: X first-sync seed lookup failed: %s", exc)
            return []
        seeded: list[str] = []
        for url in urls:
            if "x.com" not in url and "twitter.com" not in url:
                continue
            tweet_id = _tweet_id_from_url(url)
            if tweet_id:
                seeded.append(tweet_id)
        return seeded

    def _x_ingest(
        self,
        tweets: list[dict[str, Any]],
        *,
        event_type: str,
        seen_ids: list[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Build events for tweets whose id is unseen; return (events, capped ids)."""
        seen = set(seen_ids)
        emitted: list[dict[str, Any]] = []
        fetched_ids: list[str] = []
        for tweet in tweets:
            if not isinstance(tweet, dict):
                continue
            tweet_id = str(tweet.get("id", "") or "").strip()
            if not tweet_id:
                continue
            fetched_ids.append(tweet_id)
            if tweet_id in seen:
                continue
            event = self._x_event(tweet, event_type=event_type)
            if event is not None:
                emitted.append(event)
        return emitted, self._cap_x_ids(fetched_ids, seen_ids)

    @staticmethod
    def _cap_x_ids(fetched_newest_first: list[str], existing: list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for tweet_id in [*fetched_newest_first, *existing]:
            if tweet_id and tweet_id not in seen:
                seen.add(tweet_id)
                ordered.append(tweet_id)
        return ordered[:_X_ID_CAP]

    @staticmethod
    def _x_event(tweet: dict[str, Any], *, event_type: str) -> dict[str, Any] | None:
        """Normalize a ``tweet_to_dict`` payload into a unified account_sync event.

        Mirrors ``cli._x_tweet_to_event`` (init backfill) so scheduled and init
        X events share one shape. ``source_platform`` is the canonical
        ``"twitter"`` — never ``"x"``, which would split source-mix accounting.
        """
        from openbiliclaw.sources.event_format import SOURCE_TWITTER, build_event

        tweet_id = str(tweet.get("id", "") or "").strip()
        if not tweet_id:
            return None
        raw_author = tweet.get("author")
        author = raw_author if isinstance(raw_author, dict) else {}
        screen_name = str(author.get("screenName", "") or "").strip()
        if screen_name:
            author_name = f"@{screen_name}"
        else:
            author_name = str(author.get("name", "") or "").strip()
        handle = screen_name or "i"  # x.com/i/status/<id> resolves without a handle
        text = str(tweet.get("articleText") or tweet.get("text") or "").strip()
        first_line = text.splitlines()[0] if text else ""
        title = first_line[:120]
        return build_event(
            event_type=event_type,
            source_platform=SOURCE_TWITTER,
            title=title,
            url=f"https://x.com/{handle}/status/{tweet_id}",
            author=author_name,
            metadata={
                "tweet_id": tweet_id,
                "screen_name": screen_name,
                "body_text": text,
                "source": "account_sync",
            },
        )

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for raw in value if (item := str(raw).strip())]

    def _persist_profile_analysis_error(self, message: str) -> None:
        """Record a profile-analysis failure without advancing any sync cursor.

        Loads a FRESH state so the failing tick's in-flight cursor bumps
        (history / favorites / following) are not persisted — the tick still
        rolls back and retries next cycle — while ``last_sync_error`` carries a
        user-visible reason to ``/api/init-status`` and the account-sync status
        surface. ``last_account_sync_at`` is deliberately left untouched so the
        throttle does not lock the retry out for ``sync_interval_hours``.
        """
        try:
            state = self.memory_manager.load_account_sync_state()
            state["last_sync_error"] = f"画像分析失败：{message}"
            self.memory_manager.save_account_sync_state(state)
        except Exception:
            logger.debug("Failed to persist profile-analysis error", exc_info=True)

    def get_runtime_status(self) -> dict[str, object]:
        """Expose lightweight account sync runtime fields."""
        state = self.memory_manager.load_account_sync_state()
        kind = str(state.get("last_sync_error_kind", ""))
        detail = str(state.get("last_sync_error", ""))
        return {
            "last_account_sync_at": str(state.get("last_account_sync_at", "")),
            # Raw provider text — diagnostics only, never the display string.
            "last_account_sync_error": detail,
            "last_account_sync_error_kind": kind,
            # Display copy lives here so all four surfaces render the same
            # sentence instead of each formatting the raw error themselves.
            "last_account_sync_message": self._user_facing_sync_message(kind, detail),
            "last_account_sync_severity": "warning"
            if kind == "auth_expired"
            else ("error" if detail else ""),
        }

    @staticmethod
    def _user_facing_sync_message(kind: str, detail: str) -> str:
        """Render the user-visible sentence for a sync error.

        An expired cookie is a normal lifecycle event, not a fault, so it gets
        an actionable instruction rather than the provider's English error.
        """
        if kind == "auth_expired":
            return (
                "B 站登录已失效，账号同步已停止。"
                "请在浏览器重新登录 B 站，或保持扩展在线以同步新的 Cookie。"
            )
        if detail:
            return "账号同步出错，稍后会自动重试。"
        return ""

    async def _auto_bootstrap_soul_profile(self, event_count: int) -> None:
        """Build the first soul profile after account sync learns preferences.

        The caller (:meth:`_apply_profile_update`) only reaches this when the
        profile is *definitively* not ready, so readiness is no longer
        re-probed here — that keeps ``is_profile_ready`` to one call per sync.
        """
        if self._auto_bootstrap_attempted:
            return

        build_candidate = getattr(self.soul_engine, "build_initial_profile", None)
        if not callable(build_candidate):
            self._auto_bootstrap_attempted = True
            return
        build_fn = cast("Callable[[list[dict[str, Any]]], Awaitable[Any]]", build_candidate)

        self._auto_bootstrap_attempted = True
        try:
            logger.info(
                "Auto-bootstrapping soul profile after account sync (%d new events)",
                event_count,
            )
            await build_fn([])
        except Exception:
            logger.warning(
                "Auto-bootstrap of soul profile failed; run 'openbiliclaw init' "
                "manually for a richer profile",
                exc_info=True,
            )

    # v0.3.57+: tighter retry while cookie hasn't arrived. The default
    # ``check_interval_seconds`` of 300 is right for steady-state polling
    # but stretches the cookie-race symptom — daemon up, cookie arrives
    # ~2s later, but next history fetch waits up to 5 min. Drop to 15s
    # until first auth, restore to ``check_interval_seconds`` after.
    _UNAUTH_RETRY_INTERVAL_SECONDS: ClassVar[int] = 15

    async def run_forever(self) -> None:
        """Run account sync loop until cancelled."""
        while True:
            authed_before = self._last_seen_authenticated
            try:
                await self.sync_if_due()
            except Exception as exc:
                kind = classify_llm_unavailability(exc)
                if kind == "no_provider":
                    logger.info(
                        "account sync skipped: no chat LLM provider configured yet "
                        "(retry next cycle)"
                    )
                elif kind == "model_not_found":
                    logger.warning(
                        "account sync deferred: configured chat model not found "
                        "(pull the local model or fix the model name); retry next cycle"
                    )
                elif kind == "rate_limited":
                    logger.warning(
                        "account sync deferred: LLM provider rate-limited/cooling "
                        "down (retry next cycle)"
                    )
                else:
                    logger.exception("Unexpected error in account sync loop")
            interval = (
                self.check_interval_seconds
                if self._last_seen_authenticated or authed_before
                else self._UNAUTH_RETRY_INTERVAL_SECONDS
            )
            await asyncio.sleep(interval)

    def _filter_new_history(
        self,
        items: list[dict[str, Any]],
        *,
        last_view_at: int,
        last_bvid: str,
        seen_bvids_at_last_view_at: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], int, str]:
        newest_view_at = last_view_at
        newest_bvid = last_bvid
        seen_at_cursor = set(seen_bvids_at_last_view_at or set())
        if last_bvid:
            seen_at_cursor.add(last_bvid)
        accepted: list[dict[str, Any]] = []
        for item in items:
            history_meta = item.get("history", {})
            if not isinstance(history_meta, dict):
                history_meta = {}
            view_at = self._to_int(history_meta.get("view_at", item.get("view_at", 0)))
            bvid = str(history_meta.get("bvid", "")).strip()
            if view_at < last_view_at:
                continue
            if view_at == last_view_at and bvid and bvid in seen_at_cursor:
                continue
            accepted.append(item)
            if view_at > newest_view_at:
                newest_view_at = view_at
                newest_bvid = bvid
            elif view_at == newest_view_at and bvid:
                newest_bvid = bvid
        return accepted, newest_view_at, newest_bvid

    def _history_events(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for item in items:
            history_meta = item.get("history", {})
            if not isinstance(history_meta, dict):
                history_meta = {}
            bvid = str(history_meta.get("bvid", "")).strip()
            events.append(
                {
                    "event_type": "view",
                    "title": str(item.get("title", "")).strip(),
                    "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                    "metadata": {
                        "bvid": bvid,
                        "author": str(item.get("author", "")).strip(),
                        "view_at": self._to_int(
                            history_meta.get("view_at", item.get("view_at", 0))
                        ),
                        "source": "account_sync",
                        "signal_strength": 0.35,
                    },
                }
            )
        return events

    def _history_cursor_bvids(
        self,
        items: list[dict[str, Any]],
        view_at: int,
        *,
        fallback_bvid: str = "",
        previous_seen: set[str] | None = None,
    ) -> list[str]:
        bvids: set[str] = set()
        if view_at > 0:
            for item in items:
                history_meta = item.get("history", {})
                if not isinstance(history_meta, dict):
                    history_meta = {}
                item_view_at = self._to_int(history_meta.get("view_at", item.get("view_at", 0)))
                if item_view_at != view_at:
                    continue
                bvid = str(history_meta.get("bvid", "")).strip()
                if bvid:
                    bvids.add(bvid)
        if previous_seen:
            bvids.update(str(item).strip() for item in previous_seen if str(item).strip())
        if fallback_bvid:
            bvids.add(fallback_bvid)
        return sorted(bvids)

    def _favorite_signature(self, folders: list[Any]) -> str:
        parts: list[str] = []
        for folder in folders:
            folder_id = str(getattr(getattr(folder, "folder", None), "media_id", ""))
            item_ids = sorted(
                str(item.get("bvid", "")).strip()
                for item in getattr(folder, "items", [])
                if isinstance(item, dict) and str(item.get("bvid", "")).strip()
            )
            if folder_id and item_ids:
                parts.append(f"{folder_id}:{','.join(item_ids)}")
        return "|".join(sorted(parts))

    def _favorite_bvids(self, folders: list[Any]) -> list[str]:
        bvids = {
            str(item.get("bvid", "")).strip()
            for folder in folders
            for item in getattr(folder, "items", [])
            if isinstance(item, dict) and str(item.get("bvid", "")).strip()
        }
        return sorted(bvids)

    def _favorite_bvids_from_state(self, state: dict[str, object]) -> set[str]:
        stored = self._string_set(state.get("favorite_bvids", []))
        if stored:
            return stored
        return self._bvids_from_signature(str(state.get("favorite_signature", "")))

    def _filter_favorite_folders(self, folders: list[Any], seen_bvids: set[str]) -> list[Any]:
        if not seen_bvids:
            return folders
        filtered: list[Any] = []
        for folder in folders:
            items = [
                item
                for item in getattr(folder, "items", [])
                if isinstance(item, dict)
                and str(item.get("bvid", "")).strip()
                and str(item.get("bvid", "")).strip() not in seen_bvids
            ]
            if not items:
                continue
            filtered.append(
                SimpleNamespace(
                    folder=getattr(folder, "folder", None),
                    items=items,
                    truncated=bool(getattr(folder, "truncated", False)),
                )
            )
        return filtered

    def _favorite_events(self, folders: list[Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for folder in folders:
            folder_obj = getattr(folder, "folder", None)
            folder_title = str(getattr(folder_obj, "title", "")).strip()
            folder_id = int(getattr(folder_obj, "media_id", 0) or 0)
            for item in getattr(folder, "items", []):
                if not isinstance(item, dict):
                    continue
                bvid = str(item.get("bvid", "")).strip()
                upper = item.get("upper", {})
                if not isinstance(upper, dict):
                    upper = {}
                events.append(
                    {
                        "event_type": "favorite",
                        "title": str(item.get("title", "")).strip(),
                        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                        "metadata": {
                            "bvid": bvid,
                            "folder_id": folder_id,
                            "folder_title": folder_title,
                            "up_name": str(upper.get("name", "")).strip(),
                            "source": "account_sync",
                            "signal_strength": 1.0,
                        },
                    }
                )
        return events

    async def _collect_following(self) -> tuple[list[Any], Exception | None]:
        """Fetch following across pages (cli.py:5477-5494 pattern).

        Stops on a short page or once ``following_max_pages`` is reached. On a
        page fetch raising, returns the users collected so far plus the
        exception so the caller can ingest the partial import *and* record the
        error (auth-expired precedence preserved upstream).
        """
        collected: list[Any] = []
        page = 1
        while page <= self.following_max_pages:
            try:
                page_users = await self.bilibili_client.get_following(
                    page=page,
                    page_size=self.following_page_size,
                )
            except Exception as exc:
                return collected, exc
            if not page_users:
                break
            collected.extend(page_users)
            if len(page_users) < self.following_page_size:
                break
            page += 1
        return collected, None

    def _following_signature(self, following: list[Any]) -> str:
        return ",".join(self._following_mids(following))

    def _following_mids(self, following: list[Any]) -> list[str]:
        mids = {
            str(getattr(user, "mid", "")).strip()
            for user in following
            if str(getattr(user, "mid", "")).strip()
        }
        return sorted(mids)

    def _following_mids_from_state(self, state: dict[str, object]) -> set[str]:
        stored = self._string_set(state.get("following_mids", []))
        if stored:
            return stored
        return {
            item.strip()
            for item in str(state.get("following_signature", "")).split(",")
            if item.strip()
        }

    def _filter_following(self, following: list[Any], seen_mids: set[str]) -> list[Any]:
        if not seen_mids:
            return following
        return [
            user
            for user in following
            if str(getattr(user, "mid", "")).strip()
            and str(getattr(user, "mid", "")).strip() not in seen_mids
        ]

    def _following_events(self, following: list[Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for user in following:
            mid = int(getattr(user, "mid", 0) or 0)
            uname = str(getattr(user, "uname", "")).strip()
            events.append(
                {
                    "event_type": "follow",
                    "title": uname,
                    "url": f"https://space.bilibili.com/{mid}" if mid else "",
                    "metadata": {
                        "up_mid": mid,
                        "up_name": uname,
                        "sign": str(getattr(user, "sign", "")).strip(),
                        "source": "account_sync",
                        "signal_strength": 0.6,
                    },
                }
            )
        return events

    def _is_due(self, last_sync_at: str) -> bool:
        parsed = self._parse_iso_datetime(last_sync_at)
        if parsed is None:
            return True
        return self._now() - parsed >= timedelta(hours=self.sync_interval_hours)

    def _parse_iso_datetime(self, value: str) -> datetime | None:
        if not value:
            return None
        with_timezone = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(with_timezone)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _now(self) -> datetime:
        return datetime.now(tz=UTC)

    @staticmethod
    def _to_int(value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _string_set(value: object) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {str(item).strip() for item in value if str(item).strip()}

    @staticmethod
    def _bvids_from_signature(signature: str) -> set[str]:
        bvids: set[str] = set()
        for folder_part in signature.split("|"):
            _, sep, item_part = folder_part.partition(":")
            if not sep:
                continue
            bvids.update(item.strip() for item in item_part.split(",") if item.strip())
        return bvids
