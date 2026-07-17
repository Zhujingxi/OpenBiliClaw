"""Static contracts for Bangumi settings and recommendation surfaces."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_round_trips_bangumi_settings() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in (
        "bangumiEnabled",
        "bangumiUsername",
        "bangumiModeSearch",
        "bangumiModeRanked",
        "bangumiModeLatest",
        "bangumiTypeAnime",
        "bangumiTypeBook",
        "bangumiTypeGame",
        "bangumiTypeMusic",
        "bangumiTypeReal",
        "bangumiDailySearchBudget",
        "bangumiDailyRankedBudget",
        "bangumiDailyLatestBudget",
        "bangumiRequestInterval",
        "bangumiMinInterval",
        "bangumiBootstrapLimit",
        "shareBangumi",
    ):
        assert f'id="{element_id}"' in html
        assert f'"{element_id}"' in js

    assert 'data-source-status="bangumi"' in html
    assert 'data-source-credential="bangumi"' in html
    assert "config.sources?.bangumi?.source_modes" in js
    assert "config.sources?.bangumi?.subject_types" in js
    assert 'bangumi: getIntInput("shareBangumi", 1)' in js
    assert 'if (shares.bangumi !== undefined) setInput("shareBangumi", shares.bangumi)' in js
    assert "initBangumiUsernameTouched" in js
    assert "formatCountCn(item.source_rank)" not in js
    assert "segments.push(`排名 #${sourceRank}`)" in js
    for field in ("rating_score", "rating_count", "source_rank"):
        assert js.count(f"{field}: Number(item?.{field}") >= 2


def test_mobile_recognizes_bangumi_identity_and_catalog_metrics() -> None:
    js = (ROOT / "src/openbiliclaw/web/js/view-models.js").read_text(encoding="utf-8")
    css = (ROOT / "src/openbiliclaw/web/css/app.css").read_text(encoding="utf-8")
    saved = (ROOT / "src/openbiliclaw/web/js/views/saved.js").read_text(encoding="utf-8")

    assert 'bangumi: "Bangumi"' in js
    assert 'bgm: "bangumi"' in js
    assert '["bgm.tv", "bangumi.tv"]' in js
    assert "https://bgm.tv/subject/" in js
    assert "formatCountCn(item.source_rank)" not in js
    assert "segments.push(`排名 #${sourceRank}`)" in js
    for field in ("rating_score", "rating_count", "source_rank"):
        assert js.count(f"{field}: Number(item?.{field}") >= 2
    assert '.card-source[data-source="bangumi"]' in css
    assert 'bangumi: "Bangumi"' in saved


def test_setup_exposes_anonymous_bangumi_bootstrap() -> None:
    html = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert '{ key: "bangumi", label: "Bangumi" }' in html
    assert 'bangumiInput.id = "initBangumiUsername"' in html
    assert "Bangumi \u4f7f\u7528\u516c\u5f00 API，\u4e0d\u9700\u767b\u5f55" in html
    assert 'if (selected.includes("bangumi"))' in html
    assert "payload.source_options = { bangumi: { username: bangumiUsername } }" in html
    assert "no_profile_signal_sources" in html
    assert 'let initBangumiUsername = "", initBangumiUsernameTouched = false;' in html
    assert "bangumiInput.value = initBangumiUsername;" in html
