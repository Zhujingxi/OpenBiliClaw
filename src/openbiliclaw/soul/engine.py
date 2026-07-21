"""User Soul Engine — the heart of OpenBiliClaw.

Transforms raw behavioral data into deep, layered understanding of a person.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from openbiliclaw.llm.service import ModuleOverride, SupportsComplete
    from openbiliclaw.memory.manager import MemoryManager

from openbiliclaw.llm.service import LLMService

from .avoidance_speculator import AvoidanceSpeculator
from .awareness_analyzer import AwarenessAnalyzer
from .cognition_cycle import (
    DEFAULT_MIN_INTERVAL_SECONDS as _DEFAULT_COG_INTERVAL,
)
from .cognition_cycle import (
    CognitionCycle,
)
from .confusion import ConfusionManager, apply_confusion_freeze
from .consolidator import ProfileConsolidator
from .dialogue_insight_analyzer import (
    DialogueInsightAnalysisError,
    DialogueInsightAnalyzer,
)
from .identity import build_hash8_map
from .insight_analyzer import InsightAnalyzer
from .ledger import ProfileLedger
from .overrides import ProfileOverrides, apply_edit, apply_overrides
from .pipeline import ProfileUpdatePipeline, migrate_pipeline_deep_buffers
from .posture_gate import ACCEPT, GateDecision, PostureGate
from .preference_analyzer import (
    DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
    INIT_COGNITION_CONTEXT_KEY,
    PreferenceAnalyzer,
)
from .profile import (
    AwarenessNote,
    InsightHypothesis,
    OnionProfile,
    awareness_note_from_dict,
    awareness_note_to_dict,
    insight_hypothesis_from_dict,
    insight_hypothesis_to_dict,
)
from .profile_builder import ProfileBuilder
from .speculator import InterestSpeculator

logger = logging.getLogger(__name__)

# Dialogue candidate kinds that write DEEP profile layers and therefore pass the
# posture gate (access point ①). interest/dislike take the fast line unchanged.
_DEEP_CANDIDATE_KINDS = frozenset({"goal", "value", "state"})

# Soul-rebuild triggers (access point ③, generalized — spec r3/F4). Each drives
# a full gated rebuild but carries a distinct ledger write point so the audit
# trail distinguishes what caused it: dialogue learning, a feedback batch with a
# significant preference shift (P2 — previously ungated), or a batch of
# newly-confirmed hypotheses (the pending-rebuild state machine).
_REBUILD_TRIGGER_DIALOGUE = "dialogue"
_REBUILD_TRIGGER_FEEDBACK_BATCH = "feedback_batch"
_REBUILD_TRIGGER_CONFIRMED_HYPOTHESES = "confirmed_hypotheses"
_REBUILD_WRITE_POINT: dict[str, str] = {
    _REBUILD_TRIGGER_DIALOGUE: "dialogue_soul_rebuild",
    _REBUILD_TRIGGER_FEEDBACK_BATCH: "feedback_soul_rebuild",
    _REBUILD_TRIGGER_CONFIRMED_HYPOTHESES: "hypotheses_soul_rebuild",
}

# A hypothesis only shapes a soul rebuild once it is validated AND confident
# (spec invariant 3 / r3/F1). Rejected or unvalidated hypotheses are invisible
# to every rebuild, so a reject's next rebuild squeezes an old conclusion out.
_REBUILD_MIN_CONFIDENCE = 0.75
# Debounce between a confirm/reject migration and the gated rebuild it schedules
# (spec r3/F3). 6h sits between conversational cadence and the 12h cognition
# loop — first-round calibration, re-tune after the first production month
# (pitfall #3).
_DEEP_REBUILD_DEBOUNCE_HOURS = 6
# Bounded retry for a pending rebuild that keeps hitting a transient LLM/parse
# error (is_error path). After this many failures the marker is cleared with a
# WARNING so a persistently broken provider can't wedge the pending state.
_REBUILD_MAX_RETRIES = 2
# Cap on the confirmed-hypothesis list carried in the gate snapshot context.
_REBUILD_CONTEXT_HYPOTHESIS_CAP = 12


def _memory_database(memory: Any) -> Any | None:
    """Resolve the SQLite database handle a memory manager owns (may be None)."""
    return getattr(memory, "_database", None)


def _as_dict_list(raw_value: object) -> list[dict[str, object]]:
    if not isinstance(raw_value, list):
        return []
    return [item for item in raw_value if isinstance(item, dict)]


SOURCE_LABELS = {
    "feedback": "推荐反馈",
    "chat": "聊天",
    "profile_refresh": "聚合观察",
    "manual": "手动编辑",
}

# Human-readable labels for manual-edit cognition summaries, keyed by the
# editable onion field path / interest polarity.
_MANUAL_EDIT_LABELS = {
    "personality_portrait": "人格画像",
    "core.core_traits": "核心特质",
    "core.deep_needs": "深层需求",
    "values_layer.values": "价值观",
    "values_layer.motivational_drivers": "内在驱动",
    "surface.cognitive_style": "认知风格",
    "interest.favorite_up_users": "常看 UP 主",
    "role.life_stage": "人生阶段",
    "role.current_phase": "当前阶段",
    "likes": "喜欢",
    "dislikes": "不喜欢",
    "surface.exploration_openness": "探索开放度",
    "surface.style.quality_sensitivity": "画质敏感度",
    "surface.style.humor_preference": "幽默偏好",
    "surface.style.depth_preference": "深度偏好",
}

_FEEDBACK_ANALYSIS_METADATA_KEYS = frozenset(
    {
        "recommendation_id",
        "bvid",
        "aid",
        "content_id",
        "content_url",
        "source_platform",
        "feedback_type",
        "feedback_note",
        "reaction",
        "up_name",
        "author",
        "topic_label",
        "watch_seconds",
        "video_duration_seconds",
        "signal_strength",
    }
)


class SoulProfileNotInitializedError(Exception):
    """Raised when the soul layer has not been initialized yet."""


class SoulEngine:
    """Engine for building and maintaining deep user understanding.

    The Soul Engine orchestrates the transformation of raw behavioral data
    through the five-layer memory architecture:
      Event → Preference → Awareness → Insight → Soul

    It is responsible for:
    1. Analyzing new behavioral events
    2. Updating preference patterns
    3. Writing daily awareness notes
    4. Generating insight hypotheses
    5. Maintaining the soul-level personality portrait
    """

    def __init__(
        self,
        llm: SupportsComplete,
        memory: MemoryManager,
        *,
        embedding_service: Any | None = None,
        cognition_cycle_interval_seconds: int | None = None,
        usage_recorder: Any | None = None,
        satisfaction_filter_enabled: bool = True,
        module_overrides: Mapping[str, ModuleOverride] | None = None,
        llm_concurrency: int = 4,
        llm_concurrency_gate: Any | None = None,
        speculation_interval_minutes: int = 10,
        speculation_ttl_days: int = 3,
        speculation_cooldown_days: int = 7,
        speculation_confirmation_threshold: int = 3,
        speculation_max_active: int = 5,
        speculation_max_primary_interests: int = 15,
        speculation_max_secondary_interests: int = 60,
        avoidance_speculation_interval_minutes: int = 10,
        avoidance_speculation_ttl_days: int = 3,
        avoidance_speculation_cooldown_days: int = 7,
        avoidance_speculation_confirmation_threshold: int = 3,
        avoidance_speculation_max_active: int = 5,
        speculator_idle_interval_minutes: int = 30,
        profile_consolidation_enabled: bool = True,
        profile_consolidation_interval_hours: int = 12,
        profile_consolidation_like_target_upper: int = 512,
        profile_consolidation_like_target_soft: int = 450,
        profile_consolidation_archive_enabled: bool = True,
        feedback_batch_threshold: int = 3,
        posture_gate_mode: str = "shadow",
        posture_gate_force_enforce: bool = False,
        database: Any | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._satisfaction_filter_enabled = satisfaction_filter_enabled
        self._feedback_batch_threshold = max(1, feedback_batch_threshold)
        self._feedback_batch_lock = asyncio.Lock()
        # Pending confirmed-hypotheses rebuild state machine (spec r3/F3). The
        # lock guards read-modify-write of the persisted marker; ``_rebuild_running``
        # prevents overlapping builds while the lock is released for the long
        # LLM build (compare-and-swap on ``set_at`` reconciles a concurrent
        # re-mark). Restart recovery is automatic: the marker persists to disk
        # and ``_rebuild_running`` resets to False on construction.
        self._rebuild_pending_lock = asyncio.Lock()
        self._rebuild_running = False
        self._module_overrides = dict(module_overrides or {})
        self._llm_concurrency = llm_concurrency
        self._llm_concurrency_gate = llm_concurrency_gate
        # Pass usage_recorder through so internal LLM calls
        # (preference / awareness / insight / profile_builder / speculator
        # / dialogue_insight) appear in the cost ledger with their caller
        # tags. Without this, the entire ``soul.*`` namespace was
        # invisible in `openbiliclaw cost --by caller` and bypassed the
        # empty-content guard in LLMService — speculator failures showed
        # up as silent "0 new generations" instead of explicit WARNs.
        self._llm_service: LLMService = LLMService(
            registry=llm,
            memory=memory,
            usage_recorder=usage_recorder,
            module_overrides=self._module_overrides,
            concurrency=llm_concurrency,
            concurrency_gate=llm_concurrency_gate,
        )
        self._awareness_analyzer = AwarenessAnalyzer(self._llm_service)
        self._dialogue_insight_analyzer = DialogueInsightAnalyzer(self._llm_service)
        self._insight_analyzer = InsightAnalyzer(self._llm_service)
        self._preference_analyzer = PreferenceAnalyzer(
            self._llm_service,
            satisfaction_filter_enabled=satisfaction_filter_enabled,
            embedding_service=embedding_service,
        )
        self._profile_builder = ProfileBuilder(self._llm_service)
        data_dir = getattr(memory, "_data_dir", None)
        self._speculator = InterestSpeculator(
            llm_service=self._llm_service,
            data_dir=data_dir,
            generation_interval_minutes=speculation_interval_minutes,
            default_ttl_days=speculation_ttl_days,
            cooldown_days=speculation_cooldown_days,
            confirmation_threshold=speculation_confirmation_threshold,
            max_active=speculation_max_active,
            max_primary_interests=speculation_max_primary_interests,
            max_secondary_interests=speculation_max_secondary_interests,
        )
        self._avoidance_speculator = AvoidanceSpeculator(
            llm_service=self._llm_service,
            data_dir=data_dir,
            generation_interval_minutes=avoidance_speculation_interval_minutes,
            default_ttl_days=avoidance_speculation_ttl_days,
            cooldown_days=avoidance_speculation_cooldown_days,
            confirmation_threshold=avoidance_speculation_confirmation_threshold,
            max_active=avoidance_speculation_max_active,
        )
        self._embedding_service = embedding_service
        self._cognition_cycle = CognitionCycle(
            memory=memory,
            awareness_analyzer=self._awareness_analyzer,
            insight_analyzer=self._insight_analyzer,
            min_interval_seconds=(
                cognition_cycle_interval_seconds
                if cognition_cycle_interval_seconds is not None
                else _DEFAULT_COG_INTERVAL
            ),
            # 12h-loop fallback trigger for the debounced confirmed-hypotheses
            # rebuild (spec invariant 4). Bound method; only invoked at run time.
            pending_rebuild_hook=self.run_pending_rebuild_if_due,
        )
        self._profile_consolidator: ProfileConsolidator | None = None
        if profile_consolidation_enabled:
            self._profile_consolidator = ProfileConsolidator(
                memory=memory,
                llm_service=self._llm_service,
                embedding_service=embedding_service,
                data_dir=data_dir,
                min_interval_seconds=profile_consolidation_interval_hours * 3600,
                like_target_upper=profile_consolidation_like_target_upper,
                like_target_soft=profile_consolidation_like_target_soft,
                archive_enabled=profile_consolidation_archive_enabled,
                database=database,
            )
        self._pipeline = ProfileUpdatePipeline(
            memory=memory,
            preference_analyzer=self._preference_analyzer,
            profile_builder=self._profile_builder,
            speculator=self._speculator,
            avoidance_speculator=self._avoidance_speculator,
            embedding_service=embedding_service,
            cognition_cycle=self._cognition_cycle,
            speculator_idle_interval_minutes=speculator_idle_interval_minutes,
            profile_consolidator=self._profile_consolidator,
        )
        # Detached dislike writeback from manual edits, feedback batches, and
        # dialogue learning. The purge runs an LLM+embedding recall that must
        # not block the interactive response, so keep a strong task reference
        # and expose a deterministic wait hook for tests / shutdown.
        self._background_edit_tasks: set[asyncio.Task[Any]] = set()
        self._init_cognition_context: dict[str, object] = {}
        # Phase 0 audit ledger. Best-effort observer over profile write points;
        # a ledger failure is logged at WARNING and never blocks a write. Resolve
        # the database from the explicit arg or the memory manager's handle.
        self._ledger_database = database if database is not None else _memory_database(memory)
        self._ledger = ProfileLedger(self._ledger_database)
        # Deep-line consolidation: one-time migration of any persisted VALUES/CORE
        # pipeline buffer signals into awareness notes, then seal the deep buffers
        # (P1 retired). Idempotent (marker + content-hash dedup) and best-effort —
        # a failure never blocks engine construction. Runs before the first
        # pipeline save so the raw deep-buffer keys are still on disk to read.
        if data_dir is not None:
            try:
                migrate_pipeline_deep_buffers(data_dir, memory, self._ledger)
            except Exception:
                logger.warning("pipeline deep-buffer migration failed", exc_info=True)
        # Confusion state machine over the same database — drives the topic
        # freeze reflex at the dialogue preference write chokepoint (Phase 2).
        self._confusion_manager = ConfusionManager(self._ledger_database, self._ledger)
        # Wire the same ledger into the speculator so promote/confirm/reject
        # write points (D5 #5) land in the same audit trail.
        attach_ledger = getattr(self._speculator, "attach_ledger", None)
        if callable(attach_ledger):
            attach_ledger(self._ledger)
        # Phase 3 posture gate over deep writes (dialogue deep candidates /
        # pipeline VALUES+CORE / soul rebuild). shadow (default) is a zero-delay
        # async side-channel; off is a byte-identical bypass. The pipeline shares
        # the same instance so its VALUES/CORE updater gates through it.
        self._posture_gate = PostureGate(
            mode=posture_gate_mode,
            registry=self._llm_service,
            ledger=self._ledger,
            background_tasks=self._background_edit_tasks,
        )
        set_gate = getattr(self._pipeline, "set_posture_gate", None)
        if callable(set_gate):
            set_gate(self._posture_gate)
        # Held-replay crash recovery (Wave B, r5/R4-1): any held update left in
        # ``replaying`` at construction is a leftover from a previously crashed
        # session — reconcile it to ``applied_unverified`` (never resubmit;
        # prefer under- to double-counting). Fresh replays created later in THIS
        # session are consumed by ``replay_held_updates`` instead. Best-effort.
        try:
            self._confusion_manager.recover_replaying()
        except Exception:
            logger.debug("held-replay crash recovery failed", exc_info=True)

    def set_embedding_service(self, embedding_service: Any) -> None:
        """Attach or update the embedding service after construction.

        Useful when the embedding service is built later than the soul
        engine in the bootstrap order.
        """
        self._embedding_service = embedding_service
        self._preference_analyzer.embedding_service = embedding_service
        self._pipeline.set_embedding_service(embedding_service)
        if self._profile_consolidator is not None:
            self._profile_consolidator.set_embedding_service(embedding_service)

    @property
    def pipeline(self) -> Any:
        """Access the ProfileUpdatePipeline for direct signal ingestion."""
        return self._pipeline

    async def analyze_events(
        self,
        events: list[dict[str, Any]],
        *,
        event_chunk_size: int = 0,
        progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> None:
        """Analyze new behavioral events and update all memory layers.

        This is the primary entry point for processing new user behavior.
        Events flow upward through the memory layers, with each layer
        potentially triggering updates in the layers above.

        Args:
            events: List of behavioral event dicts from the collector.
            event_chunk_size: When > 0, split the event list into chunks
                of this size and analyse each chunk in parallel. Useful
                for the init bootstrap where a single max-thinking call
                on ~800 events would block for ~6 minutes.
        """
        import time as _time

        logger.info(
            "analyze_events start: events=%d chunk_size=%d",
            len(events),
            event_chunk_size,
        )
        t0 = _time.monotonic()
        preference_layer = self._memory.get_layer("preference")
        updated_preference = await self._preference_analyzer.analyze_events(
            events=events,
            existing_preference=preference_layer.data,
            event_chunk_size=event_chunk_size,
            progress_callback=progress_callback,
        )
        init_cognition = updated_preference.pop(INIT_COGNITION_CONTEXT_KEY, None)
        self._init_cognition_context = init_cognition if isinstance(init_cognition, dict) else {}
        # Ledger write point D5 #7: full-preference (re)build from raw events —
        # the init bootstrap and any full-events re-analysis both land here.
        existing_preference = dict(preference_layer.data)
        # Topic-lifecycle (Phase 4): carry lifecycle metadata forward and count
        # this analysis as evidence (new topics enter trial; sustained/dormant
        # topics transition). Best-effort — never breaks the analysis path.
        self._apply_topic_lifecycle_evidence(existing_preference, updated_preference)
        with self._ledger.action(
            write_point="init_preference_build",
            source="init",
            before=existing_preference,
            source_refs=[f"events:{len(events)}"],
        ) as _entry:
            preference_layer.data.clear()
            preference_layer.data.update(updated_preference)
            preference_layer.save()
            _entry.after = dict(updated_preference)
        logger.info(
            "analyze_events done: events=%d elapsed=%.1fs",
            len(events),
            _time.monotonic() - t0,
        )

    async def build_initial_profile(self, history: list[dict[str, Any]]) -> OnionProfile:
        """Build an initial soul profile from historical data.

        Used on first run to bootstrap the user understanding model
        from existing Bilibili watch history, favorites, etc.

        Args:
            history: Historical data from Bilibili API.

        Returns:
            Initial OnionProfile.
        """
        import time as _time

        logger.info("build_initial_profile start: history=%d items", len(history))
        t0 = _time.monotonic()
        preference_layer = self._memory.get_layer("preference").data
        awareness_notes = [awareness_note_to_dict(item) for item in self._load_awareness_notes()]
        active_insights = [insight_hypothesis_to_dict(item) for item in self._load_insights()]
        awareness_notes.extend(self._init_awareness_context())
        active_insights.extend(self._init_insight_context())
        legacy_profile = await self._profile_builder.build(
            history=history,
            preference=preference_layer,
            awareness_notes=awareness_notes,
            active_insights=active_insights,
        )
        logger.info(
            "build_initial_profile: legacy profile built in %.1fs",
            _time.monotonic() - t0,
        )
        profile = OnionProfile.from_legacy(legacy_profile)
        profile.populate_from_flat_preference(preference_layer)
        soul_layer = self._memory.get_layer("soul")
        # Ledger write point (extra, discovered during Phase 0 — clist item 7
        # "init 全量建像" also covers the soul-layer bootstrap write here).
        existing_soul = dict(soul_layer.data)
        with self._ledger.action(
            write_point="init_soul_build",
            source="init",
            before=existing_soul,
            source_refs=[f"history:{len(history)}"],
        ) as _entry:
            soul_layer.data.clear()
            soul_layer.data.update(profile.to_dict())
            soul_layer.save()
            _entry.after = dict(soul_layer.data)
        self._memory.sync_profile_files(profile)
        self._init_cognition_context = {}
        logger.info(
            "build_initial_profile done: total_elapsed=%.1fs",
            _time.monotonic() - t0,
        )

        # This return is the strict profile-commit barrier for guided init.
        # Initial interest/avoidance probes are intentionally scheduled by
        # RuntimeContext.restart_background_tasks *after* the first serviceable
        # content pool is attempted. Keeping them out of this method prevents a
        # non-essential maintenance task from extending or deadlocking the
        # load-bearing profile stage.

        return profile

    def _init_awareness_context(self) -> list[dict[str, object]]:
        raw_items = self._init_cognition_context.get("awareness")
        items = raw_items if isinstance(raw_items, list) else []
        result: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            observation = str(raw.get("observation", "")).strip()
            key = self._normalize_context_text(observation)
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "date": str(raw.get("date") or "init"),
                    "observation": observation,
                    "trend": str(raw.get("trend", "")).strip(),
                    "emotion_guess": str(raw.get("emotion_guess", "")).strip(),
                }
            )
        return result

    def _init_insight_context(self) -> list[dict[str, object]]:
        raw_items = self._init_cognition_context.get("insights")
        items = raw_items if isinstance(raw_items, list) else []
        result: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            hypothesis = str(raw.get("hypothesis", "")).strip()
            key = self._normalize_context_text(hypothesis)
            if not key or key in seen:
                continue
            seen.add(key)
            evidence = raw.get("evidence")
            result.append(
                {
                    "hypothesis": hypothesis,
                    "evidence": [
                        str(item).strip()
                        for item in (evidence if isinstance(evidence, list) else [])
                        if str(item).strip()
                    ][:5],
                    "confidence": self._clamp_confidence(raw.get("confidence", 0.5)),
                    "validated": bool(raw.get("validated", False)),
                    "created_at": str(raw.get("created_at") or "init"),
                }
            )
        return result

    @staticmethod
    def _normalize_context_text(value: str) -> str:
        return " ".join(value.strip().lower().split())

    @staticmethod
    def _clamp_confidence(value: object) -> float:
        if not isinstance(value, str | int | float) or isinstance(value, bool):
            return 0.5
        try:
            number = float(value)
        except ValueError:
            return 0.5
        return max(0.0, min(1.0, number))

    def is_profile_ready(self) -> bool:
        """Cheap, non-raising check for whether a soul profile exists.

        Background-task consumers call this first to avoid using
        ``SoulProfileNotInitializedError`` as flow control during the
        ~7-minute init window — which would otherwise produce ERROR-level
        traces for every classify / awareness / speculator tick that
        runs before the profile lands.
        """
        try:
            return bool(self._memory.get_layer("soul").data)
        except Exception:
            return False

    async def get_profile(self) -> OnionProfile:
        """Get the current *effective* soul profile (AI profile ⊕ user overrides).

        Returns:
            The OnionProfile from the soul memory layer with user overrides
            merged on top. Active speculative interests are attached as
            ``_active_speculations``.
        """
        soul_data = self._memory.get_layer("soul").data
        if not soul_data:
            raise SoulProfileNotInitializedError("Soul profile has not been initialized yet.")
        profile = OnionProfile.from_dict(soul_data)
        profile = apply_overrides(profile, self._memory.load_profile_overrides())
        # Attach active speculations so downstream consumers (Discovery) can use them
        active_specs = self._speculator.get_active_speculations()
        if active_specs:
            profile._active_speculations = active_specs  # type: ignore[attr-defined]
        return profile

    async def get_raw_profile(self) -> OnionProfile:
        """Get the AI-generated profile WITHOUT user overrides.

        Used by the edit-state endpoint and drift detection so the UI can show
        the AI's current suggestion alongside the user's pinned value.
        """
        soul_data = self._memory.get_layer("soul").data
        if not soul_data:
            raise SoulProfileNotInitializedError("Soul profile has not been initialized yet.")
        return OnionProfile.from_dict(soul_data)

    def get_overrides(self) -> ProfileOverrides:
        """Return the current user-authored profile overrides."""
        return self._memory.load_profile_overrides()

    def get_effective_disliked_topics(self) -> list[str]:
        """Effective dislike terms for hard filters.

        Soul-side dislikes are taken from the EFFECTIVE profile (``apply_overrides``)
        so overlay edits at *every* granularity reflect here — domain add/remove
        AND per-domain specific add/remove. Flat ``preference.disliked_topics``
        (which lives outside the soul layer) is unioned in, but suppressed by any
        overlay dislike removal (domain- or specific-level) so a user-removed term
        is not re-added by the raw preference layer (F6).
        """
        overrides = self._memory.load_profile_overrides()
        terms: list[str] = []
        soul_data = self._memory.get_layer("soul").data
        if soul_data:
            effective = apply_overrides(OnionProfile.from_dict(soul_data), overrides)
            for domain in effective.interest.dislikes:
                terms.append(domain.domain)
                terms.extend(spec.name for spec in domain.specifics)
        remove_keys: set[str] = set()
        dislikes_edit = overrides.interest_edits.get("dislikes")
        if dislikes_edit is not None:
            removals = list(dislikes_edit.remove_domains)
            for spec_edit in dislikes_edit.specific_edits.values():
                removals.extend(spec_edit.remove)
            remove_keys = {item.strip().lower() for item in removals if item.strip()}
        preference_data = self._memory.get_layer("preference").data
        if isinstance(preference_data, dict):
            raw_topics = preference_data.get("disliked_topics")
            if isinstance(raw_topics, list):
                terms.extend(str(topic) for topic in raw_topics)
        result: list[str] = []
        seen: set[str] = set()
        for term in terms:
            key = term.strip().lower()
            if not key or key in seen or key in remove_keys:
                continue
            seen.add(key)
            result.append(term)
        return result

    async def apply_user_edit(
        self,
        *,
        target: str,
        op: str,
        value: object = None,
        parent: str = "",
        weight: float | None = None,
        database: Any | None = None,
        embedding_service: Any | None = None,
        llm_service: Any | None = None,
    ) -> dict[str, object]:
        """Apply one deterministic user edit to the profile overrides.

        Pipeline: snapshot effective dislikes → fold the edit into the
        overrides (validated; raises ``ProfileEditError`` on bad input) →
        persist → if the edit added *new* effective dislikes, purge matching
        already-pooled content (diff, not the raw value) → sync the matching
        speculator → record a manual cognition update → refresh the
        human-readable mirror (re-applies the overlay) and notify both
        surfaces. Returns ``{ok, target, op}``.
        """
        before = set(self.get_effective_disliked_topics())

        overrides = self._memory.load_profile_overrides()
        updated, _ = apply_edit(
            overrides, target=target, op=op, value=value, parent=parent, weight=weight
        )
        updated.updated_at = datetime.now().isoformat()
        self._memory.save_profile_overrides(updated)

        after = set(self.get_effective_disliked_topics())
        newly_added = sorted(after - before)

        self._sync_speculators_for_edit(target=target, op=op, value=value)
        self._record_manual_cognition(target=target, op=op, value=value)

        if self._memory.get_layer("soul").data:
            self._memory.sync_profile_files(await self.get_raw_profile())

        # The dislike pool purge does an embedding recall + LLM classification
        # that can take tens of seconds. It is a best-effort cleanup of
        # already-pooled content and MUST NOT block the edit response — doing so
        # makes the UI hang for the whole call and the new dislike appears "not
        # saved". Run it detached; the override itself is already persisted.
        if newly_added:
            self._schedule_dislike_purge(
                newly_added=newly_added,
                all_dislikes=sorted(after),
                database=database,
                embedding_service=embedding_service,
                llm_service=llm_service,
            )

        return {"ok": True, "target": target, "op": op}

    def _schedule_dislike_purge(self, **kwargs: Any) -> None:
        """Run a learned-dislike pool purge outside the interactive request.

        Every caller runs inside an event loop. Failures are swallowed inside
        ``_purge_for_new_dislikes``; the done-callback only drops the tracking
        reference.
        """
        task = asyncio.ensure_future(self._purge_for_new_dislikes(**kwargs))
        self._background_edit_tasks.add(task)
        task.add_done_callback(self._background_edit_tasks.discard)

    async def wait_for_pending_edits(self) -> None:
        """Await detached dislike-purge work from any learning path.

        Used by tests and graceful shutdown so the background purge can finish
        deterministically. No-op when nothing is pending.
        """
        tasks = list(self._background_edit_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _purge_for_new_dislikes(
        self,
        *,
        newly_added: list[str],
        all_dislikes: list[str],
        database: Any | None,
        embedding_service: Any | None,
        llm_service: Any | None,
    ) -> None:
        """Reuse the confirmed-avoidance purge for a newly learned dislike."""
        db = database if database is not None else getattr(self._memory, "_database", None)
        if db is None:
            logger.info("skip learned-dislike pool purge: no database available")
            return
        embedding = embedding_service if embedding_service is not None else self._embedding_service
        llm = llm_service if llm_service is not None else self._llm_service
        try:
            from openbiliclaw.soul.dislike_writeback import purge_pool_for_new_dislikes

            await purge_pool_for_new_dislikes(
                database=db,
                embedding_service=embedding,
                llm_service=llm,
                newly_added=newly_added,
                all_dislikes=all_dislikes,
            )
        except Exception:
            logger.exception("learned-dislike pool purge failed")

    def _apply_topic_lifecycle_evidence(
        self,
        existing_preference: dict[str, Any],
        updated_preference: dict[str, Any],
    ) -> None:
        """Overlay topic-lifecycle metadata onto a freshly analysed preference.

        Carries lifecycle fields forward from ``existing_preference`` and counts
        this analysis as one unit of evidence per surviving/new topic (new →
        trial; sustained → active; dormant → active). Best-effort: any failure
        is logged at DEBUG and never breaks the analysis path. Each transition
        is recorded to the ledger (write point ``topic_lifecycle``).
        """
        try:
            from openbiliclaw.soul.topic_lifecycle import apply_evidence

            existing_interests = [
                item for item in existing_preference.get("interests", []) if isinstance(item, dict)
            ]
            updated_interests = updated_preference.get("interests")
            if not isinstance(updated_interests, list):
                return
            merged, transitions = apply_evidence(existing_interests, updated_interests)
            updated_preference["interests"] = merged
            for tr in transitions:
                self._ledger.record(
                    write_point="topic_lifecycle",
                    source="evidence",
                    before={"topic": tr.name, "state": tr.from_state},
                    after={"topic": tr.name, "state": tr.to_state},
                    source_refs=[f"reason:{tr.reason}"],
                )
        except Exception:
            logger.debug("topic lifecycle evidence overlay failed", exc_info=True)

    def _archive_disliked_topics(
        self,
        updated_preference: dict[str, Any],
        disliked_topics: list[str],
    ) -> None:
        """Archive interests matching newly disliked topics (归档+避雷).

        The interest is archived, not deleted — it survives for audit/revert but
        stops competing for prompt slots. Best-effort; ledgers each transition.
        """
        try:
            from openbiliclaw.soul.topic_lifecycle import archive_topics

            interests = updated_preference.get("interests")
            if not isinstance(interests, list):
                return
            archived, transitions = archive_topics(interests, disliked_topics)
            updated_preference["interests"] = archived
            for tr in transitions:
                self._ledger.record(
                    write_point="topic_lifecycle",
                    source="dislike",
                    before={"topic": tr.name, "state": tr.from_state},
                    after={"topic": tr.name, "state": tr.to_state},
                    source_refs=[f"reason:{tr.reason}"],
                )
        except Exception:
            logger.debug("topic lifecycle dislike-archive failed", exc_info=True)

    def _sync_speculators_for_edit(self, *, target: str, op: str, value: object) -> None:
        """Keep the interest / avoidance speculators consistent with the edit.

        like add/remove → interest speculator confirm/reject; dislike
        add/remove → avoidance speculator confirm/reject. Defensive via
        getattr so older speculator doubles don't break edits.
        """
        if not isinstance(value, str) or not value.strip():
            return
        domain = value.strip()
        speculator: Any = None
        method_name = ""
        if target == "likes":
            speculator = self._speculator
            if op == "add":
                method_name = "user_confirm_speculation"
            elif op == "remove":
                method_name = "user_reject_speculation"
        elif target == "dislikes":
            speculator = self._avoidance_speculator
            if op == "add":
                method_name = "user_confirm_avoidance"
            elif op == "remove":
                method_name = "user_reject_avoidance"
        if not method_name:
            return
        fn = getattr(speculator, method_name, None)
        if callable(fn):
            try:
                fn(domain)
            except Exception:
                logger.debug("speculator sync failed: %s %s", target, op, exc_info=True)

    def _record_manual_cognition(self, *, target: str, op: str, value: object) -> None:
        summary = self._manual_edit_summary(target=target, op=op, value=value)
        if not summary:
            return
        updates = self._memory.load_cognition_updates()
        updates.insert(
            0,
            {
                "id": f"cognition-{uuid4()}",
                "kind": "manual_edit",
                "summary": summary,
                "impact": "",
                "reasoning": "",
                "evidence": "",
                "context_line": "你手动编辑了画像",
                "confidence": 1.0,
                "created_at": datetime.now().isoformat(),
                "source": "manual",
                "source_label": "手动编辑",
                "expand_hint": "summary_only",
                "notified": False,
            },
        )
        self._memory.save_cognition_updates(updates)

    @staticmethod
    def _manual_edit_summary(*, target: str, op: str, value: object) -> str:
        label = _MANUAL_EDIT_LABELS.get(target, target)
        text = value.strip() if isinstance(value, str) else ""
        if op == "add" and text:
            return f"你把「{text}」加进了{label}。"
        if op == "remove" and text:
            return f"你把「{text}」从{label}移除了。"
        if op == "set":
            return f"你改写了{label}。"
        if op == "reset":
            return f"你恢复了{label}的 AI 建议。"
        return f"你编辑了{label}。"

    async def update_from_feedback(self, feedback: dict[str, Any]) -> dict[str, Any]:
        """Update soul understanding based on explicit user feedback on a hypothesis.

        Confirm/reject feedback on a specific insight hypothesis calibrates that
        hypothesis: a confirm pins ``validated=True`` and raises confidence to at
        least 0.75; a reject sets ``validated=False`` and caps confidence at 0.35
        (the "soft invalidation" — the hypothesis is down-weighted in delight
        scoring rather than deleted). The feedback is also logged as an event.

        Wired to ``POST /api/insights/feedback`` so the UI's insight cards can
        drive this loop.

        Args:
            feedback: ``{"hypothesis": str, "signal": str}``. ``signal`` is one
                of confirm/like/support (positive) or reject/dislike/deny.

        Returns:
            A result dict describing whether a hypothesis matched and its
            post-update state — consumed by the API endpoint.
        """
        logger.info("Updating soul from feedback...")
        await self._memory.propagate_event(
            {
                "event_type": "feedback",
                "title": str(feedback.get("hypothesis", "")),
                "metadata": feedback,
            }
        )
        hypotheses = self._load_insights()
        target = self._normalize_text(str(feedback.get("hypothesis", "")))
        signal = str(feedback.get("signal", "")).strip().lower()
        result: dict[str, Any] = {
            "matched": False,
            "hypothesis": str(feedback.get("hypothesis", "")),
            "signal": signal,
            "validated": False,
            "confidence": 0.0,
        }
        migrated = False
        for item in hypotheses:
            if self._normalize_text(item.hypothesis) != target:
                continue
            if signal in {"confirm", "like", "support"}:
                item.validated = True
                item.confidence = min(1.0, round(max(item.confidence, 0.75), 4))
                migrated = True
            elif signal in {"reject", "dislike", "deny"}:
                item.validated = False
                item.confidence = max(0.0, round(min(item.confidence, 0.35), 4))
                migrated = True
            result["matched"] = True
            result["hypothesis"] = item.hypothesis
            result["validated"] = item.validated
            result["confidence"] = item.confidence
            break
        if result["matched"]:
            self._save_insights(hypotheses)
            # A confirm OR reject migration (single-point ownership, spec
            # invariant 4) schedules a debounced gated rebuild: the filtered
            # rebuild input changes (a confirm adds the hypothesis; a reject
            # drops it), so the next rebuild reflects — or squeezes out — it.
            if migrated:
                await self._mark_rebuild_pending(
                    [f"insight_feedback:{signal}:{str(result['hypothesis'])[:60]}"]
                )
            # The insight layer is the source of truth, but get_profile()
            # (UI profile-summary + delight scoring) reads the windowed
            # ``active_insights`` snapshot cached on the soul layer. Without
            # mirroring the calibration there, a confirm/reject wouldn't take
            # visible or recommendation effect until the next 12h cognition
            # sync. Patch the snapshot in place so the change is immediate.
            self._sync_insight_to_soul_snapshot(
                target_normalized=target,
                validated=bool(result["validated"]),
                confidence=float(result["confidence"]),
            )
        return result

    def _sync_insight_to_soul_snapshot(
        self,
        *,
        target_normalized: str,
        validated: bool,
        confidence: float,
    ) -> None:
        """Mirror an insight calibration onto the soul layer's active_insights.

        No-op when the soul profile has no matching active insight (e.g. the
        hypothesis exists only in the insight layer, not in the surfaced
        window). Re-syncs the human-readable profile files on change.
        """
        soul_layer = self._memory.get_layer("soul")
        if not soul_layer.data:
            return
        try:
            profile = OnionProfile.from_dict(soul_layer.data)
        except Exception:
            logger.debug("Failed to load OnionProfile for insight snapshot sync", exc_info=True)
            return
        changed = False
        for insight in profile.active_insights:
            if self._normalize_text(insight.hypothesis) == target_normalized:
                insight.validated = validated
                insight.confidence = confidence
                changed = True
        if not changed:
            return
        soul_layer.data.clear()
        soul_layer.data.update(profile.to_dict())
        soul_layer.save()
        try:
            self._memory.sync_profile_files(profile)
        except Exception:
            logger.debug("sync_profile_files after insight feedback failed", exc_info=True)

    async def learn_from_dialogue(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        session: str,
        scope: str = "chat",
        turn_id: str = "",
    ) -> dict[str, object]:
        """Persist a chat turn and update long-term understanding when warranted.

        ``scope`` / ``turn_id`` (Phase 1) are threaded from the durable chat
        path. ``scope`` defaults to ``"chat"``; only ``"chat"`` turns run the
        ``settles`` inventory settling (Task 3) — probe / confusion scopes are
        settled by the durable side-effect path (single ownership). ``turn_id``
        is stamped on the ledger rows as an idempotency observation key.
        """
        await self._memory.propagate_event(
            {
                "event_type": "dialogue",
                "title": user_message[:60],
                "metadata": {
                    "user_message": user_message,
                    "assistant_reply": assistant_reply,
                    "source": "chat",
                    "session": session,
                },
            }
        )
        active_list, insight_hash_map = self._build_dialogue_active_list()
        try:
            extract_result = await self._dialogue_insight_analyzer.extract(
                user_message=user_message,
                assistant_reply=assistant_reply,
                core_memory=self._memory.get_core_memory(),
                active_list=active_list,
            )
            # Tolerate the legacy list return as well as the new
            # {"candidates", "settles"} dict.
            if isinstance(extract_result, dict):
                extracted = list(extract_result.get("candidates", []))
                settles = list(extract_result.get("settles", []))
            else:
                extracted = list(extract_result)
                settles = []
        except DialogueInsightAnalysisError:
            logger.exception("Failed to extract dialogue insight candidates.")
            extracted = []
            settles = []

        # Process settles (single ownership, spec §invariant 6): only plain
        # scope="chat" turns settle here — probe / confusion durable turns are
        # settled by the durable side-effect path, so skip to avoid double
        # settling.
        if scope == "chat" and settles:
            await self._process_dialogue_settles(
                settles=settles,
                active_list=active_list,
                insight_hash_map=insight_hash_map,
                turn_id=turn_id,
            )

        merged_candidates = self._merge_insight_candidates(
            self._memory.load_insight_candidates(),
            extracted,
        )
        self._memory.save_insight_candidates(merged_candidates)
        self._record_immediate_dialogue_cognition(merged_candidates)
        eligible_candidates = [
            item for item in merged_candidates if self._candidate_ready_for_learning(item)
        ]
        if not eligible_candidates:
            return {
                "event_logged": True,
                "candidate_count": len(extracted),
                "preference_updated": False,
                "profile_rebuilt": False,
            }

        # Posture-gate access point ① (Phase 3): interest/dislike take the fast
        # line unchanged; goal/value/state deep candidates pass the gate. In
        # ``off`` this is a no-op (byte-identical feed); in ``shadow`` every deep
        # candidate stays but its judgement is recorded asynchronously; only
        # ``enforce`` drops rejected candidates and demotes downgraded ones to
        # insight hypotheses (confidence × 0.6).
        gated_candidates = await self._gate_dialogue_candidates(eligible_candidates)
        if not gated_candidates:
            return {
                "event_logged": True,
                "candidate_count": len(extracted),
                "preference_updated": False,
                "profile_rebuilt": False,
            }

        preference_layer = self._memory.get_layer("preference")
        existing_preference = dict(preference_layer.data)
        existing_profile = dict(self._memory.get_layer("soul").data)
        updated_preference = await self._preference_analyzer.analyze_events(
            events=[
                {
                    "event_type": "dialogue_insight",
                    "title": str(item.get("content", "")),
                    "metadata": {
                        "kind": item.get("kind", ""),
                        "confidence": item.get("confidence", 0.0),
                        "evidence": item.get("evidence", ""),
                        "source": "dialogue",
                        "occurrences": item.get("occurrences", 1),
                    },
                }
                for item in gated_candidates
            ],
            existing_preference=existing_preference,
        )
        old_disliked = {
            str(item).strip()
            for item in self._as_str_list(existing_preference.get("disliked_topics", []))
            if str(item).strip()
        }
        new_disliked = {
            str(item).strip()
            for item in self._as_str_list(updated_preference.get("disliked_topics", []))
            if str(item).strip()
        }
        newly_added_dislikes = sorted(new_disliked - old_disliked)
        candidate_refs = self._candidate_ledger_refs(eligible_candidates)
        # Topic freeze (Phase 2): a topic under an unresolved confusion must not
        # be further reinforced. New/upgraded weights for frozen topics are held
        # back here (existing weights untouched); no-op when nothing is frozen,
        # so a confusion-free database yields a byte-identical write.
        try:
            frozen_topics = self._confusion_manager.frozen_topics()
        except Exception:
            frozen_topics = set()
        if frozen_topics:
            updated_preference, held_updates = apply_confusion_freeze(
                before=existing_preference,
                after=updated_preference,
                frozen_topics=frozen_topics,
            )
            if held_updates:
                try:
                    self._confusion_manager.record_held_updates(held_updates)
                except Exception:
                    logger.debug("Failed to record held confusion updates", exc_info=True)
        # Topic-lifecycle (Phase 4): count this dialogue as evidence, then
        # archive any newly disliked topic (归档+避雷). Archive wins over the
        # evidence promotion for the same topic.
        self._apply_topic_lifecycle_evidence(existing_preference, updated_preference)
        if newly_added_dislikes:
            self._archive_disliked_topics(updated_preference, newly_added_dislikes)
        # Ledger write point D5 #1a: dialogue-driven preference overwrite.
        with self._ledger.action(
            write_point="dialogue_preference_overwrite",
            source="chat",
            before=existing_preference,
            source_refs=candidate_refs,
            turn_id=turn_id,
        ) as _entry:
            preference_layer.data.clear()
            preference_layer.data.update(updated_preference)
            preference_layer.save()
            _entry.after = dict(updated_preference)

        if newly_added_dislikes:
            # Start the deterministic purge as soon as the durable preference
            # write succeeds. A full profile rebuild can take tens of seconds;
            # it must not delay removing an explicitly rejected topic from the
            # active pool. Semantic recall continues detached in parallel.
            # Ledger write point D5 #2: dislike purge (records the intent at
            # schedule time; the detached recall itself is best-effort).
            self._ledger.record(
                write_point="dislike_purge",
                source="chat",
                before={"disliked_topics": sorted(old_disliked)},
                after={"disliked_topics": sorted(new_disliked)},
                source_refs=list(newly_added_dislikes),
                outcome="success",
                turn_id=turn_id,
            )
            self._schedule_dislike_purge(
                newly_added=newly_added_dislikes,
                all_dislikes=sorted(new_disliked),
                database=getattr(self._memory, "_database", None),
                embedding_service=self._embedding_service,
                llm_service=self._llm_service,
            )

        profile_rebuilt = False
        rebuild_gate_ok = (
            self._preference_changed_significantly(existing_preference, updated_preference)
            and not (
                await self._gate_soul_rebuild(
                    trigger=_REBUILD_TRIGGER_DIALOGUE,
                    existing_preference=existing_preference,
                    updated_preference=updated_preference,
                    source_refs=candidate_refs,
                    context={"candidate_refs": candidate_refs},
                )
            ).blocks
        )
        if rebuild_gate_ok:
            # Ledger write point D5 #1b: dialogue-driven full soul rebuild.
            try:
                with self._ledger.action(
                    write_point="dialogue_soul_rebuild",
                    source="chat",
                    before=existing_profile,
                    source_refs=candidate_refs,
                    turn_id=turn_id,
                ) as _entry:
                    legacy_profile = await self._profile_builder.build(
                        history=[],
                        preference=updated_preference,
                        awareness_notes=[
                            awareness_note_to_dict(item) for item in self._load_awareness_notes()
                        ],
                        active_insights=self._rebuild_active_insights(),
                    )
                    profile = OnionProfile.from_legacy(legacy_profile)
                    profile.populate_from_flat_preference(updated_preference)
                    soul_layer = self._memory.get_layer("soul")
                    soul_layer.data.clear()
                    soul_layer.data.update(profile.to_dict())
                    soul_layer.save()
                    _entry.after = dict(soul_layer.data)
                self._memory.sync_profile_files(profile)
                profile_rebuilt = True
            except Exception:
                logger.exception("Failed to rebuild soul profile after dialogue learning.")

        self._record_cognition_updates(
            existing_preference=existing_preference,
            updated_preference=updated_preference,
            previous_profile=existing_profile,
            current_profile=dict(self._memory.get_layer("soul").data),
            source="chat",
        )

        for item in merged_candidates:
            if self._candidate_ready_for_learning(item):
                item["applied"] = True
                item["updated_at"] = datetime.now().isoformat()
        self._memory.save_insight_candidates(merged_candidates)

        # Next dialogue-learning pass also triggers the debounced confirmed-
        # hypotheses rebuild (spec invariant 4). Best-effort.
        try:
            await self.run_pending_rebuild_if_due()
        except Exception:
            logger.debug("pending rebuild trigger (dialogue) failed", exc_info=True)

        return {
            "event_logged": True,
            "candidate_count": len(extracted),
            "preference_updated": True,
            "profile_rebuilt": profile_rebuilt,
        }

    async def process_feedback_batch_if_needed(self) -> dict[str, object]:
        """Reanalyze preference/profile after enough new feedback has accumulated."""
        if self._feedback_batch_lock.locked():
            return {
                "triggered": False,
                "feedback_count": 0,
                "preference_updated": False,
                "profile_rebuilt": False,
                "skipped": True,
                "reason": "feedback_batch_in_progress",
            }
        async with self._feedback_batch_lock:
            result = await self._process_feedback_batch_if_needed_locked()
        # Consume any held updates left ``replaying`` by a resolved real-interest
        # confusion (Wave B held-replay leftover). Best-effort — a replay failure
        # never breaks feedback processing; the items stay ``replaying`` for a
        # later run (and startup crash recovery bounds the worst case).
        try:
            await self.replay_held_updates()
        except Exception:
            logger.debug("held-replay consumer failed", exc_info=True)
        # A periodic hook for the debounced confirmed-hypotheses rebuild (spec
        # invariant 4). Best-effort — never breaks feedback processing.
        try:
            await self.run_pending_rebuild_if_due()
        except Exception:
            logger.debug("pending rebuild trigger (feedback batch) failed", exc_info=True)
        return result

    async def _process_feedback_batch_if_needed_locked(self) -> dict[str, object]:
        """Feedback batch implementation guarded by ``_feedback_batch_lock``."""
        state = self._memory.load_feedback_state()
        last_processed_id = self._to_int(state.get("last_processed_feedback_event_id", 0))
        all_feedback_events = [
            self._deserialize_event(event)
            for event in self._memory.query_events_since(
                after_event_id=last_processed_id,
                event_types=["feedback"],
            )
        ]
        # Retractions (X unlike/unbookmark) are neutralizations, not
        # preference-learning input — exclude them from BOTH the threshold
        # count and the LLM analysis batch. They still advance the cursor
        # below so they aren't rescanned every cycle.
        feedback_events = [
            event for event in all_feedback_events if not self._is_retraction_feedback(event)
        ]
        feedback_count = len(feedback_events)
        if feedback_count < self._feedback_batch_threshold:
            return {
                "triggered": False,
                "feedback_count": feedback_count,
                "preference_updated": False,
                "profile_rebuilt": False,
            }

        preference_layer = self._memory.get_layer("preference")
        existing_preference = dict(preference_layer.data)
        existing_profile = dict(self._memory.get_layer("soul").data)
        updated_preference = await self._preference_analyzer.analyze_events(
            events=[self._compact_feedback_event_for_analysis(event) for event in feedback_events],
            existing_preference=existing_preference,
            event_chunk_size=DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
        )
        old_disliked = {
            str(item).strip()
            for item in self._as_str_list(existing_preference.get("disliked_topics", []))
            if str(item).strip()
        }
        new_disliked = {
            str(item).strip()
            for item in self._as_str_list(updated_preference.get("disliked_topics", []))
            if str(item).strip()
        }
        newly_added_dislikes = sorted(new_disliked - old_disliked)
        # Topic-lifecycle (Phase 4): evidence overlay + dislike archive.
        self._apply_topic_lifecycle_evidence(existing_preference, updated_preference)
        if newly_added_dislikes:
            self._archive_disliked_topics(updated_preference, newly_added_dislikes)
        feedback_refs = [
            f"feedback_event:{self._to_int(event.get('id', 0))}" for event in feedback_events
        ] or ["feedback_batch"]
        # Ledger write point D5 #4a: feedback-batch preference overwrite.
        with self._ledger.action(
            write_point="feedback_preference_overwrite",
            source="feedback",
            before=existing_preference,
            source_refs=feedback_refs,
        ) as _entry:
            preference_layer.data.clear()
            preference_layer.data.update(updated_preference)
            preference_layer.save()
            _entry.after = dict(updated_preference)

        profile_rebuilt = False
        # P2 (spec r3/F4): a feedback batch with a significant preference shift
        # now passes access point ③ — previously this rebuild bypassed every
        # gate. off/shadow proceed; enforce downgrade/reject abandons the rebuild.
        feedback_rebuild_ok = (
            self._preference_changed_significantly(existing_preference, updated_preference)
            and not (
                await self._gate_soul_rebuild(
                    trigger=_REBUILD_TRIGGER_FEEDBACK_BATCH,
                    existing_preference=existing_preference,
                    updated_preference=updated_preference,
                    source_refs=feedback_refs,
                    context={"feedback_count": feedback_count, "feedback_refs": feedback_refs},
                )
            ).blocks
        )
        if feedback_rebuild_ok:
            # Ledger write point D5 #4b: feedback-batch full soul rebuild.
            try:
                with self._ledger.action(
                    write_point="feedback_soul_rebuild",
                    source="feedback",
                    before=existing_profile,
                    source_refs=feedback_refs,
                ) as _entry:
                    legacy_profile = await self._profile_builder.build(
                        history=[],
                        preference=updated_preference,
                        awareness_notes=[
                            awareness_note_to_dict(item) for item in self._load_awareness_notes()
                        ],
                        active_insights=self._rebuild_active_insights(),
                    )
                    profile = OnionProfile.from_legacy(legacy_profile)
                    profile.populate_from_flat_preference(updated_preference)
                    soul_layer = self._memory.get_layer("soul")
                    soul_layer.data.clear()
                    soul_layer.data.update(profile.to_dict())
                    soul_layer.save()
                    _entry.after = dict(soul_layer.data)
                self._memory.sync_profile_files(profile)
                profile_rebuilt = True
            except Exception:
                logger.exception("Failed to rebuild soul profile after feedback refresh.")

        if newly_added_dislikes:
            self._schedule_dislike_purge(
                newly_added=newly_added_dislikes,
                all_dislikes=sorted(new_disliked),
                database=getattr(self._memory, "_database", None),
                embedding_service=self._embedding_service,
                llm_service=self._llm_service,
            )

        self._record_cognition_updates(
            existing_preference=existing_preference,
            updated_preference=updated_preference,
            previous_profile=existing_profile,
            current_profile=dict(self._memory.get_layer("soul").data),
            source="feedback",
        )

        # Advance past everything scanned (retractions included) so excluded
        # rows aren't rescanned each cycle.
        last_scanned_id = max(
            (self._to_int(event.get("id", 0)) for event in all_feedback_events),
            default=0,
        )
        self._memory.save_feedback_state(
            {
                "last_processed_feedback_event_id": last_scanned_id,
                "last_feedback_reanalyzed_at": datetime.now().isoformat(),
            }
        )
        return {
            "triggered": True,
            "feedback_count": feedback_count,
            "preference_updated": True,
            "profile_rebuilt": profile_rebuilt,
        }

    def _compact_feedback_event_for_analysis(
        self,
        event: dict[str, object],
    ) -> dict[str, object]:
        """Keep only preference-relevant feedback fields before LLM analysis."""
        compact: dict[str, object] = {}
        for key in (
            "id",
            "event_type",
            "url",
            "title",
            "context",
            "inferred_satisfaction",
            "satisfaction_reason",
            "created_at",
        ):
            value = event.get(key)
            if value not in (None, ""):
                compact[key] = value

        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            compact_metadata = {
                key: value
                for key, value in metadata.items()
                if key in _FEEDBACK_ANALYSIS_METADATA_KEYS and value not in (None, "")
            }
            if compact_metadata:
                compact["metadata"] = compact_metadata
        return compact

    def record_immediate_feedback_cognition(
        self,
        *,
        feedback_type: str,
        title: str,
        note: str = "",
    ) -> None:
        """Record one lightweight cognition update from a single strong feedback.

        This path is intentionally cheap: it only appends a short cognition update
        for UI visibility and does not trigger preference/profile rebuilds.
        """
        normalized_feedback = feedback_type.strip().lower()
        summary = ""
        kind = ""
        impact = ""
        reasoning = ""
        evidence = ""
        context_line = ""
        if normalized_feedback == "comment" and note.strip():
            kind = "profile_shift"
            title_text = title.strip()
            if title_text:
                summary = f"阿B 刚记下了你对《{title_text}》的评论。"
                evidence = f"你评论《{title_text}》时说：{note.strip()}"
                context_line = f"来自：《{title_text}》"
            else:
                summary = f"阿B 刚记下了：{note.strip()}"
                evidence = note.strip()
                context_line = "来自：这次推荐反馈"
            impact = "画像会结合评论内容判断这是喜欢、不喜欢还是补充说明，不会默认当成正向偏好。"
            reasoning = "这属于一条中性直接反馈，先记作方向修正，不直接重写整张画像。"
        elif normalized_feedback == "dislike":
            note_text = note.strip()
            generic_dislike_notes = {"太浅了", "不喜欢", "一般", "太水了", "没意思"}
            topic = (
                title.strip() if not note_text or note_text in generic_dislike_notes else note_text
            )
            if topic:
                kind = "dislike_added"
                summary = f"阿B 记住了：像“{topic}”这种内容你大概率会划走。"
                impact = "画像里的避雷方向会更明确，后面会更主动绕开这类内容。"
                reasoning = "这是一次明确负反馈，先把这个方向记成近期避雷。"
                evidence = note_text or title.strip()
                context_line = self._build_feedback_context_line(title)
        elif normalized_feedback == "like":
            title_text = title.strip()
            if title_text:
                kind = "interest_added"
                summary = f"阿B 记住了：像《{title_text}》这一路你大概率会继续想看。"
                impact = "画像里对这类方向的偏好会更明确，后面会更愿意继续补。"
                reasoning = "这是一次明确正反馈，先把这个方向记成近期偏好强化。"
                evidence = note.strip() or title_text
                context_line = self._build_feedback_context_line(title)
        else:
            return

        if not summary:
            return

        updates = self._memory.load_cognition_updates()
        if any(
            str(item.get("summary", "")).strip() == summary
            for item in updates
            if isinstance(item, dict)
        ):
            return
        updates.insert(
            0,
            {
                "id": f"cognition-{uuid4()}",
                "kind": kind,
                "summary": summary,
                "impact": impact,
                "reasoning": reasoning,
                "evidence": evidence,
                "context_line": context_line or "基于最近几条相关内容",
                "confidence": 0.82 if kind == "dislike_added" else 0.84,
                "created_at": datetime.now().isoformat(),
                "source": "feedback",
                "source_label": self._build_source_label("feedback"),
                "expand_hint": self._build_expand_hint(
                    impact=impact,
                    reasoning=reasoning,
                    evidence=evidence,
                ),
                "notified": False,
            },
        )
        self._memory.save_cognition_updates(updates)

    def _record_immediate_dialogue_cognition(
        self,
        candidates: list[dict[str, object]],
    ) -> None:
        """Record one lightweight cognition update from a single strong chat signal."""
        updates = self._memory.load_cognition_updates()
        changed = False
        for candidate in candidates:
            if not self._candidate_ready_for_immediate_dialogue_cognition(candidate):
                continue
            (
                summary,
                kind,
                impact,
                reasoning,
                evidence,
                context_line,
            ) = self._build_immediate_dialogue_cognition(candidate)
            if not summary:
                continue
            if any(
                str(item.get("summary", "")).strip() == summary
                for item in updates
                if isinstance(item, dict)
            ):
                continue
            updates.insert(
                0,
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": kind,
                    "summary": summary,
                    "impact": impact,
                    "reasoning": reasoning,
                    "evidence": evidence,
                    "context_line": context_line,
                    "confidence": round(self._to_float(candidate.get("confidence", 0.0)), 4),
                    "created_at": datetime.now().isoformat(),
                    "source": "chat",
                    "source_label": self._build_source_label("chat"),
                    "expand_hint": self._build_expand_hint(
                        impact=impact,
                        reasoning=reasoning,
                        evidence=evidence,
                    ),
                    "notified": False,
                },
            )
            changed = True
        if changed:
            self._memory.save_cognition_updates(updates)

    async def generate_awareness_note(self) -> str:
        """Generate a daily awareness note.

        The awareness note captures what the agent has observed about
        the user's recent behavior patterns, mood changes, and interest shifts.

        Returns:
            Natural language awareness note.
        """
        events = self._memory.query_events(limit=50)
        notes = await self._awareness_analyzer.analyze(
            events=events,
            preference=self._memory.get_layer("preference").data,
            soul_profile=self._memory.get_layer("soul").data,
        )
        if not notes:
            return ""
        merged = self._awareness_analyzer.merge_notes(self._load_awareness_notes(), notes)
        self._save_awareness_notes(merged)
        return notes[0].observation

    async def generate_insight(self) -> str:
        """Generate or update insight hypotheses.

        Insights are deeper interpretations of user behavior:
        - Why they do what they do
        - What psychological needs are being met
        - What latent interests might exist

        Returns:
            Natural language insight.
        """
        awareness_notes = self._load_awareness_notes()
        insights = await self._insight_analyzer.analyze(
            awareness_notes=awareness_notes,
            preference=self._memory.get_layer("preference").data,
            soul_profile=self._memory.get_layer("soul").data,
        )
        if not insights:
            return ""
        merged = self._insight_analyzer.merge_insights(self._load_insights(), insights)
        self._save_insights(merged)
        return insights[0].hypothesis

    def _load_awareness_notes(self) -> list[AwarenessNote]:
        layer_data = self._memory.get_layer("awareness").data
        notes = layer_data.get("notes", [])
        return [awareness_note_from_dict(item) for item in notes if isinstance(item, dict)]

    def _save_awareness_notes(self, notes: list[AwarenessNote]) -> None:
        layer = self._memory.get_layer("awareness")
        layer.data.clear()
        layer.data.update({"notes": [awareness_note_to_dict(item) for item in notes]})
        layer.save()

    def _load_insights(self) -> list[InsightHypothesis]:
        layer_data = self._memory.get_layer("insight").data
        hypotheses = layer_data.get("hypotheses", [])
        return [insight_hypothesis_from_dict(item) for item in hypotheses if isinstance(item, dict)]

    def _save_insights(self, insights: list[InsightHypothesis]) -> None:
        layer = self._memory.get_layer("insight")
        layer.data.clear()
        layer.data.update({"hypotheses": [insight_hypothesis_to_dict(item) for item in insights]})
        layer.save()

    def _rebuild_active_insights(self) -> list[dict[str, object]]:
        """Insight dicts eligible to shape a soul rebuild (spec invariant 3 / F1).

        Only validated hypotheses with confidence >= 0.75 are visible to a
        rebuild; rejected/unvalidated ones are filtered out, so a reject's next
        rebuild squeezes the old conclusion out instead of leaving it forever.
        """
        return [
            insight_hypothesis_to_dict(item)
            for item in self._load_insights()
            if item.validated and item.confidence >= _REBUILD_MIN_CONFIDENCE
        ]

    # -- Pending confirmed-hypotheses rebuild state machine (spec r3/F3) -------

    def _rebuild_state_path(self) -> Path | None:
        data_dir = getattr(self._memory, "_data_dir", None)
        if data_dir is None:
            return None
        return Path(data_dir) / "memory" / "rebuild_pending_state.json"

    def _load_rebuild_state(self) -> dict[str, Any]:
        path = self._rebuild_state_path()
        if path is None or not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_rebuild_state(self, state: dict[str, Any]) -> None:
        path = self._rebuild_state_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            tmp_path.replace(path)
        except OSError:
            logger.debug("Failed to save rebuild pending state", exc_info=True)

    async def _mark_rebuild_pending(self, trigger_refs: list[str]) -> None:
        """Set/refresh the pending marker (confirm & reject single point, inv 4).

        A new migration always re-stamps ``set_at`` and resets ``retry_count`` —
        "new evidence reopens" — merging its refs with any still-pending ones.
        """
        async with self._rebuild_pending_lock:
            state = self._load_rebuild_state()
            existing = state.get("pending")
            refs = (
                list(existing.get("trigger_refs", []))
                if isinstance(existing, dict) and isinstance(existing.get("trigger_refs"), list)
                else []
            )
            for ref in trigger_refs:
                if ref not in refs:
                    refs.append(ref)
            state["pending"] = {
                "set_at": datetime.now().isoformat(),
                "trigger_refs": refs,
                "retry_count": 0,
            }
            self._save_rebuild_state(state)

    async def run_pending_rebuild_if_due(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Run the debounced gated confirmed-hypotheses rebuild when due (inv 4).

        Triggered by the 12h cognition loop and by the next dialogue-learning /
        feedback-batch pass. Debounced by ``_DEEP_REBUILD_DEBOUNCE_HOURS``.
        Clear-marker semantics (spec invariant 4):

        - gate accept + build ok → clear the marker.
        - gate downgrade/reject that is NOT an error (``is_error=False``) → this
          batch is abandoned: clear the marker + record ``last_gate_refusal``. A
          later confirm/reject re-opens it (no infinite retry).
        - gate error OR build exception (``is_error=True``) → keep the marker,
          bump ``retry_count``; after ``_REBUILD_MAX_RETRIES`` clear + WARNING.

        A concurrent re-mark during the build is reconciled by comparing
        ``set_at`` (compare-and-swap): if it changed, the fresh marker is left
        intact rather than clobbered by this run's outcome.
        """
        current = now or datetime.now()
        async with self._rebuild_pending_lock:
            if self._rebuild_running:
                return {"ran": False, "reason": "in_progress"}
            state = self._load_rebuild_state()
            pending = state.get("pending")
            if not isinstance(pending, dict):
                return {"ran": False, "reason": "not_pending"}
            set_at = self._parse_iso(pending.get("set_at"))
            if set_at is None or (current - set_at) < timedelta(hours=_DEEP_REBUILD_DEBOUNCE_HOURS):
                return {"ran": False, "reason": "debounced"}
            started_set_at = str(pending.get("set_at", ""))
            trigger_refs = [str(r) for r in pending.get("trigger_refs", []) if isinstance(r, str)]
            retry_count = int(pending.get("retry_count", 0) or 0)
            self._rebuild_running = True

        # The long LLM build runs WITHOUT the lock so a concurrent confirm/reject
        # can re-mark pending (reconciled below via compare-and-swap on set_at).
        try:
            outcome = await self._execute_pending_rebuild(trigger_refs)
        except Exception:
            logger.exception("pending rebuild dispatch failed")
            outcome = "error"

        async with self._rebuild_pending_lock:
            self._rebuild_running = False
            state = self._load_rebuild_state()
            pending = state.get("pending")
            if not isinstance(pending, dict) or str(pending.get("set_at", "")) != started_set_at:
                # A newer confirm/reject reopened the marker mid-build — leave it.
                return {"ran": True, "outcome": outcome, "superseded": True}
            if outcome == "accept":
                state["pending"] = None
            elif outcome == "refusal":
                state["pending"] = None
                state["last_gate_refusal"] = {
                    "at": current.isoformat(),
                    "trigger_refs": trigger_refs,
                }
            else:  # error
                retry_count += 1
                if retry_count >= _REBUILD_MAX_RETRIES:
                    logger.warning(
                        "pending rebuild exceeded retry budget (%d); clearing marker",
                        _REBUILD_MAX_RETRIES,
                    )
                    state["pending"] = None
                else:
                    pending["retry_count"] = retry_count
                    state["pending"] = pending
            self._save_rebuild_state(state)
            return {"ran": True, "outcome": outcome, "retry_count": retry_count}

    async def _execute_pending_rebuild(self, trigger_refs: list[str]) -> str:
        """Gate + run one confirmed-hypotheses rebuild. Returns the outcome tag.

        ``accept`` (rebuilt), ``refusal`` (gate downgrade/reject, real verdict),
        or ``error`` (gate is_error, or a build exception).
        """
        preference = dict(self._memory.get_layer("preference").data)
        existing_profile = dict(self._memory.get_layer("soul").data)
        validated = [
            item
            for item in self._load_insights()
            if item.validated and item.confidence >= _REBUILD_MIN_CONFIDENCE
        ]
        context: dict[str, object] = {
            "confirmed_hypotheses": [item.hypothesis for item in validated][
                :_REBUILD_CONTEXT_HYPOTHESIS_CAP
            ],
            "trigger_refs": trigger_refs,
        }
        source_refs = trigger_refs or ["rebuild_pending"]
        decision = await self._gate_soul_rebuild(
            trigger=_REBUILD_TRIGGER_CONFIRMED_HYPOTHESES,
            existing_preference=preference,
            updated_preference=preference,
            source_refs=source_refs,
            context=context,
        )
        if decision.blocks:
            return "error" if decision.is_error else "refusal"
        try:
            with self._ledger.action(
                write_point="hypotheses_soul_rebuild",
                source="hypotheses",
                before=existing_profile,
                source_refs=source_refs,
            ) as _entry:
                legacy_profile = await self._profile_builder.build(
                    history=[],
                    preference=preference,
                    awareness_notes=[
                        awareness_note_to_dict(item) for item in self._load_awareness_notes()
                    ],
                    active_insights=self._rebuild_active_insights(),
                )
                profile = OnionProfile.from_legacy(legacy_profile)
                profile.populate_from_flat_preference(preference)
                soul_layer = self._memory.get_layer("soul")
                soul_layer.data.clear()
                soul_layer.data.update(profile.to_dict())
                soul_layer.save()
                _entry.after = dict(soul_layer.data)
            self._memory.sync_profile_files(profile)
            return "accept"
        except Exception:
            logger.exception("Failed to rebuild soul profile from confirmed hypotheses")
            return "error"

    @staticmethod
    def _parse_iso(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _merge_insight_candidates(
        self,
        existing_candidates: list[dict[str, object]],
        new_candidates: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        merged = [dict(item) for item in existing_candidates if isinstance(item, dict)]
        for raw_candidate in new_candidates:
            kind = str(raw_candidate.get("kind", "")).strip() or "state"
            content = str(raw_candidate.get("content", "")).strip()
            if not content:
                continue
            normalized_content = self._normalize_text(content)
            existing = next(
                (
                    item
                    for item in merged
                    if self._normalize_text(str(item.get("content", ""))) == normalized_content
                    and str(item.get("kind", "")).strip() == kind
                ),
                None,
            )
            now = datetime.now().isoformat()
            confidence = self._to_float(raw_candidate.get("confidence", 0.0))
            evidence = str(raw_candidate.get("evidence", "")).strip()
            if existing is None:
                merged.append(
                    {
                        "id": str(uuid4()),
                        "kind": kind,
                        "content": content,
                        "confidence": max(0.0, min(1.0, round(confidence, 4))),
                        "evidence": evidence,
                        "occurrences": 1,
                        "confirmed": False,
                        "applied": False,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                continue
            existing["occurrences"] = self._to_int(existing.get("occurrences", 0)) + 1
            existing["confidence"] = max(
                self._to_float(existing.get("confidence", 0.0)),
                max(0.0, min(1.0, round(confidence, 4))),
            )
            if evidence:
                existing["evidence"] = evidence
            existing["updated_at"] = now
        return merged

    def _build_dialogue_active_list(
        self,
    ) -> tuple[dict[str, object], dict[str, str]]:
        """Assemble the settle-injection list (speculations / insights / confusions).

        Returns ``(active_list, insight_hash_map)`` where ``insight_hash_map``
        maps the injected insight hash key -> hypothesis text. Confusions are an
        empty list in Wave A (the confusion object lands in Wave B).
        """
        speculations: list[dict[str, object]] = []
        try:
            active_specs = self._speculator.get_active_speculations()
            # Cap at 10 (spec: activelist speculation ≤10) to bound the prompt.
            for spec in active_specs[:10]:
                domain = str(getattr(spec, "domain", "")).strip()
                if domain:
                    speculations.append({"domain": domain})
        except Exception:
            logger.debug("Failed to load active speculations for settles", exc_info=True)

        insight_hypotheses = [
            item.hypothesis for item in self._load_insights() if item.hypothesis.strip()
        ]
        insight_hash_map = build_hash8_map(insight_hypotheses)
        # Reverse map (hypothesis -> key) preserves injection order for the prompt.
        key_by_text = {text: key for key, text in insight_hash_map.items()}
        insights = [
            {"hash": key_by_text[text], "hypothesis": text}
            for text in insight_hypotheses
            if text in key_by_text
        ]

        confusions: list[dict[str, object]] = []
        try:
            for confusion in self._confusion_manager.list_active():
                confusions.append(
                    {
                        "id": str(confusion.id),
                        "topic": confusion.topic,
                        "observation": confusion.observation,
                    }
                )
        except Exception:
            logger.debug("Failed to load active confusions for settles", exc_info=True)

        active_list: dict[str, object] = {
            "speculations": speculations,
            "insights": insights,
            "confusions": confusions,
        }
        return active_list, insight_hash_map

    async def _process_dialogue_settles(
        self,
        *,
        settles: list[dict[str, object]],
        active_list: dict[str, object],
        insight_hash_map: dict[str, str],
        turn_id: str,
    ) -> None:
        """Settle active objects referenced by a chat turn (whitelist = injected).

        ``ref`` must appear in the round's injection list (spec §invariant 5);
        unknown refs are dropped with WARNING. Settling calls existing functions
        (speculation confirm/reject, insight feedback) and records a ledger row
        stamped with ``turn_id`` (idempotency observation key).
        """
        spec_domains = {
            str(item.get("domain", "")).strip()
            for item in _as_dict_list(active_list.get("speculations"))
        }
        confusion_ids = {
            str(item.get("id", "")).strip() for item in _as_dict_list(active_list.get("confusions"))
        }
        for settle in settles:
            kind = str(settle.get("kind", "")).strip()
            ref = str(settle.get("ref", "")).strip()
            verdict = str(settle.get("verdict", "")).strip()
            if not ref:
                continue
            if kind == "speculation":
                if ref not in spec_domains:
                    logger.warning("dialogue settle ref not in injected list: %s", ref)
                    continue
                await self._settle_speculation(ref, verdict, turn_id)
            elif kind == "insight":
                hypothesis = insight_hash_map.get(ref)
                if hypothesis is None:
                    logger.warning("dialogue settle ref not in injected list: %s", ref)
                    continue
                await self._settle_insight(hypothesis, verdict, turn_id)
            elif kind == "confusion":
                if ref not in confusion_ids:
                    logger.warning("dialogue settle ref not in injected list: %s", ref)
                    continue
                self._settle_confusion(ref, verdict, turn_id)
            else:
                logger.warning("dialogue settle dropped: unknown kind=%s", kind)

    async def _settle_speculation(self, domain: str, verdict: str, turn_id: str) -> None:
        before = {"domain": domain, "verdict": verdict}
        applied = False
        try:
            if verdict == "confirm":
                applied = bool(self._speculator.user_confirm_speculation(domain))
            elif verdict == "reject":
                applied = bool(self._speculator.user_reject_speculation(domain))
        except Exception:
            logger.exception("Failed to settle speculation %s", domain)
        self._ledger.record(
            write_point="settle_speculation",
            source="chat",
            before=before,
            after={"domain": domain, "applied": applied},
            source_refs=[domain],
            outcome="success" if applied else "failed",
            turn_id=turn_id,
        )

    async def _settle_insight(self, hypothesis: str, verdict: str, turn_id: str) -> None:
        signal = "confirm" if verdict == "confirm" else "reject"
        result: dict[str, Any] = {}
        try:
            result = await self.update_from_feedback({"hypothesis": hypothesis, "signal": signal})
        except Exception:
            logger.exception("Failed to settle insight via feedback")
        matched = bool(result.get("matched", result.get("updated", False)))
        self._ledger.record(
            write_point="settle_insight",
            source="chat",
            before={"hypothesis": hypothesis[:80], "verdict": verdict},
            after={"matched": matched},
            source_refs=[hypothesis[:60]],
            outcome="success" if matched else "failed",
            turn_id=turn_id,
        )

    def _settle_confusion(self, ref: str, verdict: str, turn_id: str) -> None:
        """Directly resolve a confusion referenced from a plain chat turn.

        ``confirm`` → the confused behaviour reflects a real interest
        (``real_interest``, held updates replay); ``reject`` → it was a proxy /
        misread (``proxy_behavior``, held updates discarded). The confusion's
        direct-settle exit (gate off ⇒ direct write + ledger, spec §Phase 2).
        """
        try:
            confusion_id = int(ref)
        except (TypeError, ValueError):
            logger.warning("confusion settle dropped: non-int ref=%r", ref)
            return
        resolution = "real_interest" if verdict == "confirm" else "proxy_behavior"
        terminal: str | None = None
        try:
            terminal = self._confusion_manager.resolve(
                confusion_id, resolution=resolution, note="chat_settle"
            )
        except Exception:
            logger.exception("Failed to settle confusion %s", confusion_id)
        self._ledger.record(
            write_point="settle_confusion",
            source="chat",
            before={"confusion_id": confusion_id, "verdict": verdict},
            after={"resolution": resolution, "status": terminal},
            source_refs=[ref],
            outcome="success" if terminal else "failed",
            turn_id=turn_id,
        )

    async def replay_held_updates(self) -> dict[str, object]:
        """Rebase resolved-real-interest held updates into preference analysis.

        Wave B held-replay consumer (leftover wiring). A confusion resolved as
        ``real_interest`` leaves its held topic updates in the ``replaying``
        state with a receipt. This consumer feeds those held topics as evidence
        into the preference analyzer (rebase semantics — never a direct weight
        write), persists the result through the normal chokepoint (freeze + the
        interest fast line, which is not gated), then marks the replay
        ``applied``. Idempotent: once applied the items are no longer
        ``replaying``, so a second run is a no-op. A crash between the
        preference write and ``mark_replay_applied`` is reconciled to
        ``applied_unverified`` by :meth:`recover_replaying` at next startup.
        """
        pending = self._confusion_manager.pending_replays()
        if not pending:
            return {"replayed": 0, "confusions": 0}
        events: list[dict[str, object]] = []
        for confusion in pending:
            for held in confusion.held_updates:
                if held.state != "replaying":
                    continue
                events.append(
                    {
                        "event_type": "dialogue_insight",
                        "title": held.topic,
                        "metadata": {
                            "kind": "interest",
                            "confidence": held.value,
                            "evidence": "疑惑被确认为真实兴趣，重放此前搁置的兴趣变更。",
                            "source": "confusion_replay",
                            "occurrences": 1,
                        },
                    }
                )
        if not events:
            return {"replayed": 0, "confusions": 0}
        preference_layer = self._memory.get_layer("preference")
        existing_preference = dict(preference_layer.data)
        updated_preference = await self._preference_analyzer.analyze_events(
            events=events,
            existing_preference=existing_preference,
        )
        # Freeze filter still applies (other topics may be frozen); the replayed
        # topics themselves are resolved, so they are no longer frozen.
        try:
            frozen_topics = self._confusion_manager.frozen_topics()
        except Exception:
            frozen_topics = set()
        if frozen_topics:
            updated_preference, held_updates = apply_confusion_freeze(
                before=existing_preference,
                after=updated_preference,
                frozen_topics=frozen_topics,
            )
            if held_updates:
                try:
                    self._confusion_manager.record_held_updates(held_updates)
                except Exception:
                    logger.debug("Failed to record held confusion updates", exc_info=True)
        with self._ledger.action(
            write_point="confusion_replay_preference",
            source="confusion",
            before=existing_preference,
            source_refs=[c.topic for c in pending if c.topic],
        ) as _entry:
            preference_layer.data.clear()
            preference_layer.data.update(updated_preference)
            preference_layer.save()
            _entry.after = dict(updated_preference)
        for confusion in pending:
            self._confusion_manager.mark_replay_applied(confusion.id)
        return {"replayed": len(events), "confusions": len(pending)}

    # -- Posture gate (Phase 3) ----------------------------------------------

    def _ledger_digest_for_gate(self) -> list[dict[str, object]]:
        """Compact 30-day ledger digest fed to the posture gate as context."""
        query = getattr(self._ledger_database, "query_profile_ledger", None)
        if not callable(query):
            return []
        try:
            rows = query(days=30, limit=30)
        except Exception:
            return []
        digest: list[dict[str, object]] = []
        for row in rows:
            digest.append(
                {
                    "write_point": str(row.get("write_point", "")),
                    "source": str(row.get("source", "")),
                    "outcome": str(row.get("outcome", "")),
                    "gate_verdict": str(row.get("gate_verdict", "")),
                }
            )
        return digest

    async def _gate_dialogue_candidates(
        self, candidates: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        """Access point ①: gate goal/value/state candidates (Phase 3).

        ``off`` returns the input untouched (byte-identical feed). Otherwise
        interest/dislike pass through; deep kinds are judged. Under enforce a
        rejected candidate is dropped and a downgraded one is demoted to an
        insight hypothesis (confidence × 0.6). Shadow keeps every candidate (its
        judgement is recorded asynchronously).
        """
        if not self._posture_gate.enabled:
            return candidates
        core_memory = self._memory.get_core_memory()
        ledger_digest = self._ledger_digest_for_gate()
        kept: list[dict[str, object]] = []
        downgraded: list[InsightHypothesis] = []
        for item in candidates:
            kind = str(item.get("kind", "")).strip()
            if kind not in _DEEP_CANDIDATE_KINDS:
                kept.append(item)
                continue
            content = str(item.get("content", ""))
            decision = await self._posture_gate.evaluate(
                write_point="dialogue_deep_candidate",
                change={
                    "kind": kind,
                    "content": content,
                    "confidence": item.get("confidence", 0.0),
                    "evidence": item.get("evidence", ""),
                },
                core_memory=core_memory,
                ledger_digest=ledger_digest,
                source_refs=[f"{kind}:{content[:60]}"],
            )
            if not decision.blocks:
                kept.append(item)
                continue
            # enforce reject / downgrade: excluded from the deep write.
            if decision.downgraded:
                downgraded.append(self._candidate_to_insight(item))
        if downgraded:
            self._persist_downgraded_insights(downgraded)
        return kept

    def _candidate_to_insight(self, item: dict[str, object]) -> InsightHypothesis:
        """Demote a downgraded deep candidate to a hypothesis (confidence × 0.6)."""
        raw_conf = item.get("confidence", 0.0)
        try:
            confidence = float(raw_conf)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            confidence = 0.0
        evidence_text = str(item.get("evidence", "")).strip()
        return InsightHypothesis(
            hypothesis=str(item.get("content", "")).strip(),
            evidence=[evidence_text] if evidence_text else [],
            confidence=round(max(0.0, min(1.0, confidence)) * 0.6, 4),
        )

    def _persist_downgraded_insights(self, insights: list[InsightHypothesis]) -> None:
        existing = self._load_insights()
        self._save_insights(existing + insights)
        for insight in insights:
            self._ledger.record(
                write_point="posture_gate_downgrade_insight",
                source="posture_gate",
                after={"hypothesis": insight.hypothesis[:80], "confidence": insight.confidence},
                source_refs=[insight.hypothesis[:60]],
                gate_verdict="downgrade",
            )

    async def _gate_soul_rebuild(
        self,
        *,
        trigger: str,
        existing_preference: dict[str, object],
        updated_preference: dict[str, object],
        source_refs: list[str],
        context: dict[str, object] | None = None,
    ) -> GateDecision:
        """Access point ③: gate a full soul rebuild (Phase 3, generalized r3/F4).

        Shared by all three rebuild triggers (dialogue / feedback_batch /
        confirmed_hypotheses). The judged snapshot carries the ``trigger``, its
        ledger ``write_point``, an old-soul interest-diff summary, and the
        trigger-specific ``context`` (dialogue candidates / feedback-batch
        summary / confirmed-hypothesis list) so the gate sees the right
        provenance. off never calls the gate (byte-identical) and returns an
        ``accept`` decision; shadow/accept → proceed; enforce downgrade/reject →
        abandon + ledger row.

        Returns the :class:`GateDecision`. Callers proceed on ``not
        decision.blocks``; the pending-rebuild state machine additionally reads
        ``decision.is_error`` to keep vs clear its marker (F7).
        """
        write_point = _REBUILD_WRITE_POINT.get(trigger, "soul_rebuild")
        if not self._posture_gate.enabled:
            return GateDecision(verdict=ACCEPT, enforced=False)
        core_memory = self._memory.get_core_memory()
        decision = await self._posture_gate.evaluate(
            write_point=write_point,
            change={
                "kind": "soul_rebuild",
                "trigger": trigger,
                "write_point": write_point,
                "before_interests": existing_preference.get("interests", []),
                "after_interests": updated_preference.get("interests", []),
                "context": context or {},
            },
            core_memory=core_memory,
            ledger_digest=self._ledger_digest_for_gate(),
            source_refs=source_refs,
        )
        if decision.blocks:
            self._ledger.record(
                write_point=write_point,
                source="posture_gate",
                before={"rebuild": "requested", "trigger": trigger},
                after={"rebuild": "abandoned", "verdict": decision.verdict},
                source_refs=source_refs,
                gate_verdict=decision.verdict,
                outcome="failed",
            )
        return decision

    @staticmethod
    def _candidate_ledger_refs(candidates: list[dict[str, object]]) -> list[str]:
        """Compact source refs for a dialogue-learning ledger row.

        Non-empty so the ledger's ``source_refs`` provenance is auditable even
        when candidate contents are terse.
        """
        refs = [
            f"{str(item.get('kind', '')).strip()}:{str(item.get('content', '')).strip()[:60]}"
            for item in candidates
            if str(item.get("content", "")).strip()
        ]
        return refs or ["dialogue"]

    def _candidate_ready_for_learning(self, candidate: dict[str, object]) -> bool:
        if bool(candidate.get("applied", False)):
            return False
        confidence = self._to_float(candidate.get("confidence", 0.0))
        occurrences = self._to_int(candidate.get("occurrences", 0))
        return confidence >= 0.8 or occurrences >= 2

    def _candidate_ready_for_immediate_dialogue_cognition(
        self,
        candidate: dict[str, object],
    ) -> bool:
        kind = str(candidate.get("kind", "")).strip()
        confidence = self._to_float(candidate.get("confidence", 0.0))
        if kind in {"goal", "dislike", "interest", "value"}:
            return confidence >= 0.8
        return confidence >= 0.9 and kind == "state"

    def _build_immediate_dialogue_cognition(
        self,
        candidate: dict[str, object],
    ) -> tuple[str, str, str, str, str, str]:
        kind = str(candidate.get("kind", "")).strip()
        content = str(candidate.get("content", "")).strip()
        evidence = str(candidate.get("evidence", "")).strip() or content
        context_line = self._build_dialogue_context_line(content)
        if not content:
            return "", "", "", "", "", ""
        if kind == "goal":
            return (
                f"阿B 刚记下了：你最近在意的是“{content}”。",
                "profile_shift",
                "画像里这类目标感会更靠前，后面更容易往因果链和结构解释上贴。",
                "因为你在聊天里主动提到这个目标，这是一次高置信即时信号。",
                evidence,
                context_line,
            )
        if kind == "dislike":
            return (
                f"阿B 刚听出来：像“{content}”这种你现在大概率不太想看。",
                "dislike_added",
                "画像里的避雷方向会更靠前，推荐时会更主动避开这类内容。",
                "因为你在聊天里明确表达了排斥，这比普通停留信号更直接。",
                evidence,
                context_line,
            )
        if kind == "interest":
            return (
                f"阿B 刚摸到一点：你最近可能开始吃“{content}”这一口。",
                "interest_added",
                "画像里这类兴趣会更靠前，后面更容易继续补同方向内容。",
                "因为你在聊天里主动提到这个方向，已经不只是被动刷到。",
                evidence,
                context_line,
            )
        if kind == "value":
            return (
                f"阿B 刚摸到一点：你其实挺看重“{content}”。",
                "profile_shift",
                "画像里的价值取向会更靠前，后面会更偏向同类表达方式。",
                "因为你在聊天里主动提到这类判断标准，这是一次高置信即时信号。",
                evidence,
                context_line,
            )
        return "", "", "", "", "", ""

    def _record_cognition_updates(
        self,
        *,
        existing_preference: dict[str, Any],
        updated_preference: dict[str, Any],
        previous_profile: dict[str, Any],
        current_profile: dict[str, Any],
        source: str,
    ) -> None:
        new_updates = self._build_cognition_updates(
            existing_preference=existing_preference,
            updated_preference=updated_preference,
            previous_profile=previous_profile,
            current_profile=current_profile,
            source=source,
        )
        if not new_updates:
            return
        updates = self._memory.load_cognition_updates()
        updates.extend(new_updates)
        self._memory.save_cognition_updates(updates)

    def _build_cognition_updates(
        self,
        *,
        existing_preference: dict[str, Any],
        updated_preference: dict[str, Any],
        previous_profile: dict[str, Any],
        current_profile: dict[str, Any],
        source: str,
    ) -> list[dict[str, object]]:
        now = datetime.now().isoformat()
        updates: list[dict[str, object]] = []

        existing_interests = {
            self._normalize_text(str(item.get("name", ""))): item
            for item in self._as_dict_list(existing_preference.get("interests", []))
            if str(item.get("name", "")).strip()
        }
        for item in self._as_dict_list(updated_preference.get("interests", [])):
            name = str(item.get("name", "")).strip()
            normalized_name = self._normalize_text(name)
            if not normalized_name or normalized_name in existing_interests:
                continue
            weight = self._to_float(item.get("weight", 0.0))
            if weight < 0.75:
                continue
            updates.append(
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "interest_added",
                    "summary": f"阿B 现在更确定你会吃“{name}”这一口。",
                    "context_line": self._build_topic_context_line([name]),
                    "impact": f"画像里“{name}”这条兴趣会更靠前，后面补货会更主动覆盖这个方向。",
                    "reasoning": "这不是一次偶发波动，更像是最近重复出现后的稳定兴趣强化。",
                    "evidence": f"最近聚合到的新主题里，“{name}”已经达到高权重。",
                    "confidence": round(weight, 4),
                    "created_at": now,
                    "source": source,
                    "source_label": self._build_source_label(source),
                    "expand_hint": "expandable",
                    "notified": False,
                }
            )

        existing_dislikes = {
            self._normalize_text(item)
            for item in self._as_str_list(existing_preference.get("disliked_topics", []))
        }
        for topic in self._as_str_list(updated_preference.get("disliked_topics", [])):
            normalized_topic = self._normalize_text(topic)
            if not normalized_topic or normalized_topic in existing_dislikes:
                continue
            updates.append(
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "dislike_added",
                    "summary": f"阿B 记住了：像“{topic}”这种内容你大概率会划走。",
                    "context_line": self._build_topic_context_line([topic]),
                    "impact": f"画像里对“{topic}”这类内容的避雷会更明确。",
                    "reasoning": "这不是一次情绪化表达，而是最近反馈里重复浮出来的排斥方向。",
                    "evidence": f"最近聚合到的负反馈里，多次指向“{topic}”这个方向。",
                    "confidence": 0.86,
                    "created_at": now,
                    "source": source,
                    "source_label": self._build_source_label(source),
                    "expand_hint": "expandable",
                    "notified": False,
                }
            )

        if self._profile_shifted(previous_profile, current_profile):
            portrait = str(current_profile.get("personality_portrait", "")).strip()
            summary = portrait[:72].rstrip("，。！？,.!?") if portrait else "我对你又对上了一点。"
            updates.append(
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "profile_shift",
                    "summary": summary,
                    "context_line": self._build_profile_shift_context_line(updated_preference),
                    "impact": "画像里的人格描述和关注重心已经发生可见调整。",
                    "reasoning": "这不是单次波动，而是最近重复出现后的稳定变化。",
                    "evidence": self._build_profile_shift_evidence(updated_preference),
                    "confidence": 0.9,
                    "created_at": now,
                    "source": "profile_refresh",
                    "source_label": self._build_source_label("profile_refresh"),
                    "expand_hint": "expandable",
                    "notified": False,
                }
            )

        return updates

    @staticmethod
    def _normalize_text(value: str) -> str:
        return "".join(value.split())

    def _build_profile_shift_evidence(self, preference: dict[str, Any]) -> str:
        interests = [
            str(item.get("name", "")).strip()
            for item in self._as_dict_list(preference.get("interests", []))
            if str(item.get("name", "")).strip()
        ][:2]
        if interests:
            return f"最近重复出现的主题包括：{' / '.join(interests)}。"
        return "最近重复出现的信号已经足够多，开始推动画像整体调整。"

    @staticmethod
    def _build_source_label(source: str) -> str:
        return SOURCE_LABELS.get(source.strip(), "")

    @staticmethod
    def _build_expand_hint(*, impact: str, reasoning: str, evidence: str) -> str:
        if any((impact.strip(), reasoning.strip(), evidence.strip())):
            return "expandable"
        return "summary_only"

    @staticmethod
    def _build_feedback_context_line(title: str) -> str:
        title_text = title.strip()
        if title_text:
            return f"来自：《{title_text}》"
        return "来自：这次推荐反馈"

    @staticmethod
    def _build_dialogue_context_line(content: str) -> str:
        if content.strip():
            return f"来自最近这轮聊天：{content.strip()}"
        return "来自最近这轮聊天"

    @staticmethod
    def _build_topic_context_line(topics: list[str]) -> str:
        normalized = [topic.strip() for topic in topics if topic.strip()]
        if normalized:
            return f"基于最近主题：{' / '.join(normalized[:3])}"
        return "基于最近几条相关内容"

    def _build_profile_shift_context_line(self, preference: dict[str, Any]) -> str:
        interests = [
            str(item.get("name", "")).strip()
            for item in self._as_dict_list(preference.get("interests", []))
            if str(item.get("name", "")).strip()
        ]
        dislikes = self._as_str_list(preference.get("disliked_topics", []))
        return self._build_topic_context_line([*interests[:2], *dislikes[:1]])

    @staticmethod
    def _is_retraction_feedback(event: dict[str, Any]) -> bool:
        """True when a deserialized feedback event is an X retraction."""
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            return False
        return str(metadata.get("feedback_type") or "").strip().lower() == "retraction"

    @staticmethod
    def _deserialize_event(event: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(event)
        for key in ("context", "metadata"):
            raw_value = normalized.get(key)
            if isinstance(raw_value, str):
                try:
                    parsed = json.loads(raw_value)
                except json.JSONDecodeError:
                    parsed = {}
                normalized[key] = parsed if isinstance(parsed, dict) else {}
        return normalized

    @staticmethod
    def _preference_changed_significantly(
        old_preference: dict[str, Any],
        new_preference: dict[str, Any],
    ) -> bool:
        def high_weight_interests(source: dict[str, Any]) -> dict[tuple[str, str], float]:
            items = source.get("interests", [])
            if not isinstance(items, list):
                return {}
            result: dict[tuple[str, str], float] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                weight = float(item.get("weight", 0.0) or 0.0)
                if weight < 0.6:
                    continue
                key = (str(item.get("name", "")).strip(), str(item.get("category", "")).strip())
                result[key] = weight
            return result

        old_interests = high_weight_interests(old_preference)
        new_interests = high_weight_interests(new_preference)
        if not old_interests and new_interests:
            return True
        changed_keys = set(old_interests) ^ set(new_interests)
        if len(changed_keys) >= 2:
            return True
        for key in set(old_interests) & set(new_interests):
            if abs(old_interests[key] - new_interests[key]) >= 0.2:
                return True
        old_disliked = {
            str(item).strip()
            for item in old_preference.get("disliked_topics", [])
            if str(item).strip()
        }
        new_disliked = {
            str(item).strip()
            for item in new_preference.get("disliked_topics", [])
            if str(item).strip()
        }
        return len(new_disliked - old_disliked) >= 1

    @staticmethod
    def _profile_shifted(previous_profile: dict[str, Any], current_profile: dict[str, Any]) -> bool:
        if not current_profile:
            return False
        if not previous_profile:
            return bool(
                SoulEngine._as_str_list(current_profile.get("core_traits", []))
                or SoulEngine._as_str_list(current_profile.get("deep_needs", []))
                or str(current_profile.get("personality_portrait", "")).strip()
            )
        previous_traits = set(SoulEngine._as_str_list(previous_profile.get("core_traits", [])))
        current_traits = set(SoulEngine._as_str_list(current_profile.get("core_traits", [])))
        if current_traits - previous_traits:
            return True
        previous_needs = set(SoulEngine._as_str_list(previous_profile.get("deep_needs", [])))
        current_needs = set(SoulEngine._as_str_list(current_profile.get("deep_needs", [])))
        if current_needs - previous_needs:
            return True
        previous_portrait = SoulEngine._normalize_text(
            str(previous_profile.get("personality_portrait", ""))
        )
        current_portrait = SoulEngine._normalize_text(
            str(current_profile.get("personality_portrait", ""))
        )
        return bool(
            previous_portrait and current_portrait and previous_portrait != current_portrait
        )

    @staticmethod
    def _as_dict_list(raw_value: object) -> list[dict[str, Any]]:
        if not isinstance(raw_value, list):
            return []
        return [item for item in raw_value if isinstance(item, dict)]

    @staticmethod
    def _as_str_list(raw_value: object) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        return [str(item).strip() for item in raw_value if str(item).strip()]

    @staticmethod
    def _to_int(raw_value: object) -> int:
        if isinstance(raw_value, bool):
            return int(raw_value)
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, float):
            return int(raw_value)
        if isinstance(raw_value, str):
            try:
                return int(raw_value)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _to_float(raw_value: object) -> float:
        if isinstance(raw_value, bool):
            return float(raw_value)
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        if isinstance(raw_value, str):
            try:
                return float(raw_value)
            except ValueError:
                return 0.0
        return 0.0
