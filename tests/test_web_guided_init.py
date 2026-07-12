import re
from pathlib import Path


def test_setup_wizard_static_contract_uses_guided_init_endpoint() -> None:
    """Static guard: setup must reference guided init and not the legacy poke."""
    html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'data-panel="3"' in html
    assert "GET /api/init-status" in html or 'fetch("/api/init-status"' in html
    assert 'fetch("/api/init"' in html
    assert "init_progress" in html
    assert "/api/init-completed" not in html


def test_desktop_web_static_contract_exposes_guided_init_cta() -> None:
    """Static guard for the desktop guided-init CTA wiring."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    assert 'initStatus: "/init-status"' in app_js
    assert 'startInit: "/init"' in app_js
    assert "renderInitOnboarding" in app_js
    assert "buildInitChecklist" in app_js
    assert "INIT_SOURCE_OPTIONS" in app_js
    assert "init_progress" in app_js
    # "openbiliclaw init" may appear ONLY inside the unsupported_runtime copy
    # (the container-blocked docker-exec fallback) — never as generic guidance
    # steering users away from the in-page guided-init CTA.
    assert app_js.count("openbiliclaw init") == 1
    unsupported_line = next(line for line in app_js.splitlines() if "unsupported_runtime:" in line)
    assert "docker exec" in unsupported_line
    assert "openbiliclaw init" in unsupported_line
    assert ".init-onboarding" in app_css
    assert ".init-progress-fill" in app_css


def test_web_guided_init_polling_is_single_flight() -> None:
    """Runtime-stream events and timer fallback must not compound status polls."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "initPollInFlight" in setup_html
    assert "initPollPending" in setup_html
    assert "scheduleInitPoll(" in setup_html
    assert "initRefreshInFlight" in app_js
    assert "initRefreshPending" in app_js
    assert "scheduleInitStatusRefresh(" in app_js


def test_unknown_init_reasons_remain_diagnosable() -> None:
    """Frontend fallback should surface unknown backend reason codes."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "未知初始化状态" in setup_html
    assert "未知初始化状态" in app_js
    assert re.search(r"INIT_REASON_TEXT\[reason\]\s*\|\|\s*`未知初始化状态", setup_html)
    assert re.search(r"INIT_REASON_TEXT\[reason\]\s*\|\|\s*`未知初始化状态", app_js)


def test_web_surfaces_no_longer_block_reddit_only_init() -> None:
    """Reddit bootstrap events are valid init signals."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "no_profile_signal_sources" not in setup_html
    assert "Reddit 当前只启用 discovery" not in setup_html
    assert "连接你的 B站 账号" not in setup_html
    assert "连接浏览器扩展和平台账号" in setup_html
    assert "reddit.com" in setup_html
    assert "先检查 B站 登录 / AI 服务 / 向量模型" not in setup_html
    assert "所选平台的登录状态" in setup_html
    assert "no_profile_signal_sources" not in app_js
    assert "Reddit 当前只启用 discovery" not in app_js


def test_setup_llm_model_is_visible_and_save_suppresses_background_llm_work() -> None:
    """Setup step 1 saves config only; model name is a normal required field."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "高级（可选：自定义模型名）" not in setup_html
    assert '<label for="model">模型名</label>' in setup_html
    assert "suppress_background_llm_work: true" in setup_html


def test_setup_init_sources_are_explicit_opt_in_without_settings_enable_block() -> None:
    """Checked setup sources are this-run opt-ins, not a filter over settings toggles."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "勾选会同时开启该来源" in setup_html
    assert "selectedSourcesNeedingEnable" not in setup_html
    assert "还没在设置里开启" not in setup_html
    for source in ("bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu", "reddit"):
        assert f'key: "{source}"' in setup_html
        assert f'key: "{source}"' in app_js


