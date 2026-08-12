"""Safe Assistant pending-action rendering and deterministic confirmation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from .models import PendingActionSummary


class ConfirmActionWorkflow(Protocol):
    async def __call__(self, pending_action_id: str, user_id: str) -> object: ...


def render_pending_action(action: PendingActionSummary) -> PendingActionSummary:
    """Return only exact safe effect and expiry; no executable model payload."""
    return action


@dataclass(slots=True)
class ActionConfirmation:
    workflow: ConfirmActionWorkflow
    clock: Callable[[], datetime]
    _results: dict[tuple[str, str], object] = field(default_factory=dict, init=False)

    async def confirm(self, action: PendingActionSummary, *, user_id: str) -> object:
        current_time = self.clock()
        if action.expires_at <= current_time:
            raise ApplicationError(ApplicationErrorCode.EXPIRED, "pending action expired")
        key = (action.pending_action_id, user_id)
        if key in self._results:
            return self._results[key]
        result = await self.workflow(action.pending_action_id, user_id)
        self._results[key] = result
        return result
