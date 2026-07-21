"""Static regressions for mobile recommend first-load resilience."""

from pathlib import Path


def test_mobile_recommend_api_requests_have_timeouts() -> None:
    api_js = Path("src/openbiliclaw/web/js/api.ts").read_text()

    assert "DEFAULT_READ_TIMEOUT_MS" in api_js
    assert 'requestJson("/recommendations", { timeoutMs: DEFAULT_READ_TIMEOUT_MS })' in api_js
    assert 'requestJson("/runtime-status", { timeoutMs: QUICK_READ_TIMEOUT_MS })' in api_js
    assert "timeoutMs: DEFAULT_READ_TIMEOUT_MS" in api_js


def test_mobile_recommend_initial_load_does_not_wait_forever_on_recommendations() -> None:
    recommend_js = Path("src/openbiliclaw/web/js/views/recommend.ts").read_text()

    assert "await fetchRecommendations().catch(() => [])" not in recommend_js
    assert "hydrateRecommendSideChannels()" in recommend_js
    assert "const [recs, status, delights, activity] = await Promise.all([" not in recommend_js
    assert "loading = false;" in recommend_js


def test_mobile_recommend_failure_is_not_coerced_to_empty_success() -> None:
    recommend_js = Path("src/openbiliclaw/web/js/views/recommend.ts").read_text()

    assert 'recommendationLoadState = "failed"' in recommend_js
    assert "scheduleRecommendationRecovery" in recommend_js
    assert "state.recommendations.length > 0" in recommend_js
    assert 'recommendationLoadState = "empty-success"' in recommend_js


def test_mobile_recovery_is_bounded_and_reconnectable() -> None:
    recommend_js = Path("src/openbiliclaw/web/js/views/recommend.ts").read_text()
    app_js = Path("src/openbiliclaw/web/js/app.ts").read_text()

    assert "[1000, 2000, 4000, 8000]" in recommend_js
    assert "export function onStreamConnect" in recommend_js
    assert "recStreamConnect()" in app_js
    assert 'runtimeStatusLoadState = "failed"' in recommend_js
    assert "scheduleRuntimeStatusRecovery" in recommend_js


def test_mobile_manual_reshuffle_preserves_cards_on_empty_and_clears_failed_recovery() -> None:
    recommend_js = Path("src/openbiliclaw/web/js/views/recommend.ts").read_text()

    assert "reconcileRecommendationReplacement(" in recommend_js
    assert 'recommendationActionMessage = "这次暂时没换出新内容，已保留当前推荐。"' in recommend_js
    assert 'clearRecommendationRecovery("ready")' in recommend_js
    assert "applyRecommendationSnapshot(replacement.items, { replace: true })" in recommend_js


def test_mobile_late_runtime_timeout_does_not_override_stream_recovery() -> None:
    recommend_js = Path("src/openbiliclaw/web/js/views/recommend.ts").read_text()

    assert "let runtimeStatusGeneration = 0;" in recommend_js
    assert "const requestGeneration = runtimeStatusGeneration;" in recommend_js
    assert "if (requestGeneration !== runtimeStatusGeneration) return;" in recommend_js
    assert "runtimeStatusGeneration += 1;" in recommend_js
    assert (
        'if (typeof poolEvent?.pool_available_count === "number") {\n'
        "      runtimeStatusGeneration += 1;"
    ) in recommend_js


def test_mobile_healthy_stream_reconnect_does_not_rebuild_cards() -> None:
    recommend_js = Path("src/openbiliclaw/web/js/views/recommend.ts").read_text()

    assert "if (recommendationRestarted) render();" in recommend_js
    assert "else if (runtimeRestarted) rerenderRuntimeDependentChrome();" in recommend_js


def test_mobile_badge_load_does_not_fetch_delights_eagerly() -> None:
    chat_js = Path("src/openbiliclaw/web/js/views/chat.ts").read_text()

    assert "includeDelights = false" in chat_js
    assert "includeDelights ? fetchDelightBatch().catch(() => [])" in chat_js


def test_mobile_delight_batch_default_uses_backend_configured_limit() -> None:
    api_js = Path("src/openbiliclaw/web/js/api.ts").read_text()
    recommend_js = Path("src/openbiliclaw/web/js/views/recommend.ts").read_text()

    assert "export async function fetchDelightBatch(limit: number | null = null)" in api_js
    assert 'requestJson(`/delight/pending-batch${qs ? `?${qs}` : ""}`' in api_js
    assert "fetchDelightBatch()" in recommend_js
