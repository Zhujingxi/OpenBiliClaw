from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from .models import (
    NativeSaveAction,
    NativeSaveResult,
    NativeSaveStatus,
    SavedItemInput,
    SavedListKind,
    SavedMembershipResult,
    SavedSyncBatchResult,
)
from .router import UnsupportedNativeSaveError

if TYPE_CHECKING:
    from openbiliclaw.storage.database import Database

    from .router import NativeSaveRouter

TaskStarter = Callable[[str, Coroutine[Any, Any, Any]], asyncio.Task[Any]]

_ACTIVE_STATUSES = frozenset({"pending"})
_MAX_ADAPTER_TIMEOUT_SECONDS = 240.0
_TERMINAL_ADAPTER_STATUSES = frozenset(
    {
        "synced",
        "already_synced",
        "login_required",
        "unsupported",
        "rate_limited",
        "extension_required",
        "failed",
    }
)


@dataclass(slots=True)
class _TaskRunLockEntry:
    lock: asyncio.Lock
    users: int = 0


class _NativeSaveClaimLostError(RuntimeError):
    """The execution lease no longer belongs to this adapter call."""


class SavedSyncService:
    """Persist local membership first, then coordinate optional native saves."""

    def __init__(
        self,
        database: Database,
        router: NativeSaveRouter,
        task_starter: TaskStarter | None = None,
        *,
        claim_heartbeat_interval_seconds: float = 30.0,
        adapter_timeout_seconds: float = 240.0,
    ) -> None:
        self._database = database
        self._router = router
        self._task_starter = task_starter
        self._claim_heartbeat_interval_seconds = max(
            0.001,
            float(claim_heartbeat_interval_seconds),
        )
        self._adapter_timeout_seconds = min(
            _MAX_ADAPTER_TIMEOUT_SECONDS,
            max(0.001, float(adapter_timeout_seconds)),
        )
        self._task_run_locks: dict[str, _TaskRunLockEntry] = {}

    def save_local(
        self,
        list_kind: SavedListKind,
        item: SavedItemInput,
        note: str = "",
        auto_sync: bool = False,
    ) -> SavedMembershipResult:
        """Commit a local save before optionally creating a native-sync task."""
        self._database.upsert_saved_membership(list_kind, item, note)
        current = self._database.get_saved_membership(list_kind, item.item_key)
        if current is None:  # pragma: no cover - membership vanished after committed write
            raise RuntimeError("saved membership disappeared after upsert")
        if not auto_sync:
            if not str(current["requested_action"]):
                self._database.upsert_native_save_state(
                    list_kind,
                    item.item_key,
                    requested_action=list_kind,
                    status="pending",
                )
                current = self._database.get_saved_membership(list_kind, item.item_key)
                if current is None:  # pragma: no cover - state parent vanished externally
                    raise RuntimeError("saved membership disappeared after state upsert")
            return SavedMembershipResult(
                saved=True,
                item_key=item.item_key,
                sync_status=cast("NativeSaveStatus", str(current["sync_status"])),
                sync_task_id=str(current["sync_task_id"]),
            )

        created = self.create_sync_task(list_kind, [item.item_key], "auto")
        if created.items:
            return SavedMembershipResult(
                saved=True,
                item_key=item.item_key,
                sync_status="pending",
                sync_task_id=created.task_id,
            )

        row = self._database.get_saved_membership(list_kind, item.item_key)
        status = cast("NativeSaveStatus", row["sync_status"] if row is not None else "pending")
        task_id = str(row["sync_task_id"]) if row is not None else ""
        return SavedMembershipResult(
            saved=True,
            item_key=item.item_key,
            sync_status=status,
            sync_task_id=task_id,
        )

    def create_sync_task(
        self,
        list_kind: SavedListKind,
        item_keys: Sequence[str],
        trigger: str,
    ) -> SavedSyncBatchResult:
        """Persist one pending task for selected eligible memberships."""
        task_id = str(uuid.uuid4())
        selected_keys: Sequence[str] | None
        if item_keys:
            selected_keys = tuple(
                dict.fromkeys(item_key.strip() for item_key in item_keys if item_key.strip())
            )
            if not selected_keys:
                raise ValueError("item_keys must contain at least one non-blank key")
        else:
            selected_keys = None
        self._database.release_stale_unstarted_native_sync_tasks(list_kind)
        claimed_keys = self._database.claim_native_sync_task(
            list_kind,
            selected_keys,
            task_id,
        )
        items: list[NativeSaveResult] = []
        for item_key in claimed_keys:
            items.append(
                NativeSaveResult(
                    item_key=item_key,
                    status="pending",
                    resolved_action=list_kind,
                    resolved_target="",
                )
            )

        result = SavedSyncBatchResult(task_id=task_id, items=tuple(items))
        if items and self._task_starter is not None:
            coro = self.run_sync_task(task_id)
            try:
                self._task_starter(f"saved-sync:{trigger}:{task_id}", coro)
            except Exception:
                coro.close()
                self._database.release_native_sync_task(task_id)
                raise
        return result

    async def run_sync_task(self, task_id: str) -> SavedSyncBatchResult:
        """Execute persisted task rows sequentially within each platform group."""
        entry = self._task_run_locks.setdefault(
            task_id,
            _TaskRunLockEntry(lock=asyncio.Lock()),
        )
        entry.users += 1
        try:
            async with entry.lock:
                self._database.mark_native_sync_task_started(task_id)
                self._database.reconcile_stale_native_save_claims(task_id)
                rows = self._database.list_native_save_states_by_task(task_id)
                grouped_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for row in rows:
                    grouped_rows[str(row["source_platform"])].append(row)

                await asyncio.gather(
                    *(self._run_platform_group(group) for group in grouped_rows.values())
                )
                return self.get_sync_task(task_id)
        finally:
            entry.users -= 1
            if entry.users == 0 and self._task_run_locks.get(task_id) is entry:
                del self._task_run_locks[task_id]

    def get_sync_task(self, task_id: str) -> SavedSyncBatchResult:
        """Reconstruct a batch entirely from persisted native-save states."""
        rows = self._database.list_native_save_states_by_task(task_id)
        items = tuple(self._result_from_row(row) for row in rows)
        return SavedSyncBatchResult(task_id=task_id, items=items)

    async def _run_platform_group(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if str(row["status"]) not in _ACTIVE_STATUSES:
                continue
            await self._run_item(row)

    async def _run_item(self, row: dict[str, Any]) -> None:
        item = self._item_from_row(row)
        list_kind = cast("SavedListKind", str(row["list_kind"]))
        requested_action = cast("NativeSaveAction", str(row["requested_action"]))
        task_id = str(row["task_id"])
        execution_id = str(uuid.uuid4())
        if not self._database.claim_native_save_item(
            list_kind,
            item.item_key,
            task_id,
            execution_id,
        ):
            return
        try:
            adapter, route = self._router.route(item.platform, requested_action)
        except UnsupportedNativeSaveError:
            self._persist_result(
                list_kind,
                NativeSaveResult(
                    item_key=item.item_key,
                    status="unsupported",
                    resolved_action=requested_action,
                    resolved_target="",
                    error_code="unsupported",
                    error_message="Native save is unsupported for this platform or action",
                ),
                task_id=task_id,
                execution_id=execution_id,
                requested_action=requested_action,
            )
            return
        except Exception:
            self._persist_result(
                list_kind,
                NativeSaveResult(
                    item_key=item.item_key,
                    status="failed",
                    resolved_action=requested_action,
                    resolved_target="",
                    error_code="adapter_exception",
                    error_message="Native save failed",
                ),
                task_id=task_id,
                execution_id=execution_id,
                requested_action=requested_action,
            )
            return

        route_persisted = self._database.update_native_save_claim_route(
            list_kind,
            item.item_key,
            task_id,
            execution_id,
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
        )
        if not route_persisted:
            return
        try:
            adapter_result = await self._save_with_live_claim(
                adapter.save(item, route),
                list_kind=list_kind,
                item_key=item.item_key,
                task_id=task_id,
                execution_id=execution_id,
            )
            if adapter_result.status not in _TERMINAL_ADAPTER_STATUSES:
                result = NativeSaveResult(
                    item_key=item.item_key,
                    status="failed",
                    resolved_action=route.resolved_action,
                    resolved_target=route.resolved_target,
                    error_code="invalid_adapter_result",
                    error_message="Native save adapter returned a nonterminal status",
                )
            else:
                result = NativeSaveResult(
                    item_key=item.item_key,
                    status=adapter_result.status,
                    resolved_action=route.resolved_action,
                    resolved_target=route.resolved_target,
                    error_code=adapter_result.error_code,
                    error_message=adapter_result.error_message,
                )
        except asyncio.CancelledError:
            self._persist_result(
                list_kind,
                NativeSaveResult(
                    item_key=item.item_key,
                    status="failed",
                    resolved_action=route.resolved_action,
                    resolved_target=route.resolved_target,
                    error_code="interrupted",
                    error_message="Native save was interrupted",
                ),
                task_id=task_id,
                execution_id=execution_id,
                requested_action=requested_action,
            )
            raise
        except _NativeSaveClaimLostError:
            return
        except TimeoutError:
            result = NativeSaveResult(
                item_key=item.item_key,
                status="failed",
                resolved_action=route.resolved_action,
                resolved_target=route.resolved_target,
                error_code="adapter_timeout",
                error_message="Native save timed out",
            )
        except Exception:
            result = NativeSaveResult(
                item_key=item.item_key,
                status="failed",
                resolved_action=route.resolved_action,
                resolved_target=route.resolved_target,
                error_code="adapter_exception",
                error_message="Native save failed",
            )
        self._persist_result(
            list_kind,
            result,
            task_id=task_id,
            execution_id=execution_id,
            requested_action=requested_action,
        )

    async def _save_with_live_claim(
        self,
        save_coro: Coroutine[Any, Any, NativeSaveResult],
        *,
        list_kind: SavedListKind,
        item_key: str,
        task_id: str,
        execution_id: str,
    ) -> NativeSaveResult:
        heartbeat = asyncio.create_task(
            self._heartbeat_claim(list_kind, item_key, task_id, execution_id)
        )
        save_task = asyncio.create_task(save_coro)
        try:
            async with asyncio.timeout(self._adapter_timeout_seconds):
                done, _ = await asyncio.wait(
                    {heartbeat, save_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if heartbeat in done:
                    await heartbeat
                    raise _NativeSaveClaimLostError
                return await save_task
        finally:
            heartbeat.cancel()
            save_task.cancel()
            await asyncio.gather(heartbeat, save_task, return_exceptions=True)

    async def _heartbeat_claim(
        self,
        list_kind: SavedListKind,
        item_key: str,
        task_id: str,
        execution_id: str,
    ) -> None:
        while True:
            await asyncio.sleep(self._claim_heartbeat_interval_seconds)
            if not self._database.heartbeat_native_save_claim(
                list_kind,
                item_key,
                task_id,
                execution_id,
            ):
                return

    def _persist_result(
        self,
        list_kind: SavedListKind,
        result: NativeSaveResult,
        *,
        task_id: str,
        execution_id: str,
        requested_action: NativeSaveAction,
    ) -> None:
        self._database.complete_native_save_claim(
            list_kind,
            result.item_key,
            task_id,
            execution_id,
            requested_action=requested_action,
            resolved_action=result.resolved_action,
            resolved_target=result.resolved_target,
            status=result.status,
            last_error_code=result.error_code,
            last_error_message=result.error_message,
        )

    @staticmethod
    def _item_from_row(row: dict[str, Any]) -> SavedItemInput:
        return SavedItemInput(
            source_platform=str(row["source_platform"]),
            content_id=str(row["content_id"]),
            content_url=str(row["content_url"]),
            content_type=str(row["content_type"]),
            title=str(row["title"]),
            author_name=str(row["author_name"]),
            cover_url=str(row["cover_url"]),
        )

    @staticmethod
    def _result_from_row(row: dict[str, Any]) -> NativeSaveResult:
        requested_action = cast("NativeSaveAction", str(row["requested_action"]))
        resolved_action = cast(
            "NativeSaveAction",
            str(row["resolved_action"]) or requested_action,
        )
        return NativeSaveResult(
            item_key=str(row["item_key"]),
            status=cast("NativeSaveStatus", str(row["status"])),
            resolved_action=resolved_action,
            resolved_target=str(row["resolved_target"]),
            error_code=str(row["last_error_code"]),
            error_message=str(row["last_error_message"]),
        )
