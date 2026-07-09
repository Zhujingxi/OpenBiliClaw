"""Desktop web「加载更多推荐」体验契约（issue #81）。

骨架屏占位、短批次诚实文案、候选池回升自动重试 — 静态契约测试，
与 tests/test_desktop_web_pool_status.py 同风格。
"""

import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?:async )?function {re.escape(name)}\([^)]*\) \{{(?P<body>.*?)\n    \}}",
        source,
        flags=re.S,
    )
    assert match is not None, f"desktop {name} not found"
    return match.group("body")


def test_append_more_shows_and_clears_skeletons() -> None:
    app_js = _read(APP_JS)
    body = _function_body(app_js, "appendMore")
    assert "showAppendSkeletons()" in body
    assert "removeAppendSkeletons()" in body
    # 失败路径清掉骨架后 grid 不能是空白（骨架可能顶掉了 empty-state）。
    assert "if (!grid.childElementCount) renderVideos();" in body


def test_append_more_reports_short_batches_honestly() -> None:
    app_js = _read(APP_JS)
    body = _function_body(app_js, "appendMore")
    assert "freshItems.length < APPEND_BATCH_SIZE" in body
    assert "候选池暂时见底" in body
    assert "候选池暂时没有新内容" in body
    # 自动加载开关决定重试文案，不许对关掉自动加载的用户许诺“会自动加载”。
    assert 'state.autoLoadOnScroll ? "补上后会自动加载" : "稍后可再点一次"' in body


def test_initial_grid_ships_static_skeletons() -> None:
    index_html = _read(INDEX_HTML)
    grid = re.search(
        r'<div class="card-grid" id="videoGrid">(?P<body>.*?)<div id="loadMoreSentinel"',
        index_html,
        flags=re.S,
    )
    assert grid is not None, "videoGrid not found"
    skeletons = re.findall(r'class="video-card is-skeleton"', grid.group("body"))
    assert len(skeletons) >= 4, "initial grid must ship skeleton placeholders"
    assert 'aria-hidden="true"' in grid.group("body")


def test_css_defines_skeleton_shimmer_with_reduced_motion_guard() -> None:
    app_css = _read(APP_CSS)
    assert ".video-card.is-skeleton" in app_css
    assert ".skeleton-shimmer" in app_css
    assert "@keyframes skeleton-sweep" in app_css
    assert "prefers-reduced-motion" in app_css


def test_pool_status_update_rechecks_auto_load_when_parked_at_bottom() -> None:
    app_js = _read(APP_JS)
    body = _function_body(app_js, "maybeAutoLoadAfterPoolRefill")
    # 哨兵可能已经相交但当时被库存 / 渲染 guard 拦住；状态更新后要补一次几何重检。
    assert "scheduleAutoLoadCheck();" in body
    apply_body = _function_body(app_js, "applyRuntimeStatus")
    assert "maybeAutoLoadAfterPoolRefill();" in apply_body


def test_intersect_handler_tracks_sentinel_visibility() -> None:
    app_js = _read(APP_JS)
    body = _function_body(app_js, "handleAutoLoadIntersect")
    assert "sentinelInView = entries.some((entry) => entry.isIntersecting);" in body


def test_should_auto_load_ignores_skeleton_cards() -> None:
    app_js = _read(APP_JS)
    body = _function_body(app_js, "shouldAutoLoadMore")
    assert '.video-card:not(.is-skeleton)' in body
