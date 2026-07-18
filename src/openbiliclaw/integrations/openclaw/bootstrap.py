"""Dependency bootstrap for the OpenClaw adapter."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from openbiliclaw.api.runtime_context import (
    build_runtime_model_bundle,
    build_youtube_discovery_producer,
)
from openbiliclaw.bilibili.api import BilibiliAPIClient
from openbiliclaw.bilibili.auth import resolve_runtime_cookie
from openbiliclaw.config import Config, load_config
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.discovery.strategies.strategies import (
    ExploreStrategy,
    RelatedChainStrategy,
    SearchStrategy,
    TrendingStrategy,
)
from openbiliclaw.llm.concurrency import LLMConcurrencyGate, background_llm_concurrency
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.model_config import compute_model_revision
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.runtime.account_sync import AccountSyncService
from openbiliclaw.runtime.presence import PresenceTracker
from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.runtime.source_policy import effective_pool_source_shares
from openbiliclaw.soul.engine import SoulEngine
from openbiliclaw.storage.database import Database

from .operations import OpenClawAdapter

if TYPE_CHECKING:
    from openbiliclaw.llm.service import LLMService


@dataclass(slots=True)
class OpenClawAdapterServices:
    """Shared services bundle used by the OpenClaw adapter."""

    config: Config | Any
    database: Database | Any
    memory_manager: MemoryManager | Any
    soul_engine: SoulEngine | Any
    llm_service: LLMService | Any
    bilibili_client: BilibiliAPIClient | Any
    discovery_engine: ContentDiscoveryEngine | Any
    recommendation_engine: RecommendationEngine | Any
    runtime_controller: ContinuousRefreshController | Any
    account_sync_service: AccountSyncService | Any


def build_openclaw_adapter_services() -> OpenClawAdapterServices:
    """Build the shared service bundle for the OpenClaw adapter."""
    config = load_config()
    llm_concurrency = config.models.chat.concurrency

    database = Database(config.data_path / "openbiliclaw.db")
    database.initialize()

    memory_manager = MemoryManager(config.data_path, database=database)
    memory_manager.initialize()

    llm_gate = LLMConcurrencyGate(llm_concurrency)
    count_pool = getattr(database, "count_pool_candidates", None)
    if callable(count_pool):
        try:
            state = memory_manager.load_discovery_runtime_state()
            info = state.get("xhs_self_info", {}) if isinstance(state, dict) else {}
            nickname = str(info.get("nickname", "")) if isinstance(info, dict) else ""
            available = int(count_pool(xhs_self_nickname=nickname))
        except (AttributeError, TypeError):
            available = int(count_pool())
        llm_gate.update_inventory(
            available=max(0, available),
            target=int(config.scheduler.pool_target_count),
        )

    model_bundle = build_runtime_model_bundle(
        config.models,
        compute_model_revision(config.models),
        memory=memory_manager,
        usage_sink=database,
        concurrency_gate=llm_gate,
    )
    llm_registry = model_bundle.chat_route
    llm_service = model_bundle.llm_service
    embedding_service = model_bundle.embedding_service
    usage_recorder = llm_service.usage_recorder

    soul_engine = SoulEngine(
        llm=llm_registry,
        memory=memory_manager,
        embedding_service=embedding_service,
        usage_recorder=usage_recorder,
        llm_concurrency=llm_concurrency,
        llm_concurrency_gate=llm_gate,
        speculation_interval_minutes=config.scheduler.speculation_interval_minutes,
        speculation_ttl_days=config.scheduler.speculation_ttl_days,
        speculation_cooldown_days=config.scheduler.speculation_cooldown_days,
        speculation_confirmation_threshold=config.scheduler.speculation_confirmation_threshold,
        speculation_max_active=config.scheduler.speculation_max_active,
        speculation_max_primary_interests=config.scheduler.speculation_max_primary_interests,
        speculation_max_secondary_interests=config.scheduler.speculation_max_secondary_interests,
        avoidance_speculation_interval_minutes=int(
            getattr(config.scheduler, "avoidance_speculation_interval_minutes", 10)
        ),
        avoidance_speculation_ttl_days=int(
            getattr(config.scheduler, "avoidance_speculation_ttl_days", 3)
        ),
        avoidance_speculation_cooldown_days=int(
            getattr(config.scheduler, "avoidance_speculation_cooldown_days", 7)
        ),
        avoidance_speculation_confirmation_threshold=int(
            getattr(config.scheduler, "avoidance_speculation_confirmation_threshold", 3)
        ),
        avoidance_speculation_max_active=int(
            getattr(config.scheduler, "avoidance_speculation_max_active", 5)
        ),
        speculator_idle_interval_minutes=config.scheduler.speculator_idle_interval_minutes,
        profile_consolidation_enabled=bool(
            getattr(config.scheduler, "profile_consolidation_enabled", True)
        ),
        profile_consolidation_interval_hours=int(
            getattr(config.scheduler, "profile_consolidation_interval_hours", 12)
        ),
        profile_consolidation_like_target_upper=int(
            getattr(config.scheduler, "profile_consolidation_like_target_upper", 512)
        ),
        profile_consolidation_like_target_soft=int(
            getattr(config.scheduler, "profile_consolidation_like_target_soft", 450)
        ),
        profile_consolidation_archive_enabled=bool(
            getattr(config.scheduler, "profile_consolidation_archive_enabled", True)
        ),
    )
    from openbiliclaw.recommendation.curator import PoolCurator

    curator = PoolCurator(database)
    recommendation_engine = RecommendationEngine(
        llm=llm_service,
        database=database,
        curator=curator,
        embedding_service=embedding_service,
    )
    bilibili_client = BilibiliAPIClient(
        cookie=resolve_runtime_cookie(
            data_dir=config.data_path,
            configured_cookie=config.bilibili.cookie,
        ),
        proxy=config.bilibili.proxy or None,
    )

    from openbiliclaw.discovery.engine import DiscoveryConcurrencyController

    concurrency = DiscoveryConcurrencyController(
        bilibili_request_concurrency=4,
        llm_evaluation_concurrency=background_llm_concurrency(llm_concurrency),
        search_budget_total=30,
    )

    discovery_engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=database,
        embedding_service=embedding_service,
        concurrency=concurrency,
    )
    search_strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
        database=database,
        embedding_service=embedding_service,
    )
    trending_strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        concurrency=concurrency,
        database=database,
        embedding_service=embedding_service,
    )
    related_strategy = RelatedChainStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        memory_manager=cast("Any", memory_manager),
        search_strategy=search_strategy,
        trending_strategy=trending_strategy,
        concurrency=concurrency,
        database=database,
    )
    explore_strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
        database=cast("Any", database),
    )
    discovery_engine.register_strategy(search_strategy)
    discovery_engine.register_strategy(trending_strategy)
    discovery_engine.register_strategy(related_strategy)
    discovery_engine.register_strategy(explore_strategy)

    discovery_cfg = getattr(config, "discovery", None)
    admission_min_score = float(getattr(discovery_cfg, "admission_min_score", 0.60) or 0.60)
    set_admission_min_score = getattr(database, "set_admission_min_score", None)
    if callable(set_admission_min_score):
        set_admission_min_score(admission_min_score)
    candidate_pipeline = DiscoveryCandidatePipeline(
        database=database,
        discovery_engine=discovery_engine,
        pool_target_count=config.scheduler.pool_target_count,
        admission_min_score=admission_min_score,
        min_eval_batch_size=4,
        max_eval_wait_seconds=120,
        # OpenClaw invokes a one-shot refresh rather than owning the API
        # runtime's continuous evaluator. Start with a small fixed first
        # claim so evaluation plus durable copy can complete inside the
        # adapter's bounded interaction window; a later OpenClaw request can
        # refill the next batch. The API runtime deliberately keeps its 4x
        # oversample and default two-way evaluator fan-out for sustained
        # supply.
        candidate_fetch_oversample=1,
        eval_batch_concurrency=1,
    )

    from openbiliclaw.runtime.douyin_producer import build_douyin_discovery_producer

    presence = PresenceTracker()
    douyin_producer = build_douyin_discovery_producer(
        config=config,
        database=database,
        soul_engine=soul_engine,
        discovery_engine=discovery_engine,
        candidate_pipeline=candidate_pipeline,
    )
    youtube_producer = build_youtube_discovery_producer(
        config=config,
        database=database,
        soul_engine=soul_engine,
        discovery_engine=discovery_engine,
        llm_service=llm_service,
        memory=cast("Any", memory_manager),
        concurrency=concurrency,
        candidate_pipeline=candidate_pipeline,
    )
    runtime_controller: ContinuousRefreshController

    async def _drain_one_shot_expression_copy(profile: Any) -> int:
        """Complete OpenClaw's terminal copy stage before returning control.

        OpenClaw only invokes short-lived operations and never starts
        ``ContinuousRefreshController.run_forever()``.  It must therefore
        await the durable expression-copy work itself rather than notify a
        daemon coordinator that will never run.
        """

        if profile is None:
            return 0
        before = int(runtime_controller._pool_readiness_counts().get("available", 0))  # noqa: SLF001
        # This bridge has one bounded interactive request rather than a daemon
        # retry loop.  Keep its copy work inside the same four-item first-wave
        # budget and persist any valid partial response immediately; remaining
        # rows stay durable for the next OpenClaw request instead of consuming
        # the interaction window on recursive split retries.
        copy_limit = max(1, min(4, int(runtime_controller.one_shot_inline_eval_limit or 4)))
        completed = await recommendation_engine.drain_pending_expression_copy(
            profile=profile,
            limit=copy_limit,
            max_extra_requests=0,
        )
        await runtime_controller._publish_precompute_replenishment_if_needed(  # noqa: SLF001
            before_pool_count=before,
        )
        return int(completed)

    async def _copy_after_inline_admission(profile: Any, _admitted: int) -> int:
        """Delegate admission copy to the controller's one-shot owner.

        The pipeline receipt records this callback as the durable copy owner.
        Resolving the callback through the controller keeps that owner identical
        to the controller fallback, so a one-shot caller has one observable
        path rather than two captured closures that could drift apart.
        """

        callback = runtime_controller.one_shot_expression_copy_callback
        if callback is None:
            return 0
        result = callback(profile)
        if inspect.isawaitable(result):
            result = await result
        return max(0, int(result or 0))

    runtime_controller = ContinuousRefreshController(
        memory_manager=memory_manager,
        database=database,
        soul_engine=soul_engine,
        discovery_engine=discovery_engine,
        recommendation_engine=recommendation_engine,
        discovery_candidate_pipeline=candidate_pipeline,
        pool_target_count=config.scheduler.pool_target_count,
        pool_source_shares=effective_pool_source_shares(config),
        signal_event_threshold=int(getattr(config.scheduler, "signal_event_threshold", 6)),
        trending_refresh_hours=int(getattr(config.scheduler, "trending_refresh_hours", 3)),
        explore_refresh_hours=int(getattr(config.scheduler, "explore_refresh_hours", 12)),
        check_interval_seconds=int(getattr(config.scheduler, "refresh_check_interval_seconds", 60)),
        proactive_push_interval_seconds=int(
            getattr(config.scheduler, "proactive_push_interval_seconds", 120)
        ),
        discovery_limit=int(getattr(config.scheduler, "discovery_limit", 30)),
        douyin_producer=douyin_producer,
        youtube_producer=youtube_producer,
        scheduler_config=config.scheduler,
        presence=presence,
        llm_concurrency_gate=llm_gate,
        one_shot_expression_copy_callback=_drain_one_shot_expression_copy,
        one_shot_inline_eval_limit=4,
    )
    # The OpenClaw adapter has no daemon lifecycle.  Keep the evaluator's
    # bounded inline drain, then finish copy through the awaited callback
    # above; unlike a notify-only coordinator this leaves no dormant owner or
    # background copy task behind.
    candidate_pipeline.on_candidates_admitted = _copy_after_inline_admission
    set_pool_commit_callback = getattr(
        recommendation_engine,
        "set_pool_inventory_commit_callback",
        None,
    )
    if callable(set_pool_commit_callback):
        set_pool_commit_callback(runtime_controller._pool_readiness_counts)  # noqa: SLF001
    runtime_controller.run_startup_maintenance()
    account_sync_service = AccountSyncService(
        memory_manager=memory_manager,
        bilibili_client=bilibili_client,
        soul_engine=soul_engine,
        sync_interval_hours=config.scheduler.account_sync_interval_hours,
    )

    return OpenClawAdapterServices(
        config=config,
        database=database,
        memory_manager=memory_manager,
        soul_engine=soul_engine,
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        discovery_engine=discovery_engine,
        recommendation_engine=recommendation_engine,
        runtime_controller=runtime_controller,
        account_sync_service=account_sync_service,
    )


def build_openclaw_adapter() -> OpenClawAdapter:
    """Build a ready-to-use OpenClaw adapter."""
    return OpenClawAdapter(services=build_openclaw_adapter_services())
