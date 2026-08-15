"""Typed RecommendationBrief compilation and shadow-mode policy journaling.

The model proposes semantics. ``BriefCompiler`` deterministically enforces source,
privacy, expiry, and resource invariants. ``BriefService`` journals the resulting
trace but deliberately does not execute any part of the brief while shadow mode is
active.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, cast
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from openbiliclaw.ai.runtime.errors import AIRuntimeError
from openbiliclaw.ai.runtime.execution import AgentRunRequest, AIRuntime
from openbiliclaw.ai.runtime.history import sanitize_untrusted_text
from openbiliclaw.content.integration.manifest import BiasClass
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.recommendation.policy_journal import JournalBrief, PolicyJournal

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from openbiliclaw.content.integration.manifest import ProviderManifest
    from openbiliclaw.recommendation.hypotheses import HypothesisRegistry

DEFAULT_INSPECTION_SHORTLIST_CAP = 5
_EVIDENCE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,199}$")
_PERCENTAGE = re.compile(r"(?:\d+(?:\.\d+)?\s*%|\bpercent(?:age)?\b)", re.IGNORECASE)
_Text = Annotated[str, Field(min_length=1, max_length=500)]

__all__ = [
    "BriefAction",
    "BriefCompiler",
    "BriefHypothesis",
    "BriefIntent",
    "BriefService",
    "CompiledBrief",
    "CompilerDiagnostic",
    "InspectionPlan",
    "RecommendationBrief",
    "RetrievalPlan",
    "SlateGuidance",
]


class _BriefModel(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BriefIntent(StrEnum):
    """Ephemeral episode intent, never a durable user trait."""

    ENJOY = "enjoy"
    ACCOMPLISH = "accomplish"
    DEEPEN = "deepen"
    EXPLORE = "explore"
    UNCERTAIN = "uncertain"


class BriefAction(StrEnum):
    RECOMMEND = "recommend"
    ASK = "ask"
    ABSTAIN = "abstain"


class BriefHypothesis(_BriefModel):
    """Evidence-cited, expiring semantic hypothesis proposed by the brief agent."""

    statement: str = Field(min_length=1, max_length=1000)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=50)
    falsification: str = Field(min_length=1, max_length=500)
    expires_at: AwareDatetime

    @field_validator("evidence_refs")
    @classmethod
    def opaque_evidence_ids_only(cls, refs: tuple[str, ...]) -> tuple[str, ...]:
        if any(_EVIDENCE_REF.fullmatch(ref) is None for ref in refs):
            raise ValueError("evidence_refs must contain opaque IDs, not evidence text")
        if len(refs) != len(set(refs)):
            raise ValueError("duplicate evidence reference")
        return refs


class RetrievalPlan(_BriefModel):
    """Bounded provider-channel and keyword retrieval proposal."""

    channel_refs: tuple[str, ...] = Field(default=(), max_length=20)
    keyword_queries: tuple[_Text, ...] = Field(default=(), max_length=20)
    exploration: bool = False

    @model_validator(mode="after")
    def nonempty_and_unique(self) -> RetrievalPlan:
        if not self.channel_refs and not self.keyword_queries:
            raise ValueError("retrieval plan must contain a channel or keyword query")
        if len(self.channel_refs) != len(set(self.channel_refs)):
            raise ValueError("duplicate channel reference")
        if len(self.keyword_queries) != len(set(self.keyword_queries)):
            raise ValueError("duplicate keyword query")
        return self


class InspectionPlan(_BriefModel):
    """Shortlist-only inspection targets and the episode-specific quality rubric."""

    shortlist_targets: tuple[_Text, ...] = Field(default=(), max_length=100)
    quality_rubric: str = Field(min_length=1, max_length=2000)

    @field_validator("shortlist_targets")
    @classmethod
    def unique_targets(cls, targets: tuple[str, ...]) -> tuple[str, ...]:
        if len(targets) != len(set(targets)):
            raise ValueError("duplicate inspection target")
        return targets


class SlateGuidance(_BriefModel):
    """Qualitative familiar/novel relationships; allocation owns all magnitudes."""

    familiar_relationship: str = Field(min_length=1, max_length=1000)
    novel_relationship: str = Field(min_length=1, max_length=1000)

    @field_validator("familiar_relationship", "novel_relationship")
    @classmethod
    def relationships_not_percentages(cls, value: str) -> str:
        if _PERCENTAGE.search(value):
            raise ValueError("slate guidance must describe relationships, not percentages")
        return value


class RecommendationBrief(_BriefModel):
    """One complete semantic strategy proposal for an expiring context."""

    intent: BriefIntent
    intent_expires_at: AwareDatetime
    hypotheses: tuple[BriefHypothesis, ...] = Field(max_length=20)
    retrieval_plans: tuple[RetrievalPlan, ...] = Field(max_length=20)
    inspection_plan: InspectionPlan
    slate_guidance: SlateGuidance
    action: BriefAction
    question: str | None = Field(default=None, min_length=1, max_length=500)
    stop_condition: str = Field(min_length=1, max_length=1000)
    expires_at: AwareDatetime


class CompilerDiagnostic(_BriefModel):
    """Stable rejection code and content-free explanation."""

    code: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    message: str = Field(min_length=1, max_length=500)


class CompilerChannel(_BriefModel):
    channel_ref: str
    bias_class: BiasClass


class CompilationTrace(_BriefModel):
    """All deterministic compiler inputs needed to replay validation."""

    compiled_at: AwareDatetime
    inspection_shortlist_cap: int = Field(ge=0)
    privacy_contract: Literal["opaque-evidence-refs-only"] = "opaque-evidence-refs-only"
    channels: tuple[CompilerChannel, ...]


class CompiledBrief(_BriefModel):
    """Proposal plus deterministic acceptance decision and replay inputs."""

    proposal: RecommendationBrief
    accepted: bool
    diagnostics: tuple[CompilerDiagnostic, ...]
    trace: CompilationTrace


class ActiveHypothesisContext(_BriefModel):
    hypothesis_id: str
    arm: str
    statement: str
    evidence_refs: tuple[str, ...]
    falsification: str
    expires_at: AwareDatetime
    attempts: int = Field(ge=0)
    successes: int = Field(ge=0)
    failures: int = Field(ge=0)


class RecentDecisionContext(_BriefModel):
    episode_id: str
    kind: str
    intent: str | None
    accepted: bool | None
    explore: bool | None
    arm: str | None
    hypothesis_id: str | None
    created_at: AwareDatetime


class BriefContext(_BriefModel):
    episode_id: str
    current_intent: BriefIntent | None
    active_hypotheses: tuple[ActiveHypothesisContext, ...]
    recent_decisions: tuple[RecentDecisionContext, ...]


class BriefCompiler:
    """Pure validation boundary between semantic proposal and executable policy."""

    def __init__(
        self,
        manifests: Sequence[ProviderManifest],
        *,
        inspection_shortlist_cap: int = DEFAULT_INSPECTION_SHORTLIST_CAP,
    ) -> None:
        if inspection_shortlist_cap < 0:
            raise ValueError("inspection shortlist cap must not be negative")
        channels = (
            CompilerChannel(
                channel_ref=f"{manifest.provider_id.value}:{channel.feed_id}",
                bias_class=channel.bias_class,
            )
            for manifest in manifests
            for channel in manifest.channels
        )
        self._channels = tuple(sorted(channels, key=lambda channel: channel.channel_ref))
        self._channel_by_ref = {channel.channel_ref: channel for channel in self._channels}
        self._inspection_shortlist_cap = inspection_shortlist_cap

    @property
    def inspection_shortlist_cap(self) -> int:
        return self._inspection_shortlist_cap

    def compile(self, proposal: RecommendationBrief, *, now: datetime) -> CompiledBrief:
        """Validate a typed proposal without executing or mutating either data plane."""

        diagnostics: list[CompilerDiagnostic] = []

        def reject(code: str, message: str) -> None:
            diagnostics.append(CompilerDiagnostic(code=code, message=message))

        for plan in proposal.retrieval_plans:
            for channel_ref in plan.channel_refs:
                channel = self._channel_by_ref.get(channel_ref)
                if channel is None:
                    reject("unknown-channel", "retrieval plan references an undeclared channel")
                elif plan.exploration and channel.bias_class is BiasClass.PLATFORM_PERSONALIZED:
                    reject(
                        "personalized-exploration",
                        "platform-personalized channels are exploit-class supply only",
                    )

        target_count = len(proposal.inspection_plan.shortlist_targets)
        if target_count > self._inspection_shortlist_cap:
            reject("inspection-budget", "inspection targets exceed the named shortlist cap")

        if proposal.expires_at <= now:
            reject("brief-expired", "brief expiry must be after compilation time")
        if proposal.intent_expires_at <= now:
            reject("intent-expired", "intent expiry must be after compilation time")
        if proposal.intent_expires_at > proposal.expires_at:
            reject("intent-expiry-order", "intent must not outlive its brief")
        for hypothesis in proposal.hypotheses:
            if hypothesis.expires_at <= now:
                reject("hypothesis-expired", "hypothesis expiry must be after compilation time")
            if hypothesis.expires_at > proposal.expires_at:
                reject("hypothesis-expiry-order", "hypothesis must not outlive its brief")

        if proposal.action is BriefAction.ASK and not (proposal.question or "").strip():
            reject("ask-question-required", "ask action requires a question")
        if proposal.action is not BriefAction.ASK and proposal.question is not None:
            reject("unexpected-question", "only an ask action may carry a question")

        trace = CompilationTrace(
            compiled_at=now,
            inspection_shortlist_cap=self._inspection_shortlist_cap,
            channels=self._channels,
        )
        return CompiledBrief(
            proposal=proposal,
            accepted=not diagnostics,
            diagnostics=tuple(diagnostics),
            trace=trace,
        )


class BriefService:
    """Gather compact policy context, invoke the brief agent, and journal shadow output."""

    def __init__(
        self,
        runtime: AIRuntime,
        hypotheses: HypothesisRegistry,
        journal: PolicyJournal,
        compiler: BriefCompiler,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._runtime = runtime
        self._hypotheses = hypotheses
        self._journal = journal
        self._compiler = compiler
        self._clock = clock

    async def compile_shadow(self, episode_id: str) -> CompiledBrief | None:
        """Compile and journal one shadow brief; unavailable models are a no-op."""

        now = self._clock()
        context = await self._context(episode_id, now)
        from openbiliclaw.recommendation.brief_agent import BRIEF_AGENT

        context_json = context.model_dump_json()
        try:
            result = await self._runtime.run(
                AgentRunRequest(
                    agent_id=BRIEF_AGENT.agent_id,
                    agent=BRIEF_AGENT.agent,
                    deps=None,
                    user_input=context_json,
                    history=(),
                    context=(),
                    requirements=BRIEF_AGENT.requirements,
                    policy=BRIEF_AGENT.policy,
                    workflow="recommendation.brief",
                    recommendation_batch=episode_id,
                )
            )
        except AIRuntimeError:
            return None

        compiled = self._compiler.compile(result.output, now=now)
        payload = cast(
            "dict[str, JsonValue]",
            {
                "kind": "recommendation-brief",
                "accepted": compiled.accepted,
                "proposal": compiled.proposal.model_dump(mode="json"),
                "diagnostics": [
                    diagnostic.model_dump(mode="json") for diagnostic in compiled.diagnostics
                ],
                "compiler_trace": compiled.trace.model_dump(mode="json"),
                "agent_trace": {
                    "agent_id": BRIEF_AGENT.agent_id.value,
                    "model_instance": result.model_instance,
                    "provider": result.provider,
                    "prompt_version": BRIEF_AGENT.prompt_version,
                    "schema_version": BRIEF_AGENT.schema_version,
                    "context_version": BRIEF_AGENT.context_version,
                    "context_sha256": hashlib.sha256(context_json.encode()).hexdigest(),
                },
            },
        )
        await self._journal.append_brief(
            JournalBrief(
                brief_id=f"brief_{uuid4().hex}",
                episode_id=episode_id,
                status="shadow",
                payload=payload,
                created_at=now,
            )
        )
        return compiled

    async def _context(self, episode_id: str, now: datetime) -> BriefContext:
        hypotheses = []
        for hypothesis in await self._hypotheses.active(now):
            attempts, successes, failures = await self._hypotheses.posterior(
                hypothesis.hypothesis_id
            )
            hypotheses.append(
                ActiveHypothesisContext(
                    hypothesis_id=hypothesis.hypothesis_id,
                    arm=hypothesis.arm,
                    # Journal text is model-generated history: untrusted data,
                    # never instructions (injection containment pre-live-switch).
                    statement=sanitize_untrusted_text(hypothesis.statement),
                    evidence_refs=hypothesis.evidence_refs,
                    falsification=sanitize_untrusted_text(hypothesis.falsification),
                    expires_at=hypothesis.expires_at,
                    attempts=attempts,
                    successes=successes,
                    failures=failures,
                )
            )
        records = await self._journal.list_briefs(limit=5)
        recent = tuple(self._recent_context(record) for record in records)
        return BriefContext(
            episode_id=episode_id,
            current_intent=self._current_intent(records, now),
            active_hypotheses=tuple(hypotheses),
            recent_decisions=recent,
        )

    @staticmethod
    def _recent_context(record: JournalBrief) -> RecentDecisionContext:
        kind = record.payload.get("kind")
        intent = record.payload.get("intent")
        proposal = record.payload.get("proposal")
        if not isinstance(intent, str) and isinstance(proposal, dict):
            proposed_intent = proposal.get("intent")
            intent = proposed_intent if isinstance(proposed_intent, str) else None
        accepted = record.payload.get("accepted")
        explore = record.payload.get("explore")
        arm = record.payload.get("arm")
        hypothesis_id = record.payload.get("hypothesis_id")
        return RecentDecisionContext(
            episode_id=record.episode_id,
            kind=kind if isinstance(kind, str) else "unknown",
            intent=intent if isinstance(intent, str) else None,
            accepted=accepted if isinstance(accepted, bool) else None,
            explore=explore if isinstance(explore, bool) else None,
            arm=arm if isinstance(arm, str) else None,
            hypothesis_id=hypothesis_id if isinstance(hypothesis_id, str) else None,
            created_at=record.created_at,
        )

    @staticmethod
    def _current_intent(records: tuple[JournalBrief, ...], now: datetime) -> BriefIntent | None:
        for record in records:
            if record.payload.get("kind") != "recommendation-brief":
                continue
            if record.payload.get("accepted") is not True:
                continue
            proposal = record.payload.get("proposal")
            if not isinstance(proposal, dict):
                continue
            try:
                brief = RecommendationBrief.model_validate(proposal)
            except ValueError:
                continue
            if brief.intent_expires_at > now:
                return brief.intent
        return None
