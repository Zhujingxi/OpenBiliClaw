"""Optional expression with deterministic safe-copy fallback."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ..models import ExpressionRecord, SelectionRecord
from .agent import ExpressionBatch

if TYPE_CHECKING:
    from datetime import datetime

Generator = Callable[[tuple[SelectionRecord, ...]], Awaitable[tuple[ExpressionBatch, str]]]


class ExpressionService:
    def __init__(self, generate: Generator | None, clock: Callable[[], datetime]) -> None:
        self.generate = generate
        self.clock = clock

    async def express(
        self, selections: tuple[SelectionRecord, ...]
    ) -> tuple[ExpressionRecord, ...]:
        output = None
        model = None
        if self.generate:
            with contextlib.suppress(Exception):
                output, model = await self.generate(selections)
        if output is None or tuple(x.recommendation_id for x in output.items) != tuple(
            x.recommendation_id for x in selections
        ):
            return tuple(
                ExpressionRecord(
                    recommendation_id=x.recommendation_id,
                    reason="Recommended for relevance and freshness.",
                    tone="neutral",
                    generated_at=self.clock(),
                )
                for x in selections
            )
        return tuple(
            ExpressionRecord(
                recommendation_id=x.recommendation_id,
                reason=x.reason,
                tone=x.tone,
                model_instance=model,
                generated_at=self.clock(),
            )
            for x in output.items
        )
