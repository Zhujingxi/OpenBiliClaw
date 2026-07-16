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
    settings = _read("src/openbiliclaw/web/js/views/model-settings.js")

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
    assert "saved_sync" in settings
    assert "auto_sync_enabled" in settings
    assert "保存时自动同步到对应平台" in settings
    assert WARNING in settings
    assert "switch (item.source_platform" not in saved
    assert "unsupported_content_type" in saved
    assert "unsupported_adapter_missing" in saved
    assert "aria-disabled" in saved
    assert 'statusKey === "pending"' in saved and 'statusKey === "syncing"' in saved


def test_desktop_web_saved_sync_controls_and_consent_contract() -> None:
    html = _read("src/openbiliclaw/web/desktop/index.html")
    js = _read("src/openbiliclaw/web/desktop/assets/js/app.js")
    core = _read("src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js")

    assert 'id="savedAutoSync"' in html
    assert "保存时自动同步到对应平台" in html
    assert 'id="watchLaterSyncAll"' in html
    assert 'id="favoritesSyncAll"' in html
    assert 'aria-live="polite"' in html
    assert WARNING in js
    assert "`/saved/${listKind}`" in core
    assert html.index('src="/web/assets/js/saved-sync-core.js"') < html.index(
        'src="/web/assets/js/app.js"'
    )
    assert "createStrictSavedApi(requestJsonStrict)" in js
    assert "同步未同步内容" in js
    assert all(label in js for label in ("待同步", "同步中", "已同步", "需要登录", "同步失败"))
    assert "extension_required" in js
    assert "switch (item.source_platform" not in js
    assert "unsupported_content_type" in core
    assert "unsupported_adapter_missing" in core
    assert "aria-disabled" in js
    assert "error_code" in core


def test_extension_side_panel_and_config_contract() -> None:
    html = _read("extension/popup/popup.html")
    js = _read("extension/popup/popup.js")
    runtime = _read("extension/popup/popup-saved-sync.js")

    assert 'id="cfgSavedAutoSync"' in html
    assert "保存时自动同步到对应平台" in html
    assert 'id="watchLaterSyncAll"' in html
    assert 'id="favoritesSyncAll"' in html
    assert WARNING in js
    assert "Promise.allSettled" in js
    assert "本地保存" in js and "同步中" in js and "失败" in js
    assert 'role = "alert"' in js or 'role="alert"' in html
    assert "switch (item.source_platform" not in js
    assert "unsupported_content_type" in runtime
    assert "unsupported_adapter_missing" in runtime
    assert "aria-disabled" in js


def test_all_graphical_saved_surfaces_keep_manual_controls_and_default_auto_sync_off() -> None:
    config_example = _read("config.example.toml")
    popup_html = _read("extension/popup/popup.html")
    desktop_html = _read("src/openbiliclaw/web/desktop/index.html")
    mobile_saved = _read("src/openbiliclaw/web/js/views/saved.js")

    assert "[saved_sync]" in config_example
    assert "auto_sync_enabled = false" in config_example
    for markup in (popup_html, desktop_html):
        assert 'id="watchLaterSyncAll"' in markup
        assert 'id="favoritesSyncAll"' in markup
    assert "同步未同步内容" in mobile_saved

    for source in (
        _read("extension/popup/popup.js"),
        _read("src/openbiliclaw/web/desktop/assets/js/app.js"),
        _read("src/openbiliclaw/web/js/views/model-settings.js"),
    ):
        assert "auto_sync_enabled === true" in source


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
        assert ".saved-card-sync:disabled" in css or ".small-btn:disabled" in css


def test_saved_sync_review_repairs_are_wired_to_all_surfaces() -> None:
    popup = _read("extension/popup/popup.js")
    popup_runtime = _read("extension/popup/popup-saved-sync.js")
    mobile_settings = _read("src/openbiliclaw/web/js/views/model-settings.js")
    mobile_css = _read("src/openbiliclaw/web/css/app.css")
    mobile_saved = _read("src/openbiliclaw/web/js/views/saved.js")
    mobile_recommend = _read("src/openbiliclaw/web/js/views/recommend.js")
    desktop_html = _read("src/openbiliclaw/web/desktop/index.html")
    desktop = _read("src/openbiliclaw/web/desktop/assets/js/app.js")
    desktop_core = _read("src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js")

    assert "partitionSavedQueueResults" in popup
    assert "createSavedSyncTaskTracker" in popup_runtime
    assert "createRetainedSavedListState" in popup_runtime
    assert "仍在后台同步" in popup
    assert "for (let attempt = 0; task.task_id" not in popup

    assert "createDurableTaskTracker" in mobile_saved
    assert "createRetainedSavedListState" in mobile_saved
    assert "createSavedMutationRegistry" in mobile_recommend
    assert 'setAttribute("role", "dialog")' in mobile_settings
    assert 'setAttribute("aria-modal", "true")' in mobile_settings
    assert "createDialogFocusController" in mobile_settings
    assert "mobile-settings-retry" in mobile_settings
    assert ".mobile-settings-retry[hidden] { display: none; }" in mobile_css
    assert "configLoaded" in mobile_settings

    assert "saved-sync-core.js" in desktop_html
    assert "createStrictSavedApi(requestJsonStrict)" in desktop
    assert "createDurableTaskTracker" in desktop
    assert "createRetainedSavedListState" in desktop
    assert "desktopSavedMutations" in desktop
    assert "_delightStatusCache.set(savedItem.item_key" in desktop
    assert "仍在后台同步" in desktop
    assert "for (let attempt = 0; task.task_id" not in desktop
    assert "timeoutMs" in desktop_core


def test_saved_sync_second_review_timeout_recovery_and_focus_contract() -> None:
    popup_api = _read("extension/popup/popup-api.js")
    popup = _read("extension/popup/popup.js")
    popup_html = _read("extension/popup/popup.html")
    mobile_api = _read("src/openbiliclaw/web/js/api.js")
    mobile_saved = _read("src/openbiliclaw/web/js/views/saved.js")
    desktop = _read("src/openbiliclaw/web/desktop/assets/js/app.js")
    desktop_html = _read("src/openbiliclaw/web/desktop/index.html")

    for api in (popup_api, mobile_api):
        for helper in (
            "saveItem",
            "removeSavedItem",
            "fetchSavedItems",
            "savedItemStatus",
            "syncSavedItems",
            "pollSavedSyncTask",
        ):
            definition = api.split(f"function {helper}", 1)[1].split("\n}", 1)[0]
            assert "timeoutMs" in definition
        assert "function fetchConfig(timeoutMs" in api
        assert "function updateConfig(data, timeoutMs" in api

    for source in (popup, mobile_saved, desktop):
        assert any(
            marker in source
            for marker in (
                ".coordinator.recover(",
                "taskCoordinator.recover(",
                "coordinator.recover(",
            )
        )
        assert ".coordinator.owns(" in source or "taskCoordinator.owns(" in source
        assert 'addEventListener("pagehide"' in source
        assert "同步状态查询超时" in source

    for markup in (popup_html, mobile_saved, desktop_html):
        assert "data-saved-list-action" in markup
        assert "data-saved-heading" in markup
