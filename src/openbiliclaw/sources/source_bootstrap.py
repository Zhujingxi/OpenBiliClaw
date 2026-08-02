"""Database-only enqueue helpers for browser-extension bootstrap tasks.

The CLI historically owned the five bootstrap enqueue paths.  Keeping that
logic in this module lets runtime code enqueue an already-resolved database
without importing the Typer/Rich CLI surface.  The helpers deliberately stop
at the database boundary: dispatch kicks and user-facing rendering belong to
their caller.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE = 300
DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_REDDIT_BOOTSTRAP_DEDUPE_HOURS = 6.0

_RECENT_TASK_STATUSES = ("pending", "in_progress", "completed", "failed")
Notify = Callable[[str], None]


@dataclass(frozen=True)
class BootstrapEnqueueResult:
    """Outcome of one bootstrap enqueue attempt.

    ``created`` is true only for a newly inserted row with a non-empty task
    id.  A task returned by the recent-task dedupe path has an id but
    ``created`` is false, which is important to periodic scheduling: reuse
    must not advance its attempt timestamp or cursor.
    """

    task_id: str | None
    created: bool
    reason: str


def _notify(notify: Notify | None, message: str) -> None:
    if notify is not None:
        notify(message)


def _dedupe_hours(env_var: str, default: float) -> float:
    raw = os.environ.get(env_var, str(default))
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _recent_reuse_result(
    recent: dict[str, Any], *, message: str, notify: Notify | None
) -> BootstrapEnqueueResult | None:
    task_id = str(recent.get("id", "")).strip()
    if not task_id:
        return None
    _notify(notify, message.format(status=str(recent.get("status", "unknown"))))
    return BootstrapEnqueueResult(task_id=task_id, created=False, reason="reused_recent")


def _created_or_budget_result(
    task_id: object,
    *,
    budget_message: str,
    notify: Notify | None,
) -> BootstrapEnqueueResult:
    normalized = str(task_id).strip() if task_id is not None else ""
    if not normalized:
        _notify(notify, budget_message)
        return BootstrapEnqueueResult(
            task_id=None,
            created=False,
            reason="enqueue_failed",
        )
    return BootstrapEnqueueResult(task_id=normalized, created=True, reason="created")


def _incremental_payload(payload: dict[str, Any], incremental: bool) -> dict[str, Any]:
    if incremental:
        payload["incremental"] = True
    return payload


def enqueue_xhs_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the XHS ``bootstrap_profile`` task without dispatching it."""
    from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

    scroll_rounds = int(os.environ.get("OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS", "15"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )

    try:
        queue = XhsTaskQueue(database)
        dedupe_hours = _dedupe_hours(
            "OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS", DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS
        )
        find_recent = getattr(queue, "find_recent_task", None)
        if not force and dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_profile",
                recent_hours=dedupe_hours,
                statuses=_RECENT_TASK_STATUSES,
            )
            if recent is not None:
                reused = _recent_reuse_result(
                    recent,
                    message=(
                        "  [dim]复用最近的小红书 bootstrap 任务"
                        "({status})；需要重新拉取可用 `openbiliclaw fetch-xhs --force`。[/dim]"
                    ),
                    notify=notify,
                )
                if reused is not None:
                    return reused

        payload = _incremental_payload(
            {
                "scopes": ["saved", "liked", "xhs_history"],
                "max_items_per_scope": max(1, max_items),
                "max_scroll_rounds": max(0, scroll_rounds),
            },
            incremental,
        )
        task_id = queue.enqueue_with_id("bootstrap_profile", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]小红书初始化信号未导入: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]小红书初始化信号未导入: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