def test_guided_init_web_docs_belong_to_v03110_release_block() -> None:
    """Do not retroactively claim already-released v0.3.109 shipped web Phase 2."""
    version_py = Path("src/openbiliclaw/__init__.py").read_text(encoding="utf-8")
    changelog = Path("docs/changelog.md").read_text(encoding="utf-8")
    gui_spec = Path("docs/specs/gui-init.md").read_text(encoding="utf-8")

    # Web Phase 2 shipped in v0.3.111 — the project version must never sit
    # below that (an exact pin here would break on every release bump).
    match = re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', version_py)
    assert match is not None
    assert tuple(int(part) for part in match.groups()) >= (0, 3, 111)
    top_block = changelog.split("## v0.3.109", 1)[0]
    assert "/setup/" in top_block
    assert "/web" in top_block
    assert "已落地 v0.3.111" in gui_spec
    assert "已落地 v0.3.109" not in gui_spec


def test_init_onboarding_gate_trusts_init_status_when_runtime_status_is_unavailable() -> None:
    """The guided-init gate must not depend solely on state.runtimeStatus.

    runtime-status can be transiently unreachable (hydrate re-fetch swallowed
    into null) or rebuilt from field-less runtime events whose missing
    `initialized` normalizes to true. /api/init-status stays the authoritative
    pre-init source, so an explicit initialized=false there must still surface
    the guided-init card.
    """
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    gate = app_js.split("function shouldShowInitOnboarding(", 1)[1]
    gate = gate.split("\n    }", 1)[0]
    assert "state.initStatus?.initialized === false" in gate
    assert "hasPostInitRuntimeSignals(runtime)" in gate


def test_hydrate_runtime_status_fallback_is_not_dead_catch() -> None:
    """Strict runtime reads keep the first settled snapshot as fallback."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "settleResource(readRuntimeStatusSnapshot())" in app_js
    assert "const firstRuntimeGeneration = desktopRuntimeGeneration;" in app_js
    assert "const secondRuntimeGeneration = desktopRuntimeGeneration;" in app_js
    assert "const latestRuntime = await readRuntimeStatusSnapshot();" in app_js
    assert "applyDesktopRuntimeSnapshot(effectiveRuntime, effectiveRuntimeGeneration);" in app_js


def test_bili_checklist_label_reflects_probe_result_and_surfaces_detail() -> None:
    """A failed B站 probe must never render a label containing "已登录".

    Field report (2026-07): with a proxy on, the login probe fails while the
    user IS logged in in the browser. Unchecking B站 demoted the row to the
    soft "B站 已登录（未勾选 B 站，可跳过）" label — which reads as "logged in
    now". Labels must state the actual probe result, and the failure hint must
    carry the backend's `bilibili_detail` (cookie-expired vs proxy-broken).
    """
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "B站 登录检测未通过" in setup_html
    assert "B 站登录检测未通过" in app_js
    for text in (setup_html, app_js):
        # The old unconditional "已登录（未勾选…" label is gone.
        assert "已登录（未勾选" not in text
        assert "bilibili_detail" in text


def test_runtime_stream_open_rehydrates_when_backend_data_never_loaded() -> None:
    """Frozen-entry race: /web can load before the backend binds, and the boot
    hydrate swallows every failure into nulls. An uninitialized backend emits
    no runtime events, so without a re-hydrate on the first successful
    runtime-stream connect the guided-init card would stay hidden forever."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    open_handler = app_js.split('socket.addEventListener("open"', 1)[1]
    open_handler = open_handler.split("});", 1)[0]
    assert (
        "if (!state.initStatus && !state.runtimeStatus) scheduleBackendHydration();" in open_handler
    )


def test_setup_wizard_guard_resumes_running_and_initialized_states_on_load() -> None:
    """A mid-init reload must re-attach to progress instead of landing on step 0,
    and an initialized backend must not re-present the LLM form."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    guard = setup_html.split("(async function guard()", 1)[1]
    assert "fetchInitStatus()" in guard
    assert "if (status.running)" in guard
    assert "renderInitProgress(status)" in guard
    assert "connectInitStream()" in guard
    assert "if (status.initialized)" in guard
    assert "renderWaitingForFirstPool()" in guard


def test_setup_wizard_allows_saved_api_key_to_be_reused_without_reentry() -> None:
    """PUT /api/config only touches fields present in the payload, so an empty
    key field on a provider with a persisted key must not block step 0."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "savedKeyProviders" in setup_html
    assert "!apiKey && !savedKeyProviders.has(provider)" in setup_html
    assert "已保存，留空则沿用当前 Key" in setup_html


