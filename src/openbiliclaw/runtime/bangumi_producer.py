"""Runtime producer for Bangumi's official anonymous API."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.runtime.keyword_fetch import PLATFORM_BANGUMI
from openbiliclaw.runtime.pool_gate import candidate_pool_full_for_source
from openbiliclaw.sources.bangumi import bangumi_subject_to_content
from openbiliclaw.sources.bangumi_client import BangumiAPIError

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent

logger = logging.getLogger(__name__)

BANGUMI_SOURCE_MODES = ("search", "ranked", "latest")
BANGUMI_SOURCE_STRATEGIES = {
    "search": "bangumi-search",
    "ranked": "bangumi-ranked",
    "latest": "bangumi-latest",
}
# Persisted marker key for "the configured personal token was rejected".
_TOKEN_REJECTED_STATE_KEY = "token_rejected"
_BANGUMI_TOKEN_REJECTED_DETAIL = (
    "个人令牌已被拒绝（可能过期），已降级为匿名公开发现；"
    "请到 https://next.bgm.tv/demo/access-token 重新生成。"
)
_BANGUMI_DISABLED_DETAIL = "Bangumi 来源未启用。"
# Deliberately free of the words "未启用": both renderers already prefix this
# detail with their own state label ("来源未启用"), and the popup adds another
# "(未启用)" on top, so repeating it here printed the same phrase three times in
# one line. The detail carries only what the label cannot — which credential is
# stored, that it is idle, and the one step left.
_BANGUMI_DISABLED_CREDENTIAL_DETAIL = (
    "已保存{credential}，但它现在不会被使用；把 Bangumi 来源开关切到「启用」并保存后才会生效。"
)


class _PartialSearchError(Exception):
    """Preserve candidates produced before a later Bangumi request failed."""

    def __init__(self, error: BangumiAPIError, items: list[DiscoveredContent]) -> None:
        super().__init__(str(error))
        self.error = error
        self.items = items


@dataclass
class BangumiDiscoveryProducer:
    """Feed Bangumi subjects into the shared raw candidate pool."""

    database: Any
    soul_engine: Any
    client: Any
    enabled: bool = False
    # Presence flag only (never the token value): when the injected client
    # carries a personal access token, a 401 degrades it to anonymous discovery.
    access_token: str = ""
    subject_types: tuple[str, ...] = ("anime", "book", "game")
    source_modes: tuple[str, ...] = BANGUMI_SOURCE_MODES
    daily_search_budget: int = 300
    daily_ranked_budget: int = 100
    daily_latest_budget: int = 100
    min_interval_minutes: int = 5
    candidate_pipeline: Any | None = None
    candidate_evaluation_owned_by_coordinator: bool = False
    keyword_fetch: Any | None = None
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(
        self,
        *,
        limit: int | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        """Run enabled branches while respecting cooldown, interval and budgets."""

        if not self.enabled:
            return self._skip("disabled")
        self._ensure_tables()
        self._reconcile_token_rejection()
        cooldown_until = _read_cooldown_until(self.database)
        if cooldown_until is not None and cooldown_until > datetime.now(UTC):
            return self._skip("rate_limited")
        if not force and not self._is_due():
            return self._skip("throttled")
        if self._candidate_pool_full():
            return self._skip("pool_full")
        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("bangumi producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or 10))
        modes = tuple(mode for mode in BANGUMI_SOURCE_MODES if mode in self.source_modes)
        if not modes:
            return self._skip("mode_disabled")
        allocations = _allocate(requested_limit, modes)
        all_items: list[DiscoveredContent] = []
        mode_results: dict[str, str] = {}
        successful_runs: dict[str, tuple[int, str, str]] = {}
        errors: list[str] = []
        for mode in modes:
            branch_limit = min(
                allocations[mode], self.remaining_budget(mode, per_run_budget=allocations[mode])
            )
            if branch_limit <= 0:
                mode_results[mode] = "budget_exhausted"
                continue
            try:
                if mode == "search":
                    items = await self._run_search(profile, branch_limit)
                else:
                    items = await self._run_browse(mode, branch_limit)
            except _PartialSearchError as partial:
                partial_error = partial.error
                unique_items = _dedupe_items(partial.items)
                all_items.extend(unique_items)
                mode_results[mode] = partial_error.code
                errors.append(partial_error.code)
                successful_runs[mode] = (len(unique_items), "partial", partial_error.code)
                if partial_error.code == "rate_limited":
                    self._set_cooldown(partial_error.retry_after_seconds or 300)
                    break
                if partial_error.code == "unauthorized":
                    self._degrade_access_token()
                continue
            except BangumiAPIError as exc:
                mode_results[mode] = exc.code
                errors.append(exc.code)
                if exc.code == "rate_limited":
                    self._set_cooldown(exc.retry_after_seconds or 300)
                elif exc.code == "unauthorized":
                    self._degrade_access_token()
                self._record_run(mode, units=0, discovered=0, reason="error", error_code=exc.code)
                if exc.code == "rate_limited":
                    break
                continue
            except Exception:
                logger.exception("bangumi producer branch failed: mode=%s", mode)
                mode_results[mode] = "error"
                errors.append("error")
                self._record_run(mode, units=0, discovered=0, reason="error", error_code="error")
                continue
            unique_items = _dedupe_items(items)
            all_items.extend(unique_items)
            reason = "ok" if unique_items else "empty"
            mode_results[mode] = reason
            successful_runs[mode] = (len(unique_items), reason, "")

        # A token-bearing request that completed without a 401/403 proves the
        # token still works — clear any stale rejection marker so the account
        # re-arms. Only trust this when the client is still authenticated (a
        # degraded/anonymous cycle proves nothing about the token).
        if (
            "unauthorized" not in errors
            and successful_runs
            and bool(getattr(self.client, "has_access_token", False))
        ):
            self._clear_token_rejection()

        items = _dedupe_items(all_items)[:requested_limit]
        enqueued = 0
        source_counts = Counter(item.source_strategy for item in items)
        for mode, (discovered, run_reason, error_code) in successful_runs.items():
            self._record_run(
                mode,
                units=source_counts.get(BANGUMI_SOURCE_STRATEGIES[mode], 0),
                discovered=discovered,
                reason=run_reason,
                error_code=error_code,
            )
        if self.candidate_pipeline is not None and items:
            for strategy in BANGUMI_SOURCE_STRATEGIES.values():
                grouped = [item for item in items if item.source_strategy == strategy]
                if grouped:
                    enqueued += int(
                        self.candidate_pipeline.enqueue_candidates(
                            grouped,
                            source_context=strategy,
                        )
                    )

        if not items and errors:
            reason = "error"
        elif errors:
            reason = "partial"
        elif mode_results and all(
            outcome == "budget_exhausted" for outcome in mode_results.values()
        ):
            # Every selected mode was skipped for spent daily budget — nothing
            # was fetched, so this is distinct from a mode that ran and found
            # nothing ("empty"). Surface it so the CLI/UI can point at budgets.
            reason = "budget_exhausted"
        elif not items:
            reason = "empty"
        else:
            reason = "ok"
        payload: dict[str, object] = {
            "discovered": len(items),
            "source_counts": dict(source_counts),
            "mode_results": mode_results,
            "reason": reason,
        }
        if self.candidate_pipeline is not None:
            payload["enqueued"] = enqueued
            if enqueued > 0 and not self.candidate_evaluation_owned_by_coordinator:
                payload.update(
                    await self.candidate_pipeline.drain_pending(
                        profile=profile,
                        batch_size=requested_limit,
                    )
                )
        return payload

    async def _run_search(self, profile: Any, limit: int) -> list[DiscoveredContent]:
        claimed: list[Any] = []
        coordinator = self.keyword_fetch
        if coordinator is not None and bool(getattr(coordinator, "should_claim", lambda: False)()):
            claimed = list(coordinator.claim(PLATFORM_BANGUMI, n=min(limit, 5)))
        queries: list[tuple[str, int | None, Any | None]]
        if claimed:
            queries = [(str(item.keyword).strip(), int(item.id), item) for item in claimed]
        else:
            queries = [(word, None, None) for word in _fallback_profile_keywords(profile, limit)]
        if not queries:
            return []

        items: list[DiscoveredContent] = []
        for index, (keyword, keyword_id, claimed_item) in enumerate(queries):
            if not keyword:
                if claimed_item is not None and coordinator is not None:
                    coordinator.mark_failed([claimed_item])
                continue
            if len(items) >= limit:
                if claimed_item is not None and coordinator is not None:
                    coordinator.rollback(claimed_item)
                continue
            try:
                page = await self.client.search_subjects(
                    keyword,
                    subject_types=self.subject_types,
                    limit=min(50, limit - len(items)),
                    offset=0,
                    sort="match",
                )
            except BangumiAPIError as exc:
                if claimed_item is not None and coordinator is not None:
                    if exc.code == "rate_limited":
                        coordinator.rollback(claimed_item)
                    else:
                        coordinator.mark_failed([claimed_item])
                    for _, _, pending_item in queries[index + 1 :]:
                        if pending_item is not None:
                            coordinator.rollback(pending_item)
                if items:
                    raise _PartialSearchError(exc, items[:limit]) from exc
                raise
            except Exception:
                if claimed_item is not None and coordinator is not None:
                    coordinator.mark_failed([claimed_item])
                    for _, _, pending_item in queries[index + 1 :]:
                        if pending_item is not None:
                            coordinator.rollback(pending_item)
                raise
            produced: list[DiscoveredContent] = []
            for row in page.data:
                content = bangumi_subject_to_content(
                    row,
                    strategy=BANGUMI_SOURCE_STRATEGIES["search"],
                    source_keyword_id=keyword_id,
                )
                if content is not None:
                    produced.append(content)
            items.extend(produced)
            if claimed_item is not None and coordinator is not None:
                if produced:
                    coordinator.mark_used([claimed_item])
                else:
                    coordinator.mark_failed([claimed_item])
        return items[:limit]

    async def _run_browse(self, mode: str, limit: int) -> list[DiscoveredContent]:
        strategy = BANGUMI_SOURCE_STRATEGIES[mode]
        sort = "rank" if mode == "ranked" else "date"
        types = tuple(dict.fromkeys(self.subject_types))
        if not types:
            return []
        rotation = self._cursor(mode, "__type_rotation__") % len(types)
        ordered_types = types[rotation:] + types[:rotation]
        allocations = _allocate(limit, ordered_types)
        items: list[DiscoveredContent] = []
        for subject_type in ordered_types:
            branch_limit = allocations.get(subject_type, 0)
            if branch_limit <= 0:
                continue
            cursor = self._cursor(mode, subject_type)
            try:
                page = await self.client.browse_subjects(
                    subject_type,
                    sort=sort,
                    limit=min(50, branch_limit),
                    offset=cursor,
                )
            except BangumiAPIError as exc:
                if exc.code != "invalid_request" or cursor <= 0:
                    raise
                logger.info(
                    "bangumi producer: resetting stale cursor after invalid request: "
                    "mode=%s subject_type=%s cursor=%s",
                    mode,
                    subject_type,
                    cursor,
                )
                self._set_cursor(mode, subject_type, 0, 0)
                cursor = 0
                page = await self.client.browse_subjects(
                    subject_type,
                    sort=sort,
                    limit=min(50, branch_limit),
                    offset=cursor,
                )
            for row in page.data:
                content = bangumi_subject_to_content(row, strategy=strategy)
                if content is not None:
                    items.append(content)
            next_cursor = cursor + len(page.data)
            if not page.data or next_cursor >= page.total:
                next_cursor = 0
            self._set_cursor(mode, subject_type, next_cursor, page.total)
        self._set_cursor(
            mode,
            "__type_rotation__",
            (rotation + 1) % len(types),
            len(types),
        )
        return items[:limit]

    def remaining_budget(self, mode: str, *, per_run_budget: int) -> int:
        configured = {
            "search": self.daily_search_budget,
            "ranked": self.daily_ranked_budget,
            "latest": self.daily_latest_budget,
        }.get(mode, -1)
        if configured == 0:
            return max(0, int(per_run_budget))
        if configured < 0:
            return 0
        return max(0, int(configured) - self.consumed_today(mode))

    def consumed_today(self, mode: str) -> int:
        self._ensure_tables()
        row = self.database.conn.execute(
            """
            SELECT COALESCE(SUM(units), 0)
            FROM bangumi_discovery_runs
            WHERE mode = ? AND reason IN ('ok', 'empty', 'partial')
              AND created_at >= datetime('now', 'start of day')
            """,
            (mode,),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def _record_run(
        self,
        mode: str,
        *,
        units: int,
        discovered: int,
        reason: str,
        error_code: str,
    ) -> None:
        self.database.conn.execute(
            """
            INSERT INTO bangumi_discovery_runs(mode, units, discovered, reason, error_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (mode, max(0, units), max(0, discovered), reason, error_code[:80]),
        )
        self.database.conn.commit()

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        row = self.database.conn.execute(
            """
            SELECT 1 FROM bangumi_discovery_runs
            WHERE created_at >= datetime('now', ?)
            LIMIT 1
            """,
            (f"-{int(self.min_interval_minutes)} minutes",),
        ).fetchone()
        return row is None

    def _cursor(self, mode: str, subject_type: str) -> int:
        row = self.database.conn.execute(
            "SELECT cursor FROM bangumi_discovery_state WHERE state_key = ?",
            (f"{mode}:{subject_type}",),
        ).fetchone()
        return max(0, int(row[0] if row is not None else 0))

    def _set_cursor(self, mode: str, subject_type: str, cursor: int, total: int) -> None:
        self.database.conn.execute(
            """
            INSERT INTO bangumi_discovery_state(state_key, cursor, total, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(state_key) DO UPDATE SET
                cursor = excluded.cursor,
                total = excluded.total,
                updated_at = CURRENT_TIMESTAMP
            """,
            (f"{mode}:{subject_type}", max(0, cursor), max(0, total)),
        )
        self.database.conn.commit()

    def _set_cooldown(self, seconds: int) -> None:
        until = datetime.now(UTC) + timedelta(seconds=min(86_400, max(1, seconds)))
        self.database.conn.execute(
            """
            INSERT INTO bangumi_discovery_state(state_key, cooldown_until, updated_at)
            VALUES ('global', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(state_key) DO UPDATE SET
                cooldown_until = excluded.cooldown_until,
                updated_at = CURRENT_TIMESTAMP
            """,
            (until.isoformat(),),
        )
        self.database.conn.commit()

    def _degrade_access_token(self) -> None:
        """Drop the client's Bearer token after Bangumi rejects it (401/403).

        Public discovery endpoints need no auth, so a token likely expired or
        revoked must not brick every future cycle. Log a clear, actionable
        diagnostic (never the token value), persist a rejection marker so a
        restart does not re-arm the same dead token, and fall back to anonymous
        requests.
        """

        fingerprint = _token_fingerprint(self.access_token)
        self.access_token = ""
        disable = getattr(self.client, "disable_access_token", None)
        already_anonymous = getattr(self.client, "has_access_token", True) is False
        if callable(disable):
            disable()
        # Persist the rejection keyed by a token fingerprint (never the token
        # itself). The fingerprint is a SHA-256 prefix: it is not reversible to
        # the secret but still lets a restart tell "same dead token" (stay
        # anonymous) from "user rotated the token" (try the fresh one).
        if fingerprint:
            with suppress(Exception):
                _persist_token_rejection(self.database, fingerprint)
        if not already_anonymous:
            logger.warning(
                "bangumi producer: access token rejected (401/403); it may have "
                "expired or been revoked. Falling back to anonymous public "
                "discovery. Regenerate a token at "
                "https://next.bgm.tv/demo/access-token and update "
                "[sources.bangumi].access_token."
            )

    def _reconcile_token_rejection(self) -> None:
        """Honor a persisted token-rejection marker before making requests.

        If a prior cycle recorded that this exact token (matching fingerprint)
        was rejected, start anonymous instead of re-triggering a 401 on every
        restart. A changed token (different fingerprint) means the user rotated
        it, so leave the token armed and try it once: a success clears the stale
        marker (end of cycle), and a fresh 401 re-marks with the new fingerprint.
        """

        token = str(self.access_token or "").strip()
        if not token:
            return
        try:
            marker = _read_token_rejection(self.database)
        except Exception:
            return
        if marker is None:
            return
        if str(marker.get("fingerprint") or "") == _token_fingerprint(token):
            disable = getattr(self.client, "disable_access_token", None)
            if callable(disable):
                disable()
            logger.info(
                "bangumi producer: personal token was previously rejected; "
                "starting this cycle anonymous. Regenerate at "
                "https://next.bgm.tv/demo/access-token to re-enable authenticated "
                "discovery."
            )

    def _clear_token_rejection(self) -> None:
        with suppress(Exception):
            _clear_token_rejection(self.database)

    def _candidate_pool_full(self) -> bool:
        return candidate_pool_full_for_source(
            self.candidate_pipeline, "bangumi", logger=logger, label="bangumi producer"
        )

    def _ensure_tables(self) -> None:
        self.database.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bangumi_discovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                units INTEGER NOT NULL DEFAULT 0,
                discovered INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT 'ok',
                error_code TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_bangumi_runs_mode_created
                ON bangumi_discovery_runs(mode, created_at);
            CREATE TABLE IF NOT EXISTS bangumi_discovery_state (
                state_key TEXT PRIMARY KEY,
                cursor INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                cooldown_until TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        # ``note`` is a later addition (token-rejection marker). CREATE TABLE IF
        # NOT EXISTS never adds a column to a pre-existing table, so migrate old
        # databases idempotently.
        columns = {
            str(row[1])
            for row in self.database.conn.execute("PRAGMA table_info(bangumi_discovery_state)")
        }
        if "note" not in columns:
            self.database.conn.execute(
                "ALTER TABLE bangumi_discovery_state ADD COLUMN note TEXT NOT NULL DEFAULT ''"
            )
        self.database.conn.commit()

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("bangumi producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}


def bangumi_disabled_detail(*, token_state: str = "", username_configured: bool = False) -> str:
    """Describe a *disabled* Bangumi source, naming any credential already saved.

    Saving a credential and forgetting the enable switch is an easy miss: the
    settings page validates the token via ``/v0/me`` and echoes the resolved
    account name back, which reads like "done". A bare "未启用" then hides the
    fact that the stored credential is sitting idle. Saying it here — rather
    than in each front end — keeps the remaining step visible without asking
    the desktop / popup renderers to grow per-platform branches.
    """

    if token_state == "rejected":
        return (
            "已保存的个人令牌此前被 Bangumi 拒绝（可能过期）；请到 "
            "https://next.bgm.tv/demo/access-token 重新生成，"
            "并把 Bangumi 来源开关切到「启用」并保存。"
        )
    if token_state:
        # A token supersedes the username (the account is resolved through
        # /v0/me), so naming the token alone is accurate even when both are set.
        return _BANGUMI_DISABLED_CREDENTIAL_DETAIL.format(credential="个人令牌")
    if username_configured:
        return _BANGUMI_DISABLED_CREDENTIAL_DETAIL.format(credential="公开用户名")
    return _BANGUMI_DISABLED_DETAIL


def bangumi_source_status(
    database: Any,
    *,
    enabled: bool,
    token_configured: bool = False,
    username_configured: bool = False,
) -> dict[str, object]:
    """Return local-only discovery status without contacting Bangumi.

    When ``token_configured`` is set, a ``token_state`` dimension is added:
    ``"rejected"`` if a persisted rejection marker exists (the personal token
    was denied and discovery degraded to anonymous), else ``"ok"``. The field is
    omitted entirely when no token is configured, so anonymous deployments keep
    their historical shape.

    A disabled source reports ``token_state`` too, and ``username_configured``
    folds a saved public username into its ``detail``: a stored credential that
    is not in use is a state the user must be able to see. ``state`` stays
    ``"disabled"`` in that case — it tracks discovery health, not the
    credential — so the front ends keep rendering it in their neutral tone
    unless the token itself is ``rejected``.
    """

    token_state: str | None = None
    if token_configured:
        try:
            token_state = "rejected" if _read_token_rejection(database) is not None else "ok"
        except Exception:
            token_state = "ok"

    if not enabled:
        disabled: dict[str, object] = {
            "state": "disabled",
            "detail": bangumi_disabled_detail(
                token_state=token_state or "",
                username_configured=username_configured,
            ),
        }
        if token_state is not None:
            disabled["token_state"] = token_state
        return disabled

    def _finish(payload: dict[str, object]) -> dict[str, object]:
        if token_state is not None:
            payload["token_state"] = token_state
            if token_state == "rejected":
                # The rejection is the most actionable fact — surface it over the
                # generic per-state detail so the settings page shows the warning.
                payload["detail"] = _BANGUMI_TOKEN_REJECTED_DETAIL
        return payload

    try:
        cooldown = _read_cooldown_until(database)
    except Exception:
        return _finish({"state": "unverified", "detail": "尚未运行 Bangumi 内容发现。"})
    if cooldown is not None and cooldown > datetime.now(UTC):
        return _finish(
            {
                "state": "rate_limited",
                "detail": "Bangumi API 正在退避冷却，到期后自动重试。",
                "cooldown_until": cooldown.isoformat(),
            }
        )
    try:
        rows = database.conn.execute(
            """
            SELECT r.mode, r.reason, r.error_code
            FROM bangumi_discovery_runs AS r
            JOIN (
                SELECT mode, MAX(id) AS id FROM bangumi_discovery_runs GROUP BY mode
            ) AS latest ON latest.id = r.id
            """
        ).fetchall()
    except Exception:
        return _finish({"state": "unverified", "detail": "尚未运行 Bangumi 内容发现。"})
    if not rows:
        return _finish({"state": "unverified", "detail": "尚未运行 Bangumi 内容发现。"})
    successes = sum(1 for row in rows if str(row["reason"]) in {"ok", "empty"})
    partials = sum(1 for row in rows if str(row["reason"]) == "partial")
    failures = len(rows) - successes - partials
    if partials or (successes and failures):
        state = "partial"
    elif successes:
        state = "ready"
    else:
        state = "error"
    # A configured, non-rejected token means the account is authenticated; do not
    # claim "无需登录" in that case.
    if token_state == "ok":
        detail = "Bangumi 使用官方公开 API；个人令牌有效，可读取你的私密收藏。"
    else:
        detail = "Bangumi 使用官方公开 API，无需登录。"
    return _finish(
        {
            "state": state,
            "detail": detail,
            "modes": {
                str(row["mode"]): {
                    "reason": str(row["reason"]),
                    "error_code": str(row["error_code"]),
                }
                for row in rows
            },
        }
    )


def _token_fingerprint(token: object) -> str:
    """Return a short, non-reversible SHA-256 fingerprint of a token.

    Twelve hex chars are enough to distinguish "same dead token" from "user
    rotated the token" without ever persisting the secret itself.
    """

    raw = str(token or "").strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _read_token_rejection(database: Any) -> dict[str, Any] | None:
    """Read the persisted token-rejection marker (``None`` when absent)."""

    try:
        row = database.conn.execute(
            "SELECT note FROM bangumi_discovery_state WHERE state_key = ?",
            (_TOKEN_REJECTED_STATE_KEY,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    raw = str(row[0] or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _persist_token_rejection(database: Any, fingerprint: str) -> None:
    """Persist that the token with ``fingerprint`` was rejected (never the token)."""

    payload = json.dumps(
        {"fingerprint": fingerprint, "rejected_at": datetime.now(UTC).isoformat()},
        ensure_ascii=False,
        sort_keys=True,
    )
    database.conn.execute(
        """
        INSERT INTO bangumi_discovery_state(state_key, note, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(state_key) DO UPDATE SET
            note = excluded.note,
            updated_at = CURRENT_TIMESTAMP
        """,
        (_TOKEN_REJECTED_STATE_KEY, payload),
    )
    database.conn.commit()


def _clear_token_rejection(database: Any) -> None:
    """Drop any persisted token-rejection marker (token re-armed / cleared)."""

    database.conn.execute(
        "DELETE FROM bangumi_discovery_state WHERE state_key = ?",
        (_TOKEN_REJECTED_STATE_KEY,),
    )
    database.conn.commit()


def _read_cooldown_until(database: Any) -> datetime | None:
    """Read the persisted global cooldown without constructing a producer."""

    row = database.conn.execute(
        "SELECT cooldown_until FROM bangumi_discovery_state WHERE state_key = 'global'"
    ).fetchone()
    if row is None or not str(row[0] or "").strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(row[0]))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _allocate(limit: int, keys: tuple[str, ...]) -> dict[str, int]:
    if not keys:
        return {}
    total = max(0, int(limit))
    base, remainder = divmod(total, len(keys))
    return {key: base + (1 if index < remainder else 0) for index, key in enumerate(keys)}


def _dedupe_items(items: list[DiscoveredContent]) -> list[DiscoveredContent]:
    out: list[DiscoveredContent] = []
    seen: set[str] = set()
    for item in items:
        key = item.item_key or f"bangumi:{item.content_id or item.bvid}"
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _fallback_profile_keywords(profile: Any, limit: int) -> list[str]:
    preferences = (
        profile.get("preferences")
        if isinstance(profile, dict)
        else getattr(profile, "preferences", None)
    )
    interests = (
        preferences.get("interests", [])
        if isinstance(preferences, dict)
        else getattr(preferences, "interests", [])
    )
    out: list[str] = []
    seen: set[str] = set()
    for interest in interests or []:
        name = (
            str(interest.get("name") or "").strip()
            if isinstance(interest, dict)
            else str(getattr(interest, "name", "") or interest).strip()
        )
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= max(1, int(limit)):
            break
    return out
