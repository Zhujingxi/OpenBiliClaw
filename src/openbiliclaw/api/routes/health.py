"""Health / init-status router (Phase 1 pilot 2 extraction).

Contains ``GET /api/health`` and ``GET /api/init-status`` — the readiness
and guided-init status endpoints. Extracted from ``api/app.py`` following
the same narrow-deps pattern as ``api/routes/system.py``.

Externally visible behavior is unchanged: paths, methods, response bodies,
content types, and status codes match the legacy inline handlers exactly
(including the degraded 200 JSON branch of /api/health and the full
InitStatusOut model of /api/init-status).
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from openbiliclaw.api.dependencies import HealthRouteDeps

from openbiliclaw.api.models import (
    HealthResponse,
    InitPrerequisitesOut,
    InitStageOut,
    InitStatusOut,
)
from openbiliclaw.runtime import embedding_progress


def build_health_router(deps: HealthRouteDeps) -> APIRouter:
    """Build the health / init-status router.

    The router serves:

    - ``GET /api/health`` — readiness probe with embedding check, profile
      readiness, LAN IP, and degraded-mode payload.
    - ``GET /api/init-status`` — guided-init status and pre-init checklist.
    """
    router = APIRouter()

    @router.get("/api/health", response_model=HealthResponse, response_model_exclude_none=True)
    async def health() -> HealthResponse | JSONResponse:
        profile_ready = deps.health_profile_ready()
        lan_ip = deps.get_lan_ip()
        embedding_ready = await deps.health_embedding_ready()
        if deps.degraded():
            body: dict[str, object] = {
                "status": "degraded",
                "service": "openbiliclaw-api",
                "reason": str(deps.degraded_reason()),
                "issues": deps.degraded_issues_payload(),
                "embedding_ready": embedding_ready,
            }
            if profile_ready is not None:
                body["profile_ready"] = profile_ready
            if lan_ip is not None:
                body["lan_ip"] = lan_ip
            return JSONResponse(status_code=200, content=body)
        return HealthResponse(
            status="ok",
            service="openbiliclaw-api",
            profile_ready=profile_ready,
            lan_ip=lan_ip,
            embedding_ready=embedding_ready,
        )

    @router.get("/api/init-status", response_model=InitStatusOut)
    async def init_status(request: Request) -> InitStatusOut:
        """Authoritative guided-init status + pre-init checklist (gui-init §3).

        Remote-readable (mirrors autostart-status): a non-local caller still
        sees the state but ``can_manage`` is False. Degraded-mode readable.
        """
        from openbiliclaw.docker_runtime import is_running_in_container

        coord = deps.get_init_coordinator()
        prereqs = deps.get_init_prereqs()
        run = coord.get_status()
        initialized = bool(deps.health_profile_ready())
        running = bool(run["running"])
        if initialized and not running:
            # Steady state: once a profile exists the checklist is
            # informational only (can_start is false regardless, and POST
            # /api/init revalidates live before any force rebuild). Skip the
            # real chat/Bilibili probes so an open polling page -- /setup/ or
            # the desktop web waiting for the first pool -- no longer burns a
            # billable LLM ping per TTL window. Embedding stays live: it is
            # the same TTL-cached probe /api/health already exercises.
            bili = prereqs.peek_bilibili()
            chat = prereqs.peek_chat()
            embedding = await deps.health_embedding_ready(strict=True)
        else:
            # Probe the three services concurrently -- each is a real (now
            # strict) request with a generous cold-load timeout, so running
            # them sequentially could stack to ~40s. gather() bounds the wait
            # to the slowest single probe (TTL-cached, so steady-state polls
            # are instant).
            bili, chat, embedding = await asyncio.gather(
                prereqs.bilibili_check(),
                prereqs.chat_ready(),
                deps.health_embedding_ready(strict=True),
            )
        platforms = prereqs.enabled_platforms()
        trusted = deps.get_auth_gate().is_trusted_local(request)
        supported = not is_running_in_container()
        # v0.3.118+: bilibili login is no longer a server-side hard gate --
        # whether it blocks depends on the client's per-run source selection,
        # which only POST /api/init sees. ``bilibili_logged_in`` stays in the
        # prerequisites payload so clients gate the start button themselves
        # when Bili is among the checked sources; POST revalidates regardless.
        embedding_required = deps.embedding_required_for_init()
        hard_ok = chat and (embedding or not embedding_required)
        # Mirror POST /api/init's guards: an already-initialized profile blocks
        # a (non-force) start, so can_start must reflect that too -- otherwise E1
        # and E2 disagree and a client could offer "start" that E2 rejects.
        can_start = trusted and supported and hard_ok and not running and not initialized

        # Account sync may be the first owner that tries to build preferences
        # after desktop startup. It persists a safe, user-facing failure, but
        # init-status historically never read it despite being the page's
        # authoritative source -- so the UI still sat at 49% with no reason.
        account_profile_error = ""
        if not initialized and not running:
            sync_status = getattr(deps.get_account_sync_service(), "get_runtime_status", None)
            if callable(sync_status):
                with suppress(Exception):
                    raw_status = sync_status()
                    if isinstance(raw_status, dict):
                        candidate = str(raw_status.get("last_account_sync_error", "")).strip()
                        if candidate.startswith("画像分析失败："):
                            account_profile_error = candidate[:500]

        last_failure_reason = ""
        last_failure_detail = ""
        if not initialized and not running:
            run_status = str(run.get("status") or "")
            if run_status in ("failed", "cancelled"):
                last_failure_reason = str(run.get("reason") or run_status)
                last_failure_detail = str(run.get("detail") or "")
            if account_profile_error and not last_failure_detail:
                last_failure_reason = "analyze_failed"
                last_failure_detail = account_profile_error

        embedding_check, embedding_detail = await deps.diagnose_embedding(bool(embedding))
        pull_progress = deps.embedding_pull_progress_view()
        pull_status = str(pull_progress.get("status_text") or "")

        if not supported:
            reason, detail = "unsupported_runtime", "Docker 运行时不支持图形化初始化"
        elif running:
            reason, detail = "already_running", "初始化进行中"
        elif initialized:
            if bool(run["partial_success"]):
                # Profile creation succeeded but the first discovery pass did
                # not. Preserve the terminal cause written by complete() so
                # setup / desktop / popup can explain the degraded result and
                # offer a safe way forward instead of appearing stuck at 95%.
                reason = str(run.get("reason") or "discovery_partial")
                detail = str(
                    run.get("detail")
                    or "画像已生成，但首轮内容池本次未完成；系统会在后台继续补齐。"
                )
            else:
                reason, detail = "already_initialized", "已经初始化过了；如需重建请用 force"
        elif not trusted:
            # trusted participates in can_start but had no reason branch, so
            # remote/paired-mobile viewers got can_start=false with
            # reason="none" -- every client fell back to a generic "条件未满足"
            # while the checklist showed all-green (field report 2026-07-05).
            # All clients already map local_only to "只能在本机发起初始化。".
            reason, detail = "local_only", "只能在本机发起初始化"
        elif not chat:
            reason = "llm_not_ready"
            detail = account_profile_error or "AI 服务还没配好或当前不可用"
        elif embedding_required and not embedding:
            reason, detail = "embedding_not_ready", "向量模型还没就绪"
        elif bili != "ok":
            # Informational (does not flip can_start): blocks only if the
            # client keeps bilibili selected, which the UI enforces.
            reason, detail = "bilibili_not_logged_in", "还没检测到 B站 登录"
        elif run.get("status") in ("failed", "cancelled"):
            # Prereqs are fine and nothing is running, but the last run ended
            # badly -- surface why so the UI can show it (can_start stays true so
            # the user can retry) (gui-init review). ``detail`` carries the
            # stored failure specifics (exception summary / GuidedInitError
            # message) so an internal_error is diagnosable from the UI.
            reason = run.get("reason") or str(run.get("status"))
            detail = str(run.get("detail") or "")
        elif account_profile_error:
            # The current probe is healthy again, so retry is allowed, but the
            # previous background analysis failure still explains why no
            # profile exists yet.
            reason, detail = "analyze_failed", account_profile_error
        else:
            reason, detail = "none", ""

        start_mode: Literal["web", "cli_only", "local_only"] = (
            "cli_only" if not supported else "web" if trusted else "local_only"
        )

        return InitStatusOut(
            initialized=initialized,
            running=running,
            run_id=run["run_id"],
            sequence=run["sequence"],
            current_stage=run["current_stage"],
            total_stages=run["total_stages"],
            stages=[InitStageOut(**s) for s in run["stages"]],
            partial_success=bool(run["partial_success"]),
            can_start=can_start,
            can_manage=trusted,
            start_mode=start_mode,
            prerequisites=InitPrerequisitesOut(
                bilibili_logged_in=(bili == "ok"),
                bilibili_check=bili,
                bilibili_detail=prereqs.peek_bilibili_detail() if bili == "failed" else "",
                llm_ready=chat,
                embedding_ready=embedding,
                embedding_check=embedding_check,
                embedding_detail=embedding_detail,
                embedding_repair_running=bool(pull_progress["running"]),
                embedding_repair_completed=deps.progress_int(pull_progress.get("completed")),
                embedding_repair_total=deps.progress_int(pull_progress.get("total")),
                ollama_phase=embedding_progress.ollama_phase(),
                embedding_pull_status=pull_status,
                embedding_required=embedding_required,
                enabled_platforms=platforms,
            ),
            reason=reason,
            detail=detail,
            last_failure_reason=last_failure_reason,
            last_failure_detail=last_failure_detail,
            last_activity=str(run.get("last_activity") or ""),
        )

    return router


__all__ = ["build_health_router"]
