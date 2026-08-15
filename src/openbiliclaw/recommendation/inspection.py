"""Shortlist-only visual inspection, kept separate from batched evaluation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from pydantic import ConfigDict, Field, JsonValue
from pydantic_ai import Agent, BinaryContent

from openbiliclaw.ai.runtime.budgets import RunPolicy
from openbiliclaw.ai.runtime.capabilities import AgentId, ModelRequirements
from openbiliclaw.ai.runtime.execution import AgentRunRequest, AIRuntime
from openbiliclaw.ai.runtime.history import sanitize_untrusted_text
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.recommendation.models import Candidate, record_identity
from openbiliclaw.recommendation.policy_journal import InspectionJournal, JournalInspection

if TYPE_CHECKING:
    from openbiliclaw.ai.providers.embeddings.index import EmbeddingIndex


class InspectionResult(StrictBaseModel):
    """Model judgment of visible content rather than title metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actual_topic: str = Field(min_length=1, max_length=500)
    quality: float = Field(ge=0, le=1)
    title_mismatch: bool
    summary: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True, slots=True)
class InspectionAgentDefinition:
    agent_id: AgentId
    agent: Agent[None, InspectionResult]
    requirements: ModelRequirements
    policy: RunPolicy


INSPECTION_PROMPT_VERSION = 1

INSPECTION_AGENT = InspectionAgentDefinition(
    agent_id=AgentId("recommendation.inspect"),
    agent=Agent(
        output_type=InspectionResult,
        instructions=(
            "Judge only what is visibly supported by the supplied sampled images. "
            "Compare the visible content with the title and return the actual topic, "
            "a quality score from 0 to 1, title mismatch, and a concise factual summary."
        ),
    ),
    requirements=ModelRequirements(vision=True, structured_output=True, context_tokens=4096),
    policy=RunPolicy(
        request_limit=2,
        input_tokens_limit=4096,
        output_tokens_limit=1024,
        total_tokens_limit=5120,
        tool_calls_limit=1,
        timeout_seconds=30,
        retries=0,
    ),
)


class FetchedImage(Protocol):
    content: bytes
    content_type: str


class ImageFetcher(Protocol):
    async def fetch(self, url: str) -> object: ...


StoryboardSource: TypeAlias = Callable[[Candidate], Awaitable[tuple[str, ...]]]
CoverSource: TypeAlias = Callable[[Candidate], Awaitable[str | None]]


class FrameAcquirer:
    """Acquire bounded storyboard images, falling back to the declared cover image."""

    def __init__(
        self,
        fetcher: ImageFetcher,
        storyboard: StoryboardSource | None = None,
        cover: CoverSource | None = None,
        *,
        maximum_frames: int = 4,
    ) -> None:
        if maximum_frames < 1:
            raise ValueError("maximum frames must be positive")
        self._fetcher = fetcher
        self._storyboard = storyboard
        self._cover = cover
        self._maximum_frames = maximum_frames

    async def acquire(self, candidate: Candidate) -> tuple[BinaryContent, ...]:
        storyboard_urls: tuple[str, ...] = ()
        if self._storyboard is not None:
            try:
                storyboard_urls = (await self._storyboard(candidate))[: self._maximum_frames]
            except Exception:
                storyboard_urls = ()
        frames = await self._fetch(storyboard_urls)
        if frames:
            return frames
        if self._cover is None:
            return ()
        try:
            cover_url = await self._cover(candidate)
        except Exception:
            return ()
        return await self._fetch((cover_url,)) if cover_url is not None else ()

    async def _fetch(self, urls: tuple[str, ...]) -> tuple[BinaryContent, ...]:
        frames: list[BinaryContent] = []
        for url in urls:
            try:
                image = cast("FetchedImage", await self._fetcher.fetch(url))
                if not image.content or not image.content_type.lower().startswith("image/"):
                    continue
                frames.append(BinaryContent(data=image.content, media_type=image.content_type))
            except Exception:
                continue
        return tuple(frames)


