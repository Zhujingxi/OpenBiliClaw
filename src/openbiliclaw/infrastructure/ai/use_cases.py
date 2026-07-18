"""Adapters from feature-owned AI ports to the shared typed TaskRunner."""

from __future__ import annotations

import math
from contextlib import aclosing
from typing import TYPE_CHECKING

from openbiliclaw.infrastructure.ai.tasks import (
    CANDIDATE_BATCH_ASSESSMENT_TASK,
    CHAT_RESPONSE_TASK,
    KEYWORD_GENERATION_TASK,
    PROFILE_DELTA_TASK,
    RECOMMENDATION_EXPLANATION_TASK,
    CandidateBatchAssessmentInput,
    ChatContextTurn,
    ChatResponseInput,
    KeywordGenerationInput,
    ProfileDeltaInput,
    ProfileEvidence,
    RecommendationExplanationInput,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from uuid import UUID

    from openbiliclaw.features.activity.domain import ProfileSignal
    from openbiliclaw.features.chat.domain import ChatTurn
    from openbiliclaw.features.chat.service import ChatResponseDelta
    from openbiliclaw.features.feed.domain import CandidateAssessment, ContentItem
    from openbiliclaw.features.profile.domain import ProfileDelta, ProfileSnapshot
    from openbiliclaw.infrastructure.ai.embedding import EmbeddingService
    from openbiliclaw.infrastructure.ai.runner import TaskRunner
    from openbiliclaw.infrastructure.database.uow import UnitOfWork


class TransactionalAIRunRecorder:
    """Commit each TaskRunner lifecycle transition through a fresh UoW."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def start(self, *, task_name: str, model_alias: str) -> UUID:
        with self._uow_factory() as uow:
            run_id = uow.ai_runs.start(task_name=task_name, model_alias=model_alias)
            uow.commit()
        return run_id

    def succeed(self, run_id: UUID, *, usage: dict[str, int]) -> None:
        with self._uow_factory() as uow:
            uow.ai_runs.succeed(run_id, usage=usage)
            uow.commit()

    def fail(
        self,
        run_id: UUID,
        *,
        error_kind: str,
        usage: dict[str, int] | None = None,
    ) -> None:
        with self._uow_factory() as uow:
            uow.ai_runs.fail(run_id, error_kind=error_kind, usage=usage)
            uow.commit()


class TaskRunnerProfileDeltaAI:
    """Generate a typed profile delta through the analysis lane."""

    def __init__(self, runner: TaskRunner) -> None:
        self._runner = runner

    async def propose(
        self, profile: ProfileSnapshot, signals: tuple[ProfileSignal, ...]
    ) -> ProfileDelta:
        evidence = tuple(
            ProfileEvidence(
                id=evidence_id,
                content=(
                    f"facet={signal.facet}; value={signal.value}; "
                    f"weight={signal.weight}; confidence={signal.confidence}"
                ),
            )
            for signal in signals
            for evidence_id in signal.evidence_ids
        )
        return await self._runner.run(
            PROFILE_DELTA_TASK,
            ProfileDeltaInput(profile=profile, evidence=evidence),
        )


class TaskRunnerBatchAssessor:
    """Assess one bounded candidate collection in exactly one TaskRunner call."""

    def __init__(self, runner: TaskRunner) -> None:
        self._runner = runner

    async def assess_batch(
        self,
        profile: ProfileSnapshot,
        content: tuple[ContentItem, ...],
    ) -> tuple[CandidateAssessment, ...]:
        from openbiliclaw.features.feed.domain import CandidateAssessment

        output = await self._runner.run(
            CANDIDATE_BATCH_ASSESSMENT_TASK,
            CandidateBatchAssessmentInput(profile=profile, content=content),
        )
        return tuple(
            CandidateAssessment(
                content_id=item.content_id,
                profile_revision=item.profile_revision,
                relevance=item.relevance,
                quality=item.quality,
                novelty=item.novelty,
                risk=item.risk,
                topics=item.topics,
                explanation=item.explanation,
            )
            for item in output.assessments
        )


class TaskRunnerKeywordPlanner:
    """Generate bounded, source-neutral discovery queries through the analysis lane."""

    def __init__(self, runner: TaskRunner) -> None:
        self._runner = runner

    async def plan(self, profile: ProfileSnapshot, *, limit: int) -> tuple[str, ...]:
        output = await self._runner.run(
            KEYWORD_GENERATION_TASK,
            KeywordGenerationInput(profile=profile, limit=limit),
        )
        return output.keywords


class TaskRunnerRecommendationExplainer:
    """Generate one grounded explanation only after deterministic admission."""

    def __init__(self, runner: TaskRunner) -> None:
        self._runner = runner

    async def explain(
        self,
        profile: ProfileSnapshot,
        content: ContentItem,
        assessment: CandidateAssessment,
    ) -> str:
        output = await self._runner.run(
            RECOMMENDATION_EXPLANATION_TASK,
            RecommendationExplanationInput(
                profile=profile,
                content=content,
                assessment=assessment,
            ),
        )
        return output.explanation


class EmbeddingCandidateNoveltyScorer:
    """Use the dedicated embedding alias to reward semantic diversity in one batch."""

    def __init__(self, service: EmbeddingService) -> None:
        self._service = service

    async def score(self, content: tuple[ContentItem, ...]) -> dict[UUID, float]:
        if not content:
            return {}
        batch = await self._service.embed(tuple(_embedding_text(item) for item in content))
        scores: dict[UUID, float] = {}
        for index, item in enumerate(content):
            similarities = (
                _cosine_similarity(batch.vectors[index], other)
                for other_index, other in enumerate(batch.vectors)
                if other_index != index
            )
            closest = max(similarities, default=0.0)
            scores[item.id] = max(0.0, min(1.0, 1.0 - closest))
        return scores


def _embedding_text(content: ContentItem) -> str:
    return " | ".join(part for part in (content.title, content.summary) if part.strip())


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


class TaskRunnerChatResponder:
    """Call the shared interactive lane directly; no background queue is involved."""

    def __init__(self, runner: TaskRunner) -> None:
        self._runner = runner

    async def stream(
        self,
        *,
        conversation_id: UUID,
        message: str,
        history: tuple[ChatTurn, ...],
    ) -> AsyncGenerator[ChatResponseDelta]:
        from openbiliclaw.features.chat.service import ChatResponseDelta

        previous = ""
        task_input = ChatResponseInput(
            conversation_id=conversation_id,
            message=message,
            history=tuple(
                ChatContextTurn(role=turn.role, content=turn.content) for turn in history
            ),
        )
        async with aclosing(self._runner.stream(CHAT_RESPONSE_TASK, task_input)) as outputs:
            async for item in outputs:
                content = item.output.content
                if not content.startswith(previous):
                    raise RuntimeError("streamed chat output is not monotonic")
                delta = content[len(previous) :]
                previous = content
                if delta:
                    yield ChatResponseDelta(content=delta, ai_run_id=item.run_id)


__all__ = [
    "EmbeddingCandidateNoveltyScorer",
    "TaskRunnerBatchAssessor",
    "TaskRunnerChatResponder",
    "TaskRunnerKeywordPlanner",
    "TaskRunnerProfileDeltaAI",
    "TaskRunnerRecommendationExplainer",
    "TransactionalAIRunRecorder",
]
