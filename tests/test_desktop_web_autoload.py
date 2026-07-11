import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")


def _function_body(js: str, name: str) -> str:
    match = re.search(rf"function {name}\([^)]*\) \{{(?P<body>.*?)\n    \}}", js, flags=re.S)
    assert match is not None, f"{name} function not found"
    return match.group("body")


def test_index_declares_autoload_sentinel_and_frontend_toggle() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert '<div id="loadMoreSentinel" aria-hidden="true"></div>' in html
    assert html.index('id="loadMoreSentinel"') < html.index('class="load-row"')
    assert 'id="autoLoadOnScrollSetting" type="checkbox" checked' in html
    assert "滚动到底自动加载推荐" in html


def test_autoload_setting_uses_frontend_storage_pattern() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    restore_frontend = _function_body(js, "restoreFrontendSettings")
    persist_frontend = _function_body(js, "persistFrontendSettings")

    assert 'const AUTO_LOAD_ON_SCROLL_KEY = "openbiliclaw.webui.autoLoadOnScroll";' in js
    assert 'state.autoLoadOnScroll = storageGet(AUTO_LOAD_ON_SCROLL_KEY) !== "0";' in js
    assert "renderAutoLoadOnScrollToggle();" in restore_frontend
    assert "syncAutoLoadObserver();" in restore_frontend
    assert (
        'storageSet(AUTO_LOAD_ON_SCROLL_KEY, state.autoLoadOnScroll ? "1" : "0");'
        in persist_frontend
    )


def test_autoload_observer_is_wired_to_load_more_sentinel() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    sync_observer = _function_body(js, "syncAutoLoadObserver")

    assert "const AUTO_LOAD_ROOT_MARGIN_PX = 300;" in js
    assert "autoLoadObserver.disconnect();" in sync_observer
    assert '$("#loadMoreSentinel")' in sync_observer
    assert "rootMargin: `${AUTO_LOAD_ROOT_MARGIN_PX}px`" in sync_observer
    assert "autoLoadObserver.observe(sentinel);" in sync_observer


def test_autoload_sentinel_has_stable_hit_area() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert "#loadMoreSentinel" in css
    assert "height: 1px;" in css
    assert "max-width: var(--recommendation-grid-max);" in css


def test_autoload_guards_cooldown_pool_page_grid_and_button_state() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    cooldown = re.search(r"const AUTO_LOAD_COOLDOWN_MS = (?P<value>\d+);", js)
    assert cooldown is not None, "autoload cooldown constant not found"
    assert int(cooldown.group("value")) >= 8000

    guard = _function_body(js, "shouldAutoLoadMore")
    assert "state.autoLoadOnScroll" in guard
    assert "appendMoreInFlight" in guard
    assert "now - lastAutoLoadAt < AUTO_LOAD_COOLDOWN_MS" in guard
    assert "state.runtimeStatus?.pool_available_count > 0" in guard
    assert '$("#homePage")' in guard
    assert "homePage.hidden" in guard
    # 骨架占位卡不算真实内容，不能触发自动加载（issue #81 skeleton cards）。
    assert 'grid.querySelector(".video-card:not(.is-skeleton)")' in guard
    assert "loadMore.hidden" in guard


def test_autoload_rechecks_after_scroll_render_and_runtime_status() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    render_all = _function_body(js, "renderAll")
    pool_refill = _function_body(js, "maybeAutoLoadAfterPoolRefill")

    assert 'window.addEventListener("scroll", scheduleAutoLoadCheck, { passive: true });' in js
    assert 'window.addEventListener("resize", scheduleAutoLoadCheck);' in js
    assert "scheduleAutoLoadCheck();" in render_all
    assert "scheduleAutoLoadCheck();" in pool_refill


def test_autoload_geometry_fallback_refreshes_sentinel_visibility() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    in_view = _function_body(js, "isAutoLoadSentinelInView")
    refresh = _function_body(js, "refreshAutoLoadSentinelVisibility")
    schedule = _function_body(js, "scheduleAutoLoadCheck")

    assert '$("#loadMoreSentinel")' in in_view
    assert "sentinel.getBoundingClientRect()" in in_view
    assert "window.innerHeight" in in_view
    assert "AUTO_LOAD_ROOT_MARGIN_PX" in in_view
    assert "sentinelInView = isAutoLoadSentinelInView();" in refresh
    assert "requestAnimationFrame" in schedule
    assert "refreshAutoLoadSentinelVisibility()" in schedule


def test_autoload_uses_single_flight_append_and_keeps_manual_button_bound() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    append_more = _function_body(js, "appendMore")
    auto_load = _function_body(js, "autoLoadMoreIfNeeded")

    assert "if (appendMoreInFlight) return;" in append_more
    assert "appendMoreInFlight = true;" in append_more
    assert "appendMoreInFlight = false;" in append_more
    assert 'button.textContent = "正在自动加载…";' in auto_load
    assert "button.disabled = true;" in auto_load
    assert "button.disabled = false;" in auto_load
    assert 'safeBind("#loadMoreBtn", "click", appendMore);' in js