class InspectionService:
    """Run and cache per-content visual judgments without affecting delivery."""

    def __init__(
        self,
        runtime: AIRuntime,
        frames: FrameAcquirer,
        journal: InspectionJournal,
        embedding_index: EmbeddingIndex | None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._runtime = runtime
        self._frames = frames
        self._journal = journal
        self._embedding_index = embedding_index
        self._clock = clock

    async def inspect(
        self,
        candidate: Candidate,
        *,
        quality_rubric: str = "Prefer substantive, accurate, clearly presented content.",
        recommendation_batch: str | None = None,
    ) -> InspectionResult | None:
        """Return a durable cached result, or fail open when inspection is unavailable."""

        try:
            cached = await self._cached(candidate)
        except Exception:  # cache reads fail open too (closed DB, shutdown race)
            cached = None
        if cached is not None:
            await self._embed(candidate, cached)
            return cached
        frames = await self._frames.acquire(candidate)
        if not frames:
            return None
        prompt = self._prompt(candidate, quality_rubric)
        try:
            run = await self._runtime.run(
                AgentRunRequest(
                    agent_id=INSPECTION_AGENT.agent_id,
                    agent=INSPECTION_AGENT.agent,
                    deps=None,
                    user_input=prompt,
                    history=(),
                    context=(),
                    requirements=INSPECTION_AGENT.requirements,
                    policy=INSPECTION_AGENT.policy,
                    workflow="recommendation.inspect",
                    recommendation_batch=recommendation_batch,
                    attachments=frames,
                )
            )
            await self._persist(
                candidate, run.output, run.model_instance, run.provider, quality_rubric
            )
            await self._embed(candidate, run.output)
            return run.output
        except Exception:
            return None

    @staticmethod
    def embedding_ref(candidate: Candidate) -> str:
        ref = candidate.preview.ref
        return f"inspection:{ref.provider_id.value}:{ref.provider_content_id}"

    @classmethod
    def _inspection_id(cls, candidate: Candidate) -> str:
        return record_identity("inspect", cls.embedding_ref(candidate))

    async def _cached(self, candidate: Candidate) -> InspectionResult | None:
        try:
            record = await self._journal.load_inspection(self._inspection_id(candidate))
            return InspectionResult.model_validate(record.payload.get("result"))
        except (KeyError, ValueError, TypeError):
            return None

    async def _persist(
        self,
        candidate: Candidate,
        result: InspectionResult,
        model_instance: str,
        provider: str,
        rubric: str,
    ) -> None:
        payload = cast(
            "dict[str, JsonValue]",
            {
                "kind": "content-inspection",
                "content_ref": candidate.preview.ref.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
                "agent": {
                    "agent_id": INSPECTION_AGENT.agent_id.value,
                    "model_instance": model_instance,
                    "provider": provider,
                    "prompt_version": INSPECTION_PROMPT_VERSION,
                    "rubric": rubric,
                },
            },
        )
        await self._journal.append_inspection(
            JournalInspection(
                inspection_id=self._inspection_id(candidate),
                content_ref=self.embedding_ref(candidate),
                payload=payload,
                created_at=self._clock(),
            )
        )

    async def _embed(self, candidate: Candidate, result: InspectionResult) -> None:
        if self._embedding_index is None:
            return
        text = "\n".join(
            (
                result.actual_topic,
                result.summary,
                f"quality {result.quality:.3f}",
                f"title mismatch: {result.title_mismatch}",
            )
        )
        try:
            await self._embedding_index.upsert("candidate", self.embedding_ref(candidate), text)
        except Exception:
            return

    @staticmethod
    def _prompt(candidate: Candidate, quality_rubric: str) -> str:
        title = sanitize_untrusted_text(candidate.preview.title)
        summary = sanitize_untrusted_text(candidate.preview.summary)
        rubric = sanitize_untrusted_text(quality_rubric)[:2000]
        return (
            f"Title: {title}\nMetadata summary: {summary}\n"
            f"Quality rubric: {rubric}\nInspect the supplied sampled images."
        )
