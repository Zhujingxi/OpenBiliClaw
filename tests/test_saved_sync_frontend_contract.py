from pathlib import Path

ROOT = Path(__file__).parents[1]
WARNING = (
    "开启后，在 OpenBiliClaw 点击收藏或稍后再看会修改对应平台账号中的"
    "收藏、书签、Saved、播放列表或稍后观看。"
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mobile_web_saved_sync_api_and_view_contract() -> None:
    api = _read("src/openbiliclaw/web/js/api.js")
    saved = _read("src/openbiliclaw/web/js/views/saved.js")
    app = _read("src/openbiliclaw/web/js/app.js")

    for helper in (
        "saveItem",
        "removeSavedItem",
        "fetchSavedItems",
        "syncSavedItems",
        "pollSavedSyncTask",
    ):
        assert f"function {helper}" in api
    assert "/saved/${listKind}" in api
    assert "同步未同步内容" in saved
    assert all(label in saved for label in ("待同步", "同步中", "已同步", "需要登录", "同步失败"))
    assert "重试同步" in saved
    assert "extension_required" in saved
    assert "aria-live" in saved
    assert "saved_sync" in app
    assert "auto_sync_enabled" in app
    assert "保存时自动同步到对应平台" in app
    assert WARNING in app
    assert "switch (item.source_platform" not in saved


def test_desktop_web_saved_sync_controls_and_consent_contract() -> None:
    html = _read("src/openbiliclaw/web/desktop/index.html")
    js = _read("src/openbiliclaw/web/desktop/assets/js/app.js")

    assert 'id="savedAutoSync"' in html
    assert "保存时自动同步到对应平台" in html
    assert 'id="watchLaterSyncAll"' in html
    assert 'id="favoritesSyncAll"' in html
    assert 'aria-live="polite"' in html
    assert WARNING in js
    assert "`/saved/${listKind}`" in js
    assert "同步未同步内容" in js
    assert all(label in js for label in ("待同步", "同步中", "已同步", "需要登录", "同步失败"))
    assert "extension_required" in js
    assert "switch (item.source_platform" not in js


def test_extension_side_panel_and_config_contract() -> None:
    html = _read("extension/popup/popup.html")
    js = _read("extension/popup/popup.js")

    assert 'id="cfgSavedAutoSync"' in html
    assert "保存时自动同步到对应平台" in html
    assert 'id="watchLaterSyncAll"' in html
    assert 'id="favoritesSyncAll"' in html
    assert WARNING in js
    assert "Promise.allSettled" in js
    assert "本地保存" in js and "同步中" in js and "失败" in js
    assert 'role = "alert"' in js or 'role="alert"' in html
    assert "switch (item.source_platform" not in js


def test_saved_sync_css_preserves_focus_motion_and_mobile_touch_safety() -> None:
    css_sources = (
        _read("src/openbiliclaw/web/css/app.css"),
        _read("src/openbiliclaw/web/desktop/assets/css/app.css"),
        _read("extension/popup/popup.html"),
    )
    for css in css_sources:
        assert ":focus-visible" in css
        assert "prefers-reduced-motion" in css
        assert "44px" in css
