"""Tests for the database-only extension bootstrap enqueue core."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest


class _FakeDatabase:
    conn = object()


def _queue_class(
    *,
    recent: dict[str, Any] | None = None,
    enqueue_id: str | None = "fresh-task-id",
    captured: dict[str, Any],
) -> type:
    class FakeQueue:
        def __init__(self, _database: object) -> None:
            pass

        def find_recent_task(
            self,
            task_type: str,
            *,
            recent_hours: float,
            statuses: tuple[str, ...] | None = None,
        ) -> dict[str, Any] | None:
            captured["recent_call"] = (task_type, recent_hours, statuses)
            return recent

        def enqueue_with_id(
            self,
            task_type: str,
            payload: dict[str, Any],
            *,
            daily_budget: int,
        ) -> str | None:
            captured["task_type"] = task_type
            captured["payload"] = payload
            captured["daily_budget"] = daily_budget
            return enqueue_id

    return FakeQueue


_PLATFORMS = (
    (
        "enqueue_xhs_bootstrap",
        "openbiliclaw.sources.xhs_tasks.XhsTaskQueue",
        "bootstrap_profile",
        {
            "scopes": ["saved", "liked", "xhs_history"],
            "max_items_per_scope": 300,
            "max_scroll_rounds": 15,
        },
    ),
    (
        "enqueue_dy_bootstrap",
        "openbiliclaw.sources.dy_tasks.DyTaskQueue",
        "bootstrap_profile",
        {
            "scopes": ["dy_post", "dy_collect", "dy_like", "dy_follow"],
            "max_items_per_scope": 300,
            "max_scroll_rounds": 15,
        },
    ),
    (
        "enqueue_yt_bootstrap",
        "openbiliclaw.sources.yt_tasks.YtTaskQueue",
        "bootstrap_profile",
        {
            "scopes": ["yt_history", "yt_subscriptions", "yt_likes"],
            "max_items_per_scope": 300,
            "max_scroll_rounds": 10,
        },
    ),
    (
        "enqueue_zhihu_bootstrap",
        "openbiliclaw.sources.zhihu_tasks.ZhihuTaskQueue",
        "bootstrap_events",
        {
            "scopes": ["zhihu_read_history", "zhihu_collection", "zhihu_activity"],
            "profile_slug": "",
            "max_items_per_scope": 300,
            "max_collections": 20,
            "profile_update": False,
        },
    ),
    (
        "enqueue_reddit_bootstrap",
        "openbiliclaw.sources.reddit_tasks.RedditTaskQueue",
        "bootstrap_events",
        {
            "scopes": ["reddit_saved", "reddit_upvoted", "reddit_subscribed"],
            "max_items_per_scope": 300,
            "profile_update": False,
        },
    ),
)


@pytest.mark.parametrize(
    ("helper_name", "queue_path", "task_type", "expected_payload"),
    _PLATFORMS,
)
def test_non_incremental_payloads_and_budgets_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    queue_path: str,
    task_type: str,
    expected_payload: dict[str, Any],
) -> None:
    from openbiliclaw.sources import source_bootstrap

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        queue_path,
        _queue_class(captured=captured),
    )

    helper = getattr(source_bootstrap, helper_name)
    result = helper(_FakeDatabase(), force=True)

    assert result.task_id == "fresh-task-id"
    assert result.created is True
    assert result.reason == "created"
    assert captured["task_type"] == task_type
    assert captured["payload"] == expected_payload
    assert captured["daily_budget"] == 10


@pytest.mark.parametrize(
    ("helper_name", "queue_path", "_task_type", "_expected_payload"),
    _PLATFORMS,
)
def test_force_false_reuses_recent_task(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    queue_path: str,
    _task_type: str,
    _expected_payload: dict[str, Any],
) -> None:
    from openbiliclaw.sources import source_bootstrap

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        queue_path,
        _queue_class(
            recent={"id": "recent-task-id", "status": "completed"},
            captured=captured,
        ),
    )

    result = getattr(source_bootstrap, helper_name)(_FakeDatabase())

    assert result == source_bootstrap.BootstrapEnqueueResult(
        task_id="recent-task-id",
        created=False,
        reason="reused_recent",
    )
    assert "task_type" not in captured


@pytest.mark.parametrize(
    ("helper_name", "queue_path", "_task_type", "_expected_payload"),
    _PLATFORMS,
)
def test_force_true_bypasses_recent_task(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    queue_path: str,
    _task_type: str,
    _expected_payload: dict[str, Any],
) -> None:
    from openbiliclaw.sources import source_bootstrap

    captured: dict[str, Any] = {}

    class ForceQueue:
        def __init__(self, _database: object) -> None:
            pass

        def find_recent_task(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
            raise AssertionError("force=True must not inspect recent tasks")

        def enqueue_with_id(
            self,
            task_type: str,
            payload: dict[str, Any],
            *,
            daily_budget: int,
        ) -> str:
            captured.update(
                task_type=task_type,
                payload=payload,
                daily_budget=daily_budget,
            )
            return "forced-task-id"

    monkeypatch.setattr(queue_path, ForceQueue)

    result = getattr(source_bootstrap, helper_name)(_FakeDatabase(), force=True)

    assert result == source_bootstrap.BootstrapEnqueueResult(
        task_id="forced-task-id",
        created=True,
        reason="created",
    )
    assert "task_type" in captured


@pytest.mark.parametrize(
    ("helper_name", "queue_path", "_task_type", "_expected_payload"),
    _PLATFORMS,
)
def test_incremental_marker_is_opt_in_and_preserves_profile_update_fields(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    queue_path: str,
    _task_type: str,
    _expected_payload: dict[str, Any],
) -> None:
    from openbiliclaw.sources import source_bootstrap

    captured: dict[str, Any] = {}
    monkeypatch.setattr(queue_path, _queue_class(captured=captured))

    result = getattr(source_bootstrap, helper_name)(
        _FakeDatabase(),
        force=True,
        incremental=True,
    )

    assert result.created is True
    assert captured["payload"]["incremental"] is True
    if helper_name in {"enqueue_zhihu_bootstrap", "enqueue_reddit_bootstrap"}:
        assert captured["payload"]["profile_update"] is False

    captured.clear()
    monkeypatch.setattr(queue_path, _queue_class(captured=captured))
    getattr(source_bootstrap, helper_name)(_FakeDatabase(), force=True, incremental=False)
    assert "incremental" not in captured["payload"]


def test_douyin_degraded_recent_task_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.sources import source_bootstrap

    captured: dict[str, Any] = {}
    messages: list[str] = []
    monkeypatch.setattr(
        "openbiliclaw.sources.dy_tasks.DyTaskQueue",
        _queue_class(
            recent={
                "id": "degraded-task-id",
                "status": "completed",
                "result_json": json.dumps({"status": "degraded"}),
            },
            captured=captured,
        ),
    )

    result = source_bootstrap.enqueue_dy_bootstrap(_FakeDatabase(), notify=messages.append)

    assert result.created is True
    assert result.task_id == "fresh-task-id"
    assert "task_type" in captured
    assert any("仅部分完成" in message for message in messages)


@pytest.mark.parametrize(
    ("helper_name", "queue_path", "_task_type", "_expected_payload"),
    _PLATFORMS,
)
def test_budget_exhaustion_is_a_structured_failure(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    queue_path: str,
    _task_type: str,
    _expected_payload: dict[str, Any],
) -> None:
    from openbiliclaw.sources import source_bootstrap

    captured: dict[str, Any] = {}
    messages: list[str] = []
    monkeypatch.setattr(
        queue_path,
        _queue_class(enqueue_id=None, captured=captured),
    )

    result = getattr(source_bootstrap, helper_name)(
        _FakeDatabase(),
        force=True,
        notify=messages.append,
    )

    assert result == source_bootstrap.BootstrapEnqueueResult(
        task_id=None,
        created=False,
        reason="enqueue_failed",
    )
    assert messages
    assert "今日任务预算已用完" in messages[-1]


def test_source_bootstrap_import_does_not_load_cli_or_ui_dependencies() -> None:
    code = (
        "import sys\n"
        "import openbiliclaw.sources.source_bootstrap\n"
        "for name in ('openbiliclaw.cli', 'typer', 'click', 'rich'):\n"
        "    assert name not in sys.modules, name\n"
        "print('ok')\n"
    )
    environment = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": "src",
    }
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        cwd=_repo_root(),
        env=environment,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "ok"


def test_cli_wrapper_maps_created_result_and_respects_deferred_kick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw import cli
    from openbiliclaw.sources import source_bootstrap

    kicked: list[str] = []
    calls: dict[str, Any] = {}

    def fake_enqueue(
        database: object,
        *,
        force: bool,
        incremental: bool,
        notify: Any,
    ) -> source_bootstrap.BootstrapEnqueueResult:
        calls.update(
            database=database,
            force=force,
            incremental=incremental,
            notify=notify,
        )
        return source_bootstrap.BootstrapEnqueueResult("task-id", True, "created")

    monkeypatch.setattr(cli, "_get_runtime_database", lambda: _FakeDatabase())
    monkeypatch.setattr(source_bootstrap, "enqueue_xhs_bootstrap", fake_enqueue)
    monkeypatch.setattr(cli, "_kick_task_dispatcher", kicked.append)

    assert cli._enqueue_xhs_bootstrap_task(force=True, incremental=True, kick=False) == "task-id"
    assert calls["database"].__class__ is _FakeDatabase
    assert calls["force"] is True
    assert calls["incremental"] is True
    assert kicked == []

    assert cli._enqueue_xhs_bootstrap_task(force=True, incremental=True, kick=True) == "task-id"
    assert kicked == ["xhs"]


def _repo_root() -> str:
    return str(__file__).rsplit("/tests/", 1)[0]
