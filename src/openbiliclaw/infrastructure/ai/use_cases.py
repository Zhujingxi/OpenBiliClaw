"""Adapters from feature-owned AI ports to the shared typed TaskRunner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.infrastructure.ai.tasks import (
    CANDIDATE_BATCH_ASSESSMENT_TASK,
    CHAT_RESPONSE_TASK,
    PROFILE_DELTA_TASK,
    CandidateBatchAssessmentInput,
    ChatResponseInput,
    ProfileDeltaInput,
    ProfileEvidence,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from openbiliclaw.features.activity.domain import ProfileSignal
    from openbiliclaw.features.feed.domain import CandidateAssessment, ContentItem
    from openbiliclaw.features.profile.domain import ProfileDelta, ProfileSnapshot
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

    def fail(self, run_id: UUID, *, error_kind: str) -> None:
        with self._uow_factory() as uow:
            uow.ai_runs.fail(run_id, error_kind=error_kind)
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


class TaskRunnerChatResponder:
    """Call the shared interactive lane directly; no background queue is involved."""

    def __init__(self, runner: TaskRunner) -> None:
        self._runner = runner

    async def respond(self, *, conversation_id: UUID, message: str) -> str:
        output = await self._runner.run(
            CHAT_RESPONSE_TASK,
            ChatResponseInput(conversation_id=conversation_id, message=message),
        )
        return output.content


__all__ = [
    "TaskRunnerBatchAssessor",
    "TaskRunnerChatResponder",
    "TaskRunnerProfileDeltaAI",
    "TransactionalAIRunRecorder",
]
