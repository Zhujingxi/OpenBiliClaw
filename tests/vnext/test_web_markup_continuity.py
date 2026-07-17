"""Characterization contracts for the retained Web UI shape during vNext wiring."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src/openbiliclaw/web"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


def _ordered(source: str, *needles: str) -> None:
    positions = [source.index(needle) for needle in needles]
    assert positions == sorted(positions), needles


def test_setup_keeps_the_four_stage_wizard_hierarchy_and_responsive_contract() -> None:
    html = _read("setup/index.html")

    assert html.count('class="dot') == 4
    assert re.findall(r'data-panel="(\d)"', html) == ["0", "1", "2", "3"]
    _ordered(html, 'class="card"', 'class="head"', 'class="body"')
    assert '<div class="brand"><span class="mark">B</span><span>OpenBiliClaw</span></div>' in html
    assert '<ol class="ext-steps">' in html
    assert 'id="initSources" class="init-sources"' in html
    assert 'id="initChecklist" class="init-checks"' in html
    assert (
        '<div class="progress-track"><div class="progress-fill" '
        'id="initProgressBar"></div></div>' in html
    )
    assert '<ul class="check-list">' in html
    assert html.count('class="tick"') >= 3
    _ordered(
        html,
        'id="aliasSetupLayout"',
        'id="aliasList"',
        'id="adminLink"',
        'id="biliStatus"',
        'id="initSources"',
        'id="initChecklist"',
        'id="initProgress"',
        'id="initReason"',
        'id="initEscape"',
        'id="doneLlm"',
        'id="doneBili"',
    )
    for style_contract in (
        ".brand .mark",
        ".row-actions",
        ".ext-steps",
        ".init-sources",
        ".init-source-row",
        ".init-checks",
        ".progress-track",
        ".progress-fill",
        ".check-list",
        ".alias-setup-layout",
        "@keyframes fade",
        "@keyframes rot",
        "@media (max-width: 680px)",
    ):
        assert style_contract in html


def test_desktop_keeps_topbar_drawer_layout_and_retained_page_order() -> None:
    html = _read("desktop/index.html")
    css = _read("desktop/assets/css/app.css")
    controller = _read("desktop/assets/js/app.js")

    _ordered(
        html,
        'class="topbar"',
        'class="fatal-banner"',
        'class="app-body"',
        'id="sideDrawer"',
        'id="home"',
        'id="mobileMenu"',
        'class="toast-container"',
    )
    for topbar_contract in (
        'class="top-left"',
        'class="brand-mark"',
        'class="brand-copy"',
        'class="search" id="searchForm"',
        'class="top-actions"',
        'class="backend-status-pill"',
        'id="themeToggleBtn"',
        'id="mobileMenuBtn"',
    ):
        assert topbar_contract in html
    _ordered(
        html,
        'class="side-drawer-scrim"',
        'class="side-drawer-panel"',
        'class="side-drawer-nav"',
        'class="side-section side-runtime-panel"',
    )
    _ordered(
        html,
        'id="homeBtn"',
        'id="watchLaterBtn"',
        'id="favoritesBtn"',
        'id="profileBtn"',
        'id="chatBtn"',
        'id="jobsBtn"',
        'id="settingsBtn"',
    )
    assert html.count('class="nav-glyph"') == 7
    _ordered(
        html,
        'id="homePage"',
        'id="watchLaterPage"',
        'id="favoritesPage"',
        'id="profilePage"',
        'id="chatPage"',
        'id="jobsPage"',
        'id="settingsPage"',
    )
    for page_contract in (
        'data-od-id="recommendation-home"',
        'data-od-id="recommendations"',
        'class="filter-row" id="filterRow"',
        'class="card-grid" id="videoGrid"',
        'class="content-page-head"',
        'class="profile-page-head"',
        'class="profile-list profile-page-list"',
        'class="chat-log" id="chatLog"',
        'class="settings-tabs"',
        'data-settings-panel="sources"',
        'data-settings-panel="feed"',
        'data-settings-panel="profile"',
        'data-settings-panel="tasks"',
        'data-settings-panel="scheduler"',
        'data-settings-panel="runtime"',
    ):
        assert page_contract in html
    for mobile_contract in (
        'class="mobile-menu-panel"',
        'id="mobileMenuClose"',
        'class="mobile-search" id="mobileSearchForm"',
        'class="mobile-menu-list"',
        'data-mobile-page="feed"',
        'data-mobile-page="profile"',
        'data-mobile-page="chat"',
        'data-mobile-page="jobs"',
        'data-mobile-page="settings"',
        'class="mobile-summary-card"',
    ):
        assert mobile_contract in html
    for css_contract in (
        ".app-shell",
        ".app-body",
        ".side-drawer",
        ".main-col",
        ".saved-page",
        ".profile-page",
        ".chat-page",
        ".top-left",
        ".brand-copy",
        ".top-actions",
        ".fatal-banner",
        ".side-section",
        ".content-page-head",
        ".settings-tabs",
        ".mobile-menu",
        ".mobile-menu-panel",
        ".mobile-menu-list",
        ".mobile-summary-card",
        "@media (max-width: 820px)",
    ):
        assert css_contract in css
    assert 'querySelectorAll("[data-page]")' in controller
    assert '$("#sideDrawerBtn").addEventListener("click"' in controller
    assert '$("#sideDrawer").classList.toggle("is-open")' in controller
    assert '$("#mobileMenuBtn").addEventListener("click"' in controller
    assert 'document.querySelectorAll("[data-mobile-page]")' in controller
    assert 'document.querySelectorAll("[data-settings-tab]")' in controller
    assert '<a class="top-mobile-btn" href="/m/">手机版</a>' in html


def test_retained_cards_use_safe_links_and_local_library_operations() -> None:
    desktop = _read("desktop/assets/js/app.js")
    mobile = _read("js/app.js")

    for controller in (desktop, mobile):
        assert 'target="_blank" rel="noreferrer" data-open' in controller
        assert "recordInteraction(" in controller
        assert 'request("v1_library_add"' in controller
        assert 'request("v1_library_remove"' in controller
        assert "saved-sync" not in controller
        assert "native-save" not in controller


def test_mobile_keeps_stable_views_tab_order_and_keyboard_navigation() -> None:
    html = _read("index.html")
    app = _read("js/app.js")

    _ordered(html, 'id="status-bar"', 'id="app"', 'id="tab-bar"')
    _ordered(
        app,
        'id: "recommend"',
        'id: "watchLater"',
        'id: "favorites"',
        'id: "profile"',
        'id: "chat"',
    )
    assert "`view-${tab.id}`" in app
    assert 'setAttribute("role", "tablist")' in app
    assert 'e.key === "ArrowRight"' in app
    assert 'e.key === "ArrowLeft"' in app
    for retained_class in (
        'class="recommend-header-card"',
        'class="recommend-header-top"',
        'class="recommend-kicker"',
        'class="recommend-title"',
        'class="btn btn-outline recommend-refresh-btn"',
        'el.className = "card rec-card"',
        'class="card-cover-frame rec-thumb"',
        'class="card-body rec-body"',
        'class="card-title"',
        'class="card-meta rec-meta"',
        'class="saved-view"',
        'class="saved-head"',
        'class="saved-body"',
        'class="profile-section"',
        'class="profile-section-title"',
        'class="chat-shell"',
        'class="chat-messages"',
        'class="chat-input-row"',
    ):
        assert retained_class in app


def test_desktop_and_mobile_do_not_restore_dropped_feature_controls() -> None:
    desktop = _read("desktop/index.html")
    mobile = _read("js/app.js")

    for dropped in (
        "delightBanner",
        "messagesDrawer",
        "modelRouteTabs",
        "savedAutoSync",
        "speculationInterval",
    ):
        assert dropped not in desktop
    for dropped in ("delight-tray", "messages-overlay", "mbti-type", "awareness-item"):
        assert dropped not in mobile


def test_setup_stage_controls_preserve_forward_and_back_navigation() -> None:
    html = _read("setup/index.html")

    for control in ("nextAi", "back1", "next1", "backInit", "startInit", "back2"):
        assert f'id="{control}"' in html
        assert f'getElementById("{control}").addEventListener' in html


def test_setup_hidden_admin_link_and_terminal_errors_restore_actionability() -> None:
    html = _read("setup/index.html")

    assert "[hidden]" in html
    assert "display: none !important" in html
    error_start = html.index('event === "error"')
    error_branch = html[error_start : html.index(",\n          );", error_start)]
    assert 'getElementById("startInit").disabled = false' in error_branch
    assert 'getElementById("initProgressLabel").textContent = "初始化失败"' in error_branch


def test_terminal_sse_states_and_required_alias_gate_are_explicit() -> None:
    setup = _read("setup/index.html")
    desktop = _read("desktop/assets/js/app.js")
    mobile = _read("js/app.js")

    assert "requiredAliases.every" in setup
    assert "aliasesReady" in setup
    for status in ("failed", "cancelled"):
        assert status in setup
        assert status in desktop
        assert status in mobile
    assert 'event === "done"' in setup
    assert 'event === "done"' in desktop
    assert 'event === "done"' in mobile


def test_shipped_web_graph_contains_no_legacy_runtime_clients() -> None:
    legacy = (
        "js/api.js",
        "js/stream.js",
        "js/state.js",
        "js/view-models.js",
        "js/mobile-model-settings-controller.js",
        "js/saved-sync-runtime.js",
        "js/views",
        "shared/model-config-state.js",
        "desktop/assets/js/model-settings.js",
        "desktop/assets/js/pending-actions.js",
        "desktop/assets/js/saved-sync-core.js",
    )
    for relative in legacy:
        assert not (WEB / relative).exists(), relative
