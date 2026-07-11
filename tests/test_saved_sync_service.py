from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import pytest

from openbiliclaw.saved_sync.models import (
    NativeSaveAction,
    NativeSaveCapability,
    NativeSaveResult,
    NativeSaveRoute,
    NativeSaveStatus,
    SavedItemInput,
)
from openbiliclaw.saved_sync.router import NativeSaveRouter
from openbiliclaw.saved_sync.service import SavedSyncService
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "saved-sync-service.db")
    database.initialize()
    return database


class FakeAdapter:
    def __init__(
        self,
        capability: NativeSaveCapability,
        result_status: str = "synced",
        gate: asyncio.Event | None = None,
    ) -> None:
        self.capability = capability
        self.result_status = result_status
        self.gate = gate
        self.calls: list[str] = []

    def target_label(self, action: NativeSaveAction) -> str:
        if self.capability.platform == "reddit":
            return "Reddit Saved"
        return "B站稍后观看" if action == "watch_later" else "B站 OpenBiliClaw 收藏夹"

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        if self.gate is not None:
            await self.gate.wait()
        return NativeSaveResult(
            item_key=item.item_key,
            status=cast("NativeSaveStatus", self.result_status),
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
        )


class RaisingAdapter(FakeAdapter):
    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        raise RuntimeError("private platform response body")


class RaisingTargetAdapter(FakeAdapter):
    def target_label(self, action: NativeSaveAction) -> str:
        raise ValueError("private target discovery response")


class MisroutingAdapter(FakeAdapter):
    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        return NativeSaveResult(
            item_key=item.item_key,
            status=cast("NativeSaveStatus", self.result_status),
            resolved_action="favorite" if route.resolved_action == "watch_later" else "watch_later",
            resolved_target="adapter-controlled target",
        )


class CancellationSuppressingAdapter(FakeAdapter):
    def __init__(self, release: asyncio.Event) -> None:
        super().__init__(NativeSaveCapability("bilibili", True, True, True))
        self.release = release
        self.cancel_seen = asyncio.Event()

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await self.release.wait()
        return NativeSaveResult(
            item_key=item.item_key,
            status="synced",
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
        )


class MalformedCancellationSuppressingAdapter(CancellationSuppressingAdapter):
    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await self.release.wait()
        return cast("NativeSaveResult", {"private_response": "must not leak"})


class InvalidTargetAdapter(FakeAdapter):
    def __init__(self, target: object) -> None:
        super().__init__(NativeSaveCapability("bilibili", True, True, True))
        self.target = target

    def target_label(self, action: NativeSaveAction) -> str:
        del action
        return cast("str", self.target)


class ImmediateCancellationSuccessAdapter(FakeAdapter):
    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return NativeSaveResult(
                item_key=item.item_key,
                status="synced",
                resolved_action=route.resolved_action,
                resolved_target=route.resolved_target,
            )


def test_local_save_without_auto_sync_never_invokes_adapter(db: Database) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1LOCAL")

    result = service.save_local("watch_later", item, note="later", auto_sync=False)

    row = db.get_saved_membership("watch_later", item.item_key)
    assert row is not None
    assert row["note"] == "later"
    assert row["sync_status"] == "pending"
    assert result.saved is True
    assert result.sync_status == "pending"
    assert result.sync_task_id == ""
    assert adapter.calls == []


