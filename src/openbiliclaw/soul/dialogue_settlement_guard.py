"""Actual-task-bound permit for dialogue settlement mutations.

The private context nonce is necessary but never sufficient: authorization
also requires identity equality with the one currently registered worker
``asyncio.Task`` or an explicit, queue-controlled inline delegation.  A child
task therefore cannot gain mutation authority merely by inheriting the
worker's ``ContextVar`` value.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Iterator


class DialogueSettlementMutationOutsideWorkerError(RuntimeError):
    """Raised when dialogue state mutation is attempted without worker authority."""


# The finalized contract uses this exact public exception name.  Keep the
# implementation class Ruff-compliant while exporting the specified spelling.
DialogueSettlementMutationOutsideWorker = DialogueSettlementMutationOutsideWorkerError


@dataclass(frozen=True, slots=True)
class DialogueSettlementWorkerPermit:
    """Exact worker identity and lifecycle nonce used for compare-and-clear."""

    worker_task: asyncio.Task[object]
    lifecycle_nonce: str


class DialogueSettlementGuard:
    """Own the single authorized dialogue settlement worker slot."""

    def __init__(self) -> None:
        self._registered_permit: DialogueSettlementWorkerPermit | None = None
        self._context_nonce: ContextVar[str | None] = ContextVar(
            f"dialogue_settlement_worker_nonce_{id(self)}",
            default=None,
        )
        self._delegated_task: ContextVar[asyncio.Task[object] | None] = ContextVar(
            f"dialogue_settlement_delegated_task_{id(self)}",
            default=None,
        )

    def register_worker(
        self,
        worker_task: asyncio.Task[object],
    ) -> DialogueSettlementWorkerPermit:
        """Register ``worker_task`` with a fresh lifecycle nonce.

        Registration is synchronous so revoke/register forms one event-loop
        turn with no interval containing two authorized workers.
        """
        if worker_task.done():
            raise ValueError("Cannot authorize a completed dialogue settlement worker")
        if self._registered_permit is not None:
            raise RuntimeError("A dialogue settlement worker is already registered")
        permit = DialogueSettlementWorkerPermit(
            worker_task=worker_task,
            lifecycle_nonce=uuid4().hex,
        )
        self._registered_permit = permit
        return permit

    def revoke_worker(self, permit: DialogueSettlementWorkerPermit) -> bool:
        """Revoke exactly ``permit``; stale or foreign permits are a no-op."""
        return self._clear_exact(permit)

    def clear_if_current(self, permit: DialogueSettlementWorkerPermit) -> bool:
        """Clear only when ``permit`` still owns the current worker slot."""
        return self._clear_exact(permit)

    @property
    def registered_permit(self) -> DialogueSettlementWorkerPermit | None:
        """Return the current single-slot permit for lifecycle diagnostics."""
        return self._registered_permit

    def is_current(self, permit: DialogueSettlementWorkerPermit) -> bool:
        """Return whether ``permit`` is the exact currently authorized tuple."""
        return self._is_current_permit(permit)

    def belongs_to_worker_lineage(self, permit: DialogueSettlementWorkerPermit) -> bool:
        """Return whether the current context descends from active worker execution."""
        return (
            self._is_current_permit(permit) and self._context_nonce.get() == permit.lifecycle_nonce
        )

    @contextmanager
    def activate_worker(
        self,
        permit: DialogueSettlementWorkerPermit,
    ) -> Iterator[None]:
        """Carry ``permit``'s nonce in its actual worker task context."""
        if not self._is_current_permit(permit) or asyncio.current_task() is not permit.worker_task:
            raise DialogueSettlementMutationOutsideWorker(
                "Dialogue settlement worker activation does not match the registered task"
            )
        token = self._context_nonce.set(permit.lifecycle_nonce)
        try:
            yield
        finally:
            self._context_nonce.reset(token)

    @contextmanager
    def activate_inline_lineage_task(
        self,
        permit: DialogueSettlementWorkerPermit,
    ) -> Iterator[None]:
        """Authorize one lineage task only while it runs an inline nested handler."""
        if not self.belongs_to_worker_lineage(permit):
            raise DialogueSettlementMutationOutsideWorker(
                "Inline dialogue settlement delegation requires active worker lineage"
            )
        current_task = asyncio.current_task()
        if current_task is None:
            raise DialogueSettlementMutationOutsideWorker(
                "Inline dialogue settlement delegation requires an asyncio task"
            )
        token = self._delegated_task.set(current_task)
        try:
            yield
        finally:
            self._delegated_task.reset(token)

    @contextmanager
    def dialogue_settlement_worker(
        self,
        worker_task: asyncio.Task[object],
    ) -> Iterator[DialogueSettlementWorkerPermit]:
        """Register and activate the current actual worker task for one lifecycle."""
        if asyncio.current_task() is not worker_task:
            raise DialogueSettlementMutationOutsideWorker(
                "Dialogue settlement worker context must run in the registered task"
            )
        permit = self.register_worker(worker_task)
        try:
            with self.activate_worker(permit):
                yield permit
        finally:
            self.clear_if_current(permit)

    def require_dialogue_settlement_worker(self) -> None:
        """Fail closed unless both actual task identity and nonce match."""
        permit = self._registered_permit
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        if (
            permit is None
            or self._context_nonce.get() != permit.lifecycle_nonce
            or (
                current_task is not permit.worker_task
                and current_task is not self._delegated_task.get()
            )
        ):
            raise DialogueSettlementMutationOutsideWorker(
                "Dialogue settlement mutation requires the active worker task"
            )

    def _clear_exact(self, permit: DialogueSettlementWorkerPermit) -> bool:
        if not self._is_current_permit(permit):
            return False
        self._registered_permit = None
        return True

    def _is_current_permit(self, permit: DialogueSettlementWorkerPermit) -> bool:
        current = self._registered_permit
        return (
            current is not None
            and current.worker_task is permit.worker_task
            and current.lifecycle_nonce == permit.lifecycle_nonce
        )


_DEFAULT_GUARD = DialogueSettlementGuard()


def default_dialogue_settlement_guard() -> DialogueSettlementGuard:
    """Return the process-wide single-slot dialogue mutation guard."""
    return _DEFAULT_GUARD


@contextmanager
def dialogue_settlement_worker(
    worker_task: asyncio.Task[object],
) -> Iterator[DialogueSettlementWorkerPermit]:
    """Register and activate ``worker_task`` on the process-default guard."""
    with _DEFAULT_GUARD.dialogue_settlement_worker(worker_task) as permit:
        yield permit


def require_dialogue_settlement_worker() -> None:
    """Require mutation authority from the process-default guard."""
    _DEFAULT_GUARD.require_dialogue_settlement_worker()
