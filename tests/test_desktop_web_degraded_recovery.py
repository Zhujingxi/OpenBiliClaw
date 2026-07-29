from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")


def test_desktop_web_opens_actionable_model_settings_in_degraded_mode() -> None:
    assert "function presentDegradedConfigRecovery(snapshot)" in APP_JS
    assert "snapshot?.degraded !== true" in APP_JS
    assert "当前没有可用的模型 Provider" in APP_JS
    assert 'openSettingsPage("models")' in APP_JS
    assert "presentDegradedConfigRecovery(configSnapshot)" in APP_JS
    assert 'event.type === "degraded"' in APP_JS
    assert "const pingSnapshot = await requestJson(ENDPOINTS.ping)" in APP_JS
    assert "if (pingSnapshot?.degraded === true)" in APP_JS
    assert "state.degraded = config.degraded === true" in APP_JS
    assert "保存成功后后端会原地恢复" in APP_JS
    assert "模型设置修复后重启后端" not in APP_JS


def test_desktop_web_surfaces_top_level_degraded_issues() -> None:
    assert "details.issues || details.config?.issues || details.detail?.config?.issues" in APP_JS


def test_desktop_web_routes_recommendation_degrade_to_settings_recovery() -> None:
    # A mid-session degrade blocks /api/recommendations with a 503
    # {status:"degraded"} envelope. requestJsonStrict rethrows it as
    # error.details, and the recommendation-failure path must recognise it and
    # open the model-settings recovery instead of the generic retry UI.
    assert "function markDesktopRecommendationFailedAndRecover(error)" in APP_JS
    assert 'details.status === "degraded"' in APP_JS
    # The failure path routes into the existing degraded recovery, not the
    # generic scheduleDesktopRecommendationRecovery, when degraded.
    assert "presentDegradedConfigRecovery({" in APP_JS
    assert 'degraded_reason: details.reason || "",' in APP_JS
    # The rejection handler forwards the thrown error so details survive.
    assert "(error) => markDesktopRecommendationFailedAndRecover(error)" in APP_JS
    # requestJsonStrict preserves the parsed body on the thrown error.
    assert "error.details = details;" in APP_JS


def test_desktop_web_skips_blocked_source_reads_while_degraded() -> None:
    guarded_source_reads = (
        "if (!state.degraded) {\n"
        "        void renderSourcesStatus();\n"
        "        void renderSourceCredentials();\n"
        "      }"
    )
    assert APP_JS.count(guarded_source_reads) == 2