async def test_auto_sync_returns_after_local_commit_and_runs_in_background(db: Database) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    started: list[asyncio.Task[Any]] = []

    def start_task(name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        assert name.startswith("saved-sync:")
        assert db.get_saved_membership("watch_later", "bilibili:BV1AUTO") is not None
        task = asyncio.create_task(coro, name=name)
        started.append(task)
        return task

    service = SavedSyncService(db, NativeSaveRouter([adapter]), task_starter=start_task)

    result = service.save_local(
        "watch_later",
        SavedItemInput("bilibili", "BV1AUTO"),
        auto_sync=True,
    )

    assert result.sync_status == "pending"
    assert result.sync_task_id
    assert adapter.calls == []
    assert len(started) == 1
    gate.set()
    await started[0]
    assert service.get_sync_task(result.sync_task_id).items[0].status == "synced"


@pytest.mark.parametrize("terminal_status", ["synced", "already_synced"])
def test_duplicate_local_save_preserves_terminal_native_state(
    db: Database,
    terminal_status: NativeSaveStatus,
) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", f"BV1{terminal_status.upper()}")
    service.save_local("favorite", item)
    db.upsert_native_save_state(
        "favorite",
        item.item_key,
        requested_action="favorite",
        resolved_action="favorite",
        resolved_target="B站 OpenBiliClaw 收藏夹",
        status=terminal_status,
        task_id="terminal-task",
    )

    result = service.save_local("favorite", item, note="updated", auto_sync=False)
    row = db.get_saved_membership("favorite", item.item_key)

    assert result.sync_status == terminal_status
    assert result.sync_task_id == "terminal-task"
    assert row is not None
    assert row["note"] == "updated"
    assert row["sync_status"] == terminal_status
    assert row["sync_task_id"] == "terminal-task"
    assert row["resolved_target"] == "B站 OpenBiliClaw 收藏夹"


def test_local_save_cannot_erase_task_claimed_between_membership_and_state_insert(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    claiming_service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1INTERLEAVE")
    original_get = db.get_saved_membership
    original_ensure = db.ensure_native_save_state
    claimed_task_ids: list[str] = []

    def read_then_claim(list_kind: str, item_key: str) -> dict[str, Any] | None:
        row = original_get(list_kind, item_key)
        if not claimed_task_ids and row is not None and not str(row["requested_action"]):
            claimed = claiming_service.create_sync_task(
                "favorite", [item_key], "manual_single"
            )
            claimed_task_ids.append(claimed.task_id)
        return row

    def claim_then_ensure(
        list_kind: str,
        item_key: str,
        requested_action: str,
    ) -> dict[str, Any]:
        if not claimed_task_ids:
            claimed = claiming_service.create_sync_task(
                "favorite", [item_key], "manual_single"
            )
            claimed_task_ids.append(claimed.task_id)
        return original_ensure(list_kind, item_key, requested_action)

    monkeypatch.setattr(db, "get_saved_membership", read_then_claim)
    monkeypatch.setattr(db, "ensure_native_save_state", claim_then_ensure)

    result = service.save_local("favorite", item, auto_sync=False)
    row = db.get_saved_membership("favorite", item.item_key)

    assert row is not None
    assert row["sync_task_id"] == claimed_task_ids[0]
    assert result.sync_task_id == claimed_task_ids[0]
    assert claiming_service.get_sync_task(claimed_task_ids[0]).items[0].status == "pending"


async def test_platform_failure_keeps_local_membership(db: Database) -> None:
    adapter = FakeAdapter(
        NativeSaveCapability("bilibili", True, True, True),
        result_status="failed",
    )
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1FAIL")
    local = service.save_local("watch_later", item, auto_sync=False)
    created = service.create_sync_task("watch_later", [item.item_key], "manual_single")

    result = await service.run_sync_task(created.task_id)

    assert db.get_saved_membership("watch_later", item.item_key) is not None
    assert local.saved is True
    assert result.items[0].status == "failed"


async def test_blank_task_ids_fail_closed_without_reading_or_executing(db: Database) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    first = SavedItemInput("bilibili", "BV1BLANKTASK")
    second = SavedItemInput("reddit", "blank-task-post")
    service.save_local("favorite", first)
    service.save_local("favorite", second)

    with pytest.raises(ValueError, match="task_id"):
        await service.run_sync_task("")
    with pytest.raises(ValueError, match="task_id"):
        service.get_sync_task("   ")

    assert adapter.calls == []
    assert db.get_saved_membership("favorite", first.item_key)["sync_task_id"] == ""  # type: ignore[index]
    assert db.get_saved_membership("favorite", second.item_key)["sync_task_id"] == ""  # type: ignore[index]


def test_create_sync_task_uses_one_task_id_for_selected_eligible_items(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    first = SavedItemInput("bilibili", "BV1FIRST")
    second = SavedItemInput("reddit", "post-2")
    excluded = SavedItemInput("bilibili", "BV1EXCLUDED")
    for item in (first, second, excluded):
        db.upsert_saved_membership("favorite", item)
    db.upsert_native_save_state(
        "favorite",
        excluded.item_key,
        requested_action="favorite",
        resolved_action="favorite",
        resolved_target="B站 OpenBiliClaw 收藏夹",
        status="synced",
    )

    created = service.create_sync_task(
        "favorite",
        [first.item_key, second.item_key, excluded.item_key],
        "manual_batch",
    )

    assert created.task_id
    assert {item.item_key for item in created.items} == {first.item_key, second.item_key}
    assert {item.status for item in created.items} == {"pending"}
    rows = db.list_native_save_states_by_task(created.task_id)
    assert {row["item_key"] for row in rows} == {first.item_key, second.item_key}


def test_duplicate_task_creation_does_not_steal_pending_task_rows(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1OWNED")
    service.save_local("favorite", item)

    first = service.create_sync_task("favorite", [item.item_key], "manual_single")
    duplicate = service.create_sync_task("favorite", [item.item_key], "manual_single")

    assert [result.item_key for result in first.items] == [item.item_key]
    assert duplicate.items == ()
    assert service.get_sync_task(first.task_id).items[0].status == "pending"
    assert service.get_sync_task(duplicate.task_id).items == ()


def test_task_starter_failure_releases_pending_ownership(db: Database) -> None:
    def failing_starter(name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        del name, coro
        raise RuntimeError("task registry unavailable")

    item = SavedItemInput("bilibili", "BV1STARTFAIL")
    db.upsert_saved_membership("favorite", item)
    service = SavedSyncService(db, NativeSaveRouter(), task_starter=failing_starter)

    with pytest.raises(RuntimeError, match="task registry unavailable"):
        service.create_sync_task("favorite", [item.item_key], "manual_single")

    row = db.get_saved_membership("favorite", item.item_key)
    assert row is not None
    assert row["sync_status"] == "pending"
    assert row["sync_task_id"] == ""
    retry = SavedSyncService(db, NativeSaveRouter()).create_sync_task(
        "favorite", [item.item_key], "manual_single"
    )
    assert [result.item_key for result in retry.items] == [item.item_key]


def test_task_starter_cancellation_releases_pending_ownership(db: Database) -> None:
    def cancelled_starter(name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        del name, coro
        raise asyncio.CancelledError

    item = SavedItemInput("bilibili", "BV1STARTCANCEL")
    db.upsert_saved_membership("favorite", item)
    service = SavedSyncService(db, NativeSaveRouter(), task_starter=cancelled_starter)

    with pytest.raises(asyncio.CancelledError):
        service.create_sync_task("favorite", [item.item_key], "manual_single")

    row = db.get_saved_membership("favorite", item.item_key)
    assert row is not None
    assert row["sync_status"] == "pending"
    assert row["sync_task_id"] == ""


def test_stale_never_started_task_can_be_safely_reclaimed(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1NEVERSTARTED")
    service.save_local("favorite", item)
    abandoned = service.create_sync_task("favorite", [item.item_key], "manual_single")
    db.conn.execute(
        """
        UPDATE native_save_states
        SET task_claimed_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()

    recovered = service.create_sync_task("favorite", [item.item_key], "manual_single")

    assert [result.item_key for result in recovered.items] == [item.item_key]
    assert service.get_sync_task(abandoned.task_id).items == ()
    assert service.get_sync_task(recovered.task_id).items[0].status == "pending"


def test_nonempty_blank_item_selection_fails_closed(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    first = SavedItemInput("bilibili", "BV1BLANKFIRST")
    second = SavedItemInput("bilibili", "BV1BLANKSECOND")
    service.save_local("favorite", first)
    service.save_local("favorite", second)

    with pytest.raises(ValueError, match="item_keys"):
        service.create_sync_task("favorite", ["   "], "manual_batch")

    assert db.get_saved_membership("favorite", first.item_key)["sync_task_id"] == ""  # type: ignore[index]
    assert db.get_saved_membership("favorite", second.item_key)["sync_task_id"] == ""  # type: ignore[index]


async def test_concurrent_services_atomically_claim_task_creation(db: Database) -> None:
    second_db = Database(db._db_path)
    second_db.initialize()
    first_service = SavedSyncService(db, NativeSaveRouter())
    second_service = SavedSyncService(second_db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1ATOMICCREATE")
    first_service.save_local("favorite", item)

    first, second = await asyncio.gather(
        asyncio.to_thread(
            first_service.create_sync_task,
            "favorite",
            [item.item_key],
            "manual_single",
        ),
        asyncio.to_thread(
            second_service.create_sync_task,
            "favorite",
            [item.item_key],
            "manual_single",
        ),
    )

    winner, loser = (first, second) if first.items else (second, first)
    assert [result.item_key for result in winner.items] == [item.item_key]
    assert loser.items == ()
    assert first_service.get_sync_task(winner.task_id).items[0].status == "pending"
    assert second_service.get_sync_task(loser.task_id).items == ()


async def test_adapter_exception_is_sanitized_and_persisted_per_item(db: Database) -> None:
    adapter = RaisingAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1SECRET")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)
    reconstructed = SavedSyncService(db, NativeSaveRouter()).get_sync_task(task.task_id)

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "adapter_exception"
    assert "private" not in result.items[0].error_message
    assert reconstructed == result


async def test_concurrent_task_runners_execute_each_item_once(db: Database) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1SINGLEFLIGHT")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    first_runner = asyncio.create_task(service.run_sync_task(task.task_id))
    second_runner = asyncio.create_task(service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    assert adapter.calls == [item.item_key]

    gate.set()
    first_result, second_result = await asyncio.gather(first_runner, second_runner)
    assert adapter.calls == [item.item_key]
    assert first_result == second_result
    assert first_result.items[0].status == "synced"
    assert task.task_id not in service._task_run_locks


async def test_concurrent_services_execute_claimed_item_once(db: Database) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    second_db = Database(db._db_path)
    second_db.initialize()
    first_service = SavedSyncService(db, NativeSaveRouter([adapter]))
    second_service = SavedSyncService(second_db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1CROSSSERVICE")
    first_service.save_local("favorite", item)
    task = first_service.create_sync_task("favorite", [item.item_key], "manual_single")

    first_runner = asyncio.create_task(first_service.run_sync_task(task.task_id))
    second_runner = asyncio.create_task(second_service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    assert adapter.calls == [item.item_key]

    gate.set()
    first_result, second_result = await asyncio.gather(first_runner, second_runner)
    assert adapter.calls == [item.item_key]
    assert first_result.items[0].status == "synced"
    assert second_result.items[0].status in {"syncing", "synced"}
    assert second_service.get_sync_task(task.task_id).items[0].status == "synced"


async def test_live_aged_claim_heartbeat_prevents_cross_service_reexecution(
    db: Database,
) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    second_db = Database(db._db_path)
    second_db.initialize()
    first_service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        claim_heartbeat_interval_seconds=0.01,
    )
    second_service = SavedSyncService(second_db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1LIVEAGED")
    first_service.save_local("favorite", item)
    task = first_service.create_sync_task("favorite", [item.item_key], "manual_single")
    first_runner = asyncio.create_task(first_service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    db.conn.execute(
        """
        UPDATE native_save_states
        SET last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()
    for _ in range(100):
        row = db.get_saved_membership("favorite", item.item_key)
        if row is not None and str(row["last_attempt_at"]) > "2000-01-01":
            timestamp = db.conn.execute(
                """
                SELECT last_attempt_at > datetime('now', '-1 minute')
                FROM native_save_states
                WHERE list_kind = 'favorite' AND item_key = ?
                """,
                (item.item_key,),
            ).fetchone()
            if timestamp is not None and int(timestamp[0]) == 1:
                break
        await asyncio.sleep(0.01)

    second_result = await second_service.run_sync_task(task.task_id)
    assert adapter.calls == [item.item_key]
    assert second_result.items[0].status == "syncing"

    gate.set()
    await first_runner
    assert adapter.calls == [item.item_key]


async def test_adapter_deadline_keeps_lease_until_cancellation_suppressing_call_ends(
    db: Database,
) -> None:
    release = asyncio.Event()
    adapter = CancellationSuppressingAdapter(release)
    second_db = Database(db._db_path)
    second_db.initialize()
    service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        claim_heartbeat_interval_seconds=0.005,
        adapter_timeout_seconds=0.02,
    )
    second_service = SavedSyncService(second_db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1TIMEOUT")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert adapter.calls == [item.item_key]
    assert result.items[0].status == "syncing"
    assert len(service._detached_attempts) == 1
    await adapter.cancel_seen.wait()
    db.conn.execute(
        """
        UPDATE native_save_states
        SET last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()
    for _ in range(100):
        refreshed = db.conn.execute(
            """
            SELECT last_attempt_at > datetime('now', '-1 minute')
            FROM native_save_states
            WHERE list_kind = 'favorite' AND item_key = ?
            """,
            (item.item_key,),
        ).fetchone()
        if refreshed is not None and int(refreshed[0]) == 1:
            break
        await asyncio.sleep(0.01)

    second = second_service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert second.items == ()
    assert adapter.calls == [item.item_key]

    release.set()
    for _ in range(100):
        persisted = service.get_sync_task(task.task_id)
        if persisted.items and persisted.items[0].status == "synced":
            break
        await asyncio.sleep(0.01)
    assert persisted.items[0].status == "synced"
    assert persisted.items[0].error_code == ""
    assert adapter.calls == [item.item_key]
    for _ in range(100):
        if not service._detached_attempts:
            break
        await asyncio.sleep(0)
    assert service._detached_attempts == set()


async def test_detached_malformed_adapter_result_is_sanitized_and_completed(
    db: Database,
) -> None:
    release = asyncio.Event()
    adapter = MalformedCancellationSuppressingAdapter(release)
    service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        claim_heartbeat_interval_seconds=0.005,
        adapter_timeout_seconds=0.02,
    )
    item = SavedItemInput("bilibili", "BV1LATEMALFORMED")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    initial = await service.run_sync_task(task.task_id)
    assert initial.items[0].status == "syncing"
    assert len(service._detached_attempts) == 1
    await adapter.cancel_seen.wait()

    release.set()
    for _ in range(100):
        result = service.get_sync_task(task.task_id)
        if result.items and result.items[0].status != "syncing":
            break
        await asyncio.sleep(0.01)

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "invalid_adapter_result"
    assert "private" not in result.items[0].error_message
    for _ in range(100):
        if not service._detached_attempts:
            break
        await asyncio.sleep(0)
    assert service._detached_attempts == set()


async def test_lost_route_claim_does_not_start_adapter(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1LOSTROUTE")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    monkeypatch.setattr(db, "update_native_save_claim_route", lambda *args, **kwargs: False)

    await service.run_sync_task(task.task_id)

    assert adapter.calls == []


async def test_completed_task_lock_entry_is_released(db: Database) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1LOCKCLEANUP")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    await service.run_sync_task(task.task_id)

    assert task.task_id not in service._task_run_locks


async def test_cancelled_adapter_attempt_is_persisted_as_retryable_and_reraised(
    db: Database,
) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1CANCELLED")
    service.save_local("watch_later", item)
    task = service.create_sync_task("watch_later", [item.item_key], "manual_single")

    runner = asyncio.create_task(service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    persisted = service.get_sync_task(task.task_id).items[0]
    assert persisted.status == "failed"
    assert persisted.error_code == "interrupted"
    retry = service.create_sync_task("watch_later", [item.item_key], "manual_single")
    assert [result.item_key for result in retry.items] == [item.item_key]


async def test_stale_syncing_claim_is_reconciled_without_reexecuting_adapter(
    db: Database,
) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1STALE")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    db.conn.execute(
        """
        UPDATE native_save_states
        SET status = 'syncing', execution_id = 'dead-worker',
            last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()

    result = await service.run_sync_task(task.task_id)

    assert adapter.calls == []
    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "interrupted"


def test_polling_reconciles_stale_syncing_claim(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1STALEPOLL")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert db.claim_native_save_item("favorite", item.item_key, task.task_id, "dead-poller")
    db.conn.execute(
        """
        UPDATE native_save_states
        SET last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()

    result = service.get_sync_task(task.task_id)

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "interrupted"


def test_manual_task_creation_recovers_stale_syncing_claim(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1STALERETRY")
    service.save_local("favorite", item)
    abandoned = service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert db.claim_native_save_item(
        "favorite", item.item_key, abandoned.task_id, "dead-retry-owner"
    )
    db.conn.execute(
        """
        UPDATE native_save_states
        SET last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()

    retry = service.create_sync_task("favorite", [item.item_key], "manual_single")

    assert [result.item_key for result in retry.items] == [item.item_key]
    assert retry.task_id != abandoned.task_id


@pytest.mark.parametrize("invalid_status", ["pending", "syncing"])
async def test_adapter_nonterminal_result_is_normalized_to_failed(
    db: Database,
    invalid_status: NativeSaveStatus,
) -> None:
    adapter = FakeAdapter(
        NativeSaveCapability("bilibili", True, True, True),
        result_status=invalid_status,
    )
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", f"BV1INVALID{invalid_status.upper()}")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "invalid_adapter_result"


async def test_router_resolved_route_cannot_be_overridden_by_adapter(db: Database) -> None:
    adapter = MisroutingAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1ROUTE")
    service.save_local("watch_later", item)
    task = service.create_sync_task("watch_later", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert result.items[0].status == "synced"
    assert result.items[0].resolved_action == "watch_later"
    assert result.items[0].resolved_target == "B站稍后观看"


async def test_target_resolution_exception_is_sanitized_per_item(db: Database) -> None:
    adapter = RaisingTargetAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1TARGET")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "adapter_exception"
    assert "private" not in result.items[0].error_message


@pytest.mark.parametrize("invalid_target", [None, 123, "", "   ", "x" * 300])
async def test_invalid_target_label_is_sanitized_and_releases_item_owner(
    db: Database,
    invalid_target: object,
) -> None:
    adapter = InvalidTargetAdapter(invalid_target)
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", f"BV1TARGET{len(str(invalid_target))}")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert adapter.calls == []
    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "invalid_adapter_result"
    assert "300" not in result.items[0].error_message
    row = db.list_native_save_states_by_task(task.task_id)[0]
    assert row["status"] == "failed"
    assert row["execution_id"] == ""


async def test_immediate_cancellation_suppression_persists_late_success(db: Database) -> None:
    adapter = ImmediateCancellationSuccessAdapter(
        NativeSaveCapability("bilibili", True, True, True)
    )
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1CANCELSUCCESS")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    runner = asyncio.create_task(service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    persisted = service.get_sync_task(task.task_id)
    assert persisted.items[0].status == "synced"
    assert persisted.items[0].error_code == ""
    assert adapter.calls == [item.item_key]


async def test_unregistered_platform_is_persisted_as_unsupported(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("youtube", "video-1")
    service.save_local("watch_later", item)
    task = service.create_sync_task("watch_later", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert result.items[0].status == "unsupported"
    assert service.get_sync_task(task.task_id) == result
