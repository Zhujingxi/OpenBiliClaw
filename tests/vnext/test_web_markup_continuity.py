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
    _ordered(
        html,
        'id="biliStatus"',
        'id="initSources"',
        'id="initChecklist"',
        'id="initProgress"',
        'id="initReason"',
        'id="initEscape"',
        'id="doneLlm"',
        'id="doneBili"',
    )
    assert "@media (max-width: 680px)" in html
    assert 'id="aliasList"' in html
    assert 'id="adminLink"' in html


def test_desktop_keeps_topbar_drawer_layout_and_retained_page_order() -> None:
    html = _read("desktop/index.html")
    css = _read("desktop/assets/css/app.css")
    controller = _read("desktop/assets/js/app.js")

    _ordered(html, 'class="topbar"', 'class="app-body"', 'id="sideDrawer"', 'id="home"')
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
    for css_contract in (
        ".app-shell",
        ".app-body",
        ".side-drawer",
        ".main-col",
        ".saved-page",
        ".profile-page",
        ".chat-page",
        "@media (max-width: 820px)",
    ):
        assert css_contract in css
    assert 'querySelectorAll("[data-page]")' in controller
    assert '$("#sideDrawerBtn").addEventListener("click"' in controller
    assert '$("#sideDrawer").classList.toggle("is-open")' in controller


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


def test_setup_stage_controls_preserve_forward_and_back_navigation() -> None:
    html = _read("setup/index.html")

    for control in ("nextAi", "back1", "next1", "backInit", "startInit", "back2"):
        assert f'id="{control}"' in html
        assert f'getElementById("{control}").addEventListener' in html


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
