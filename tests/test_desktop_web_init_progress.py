"""Static contract for init-progress visibility on desktop web + setup wizard.

The three GUI surfaces share no module system, so desktop web and the setup
wizard mirror the popup's reference implementation
(extension/popup/popup-init-control.js — init-progress-visibility Phase 2).
These string-level assertions keep the mirrored formulas / copy from drifting.
"""

from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")


def _app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_desktop_init_progress_mirrors_popup_fraction_formula() -> None:
    app_js = _app_js()
    # Real sub-progress fraction (done/total) capped below stage completion.
    assert "STAGE_FRACTION_CAP" in app_js
    assert "0.95" in app_js
    # Elapsed/eta pseudo progress for stages without sub-progress.
    assert "Math.exp(-elapsed / eta)" in app_js
    assert "eta_seconds" in app_js
    # Legacy fallback half-step keeps old-backend ticks unchanged.
    assert "STAGE_FRACTION_FALLBACK" in app_js
    # Sub-progress note joins the running stage label.
    assert "progress?.note" in app_js


def test_desktop_init_progress_pct_is_monotonic_per_run() -> None:
    app_js = _app_js()
    # Per-run view state with a monotonic clamp on the rendered pct.
    assert "maxPct" in app_js
    assert "Math.max(st.maxPct, pct)" in app_js
    assert "_runViewState" in app_js


def test_desktop_surfaces_stall_copy_after_90s_of_silence() -> None:
    app_js = _app_js()
    assert "INIT_STALL_THRESHOLD_SECONDS = 90" in app_js
    assert "stalenessView" in app_js
    assert "last_activity" in app_js
    assert "没有新进展" in app_js
    assert "可以继续等待，或取消后重试" in app_js
    assert "● 进行中" in app_js
    # Amber styling hook for the stalled state.
    assert "init-stall-hint" in app_js
    assert ".init-stall-hint" in APP_CSS.read_text(encoding="utf-8")


def test_desktop_shows_expectation_copy_and_stage_eta() -> None:
    app_js = _app_js()
    # Idle expectation management near the start button.
    assert "整个过程通常需要 2–5 分钟" in app_js
    assert "进度会保留" in app_js
    # Running stage row surfaces its typical duration.
    assert "本阶段通常约" in app_js
    assert "stageEtaText" in app_js


def test_desktop_keeps_existing_override_states() -> None:
    """The two pre-existing display overrides must survive the upgrade."""
    app_js = _app_js()
    # First-pool wait pins 95%.
    assert "pct: 95" in app_js
    assert "整理首轮内容池" in app_js
    # Embedding pull borrows the progress bar while idle.
    assert "embeddingPull.pct" in app_js


# ── Setup wizard mirror (single-file inline JS, no test infra of its own) ────

SETUP_HTML = Path("src/openbiliclaw/web/setup/index.html")


def _setup_html() -> str:
    return SETUP_HTML.read_text(encoding="utf-8")


def test_setup_wizard_mirrors_progress_fraction_and_clamp() -> None:
    html = _setup_html()
    assert "STAGE_FRACTION_CAP" in html
    assert "Math.exp(-elapsed / eta)" in html
    assert "STAGE_FRACTION_FALLBACK" in html
    assert "eta_seconds" in html
    assert "progress?.note" in html
    assert "maxPct" in html
    assert "Math.max(st.maxPct, pct)" in html


def test_setup_wizard_surfaces_stall_and_expectation_copy() -> None:
    html = _setup_html()
    assert "INIT_STALL_THRESHOLD_SECONDS = 90" in html
    assert "stalenessView" in html
    assert "last_activity" in html
    assert "没有新进展" in html
    assert "可以继续等待，或取消后重试" in html
    assert "● 进行中" in html
    assert "整个过程通常需要 2–5 分钟" in html
    assert "本阶段通常约" in html
    assert "initStallHint" in html


def test_setup_wizard_keeps_first_pool_and_embedding_overrides() -> None:
    html = _setup_html()
    # First-pool wait pins 95%.
    assert '"95%"' in html
    assert "整理首轮内容池" in html
    # Embedding pull borrows the progress bar while idle.
    assert "pull.active && !status?.running" in html


def test_desktop_reattaches_init_poll_when_a_run_is_live_at_load() -> None:
    """A page opened/refreshed mid-init must start polling from hydrate.

    Hydrate fetches init-status once; without a boot re-attach the progress
    bar freezes on that single frame whenever SSE is unavailable, and — since
    the touch() heartbeat publishes no SSE event — a hung backend would never
    drive the stall detector either. The poll is the only observer of
    last_activity in that case.
    """
    app_js = _app_js()
    # The hydrate path must kick the scheduled poll when a run is live.
    assert "scheduleInitStatusRefresh(INIT_STATUS_POLL_MS)" in app_js
    assert "initStatus?.running" in app_js
    # And it must also cover the embedding-pull and first-pool-wait cases.
    assert "embeddingPullProgressView(initStatus).active" in app_js
    assert "initWaitingForFirstPool(initStatus)" in app_js
