"""FastAPI app for the browser-extension backend."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from openbiliclaw.api.models import (
    ActivityFeedItemOut,
    ActivityFeedResponse,
    BehaviorEventBatchIn,
    BilibiliConfigOut,
    ChatIn,
    ChatResponse,
    CognitionUpdateSeenIn,
    CognitionUpdateSeenResponse,
    CognitionUpdateSummary,
    ConfigIssueOut,
    ConfigResponse,
    ConfigUpdateIn,
    ConfigUpdateResponse,
    DelightAckIn,
    DelightAckResponse,
    EmbeddingConfigOut,
    EventIngestResponse,
    FeedbackIn,
    FeedbackResponse,
    HealthResponse,
    LLMConfigOut,
    LLMProviderConfigOut,
    LoggingConfigOut,
    ModuleLLMConfigOut,
    NotificationAckIn,
    NotificationAckResponse,
    PendingCognitionUpdateOut,
    PendingCognitionUpdateResponse,
    PendingDelightOut,
    PendingDelightResponse,
    PendingNotificationOut,
    PendingNotificationResponse,
    ProfileSummaryResponse,
    RecommendationAppendIn,
    RecommendationClickIn,
    RecommendationClickResponse,
    RecommendationListResponse,
    RecommendationOut,
    RecommendationRefreshResponse,
    RecommendationReshuffleResponse,
    RuntimeStatusResponse,
    SchedulerConfigOut,
    StorageConfigOut,
)

logger = logging.getLogger(__name__)

SOURCE_LABELS = {
    "feedback": "推荐反馈",
    "chat": "聊天",
    "profile_refresh": "聚合观察",
}


def _normalize_cognition_update(item: dict[str, object]) -> CognitionUpdateSummary:
    impact = str(item.get("impact", "")).strip()
    reasoning = str(item.get("reasoning", "")).strip()
    evidence = str(item.get("evidence", "")).strip()
    source = str(item.get("source", "")).strip()
    source_label = str(item.get("source_label", "")).strip() or SOURCE_LABELS.get(source, "")
    expand_hint = str(item.get("expand_hint", "")).strip()
    if expand_hint not in {"expandable", "summary_only"}:
        expand_hint = "expandable" if any((impact, reasoning, evidence)) else "summary_only"
    return CognitionUpdateSummary(
        summary=str(item.get("summary", "")).strip(),
        context_line=str(item.get("context_line", "")).strip() or "基于最近几条相关内容",
        impact=impact,
        reasoning=reasoning,
        evidence=evidence,
        source=source,
        source_label=source_label,
        expand_hint=expand_hint,
        created_at=str(item.get("created_at", "")).strip(),
    )


def create_app(
    *,
    memory_manager: Any | None = None,
    database: Any | None = None,
    soul_engine: Any | None = None,
    dialogue: Any | None = None,
    runtime_controller: Any | None = None,
    recommendation_engine: Any | None = None,
    runtime_event_hub: Any | None = None,
    account_sync_service: Any | None = None,
    auto_update_service: Any | None = None,
) -> FastAPI:
    """Create the local backend API app."""
    from openbiliclaw.api.runtime_context import RuntimeContext, build_runtime_context
    from openbiliclaw.config import load_config

    app = FastAPI(title="OpenBiliClaw API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Build RuntimeContext ────────────────────────────────────────
    config = load_config()

    if soul_engine is not None:
        # Injection path: caller provides swappable components.
        # Auto-create stable components (database, memory_manager) if missing.
        from openbiliclaw.runtime.events import RuntimeEventHub as _RuntimeEventHub

        _db = database
        _created_db = False
        if _db is None:
            from openbiliclaw.storage.database import Database

            _db = Database(config.data_path / "openbiliclaw.db")
            _db.initialize()
            _created_db = True
        _mm = memory_manager
        if _mm is None:
            from openbiliclaw.memory.manager import MemoryManager

            _mm = MemoryManager(config.data_path, database=_db if _created_db else None)
            _mm.initialize()

        ctx = RuntimeContext(
            database=_db,
            memory_manager=_mm,
            event_hub=runtime_event_hub or getattr(runtime_controller, "event_hub", None) or _RuntimeEventHub(),
            # config intentionally left None in injection path — matches
            # old behaviour where closures couldn't see config when all
            # core components were provided by the caller.
            soul_engine=soul_engine,
            dialogue=dialogue,
            runtime_controller=runtime_controller,
            recommendation_engine=recommendation_engine,
            account_sync_service=account_sync_service,
            auto_update_service=auto_update_service,
        )
        if ctx.dialogue is None:
            from openbiliclaw.soul.dialogue import SocraticDialogue
            ctx.dialogue = SocraticDialogue(llm=None, soul_engine=soul_engine, session="popup")
        if ctx.auto_update_service is None:
            from openbiliclaw.runtime.updater import AutoUpdateService
            ctx.auto_update_service = AutoUpdateService(enabled=True)
    else:
        # Production path: build everything from config.
        ctx = build_runtime_context(
            config,
            memory_manager=memory_manager,
            database=database,
            event_hub=runtime_event_hub,
        )

    async def _run_post_feedback_tasks() -> None:
        with suppress(Exception):
            await ctx.soul_engine.process_feedback_batch_if_needed()
        refresh_after_feedback = getattr(ctx.runtime_controller, "refresh_after_feedback", None)
        if callable(refresh_after_feedback):
            with suppress(Exception):
                await refresh_after_feedback()

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="openbiliclaw-api")

    @app.post("/api/init-completed")
    async def init_completed() -> dict[str, object]:
        """Notify the running server that ``openbiliclaw init`` has finished.

        Called by the CLI at the end of a successful init.  The handler
        broadcasts an ``init_completed`` event via WebSocket so the
        browser extension can immediately re-fetch profile, recommendations
        and activity data.  It also kicks the continuous-refresh controller
        so the discovery pool is picked up without waiting for the next
        60-second tick.
        """
        # Broadcast to extension
        with suppress(Exception):
            await ctx.event_hub.publish({
                "type": "init_completed",
                "message": "初始化完成，画像与发现池已就绪。",
            })
        # Kick refresh controller immediately
        trigger = getattr(ctx.runtime_controller, "trigger_manual_refresh", None)
        if callable(trigger):
            with suppress(Exception):
                asyncio.create_task(trigger())
        return {"ok": True}

    def _serialize_recommendation_items(items: list[Any]) -> list[RecommendationOut]:
        return [
            RecommendationOut(
                id=int(item.recommendation_id),
                bvid=str(item.content.bvid),
                title=str(item.content.title),
                up_name=str(item.content.up_name),
                cover_url=str(item.content.cover_url),
                expression=str(item.expression),
                topic_label=str(item.topic_label),
                presented=bool(item.presented),
                content_id=str(getattr(item.content, "content_id", "") or item.content.bvid),
                content_url=str(getattr(item.content, "content_url", "") or ""),
                source_platform=str(getattr(item.content, "source_platform", "") or "bilibili"),
            )
            for item in items
        ]

    @app.websocket("/api/runtime-stream")
    async def runtime_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        subscribe = getattr(ctx.event_hub, "subscribe", None)
        unsubscribe = getattr(ctx.event_hub, "unsubscribe", None)
        if not callable(subscribe) or not callable(unsubscribe):
            await websocket.close()
            return
        queue = await subscribe()
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            await unsubscribe(queue)

    @app.on_event("startup")
    async def startup_refresh_loop() -> None:
        await ctx.restart_background_tasks(app)

    @app.on_event("shutdown")
    async def shutdown_refresh_loop() -> None:
        refresh_task = getattr(app.state, "refresh_task", None)
        if refresh_task is not None:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
        account_sync_task = getattr(app.state, "account_sync_task", None)
        if account_sync_task is not None:
            account_sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await account_sync_task
        auto_update_task = getattr(app.state, "auto_update_task", None)
        if auto_update_task is not None:
            auto_update_task.cancel()
            with suppress(asyncio.CancelledError):
                await auto_update_task

    @app.get("/api/profile-summary", response_model=ProfileSummaryResponse)
    async def profile_summary(
        limit: int = Query(default=3, ge=1, le=20),
        cursor: str = "",
    ) -> ProfileSummaryResponse:
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return ProfileSummaryResponse(initialized=False)

        from openbiliclaw.api.models import (
            AwarenessNoteOut,
            ContextModeOut,
            InsightHypothesisOut,
            InterestDomainOut,
            InterestSpecificOut,
            MBTIDimensionOut,
            MBTIOut,
            SpeculativeInterestOut,
            StylePreferenceOut,
        )
        from openbiliclaw.soul.speculator import load_speculative_state

        prefs = profile.preferences

        # ── Core layer ──
        mbti_obj = getattr(getattr(profile, "core", None), "mbti", None)
        mbti_out = MBTIOut()
        if mbti_obj is not None and getattr(mbti_obj, "type", ""):
            mbti_out = MBTIOut(
                type=str(mbti_obj.type),
                dimensions={
                    k: MBTIDimensionOut(pole=str(v.pole), strength=float(v.strength))
                    for k, v in getattr(mbti_obj, "dimensions", {}).items()
                },
                confidence=float(getattr(mbti_obj, "confidence", 0.0)),
            )

        # ── Interest layer (tree structure) ──
        interest_layer = getattr(profile, "interest", None)

        def _domain_list(raw_domains: object) -> list[InterestDomainOut]:
            if not isinstance(raw_domains, list):
                return []
            return [
                InterestDomainOut(
                    domain=str(getattr(d, "domain", "")),
                    weight=float(getattr(d, "weight", 0.5)),
                    specifics=[
                        InterestSpecificOut(
                            name=str(getattr(s, "name", "")),
                            weight=float(getattr(s, "weight", 0.5)),
                        )
                        for s in getattr(d, "specifics", [])
                        if str(getattr(s, "name", "")).strip()
                    ],
                )
                for d in raw_domains
                if str(getattr(d, "domain", "")).strip()
            ]

        likes_out = _domain_list(getattr(interest_layer, "likes", []))[:12]
        dislikes_out = _domain_list(getattr(interest_layer, "dislikes", []))[:8]

        favorite_ups = [
            str(item).strip()
            for item in getattr(prefs, "favorite_up_users", [])[:8]
            if str(item).strip()
        ]

        # ── Surface layer ──
        style_raw = getattr(prefs, "style", None)
        style_out = StylePreferenceOut()
        if style_raw is not None:
            style_out = StylePreferenceOut(
                preferred_duration=str(getattr(style_raw, "preferred_duration", "")),
                preferred_pace=str(getattr(style_raw, "preferred_pace", "")),
                quality_sensitivity=float(getattr(style_raw, "quality_sensitivity", 0.5)),
                humor_preference=float(getattr(style_raw, "humor_preference", 0.5)),
                depth_preference=float(getattr(style_raw, "depth_preference", 0.5)),
            )
        ctx_raw = getattr(prefs, "context", None)
        ctx_out = ContextModeOut()
        if ctx_raw is not None:
            ctx_out = ContextModeOut(
                weekday_patterns=str(getattr(ctx_raw, "weekday_patterns", "")),
                weekend_patterns=str(getattr(ctx_raw, "weekend_patterns", "")),
                time_of_day_patterns=str(getattr(ctx_raw, "time_of_day_patterns", "")),
                session_type=str(getattr(ctx_raw, "session_type", "")),
            )

        exploration_openness = float(getattr(prefs, "exploration_openness", 0.5))

        # ── Cognition updates ──
        cognition_updates = []
        has_more_cognition_updates = False
        next_cognition_cursor = ""
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            raw_updates = [
                item
                for item in load_cognition_updates()
                if isinstance(item, dict) and str(item.get("summary", "")).strip()
            ]
            raw_updates.sort(key=lambda item: str(item.get("created_at", "")).strip(), reverse=True)
            raw_updates.sort(key=lambda item: bool(item.get("notified", False)))
            try:
                start = max(int(cursor), 0)
            except ValueError:
                start = 0
            end = start + limit
            sliced_updates = raw_updates[start:end]
            has_more_cognition_updates = end < len(raw_updates)
            next_cognition_cursor = str(end) if has_more_cognition_updates else ""
            cognition_updates = [
                _normalize_cognition_update(item)
                for item in sliced_updates
            ]

        # ── Speculative interests ──
        spec_items: list[SpeculativeInterestOut] = []
        try:
            spec_state = load_speculative_state(ctx.config.data_path)
            from openbiliclaw.api.models import SpeculativeSpecificOut

            spec_items = [
                SpeculativeInterestOut(
                    domain=item.domain,
                    reason=item.reason,
                    confidence=item.confidence,
                    confirmation_count=item.confirmation_count,
                    confirmation_threshold=item.confirmation_threshold,
                    status=item.status,
                    specifics=[
                        SpeculativeSpecificOut(
                            name=s.name,
                            confirmation_count=s.confirmation_count,
                        )
                        for s in item.specifics
                        if s.name.strip()
                    ],
                )
                for item in spec_state.active[:6]
            ]
        except Exception:
            logger.debug("Failed to load speculative state for profile summary")

        active_insights_out = [
            InsightHypothesisOut(
                hypothesis=str(getattr(ins, "hypothesis", "")),
                evidence=[str(e) for e in getattr(ins, "evidence", [])],
                confidence=float(getattr(ins, "confidence", 0.5)),
                validated=bool(getattr(ins, "validated", False)),
                created_at=str(getattr(ins, "created_at", "")),
            )
            for ins in getattr(profile, "active_insights", [])[:6]
            if str(getattr(ins, "hypothesis", "")).strip()
        ]

        recent_awareness_out = [
            AwarenessNoteOut(
                date=str(getattr(note, "date", "")),
                observation=str(getattr(note, "observation", "")),
                trend=str(getattr(note, "trend", "")),
                emotion_guess=str(getattr(note, "emotion_guess", "")),
            )
            for note in getattr(profile, "recent_awareness", [])[:8]
            if str(getattr(note, "observation", "")).strip()
        ]

        return ProfileSummaryResponse(
            initialized=True,
            personality_portrait=profile.personality_portrait,
            # Core
            core_traits=profile.core_traits[:6],
            deep_needs=profile.deep_needs[:5],
            mbti=mbti_out,
            # Values
            values=list(getattr(profile, "values", [])[:5]),
            motivational_drivers=list(getattr(profile, "motivational_drivers", [])[:4]),
            # Interest
            likes=likes_out,
            dislikes=dislikes_out,
            favorite_up_users=favorite_ups,
            # Role
            life_stage=str(getattr(profile, "life_stage", "")),
            current_phase=str(getattr(profile, "current_phase", "")),
            # Surface
            cognitive_style=list(getattr(profile, "cognitive_style", [])[:5]),
            style=style_out,
            context=ctx_out,
            exploration_openness=exploration_openness,
            # Cross-cutting
            speculative_interests=spec_items,
            recent_cognition_updates=cognition_updates,
            has_more_cognition_updates=has_more_cognition_updates,
            next_cognition_cursor=next_cognition_cursor,
            active_insights=active_insights_out,
            recent_awareness=recent_awareness_out,
        )

    @app.post("/api/events", response_model=EventIngestResponse)
    async def ingest_events(payload: BehaviorEventBatchIn) -> EventIngestResponse:
        accepted = 0
        for item in payload.events:
            event = {
                "event_type": item.type,
                "url": item.url,
                "title": item.title,
                "context": item.context,
                "metadata": {
                    **item.metadata,
                    "timestamp": item.timestamp,
                },
            }
            await ctx.memory_manager.propagate_event(event)
            accepted += 1
        refresh_after_event_ingest = getattr(ctx.runtime_controller, "refresh_after_event_ingest", None)
        if callable(refresh_after_event_ingest):
            with suppress(Exception):
                await refresh_after_event_ingest()
        return EventIngestResponse(accepted=accepted)

    @app.get("/api/recommendations", response_model=RecommendationListResponse)
    async def recommendations() -> RecommendationListResponse:
        rows = ctx.database.get_recommendations(limit=20)
        return RecommendationListResponse(
            items=[
                RecommendationOut(
                    id=int(row["id"]),
                    bvid=str(row.get("bvid", "")),
                    title=str(row.get("title", "")),
                    up_name=str(row.get("up_name", "")),
                    cover_url=str(row.get("cover_url", "")),
                    expression=str(row.get("expression", "")),
                    topic_label=str(row.get("topic", "")),
                    presented=bool(row.get("presented", 0)),
                    content_id=str(row.get("content_id", "") or row.get("bvid", "")),
                    content_url=str(row.get("content_url", "") or ""),
                    source_platform=str(row.get("source_platform", "") or "bilibili"),
                )
                for row in rows
            ]
        )

    @app.get("/api/activity-feed", response_model=ActivityFeedResponse)
    async def activity_feed() -> ActivityFeedResponse:
        from openbiliclaw.runtime.activity_feed import ActivityFeedBuilder

        runtime_status: dict[str, object] = {}
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if callable(get_runtime_status):
            runtime_status = dict(get_runtime_status())
        get_account_sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
        if callable(get_account_sync_status):
            runtime_status.update(get_account_sync_status())

        cognition_updates: list[dict[str, object]] = []
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            cognition_updates = [
                item for item in load_cognition_updates() if isinstance(item, dict)
            ]

        builder = ActivityFeedBuilder(database=ctx.database)
        payload = builder.build(
            runtime_status=runtime_status,
            cognition_updates=cognition_updates,
        )
        payload_items = payload.get("items", [])
        item_dicts = payload_items if isinstance(payload_items, list) else []
        return ActivityFeedResponse(
            live_summary=str(payload.get("live_summary", "")),
            headline=str(payload.get("headline", "")),
            items=[
                ActivityFeedItemOut(
                    id=str(item.get("id", "")),
                    kind=str(item.get("kind", "")),
                    summary=str(item.get("summary", "")),
                    detail=str(item.get("detail", "")),
                    created_at=str(item.get("created_at", "")),
                    tone=str(item.get("tone", "info")),
                )
                for item in item_dicts
                if isinstance(item, dict)
            ],
        )

    async def _trigger_replenishment_if_needed() -> None:
        """Fire a background Discovery refresh when the pool runs low."""
        curator = getattr(ctx.recommendation_engine, "_curator", None)
        if curator is None or not hasattr(curator, "needs_replenishment"):
            return
        if not curator.needs_replenishment():
            return
        trigger = getattr(ctx.runtime_controller, "trigger_manual_refresh", None)
        if callable(trigger):
            logger.info("Pool low — triggering automatic replenishment")
            asyncio.create_task(trigger())

    @app.post("/api/recommendations/reshuffle", response_model=RecommendationReshuffleResponse)
    async def reshuffle_recommendations() -> RecommendationReshuffleResponse:
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return RecommendationReshuffleResponse(items=[])
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return RecommendationReshuffleResponse(items=[])
        items = await ctx.recommendation_engine.reshuffle_recommendations(profile=profile, limit=10)
        await _trigger_replenishment_if_needed()
        return RecommendationReshuffleResponse(items=_serialize_recommendation_items(items))

    @app.post("/api/recommendations/append", response_model=RecommendationReshuffleResponse)
    async def append_recommendations(
        payload: RecommendationAppendIn,
    ) -> RecommendationReshuffleResponse:
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return RecommendationReshuffleResponse(items=[])
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return RecommendationReshuffleResponse(items=[])
        items = await ctx.recommendation_engine.append_recommendations(
            profile=profile,
            excluded_bvids=payload.excluded_bvids,
            limit=10,
        )
        await _trigger_replenishment_if_needed()
        return RecommendationReshuffleResponse(items=_serialize_recommendation_items(items))

    @app.post("/api/recommendations/refresh", response_model=RecommendationRefreshResponse)
    async def refresh_recommendations() -> RecommendationRefreshResponse:
        trigger_manual_refresh = getattr(ctx.runtime_controller, "trigger_manual_refresh", None)
        if not callable(trigger_manual_refresh):
            return RecommendationRefreshResponse(
                ok=True,
                accepted=False,
                state="idle",
                reason="runtime_unavailable",
            )

        result = await trigger_manual_refresh()
        return RecommendationRefreshResponse(
            ok=True,
            accepted=bool(result.get("accepted", False)),
            state=str(result.get("state", "idle")),
            reason=str(result.get("reason", "")),
        )

    @app.get("/api/runtime-status", response_model=RuntimeStatusResponse)
    async def runtime_status() -> RuntimeStatusResponse:
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if not callable(get_runtime_status):
            return RuntimeStatusResponse(
                initialized=False,
                recommendation_count=0,
                pending_signal_events=0,
                unread_count=0,
            )
        payload = dict(get_runtime_status())
        get_account_sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
        if callable(get_account_sync_status):
            payload.update(get_account_sync_status())
        get_update_status = getattr(ctx.auto_update_service, "get_runtime_status", None)
        if callable(get_update_status):
            payload.update(get_update_status())
        return RuntimeStatusResponse(**payload)

    @app.get("/api/notifications/pending", response_model=PendingNotificationResponse)
    async def pending_notification() -> PendingNotificationResponse:
        get_pending_notification = getattr(ctx.runtime_controller, "get_pending_notification", None)
        item = get_pending_notification() if callable(get_pending_notification) else None
        if item is None:
            get_notification_candidate = getattr(ctx.database, "get_notification_candidate", None)
            if callable(get_notification_candidate):
                candidate = get_notification_candidate(min_confidence=0.82)
                if candidate is not None:
                    item = {
                        "recommendation_id": int(candidate["id"]),
                        "bvid": str(candidate.get("bvid", "")),
                        "title": str(candidate.get("title", "")),
                        "reason": str(candidate.get("expression", "")),
                    }
        if item is None:
            return PendingNotificationResponse(item=None)
        return PendingNotificationResponse(item=PendingNotificationOut(**item))

    @app.get(
        "/api/cognition-updates/pending",
        response_model=PendingCognitionUpdateResponse,
    )
    async def pending_cognition_update() -> PendingCognitionUpdateResponse:
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if not callable(load_cognition_updates):
            return PendingCognitionUpdateResponse(item=None)
        updates = [
            item
            for item in load_cognition_updates()
            if isinstance(item, dict) and not bool(item.get("notified", False))
        ]
        if not updates:
            return PendingCognitionUpdateResponse(item=None)
        latest = updates[-1]
        return PendingCognitionUpdateResponse(
            item=PendingCognitionUpdateOut(
                id=str(latest.get("id", "")),
                kind=str(latest.get("kind", "")),
                summary=str(latest.get("summary", "")),
            )
        )

    @app.post(
        "/api/cognition-updates/seen",
        response_model=CognitionUpdateSeenResponse,
    )
    async def cognition_update_seen(
        payload: CognitionUpdateSeenIn,
    ) -> CognitionUpdateSeenResponse:
        update_id = payload.id.strip()
        if not update_id:
            raise HTTPException(status_code=422, detail="Cognition update id is required.")
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        save_cognition_updates = getattr(ctx.memory_manager, "save_cognition_updates", None)
        if not callable(load_cognition_updates) or not callable(save_cognition_updates):
            raise HTTPException(status_code=500, detail="Cognition update storage unavailable.")
        updates = load_cognition_updates()
        found = False
        for item in updates:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "")).strip() != update_id:
                continue
            item["notified"] = True
            found = True
            break
        if not found:
            raise HTTPException(status_code=404, detail="Cognition update not found.")
        save_cognition_updates(updates)
        return CognitionUpdateSeenResponse(ok=True, id=update_id)

    @app.get("/api/delight/pending", response_model=PendingDelightResponse)
    async def pending_delight() -> PendingDelightResponse:
        get_pending_delight = getattr(ctx.runtime_controller, "get_pending_delight", None)
        item = get_pending_delight() if callable(get_pending_delight) else None
        if item is None:
            return PendingDelightResponse(item=None)
        return PendingDelightResponse(item=PendingDelightOut(**item))

    @app.post("/api/delight/sent", response_model=DelightAckResponse)
    async def mark_delight_sent(payload: DelightAckIn) -> DelightAckResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="Delight bvid is required.")
        mark_sent = getattr(ctx.runtime_controller, "mark_delight_sent", None)
        if callable(mark_sent):
            mark_sent(bvid)
        else:
            ctx.database.mark_delight_notified(bvid)
        return DelightAckResponse(ok=True, bvid=bvid)

    @app.post("/api/notifications/sent", response_model=NotificationAckResponse)
    async def mark_notification_sent(payload: NotificationAckIn) -> NotificationAckResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="Notification bvid is required.")
        mark_sent = getattr(ctx.runtime_controller, "mark_notification_sent", None)
        if callable(mark_sent):
            mark_sent(bvid)
        else:
            ctx.database.mark_notification_sent(bvid)
        return NotificationAckResponse(ok=True, bvid=bvid)

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(payload: ChatIn) -> ChatResponse:
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Chat message is required.")
        reply = await ctx.dialogue.respond(message)
        return ChatResponse(reply=reply)

    @app.post("/api/feedback", response_model=FeedbackResponse)
    async def feedback(payload: FeedbackIn) -> FeedbackResponse:
        feedback_type = payload.feedback_type.strip().lower()
        note = payload.note.strip()
        if feedback_type not in {"like", "dislike", "comment"}:
            raise HTTPException(status_code=422, detail="Unsupported feedback type.")
        if feedback_type == "comment" and not note:
            raise HTTPException(status_code=422, detail="Comment feedback requires note.")

        recommendation = ctx.database.get_recommendation_by_id(payload.recommendation_id)
        if recommendation is None:
            raise HTTPException(status_code=404, detail="Recommendation not found.")

        ctx.database.update_recommendation_feedback(
            payload.recommendation_id,
            feedback_type=feedback_type,
            feedback_note=note,
        )
        await ctx.memory_manager.propagate_event(
            {
                "event_type": "feedback",
                "title": str(recommendation.get("title", "")),
                "metadata": {
                    "recommendation_id": payload.recommendation_id,
                    "bvid": recommendation.get("bvid", ""),
                    "feedback_type": feedback_type,
                    "feedback_note": note,
                },
            }
        )
        record_immediate_feedback_cognition = getattr(
            ctx.soul_engine,
            "record_immediate_feedback_cognition",
            None,
        )
        if callable(record_immediate_feedback_cognition):
            with suppress(Exception):
                record_immediate_feedback_cognition(
                    feedback_type=feedback_type,
                    title=str(recommendation.get("title", "")),
                    note=note,
                )
        asyncio.create_task(_run_post_feedback_tasks())
        return FeedbackResponse(
            ok=True,
            recommendation_id=payload.recommendation_id,
            feedback_type=feedback_type,
        )

    @app.post(
        "/api/recommendation-click",
        response_model=RecommendationClickResponse,
    )
    async def recommendation_click(
        payload: RecommendationClickIn,
    ) -> RecommendationClickResponse:
        """Ingest a recommendation click-through as a strong profile signal.

        The click is evidence that the user actively chose to watch a
        recommended video. It is treated as a strong signal that bypasses
        the pipeline's min_signals gate and updates Interest + Surface
        immediately. If the recommendation_id resolves to a stored card,
        its metadata (title, topic, up_name) is pulled from the database
        so the payload reaches the pipeline even when the extension sends
        only a bare BV id.
        """
        from openbiliclaw.soul.pipeline import signal_from_recommendation_click

        recommendation: dict[str, object] | None = None
        if payload.recommendation_id is not None:
            recommendation = ctx.database.get_recommendation_by_id(
                payload.recommendation_id,
            )

        bvid = (payload.bvid or "").strip()
        title = (payload.title or "").strip()
        topic_label = (payload.topic_label or "").strip()
        up_name = (payload.up_name or "").strip()

        if recommendation is not None:
            bvid = bvid or str(recommendation.get("bvid", "")).strip()
            title = title or str(recommendation.get("title", "")).strip()
            topic_label = topic_label or str(
                recommendation.get("topic_label", "")
            ).strip()
            up_name = up_name or str(recommendation.get("up_name", "")).strip()

        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required.")

        # Persist the click as an event so history/query paths can see it.
        with suppress(Exception):
            await ctx.memory_manager.propagate_event(
                {
                    "event_type": "click",
                    "title": title,
                    "metadata": {
                        "recommendation_id": payload.recommendation_id,
                        "bvid": bvid,
                        "topic_label": topic_label,
                        "up_name": up_name,
                        "source": "recommendation_click",
                    },
                }
            )

        # Push a strong signal into the profile update pipeline.
        layers_updated: list[str] = []
        pipeline = getattr(ctx.soul_engine, "pipeline", None) if ctx.soul_engine else None
        if pipeline is not None:
            signal = signal_from_recommendation_click(
                bvid=bvid,
                title=title,
                recommendation_id=payload.recommendation_id,
                topic_label=topic_label,
                up_name=up_name,
            )
            try:
                ingest_result = await pipeline.ingest(signal)
            except Exception:
                logger.exception("Failed to ingest recommendation_click signal")
            else:
                layers_updated = [r.layer.value for r in ingest_result.layers_updated]

        return RecommendationClickResponse(
            ok=True,
            bvid=bvid,
            layers_updated=layers_updated,
        )

    # ── Configuration management endpoints ──────────────────────────

    def _config_to_response(
        cfg: Any,
        issues: list[Any] | None = None,
        *,
        mask_keys: bool = True,
    ) -> ConfigResponse:
        """Convert a Config dataclass to a ConfigResponse, optionally masking API keys."""

        def _mask(key: str) -> str:
            if not mask_keys or not key:
                return key
            if len(key) <= 8:
                return "*" * len(key)
            return key[:4] + "*" * (len(key) - 8) + key[-4:]

        def _provider_out(p: Any) -> LLMProviderConfigOut:
            return LLMProviderConfigOut(
                api_key=_mask(p.api_key),
                model=p.model,
                base_url=p.base_url,
                http_referer=getattr(p, "http_referer", ""),
                x_title=getattr(p, "x_title", ""),
            )

        issue_list = [
            ConfigIssueOut(field=i.field, message=i.message)
            for i in (issues or [])
        ]

        return ConfigResponse(
            language=cfg.language,
            data_dir=cfg.data_dir,
            llm=LLMConfigOut(
                default_provider=cfg.llm.default_provider,
                openai=_provider_out(cfg.llm.openai),
                claude=_provider_out(cfg.llm.claude),
                gemini=_provider_out(cfg.llm.gemini),
                deepseek=_provider_out(cfg.llm.deepseek),
                ollama=_provider_out(cfg.llm.ollama),
                openrouter=_provider_out(cfg.llm.openrouter),
                embedding=EmbeddingConfigOut(
                    provider=cfg.llm.embedding.provider,
                    model=cfg.llm.embedding.model,
                    similarity_threshold=cfg.llm.embedding.similarity_threshold,
                ),
                soul=ModuleLLMConfigOut(
                    provider=cfg.llm.soul.provider,
                    model=cfg.llm.soul.model,
                ),
                discovery=ModuleLLMConfigOut(
                    provider=cfg.llm.discovery.provider,
                    model=cfg.llm.discovery.model,
                ),
                recommendation=ModuleLLMConfigOut(
                    provider=cfg.llm.recommendation.provider,
                    model=cfg.llm.recommendation.model,
                ),
                evaluation=ModuleLLMConfigOut(
                    provider=cfg.llm.evaluation.provider,
                    model=cfg.llm.evaluation.model,
                ),
            ),
            bilibili=BilibiliConfigOut(
                auth_method=cfg.bilibili.auth_method,
                cookie=_mask(cfg.bilibili.cookie),
                browser_executable=cfg.bilibili.browser_executable,
                browser_headed=cfg.bilibili.browser_headed,
            ),
            scheduler=SchedulerConfigOut(
                enabled=cfg.scheduler.enabled,
                discovery_cron=cfg.scheduler.discovery_cron,
                pool_target_count=cfg.scheduler.pool_target_count,
                account_sync_interval_hours=cfg.scheduler.account_sync_interval_hours,
                auto_update_enabled=cfg.scheduler.auto_update_enabled,
                auto_update_check_interval_hours=cfg.scheduler.auto_update_check_interval_hours,
            ),
            storage=StorageConfigOut(db_path=cfg.storage.db_path),
            logging=LoggingConfigOut(
                level=cfg.logging.level,
                file_level=cfg.logging.file_level,
                directory=cfg.logging.directory,
                filename=cfg.logging.filename,
            ),
            issues=issue_list,
        )

    @app.get("/api/config", response_model=ConfigResponse)
    def get_config(reveal_keys: bool = False) -> ConfigResponse:
        """Return the current configuration (API keys masked by default)."""
        from openbiliclaw.config import (
            _collect_config_issues,
            load_config,
        )

        cfg = load_config()
        issues = _collect_config_issues(cfg)
        return _config_to_response(cfg, issues, mask_keys=not reveal_keys)

    @app.put("/api/config", response_model=ConfigUpdateResponse)
    async def update_config(payload: ConfigUpdateIn) -> ConfigUpdateResponse:
        """Update configuration, persist to config.toml, and hot-reload runtime.

        Only the fields included in the request body are modified.
        After persisting, the backend attempts to rebuild all swappable
        runtime components so the new settings take effect immediately.
        """
        from openbiliclaw.config import (
            _collect_config_issues,
            load_config,
            save_config,
        )

        cfg = load_config()
        update = payload.model_dump(exclude_none=True)

        # Apply top-level scalars
        if "language" in update:
            cfg.language = str(update["language"])
        if "data_dir" in update:
            cfg.data_dir = str(update["data_dir"])

        # Apply LLM updates
        if "llm" in update:
            llm_data = update["llm"]
            if "default_provider" in llm_data:
                cfg.llm.default_provider = str(llm_data["default_provider"])
            for provider_name in (
                "openai", "claude", "gemini", "deepseek", "ollama", "openrouter",
            ):
                if provider_name in llm_data and isinstance(llm_data[provider_name], dict):
                    provider_cfg = getattr(cfg.llm, provider_name)
                    pdata = llm_data[provider_name]
                    for field_name in ("api_key", "model", "base_url", "http_referer", "x_title"):
                        if field_name in pdata:
                            setattr(provider_cfg, field_name, str(pdata[field_name]))
            if "embedding" in llm_data and isinstance(llm_data["embedding"], dict):
                emb = llm_data["embedding"]
                if "provider" in emb:
                    cfg.llm.embedding.provider = str(emb["provider"])
                if "model" in emb:
                    cfg.llm.embedding.model = str(emb["model"])
                if "similarity_threshold" in emb:
                    cfg.llm.embedding.similarity_threshold = float(emb["similarity_threshold"])
            for module_name in ("soul", "discovery", "recommendation", "evaluation"):
                if module_name in llm_data and isinstance(llm_data[module_name], dict):
                    mod_cfg = getattr(cfg.llm, module_name)
                    mdata = llm_data[module_name]
                    if "provider" in mdata:
                        mod_cfg.provider = str(mdata["provider"])
                    if "model" in mdata:
                        mod_cfg.model = str(mdata["model"])

        # Apply bilibili updates
        if "bilibili" in update:
            bdata = update["bilibili"]
            if "auth_method" in bdata:
                cfg.bilibili.auth_method = str(bdata["auth_method"])
            if "cookie" in bdata:
                cfg.bilibili.cookie = str(bdata["cookie"])
            if "browser_executable" in bdata:
                cfg.bilibili.browser_executable = str(bdata["browser_executable"])
            if "browser_headed" in bdata:
                cfg.bilibili.browser_headed = bool(bdata["browser_headed"])

        # Apply scheduler updates
        if "scheduler" in update:
            sdata = update["scheduler"]
            for key in (
                "enabled", "discovery_cron", "pool_target_count",
                "account_sync_interval_hours", "auto_update_enabled",
                "auto_update_check_interval_hours",
            ):
                if key in sdata:
                    current_val = getattr(cfg.scheduler, key)
                    if isinstance(current_val, bool):
                        setattr(cfg.scheduler, key, bool(sdata[key]))
                    elif isinstance(current_val, int):
                        setattr(cfg.scheduler, key, int(sdata[key]))
                    else:
                        setattr(cfg.scheduler, key, str(sdata[key]))

        # Apply storage updates
        if "storage" in update:
            stdata = update["storage"]
            if "db_path" in stdata:
                cfg.storage.db_path = str(stdata["db_path"])

        # Apply logging updates
        if "logging" in update:
            ldata = update["logging"]
            for key in ("level", "file_level", "directory", "filename"):
                if key in ldata:
                    setattr(cfg.logging, key, str(ldata[key]))

        # Save to disk
        saved_path = save_config(cfg)
        issues = _collect_config_issues(cfg)
        logger.info("Configuration saved to %s", saved_path)

        # ── Hot-reload: rebuild runtime components ──────────────────
        reloaded = False
        reload_message = f"配置已保存到 {saved_path}。"
        try:
            ctx.rebuild_from_config(cfg)
            await ctx.restart_background_tasks(app)
            reloaded = True
            reload_message += " 运行时组件已热重载，新配置立即生效。"
            logger.info("Config hot-reload succeeded")
            # Notify WebSocket subscribers so the extension re-fetches data
            with suppress(Exception):
                await ctx.event_hub.publish({
                    "type": "config_reloaded",
                    "message": "配置已热重载，运行时组件已重建。",
                })
        except Exception as exc:
            logger.exception("Config hot-reload failed — old components remain active")
            reload_message += f" 热重载失败（{exc}），旧组件仍在运行，重启后端可完全生效。"

        return ConfigUpdateResponse(
            ok=True,
            config=_config_to_response(cfg, issues, mask_keys=True),
            message=reload_message,
            reloaded=reloaded,
        )

    return app