def test_setup_wizard_first_pool_wait_has_web_escape_hatch() -> None:
    """The 95% waiting state must never park the user on a disabled button with
    no way out of the wizard."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'id="initEscape"' in setup_html
    assert '<a href="/web">' in setup_html
    waiting = setup_html.split("function renderWaitingForFirstPool()", 1)[1].split("\n    }", 1)[0]
    assert '$("#initEscape").className = "msg show info";' in waiting


def test_issue72_gateway_fields_present_on_all_config_surfaces() -> None:
    """issue #72 — third-party gateway controls exist on every web config
    surface: Claude gets an optional Base URL, the OpenAI-protocol family
    gets an api_flavor (/v1/responses) selector, and stale Base URLs are
    never submitted for providers that don't show the field."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    desktop_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    # /setup/ wizard: Claude shows optional Base URL with a relay hint;
    # openai_compatible shows the protocol selector; base_url is only
    # submitted for providers whose form actually displayed it.
    assert 'id="baseHint"' in setup_html
    assert 'id="flavorWrap"' in setup_html
    assert 'id="apiFlavor"' in setup_html
    assert "(isCompat || isClaude)" in setup_html
    assert 'provider === "openai_compatible" || provider === "claude"' in setup_html
    assert 'pcfg.api_flavor = $("#apiFlavor").value' in setup_html

    # Desktop settings: flavor select for both the default and the fallback
    # provider panels, wired into load + save paths.
    assert 'id="llmApiFlavor"' in desktop_html
    assert 'id="llmFallbackApiFlavor"' in desktop_html
    assert "llmProviderConfig.api_flavor" in app_js
    assert "llmFallbackConfig.api_flavor" in app_js
    assert 'setSelect("llmApiFlavor"' in app_js
    assert 'setSelect("llmFallbackApiFlavor"' in app_js


