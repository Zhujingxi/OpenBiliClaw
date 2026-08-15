from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel

from openbiliclaw.ai.providers.embeddings import (
    EmbeddingModelInfo,
    EmbeddingResult,
    EmbeddingUsage,
)
from openbiliclaw.ai.providers.embeddings.index import EmbeddingIndex
from openbiliclaw.ai.runtime.capabilities import ModelCapabilities
from openbiliclaw.ai.runtime.execution import AIRuntime
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
from openbiliclaw.composition.jobs import RecommendationPipeline
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.hosts.api.media_proxy import MediaProxyError, MediaResult
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.recommendation.inspection import (
    INSPECTION_AGENT,
    FrameAcquirer,
    InspectionResult,
    InspectionService,
)
from openbiliclaw.recommendation.policy_journal import SqlitePolicyJournal
from tests.recommendation.test_prefilter_expression import candidate

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class Images:
    def __init__(self, available: set[str]) -> None:
        self.available = available
        self.urls: list[str] = []

    async def fetch(self, url: str) -> MediaResult:
        self.urls.append(url)
        if url not in self.available:
            raise MediaProxyError(404, "missing")
        return MediaResult(b"image", "image/jpeg")


async def test_frame_acquisition_degrades_storyboard_to_cover_to_none() -> None:
    item = candidate("frames")

    async def storyboard(_item: object) -> tuple[str, ...]:
        return ("https://cdn.example/frame-1.jpg", "https://cdn.example/frame-2.jpg")

    async def cover_source(_item: object) -> str:
        return "https://cdn.example/cover.jpg"

    storyboards = Images({"https://cdn.example/frame-1.jpg"})
    assert len(await FrameAcquirer(storyboards, storyboard, cover_source).acquire(item)) == 1
    assert storyboards.urls == [
        "https://cdn.example/frame-1.jpg",
        "https://cdn.example/frame-2.jpg",
    ]

    cover = Images({"https://cdn.example/cover.jpg"})
    assert len(await FrameAcquirer(cover, storyboard, cover_source).acquire(item)) == 1
    assert cover.urls[-1] == "https://cdn.example/cover.jpg"

    none = Images(set())
    assert await FrameAcquirer(none, storyboard, cover_source).acquire(item) == ()


def test_inspection_result_schema_and_route_require_vision() -> None:
    result = InspectionResult(
        actual_topic="Compiler optimization",
        quality=0.8,
        title_mismatch=False,
        summary="A substantive compiler optimization walkthrough.",
    )
    assert result.quality == 0.8
    with pytest.raises(ValidationError):
        InspectionResult(
            actual_topic="topic",
            quality=1.1,
            title_mismatch=False,
            summary="summary",
        )
    model = ConfiguredModel(
        "text-only",
        "test",
        TestModel(),
        ModelCapabilities(structured_output=True, context_tokens=4096),
    )
    with pytest.raises(ValueError, match="incompatible"):
        ModelRoute(INSPECTION_AGENT.agent_id, INSPECTION_AGENT.requirements, (model,))


async def _service(
    tmp_path: Path,
    *,
    model: TestModel,
    images: Images,
) -> tuple[InspectionService, SqliteDatabase, EmbeddingIndex]:
    path = tmp_path / "inspection.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    configured = ConfiguredModel(
        "vision-test",
        "test",
        model,
        ModelCapabilities(vision=True, structured_output=True, context_tokens=4096),
    )
    runtime = AIRuntime(
        RouteTable(
            (ModelRoute(INSPECTION_AGENT.agent_id, INSPECTION_AGENT.requirements, (configured,)),)
        ),
        ResourceBudget("model", 1),
    )
    info = EmbeddingModelInfo(
        provider="test", model="embedding", dimensions=2, normalized=True, version="1"
    )

    class Embeddings:
        async def embed_documents(self, texts: tuple[str, ...]) -> EmbeddingResult:
            return EmbeddingResult(
                vectors=tuple((1.0, 0.0) for _ in texts),
                usage=EmbeddingUsage(requests=1, input_tokens=len(texts)),
                model=info,
            )

        async def embed_query(self, text: str) -> tuple[float, ...]:
            del text
            return (1.0, 0.0)

    index = EmbeddingIndex(database, Embeddings(), info, clock=lambda: NOW)

    async def cover_source(_item: object) -> str:
        return "https://cdn.example/cover.jpg"

    return (
        InspectionService(
            runtime,
            FrameAcquirer(images, cover=cover_source),
            SqlitePolicyJournal(database),
            index,
            clock=lambda: NOW,
        ),
        database,
        index,
    )


async def test_cache_hit_skips_model_and_persists_embedded_summary(tmp_path: Path) -> None:
    output = InspectionResult(
        actual_topic="Actual topic",
        quality=0.9,
        title_mismatch=True,
        summary="The title overstates a concise but useful technical explanation.",
    )
    model = TestModel(custom_output_args=output.model_dump(mode="json"))
    item = candidate("cached")
    service, database, index = await _service(
        tmp_path, model=model, images=Images({"https://cdn.example/cover.jpg"})
    )
    try:
        pipeline = object.__new__(RecommendationPipeline)
        cast("Any", pipeline)._inspections = service
        await pipeline._inspect_shortlist((item,), episode_id="replenishment:test", brief=None)
        model.custom_output_args = {"invalid": "a second model call would fail"}
        assert await service.inspect(item, quality_rubric="A changed rubric.") == output
        assert await database.fetch_value("SELECT count(*) FROM policy_inspections") == 1
        assert await index.vector("candidate", service.embedding_ref(item)) == (1.0, 0.0)
    finally:
        await database.close()


async def test_no_frames_and_model_outage_fail_open() -> None:
    item = candidate("survives")
    runtime = AsyncMock()
    journal = AsyncMock()
    journal.load_inspection.side_effect = KeyError
    service = InspectionService(
        cast("Any", runtime),
        FrameAcquirer(Images(set())),
        cast("Any", journal),
        None,
        clock=lambda: NOW,
    )
    assert await service.inspect(item) is None
    runtime.run.assert_not_awaited()

    async def cover_source(_item: object) -> str:
        return "https://cdn.example/cover.jpg"

    service = InspectionService(
        cast("Any", runtime),
        FrameAcquirer(Images({"https://cdn.example/cover.jpg"}), cover=cover_source),
        cast("Any", journal),
        None,
        clock=lambda: NOW,
    )
    runtime.run.side_effect = RuntimeError("model outage")
    assert await service.inspect(item) is None


async def test_shortlist_inspection_is_bounded_and_failure_does_not_escape() -> None:
    pipeline = object.__new__(RecommendationPipeline)
    inspections = AsyncMock()
    inspections.inspect.side_effect = RuntimeError("vision provider down")
    cast("Any", pipeline)._inspections = inspections

    await pipeline._inspect_shortlist(
        tuple(candidate(f"item-{index}") for index in range(7)),
        episode_id="replenishment:outage",
        brief=None,
    )

    assert inspections.inspect.await_count == 5