def enqueue_dy_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the Douyin ``bootstrap_profile`` task without dispatching it."""
    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    scroll_rounds = int(os.environ.get("OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS", "15"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )

    try:
        queue = DyTaskQueue(database)
        dedupe_hours = _dedupe_hours(
            "OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS", DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS
        )
        find_recent = getattr(queue, "find_recent_task", None)
        if not force and dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_profile",
                recent_hours=dedupe_hours,
                statuses=_RECENT_TASK_STATUSES,
            )
            if recent is not None:
                raw_result = recent.get("result_json")
                if isinstance(raw_result, dict):
                    parsed_result = raw_result
                elif isinstance(raw_result, (str, bytes, bytearray)):
                    try:
                        parsed_result = json.loads(raw_result)
                    except (TypeError, ValueError):
                        parsed_result = None
                else:
                    parsed_result = None
                recent_is_degraded = (
                    isinstance(parsed_result, dict)
                    and str(parsed_result.get("status", "")).strip().lower() == "degraded"
                )
                if recent_is_degraded:
                    _notify(
                        notify,
                        "  [dim]最近的抖音 bootstrap 任务仅部分完成；"
                        "本次重新入队以补齐分页。[/dim]",
                    )
                else:
                    reused = _recent_reuse_result(
                        recent,
                        message=(
                            "  [dim]复用最近的抖音 bootstrap 任务"
                            "({status})；需要重新拉取可设 "
                            "OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                        ),
                        notify=notify,
                    )
                    if reused is not None:
                        return reused

        payload = _incremental_payload(
            {
                "scopes": ["dy_post", "dy_collect", "dy_like", "dy_follow"],
                "max_items_per_scope": max(1, max_items),
                "max_scroll_rounds": max(0, scroll_rounds),
            },
            incremental,
        )
        task_id = queue.enqueue_with_id("bootstrap_profile", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]抖音初始化信号未导入: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]抖音初始化信号未导入: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


def enqueue_yt_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the YouTube ``bootstrap_profile`` task without dispatching it."""
    from openbiliclaw.sources.yt_tasks import YtTaskQueue

    scroll_rounds = int(os.environ.get("OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS", "10"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_YT_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )

    try:
        queue = YtTaskQueue(database)
        dedupe_hours = _dedupe_hours(
            "OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS", DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS
        )
        find_recent = getattr(queue, "find_recent_task", None)
        if not force and dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_profile",
                recent_hours=dedupe_hours,
                statuses=_RECENT_TASK_STATUSES,
            )
            if recent is not None:
                reused = _recent_reuse_result(
                    recent,
                    message=(
                        "  [dim]复用最近的 YouTube bootstrap 任务"
                        "({status})；需要重新拉取可设 "
                        "OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                    ),
                    notify=notify,
                )
                if reused is not None:
                    return reused

        payload = _incremental_payload(
            {
                "scopes": ["yt_history", "yt_subscriptions", "yt_likes"],
                "max_items_per_scope": max(1, max_items),
                "max_scroll_rounds": max(0, scroll_rounds),
            },
            incremental,
        )
        task_id = queue.enqueue_with_id("bootstrap_profile", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]YouTube 初始化信号未导入: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]YouTube 初始化信号未导入: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


def enqueue_zhihu_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    profile_slug: str = "",
    profile_update: bool = False,
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the Zhihu ``bootstrap_events`` task without dispatching it."""
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    max_items = int(
        os.environ.get(
            "OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    max_collections = int(os.environ.get("OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_COLLECTIONS", "20"))

    try:
        queue = ZhihuTaskQueue(database)
        dedupe_hours = _dedupe_hours(
            "OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS",
            DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS,
        )
        find_recent = getattr(queue, "find_recent_task", None)
        if not force and dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_events",
                recent_hours=dedupe_hours,
                statuses=_RECENT_TASK_STATUSES,
            )
            if recent is not None:
                reused = _recent_reuse_result(
                    recent,
                    message=(
                        "  [dim]复用最近的知乎 bootstrap 任务"
                        "({status})；需要重新拉取可设 "
                        "OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                    ),
                    notify=notify,
                )
                if reused is not None:
                    return reused

        scopes = ["zhihu_read_history", "zhihu_collection", "zhihu_activity"]
        if not profile_slug.strip():
            _notify(
                notify,
                "  [dim]未传 --profile-slug，扩展会尝试从知乎登录态识别当前用户；"
                "识别失败时只返回浏览记录和收藏夹。[/dim]",
            )
        payload = _incremental_payload(
            {
                "scopes": scopes,
                "profile_slug": profile_slug.strip(),
                "max_items_per_scope": max(1, max_items),
                "max_collections": max(1, max_collections),
                "profile_update": bool(profile_update),
            },
            incremental,
        )
        task_id = queue.enqueue_with_id("bootstrap_events", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]知乎事件未拉取: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]知乎事件未拉取: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


def enqueue_reddit_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    profile_update: bool = False,
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the Reddit ``bootstrap_events`` task without dispatching it."""
    from openbiliclaw.sources.reddit_tasks import RedditTaskQueue

    max_items = int(
        os.environ.get(
            "OPENBILICLAW_REDDIT_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )

    try:
        queue = RedditTaskQueue(database)
        dedupe_hours = _dedupe_hours(
            "OPENBILICLAW_REDDIT_BOOTSTRAP_DEDUPE_HOURS",
            DEFAULT_REDDIT_BOOTSTRAP_DEDUPE_HOURS,
        )
        find_recent = getattr(queue, "find_recent_task", None)
        if not force and dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_events",
                recent_hours=dedupe_hours,
                statuses=_RECENT_TASK_STATUSES,
            )
            if recent is not None:
                reused = _recent_reuse_result(
                    recent,
                    message=(
                        "  [dim]复用最近的 Reddit bootstrap 任务"
                        "({status})；需要重新拉取可设 "
                        "OPENBILICLAW_REDDIT_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                    ),
                    notify=notify,
                )
                if reused is not None:
                    return reused

        payload = _incremental_payload(
            {
                "scopes": ["reddit_saved", "reddit_upvoted", "reddit_subscribed"],
                "max_items_per_scope": max(1, max_items),
                "profile_update": bool(profile_update),
            },
            incremental,
        )
        task_id = queue.enqueue_with_id("bootstrap_events", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]Reddit 初始化事件未拉取: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]Reddit 初始化事件未拉取: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


__all__ = [
    "BootstrapEnqueueResult",
    "enqueue_dy_bootstrap",
    "enqueue_reddit_bootstrap",
    "enqueue_xhs_bootstrap",
    "enqueue_yt_bootstrap",
    "enqueue_zhihu_bootstrap",
]