def test_setup_wizard_config_save_401_points_to_login_instead_of_dead_end() -> None:
    """/api/config is session-gated while init endpoints are public: an
    auth-enabled remote browser must get a login path on save, not a bare
    "保存失败：HTTP 401" dead end at step 0."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "r.status === 401" in setup_html
    assert "输入访问密码登录" in setup_html
    assert '<a href="/web">' in setup_html


def test_web_surfaces_offer_embedding_repair_and_progress() -> None:
    """Both web init checklists expose one-click model download + live progress.

    The repair button POSTs /api/embedding/repair; while the pull runs the
    backend classifies embedding_check="repairing" and the pages keep polling
    so the row's hint shows live percent (user request 2026-07-05).
    """
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    for surface in (setup_html, app_js):
        assert "data-embedding-repair" in surface
        assert "embedding_detail" in surface
        assert "embedding_pull_status" in surface
        assert "ollama_phase" in surface
        assert "Ollama 启动中" in surface
        assert "model_missing" in surface and "model_broken" in surface
        assert "model_path_encoding" in surface
        assert "disk_full" in surface and "network" in surface and "model_oom" in surface
        assert "provider_error" in surface
        assert "迁移模型目录并修复" in surface
        assert "重新检测" in surface
        assert "embeddingPullProgressView" in surface
    assert '"/api/embedding/repair"' in setup_html
    assert "embedding_repair_running" in setup_html  # keeps polling while downloading
    assert 'embeddingRepair: "/embedding/repair"' in app_js
    assert "handleEmbeddingRepairClick" in app_js
    assert ".init-repair-btn" in setup_html
    assert ".init-repair-btn" in app_css


# ── init-progress-visibility Phase 0: API models + heartbeat task ────────────


def _progress_coord(tmp_path):
    from types import SimpleNamespace

    from openbiliclaw.runtime.init_coordinator import InitCoordinator
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "hb.db")
    db.initialize()
    ctx = SimpleNamespace(database=db, event_hub=None, runtime_controller=None)
    return InitCoordinator(ctx), db


def test_init_stage_out_accepts_and_omits_progress_fields() -> None:
    """InitStageOut stays backward-compatible: old stage dicts (no progress /
    eta_seconds) parse, and new ones nest InitStageProgressOut."""
    from openbiliclaw.api.models import InitStageOut, InitStageProgressOut

    legacy = InitStageOut(n=2, label="分析偏好", status="pending", reason=None)
    assert legacy.progress is None
    assert legacy.eta_seconds is None

    rich = InitStageOut(
        n=2,
        label="分析偏好",
        status="running",
        reason=None,
        progress={"done": 3, "total": 8, "note": "第 3/8 批"},
        eta_seconds=180,
    )
    assert isinstance(rich.progress, InitStageProgressOut)
    assert rich.progress.done == 3 and rich.progress.total == 8
    assert rich.eta_seconds == 180


def test_init_status_out_has_last_activity_default() -> None:
    from openbiliclaw.api.models import InitStatusOut

    assert InitStatusOut().last_activity == ""


def test_heartbeat_interval_bounds_last_activity_freshness() -> None:
    """Goal metric 1 fallback: the heartbeat period must be ≤30s so that a
    65s hung stage still lands ≥2 touches (last_activity stays ≤30s fresh)."""
    from openbiliclaw.api.app import _INIT_HEARTBEAT_INTERVAL_SECONDS

    assert _INIT_HEARTBEAT_INTERVAL_SECONDS <= 30


async def test_heartbeat_task_keeps_touching_until_cancelled(tmp_path) -> None:
    import asyncio
    from contextlib import suppress

    from openbiliclaw.api.app import _run_init_heartbeat

    coord, db = _progress_coord(tmp_path)
    coord.try_start("run-1")
    await coord.mark_running("run-1")
    seq_before = db.get_latest_init_run()["sequence"]

    task = asyncio.create_task(_run_init_heartbeat(coord, "run-1", interval=0.01))
    await asyncio.sleep(0.06)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    seq_after = db.get_latest_init_run()["sequence"]
    # At least two heartbeat touches landed while the task ran.
    assert seq_after - seq_before >= 2
    assert coord.get_status()["last_activity"] != ""


async def test_heartbeat_swallows_touch_errors(tmp_path) -> None:
    import asyncio
    from contextlib import suppress

    from openbiliclaw.api.app import _run_init_heartbeat

    class _BoomCoord:
        async def touch(self, run_id: str) -> None:
            raise RuntimeError("db gone")

    # A failing touch must not kill the heartbeat loop (it just logs WARNING).
    task = asyncio.create_task(_run_init_heartbeat(_BoomCoord(), "run-1", interval=0.01))
    await asyncio.sleep(0.03)
    assert not task.done()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def test_init_status_endpoint_surfaces_last_activity(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "e1.db")
    db.initialize()
    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    with TestClient(app) as client:
        body = client.get("/api/init-status").json()
    assert "last_activity" in body
    assert isinstance(body["last_activity"], str)


# ── init-progress-visibility Phase 1: run_guided_init producer wiring ─────────


class _RecordingCoordinator:
    """Records the progress signals run_guided_init emits (no DB needed)."""

    def __init__(self) -> None:
        self.stage_progress_calls: list[dict] = []
        self.started_stages: list[int] = []
        self.done_stages: list[int] = []

    async def stage_started(self, run_id: str, n: int) -> None:
        self.started_stages.append(n)

    async def stage_done(self, run_id: str, n: int, *, status: str = "ok", reason=None) -> None:
        self.done_stages.append(n)

    async def stage_progress(
        self, run_id: str, stage: int, *, done: int, total: int, note=None
    ) -> None:
        self.stage_progress_calls.append(
            {"stage": stage, "done": done, "total": total, "note": note}
        )

    def register_enqueued_task(self, run_id: str, task_id: str) -> None:
        pass


class _StubEngine:
    def __init__(self, chunk_reports: int = 3) -> None:
        self.chunk_reports = chunk_reports
        self.received_callback = None

    async def analyze_events(self, events, *, event_chunk_size=0, progress_callback=None):
        self.received_callback = progress_callback
        if progress_callback is not None:
            for i in range(1, self.chunk_reports + 1):
                await progress_callback(i, self.chunk_reports)

    async def build_initial_profile(self, history):
        return object()


class _StubMemory:
    async def propagate_event(self, event) -> None:
        pass


def _patch_run_guided_init_collectors(monkeypatch, engine) -> None:
    import openbiliclaw.cli as cli

    async def _fetch_bili(client, *, history_limit, favorite_limit, follow_limit):
        return ([{"title": "hist-1"}], [], [])

    async def _rwp(coro, **kwargs):
        return await coro

    async def _discover_backfill(profile, *, target_pool_count, label_suffix=""):
        return 0

    monkeypatch.setattr(cli, "_fetch_bilibili_init_data", _fetch_bili)
    monkeypatch.setattr(
        cli, "_history_item_to_event", lambda item: {"event_type": "view", "title": "hist-1"}
    )
    monkeypatch.setattr(cli, "_collect_xhs_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_dy_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_yt_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_zhihu_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_reddit_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_enqueue_reddit_bootstrap_task", lambda kick=True: "task-r")
    monkeypatch.setattr(cli, "_kick_task_dispatcher", lambda source: None)
    monkeypatch.setattr(cli, "_build_draft_profile_for_discover", lambda memory: object())
    monkeypatch.setattr(cli, "_run_with_progress", _rwp)
    monkeypatch.setattr(cli, "_print_section_title", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_maybe_update_init_source_shares", lambda *a, **k: None)
    return _discover_backfill


async def test_run_guided_init_emits_stage_progress_for_sources_and_chunks(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    engine = _StubEngine(chunk_reports=3)
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)
    coord = _RecordingCoordinator()

    await cli.run_guided_init(
        client=object(),
        memory=_StubMemory(),
        soul_engine=engine,
        favorite_limit=0,
        follow_limit=0,
        include_bili=True,
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        include_x=False,
        include_zhihu=False,
        include_reddit=True,
        target_pool_count=0,
        discover_backfill=discover_backfill,
        coordinator=coord,
        run_id="run-1",
    )

    stage1 = [c for c in coord.stage_progress_calls if c["stage"] == 1]
    # Two selected sources: B站 then Reddit — note switches, done increments 0→1.
    assert [(c["done"], c["total"], c["note"]) for c in stage1] == [
        (0, 2, "正在采集 B 站"),
        (1, 2, "正在采集 Reddit"),
    ]

    stage2 = [c for c in coord.stage_progress_calls if c["stage"] == 2]
    assert [(c["done"], c["total"]) for c in stage2] == [(1, 3), (2, 3), (3, 3)]
    assert stage2[-1]["note"] == "第 3/3 批"

    # Stages all completed (Task 1 clears any progress residue on stage_done).
    assert coord.done_stages == [1, 2, 3, 4]


async def test_run_guided_init_cli_path_uses_console_progress_callback(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    engine = _StubEngine(chunk_reports=2)
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)

    # No coordinator (CLI path): analyze_events must still receive a callback,
    # and invoking it must not raise (it prints instead of hitting a coordinator).
    await cli.run_guided_init(
        client=object(),
        memory=_StubMemory(),
        soul_engine=engine,
        favorite_limit=0,
        follow_limit=0,
        include_bili=True,
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        include_x=False,
        include_zhihu=False,
        include_reddit=False,
        target_pool_count=0,
        discover_backfill=discover_backfill,
        coordinator=None,
        run_id=None,
    )
    assert engine.received_callback is not None
    await engine.received_callback(1, 2)  # prints without error
