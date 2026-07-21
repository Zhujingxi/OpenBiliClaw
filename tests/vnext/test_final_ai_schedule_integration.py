"""Final integration contracts for production AI tasks and durable schedules."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from openbiliclaw.features.feed.domain import CandidateAssessment, ContentItem
from openbiliclaw.features.profile.domain import ProfileSnapshot
from openbiliclaw.features.system.domain import UserSettings
from openbiliclaw.infrastructure.ai.embedding import EmbeddingBatch, EmbeddingNamespace
from openbiliclaw.infrastructure.ai.use_cases import (
    EmbeddingCandidateNoveltyScorer,
    TaskRunnerKeywordPlanner,
    TaskRunnerRecommendationExplainer,
)


class _TaskRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def run(self, spec: Any, raw_input: Any) -> Any:
        self.calls.append((spec.name, raw_input))
        if spec.name == "keyword_generation":
            from openbiliclaw.infrastructure.ai.tasks import KeywordGenerationOutput

            return KeywordGenerationOutput(keywords=("typed discovery", "modular Python"))
        if spec.name == "recommendation_explanation":
            from openbiliclaw.infrastructure.ai.tasks import RecommendationExplanationOutput

            return RecommendationExplanationOutput(explanation="Typed Python architecture")
        raise AssertionError(spec.name)


async def test_task_runner_adapters_execute_keyword_and_explanation_tasks() -> None:
    runner = _TaskRunner()
    profile = ProfileSnapshot(revision=2, narrative="Typed Python")
    content = ContentItem(
        source_id="bilibili",
        external_id="typed",
        url="https://example.com/typed",
        title="Typed Python architecture",
    )
    assessment = CandidateAssessment(
        content_id=content.id,
        profile_revision=2,
        relevance=1,
        quality=1,
        novelty=1,
        risk=0,
    )

    keywords = await TaskRunnerKeywordPlanner(runner).plan(profile, limit=2)  # type: ignore[arg-type]
    explanation = await TaskRunnerRecommendationExplainer(runner).explain(  # type: ignore[arg-type]
        profile, content, assessment
    )

    assert keywords == ("typed discovery", "modular Python")
    assert explanation == "Typed Python architecture"
    assert [name for name, _input in runner.calls] == [
        "keyword_generation",
        "recommendation_explanation",
    ]


class _EmbeddingService:
    def __init__(self) -> None:
        self.texts: tuple[str, ...] = ()

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        self.texts = texts
        return EmbeddingBatch(
            vectors=((1.0, 0.0), (0.99, 0.01), (0.0, 1.0)),
            namespace=EmbeddingNamespace(vector_dimension=2, profile_version="feed-v1"),
        )


async def test_embedding_candidate_novelty_is_a_real_bounded_production_use_case() -> None:
    service = _EmbeddingService()
    content = tuple(
        ContentItem(
            source_id="bilibili",
            external_id=str(index),
            url=f"https://example.com/{index}",
            title=title,
        )
        for index, title in enumerate(("same a", "same b", "different"))
    )

    scores = await EmbeddingCandidateNoveltyScorer(service).score(content)  # type: ignore[arg-type]

    assert service.texts == ("same a", "same b", "different")
    assert scores[content[2].id] > scores[content[0].id]
    assert set(scores) == {item.id for item in content}


def test_settings_expose_all_schedules_and_reject_task_alias_outside_declared_lane() -> None:
    settings = UserSettings()

    assert settings.schedules.model_dump() == {
        "source_sync_interval_minutes": 30,
        "profile_projection_interval_minutes": 10,
        "feed_replenishment_interval_minutes": 5,
        "cleanup_interval_minutes": 1440,
    }
    assert "candidate_assessment" not in settings.tasks
    with pytest.raises(ValidationError, match="analysis lane requires model alias obc-analysis"):
        UserSettings.model_validate(
            {"tasks": {"keyword_generation": {"model_alias": "obc-interactive"}}}
        )
    with pytest.raises(
        ValidationError,
        match="analysis lane requires model alias obc-analysis",
    ):
        UserSettings.model_validate(
            {"tasks": {"recommendation_explanation": {"model_alias": "obc-interactive"}}}
        )
