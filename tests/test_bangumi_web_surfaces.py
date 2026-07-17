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


def test_desktop_guided_init_username_omit_and_warnings() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    # F3: only send an explicit username when deliberately edited or explicitly
    # cleared after a successful prefill — never erase a configured value with an
    # empty, never-prefilled field.
    assert "initBangumiUsernamePrefilled" in js
    assert '(bangumiUsername !== "" || state.initBangumiUsernamePrefilled)' in js
    assert 'selected.includes("bangumi") && sendBangumiUsername' in js
    # F4: consume and surface the 202 warnings instead of a bare "已开始".
    assert "started?.warnings" in js
    assert 'showToast(startWarnings.length ? startWarnings.join(" ")' in js


def test_setup_guided_init_username_omit_and_warnings() -> None:
    html = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    # F3: same omit-vs-clear contract on the packaged setup surface.
    assert "initBangumiUsernamePrefilled" in html
    assert (
        'initBangumiUsernameTouched && (bangumiUsername !== "" || initBangumiUsernamePrefilled)'
        in html
    )
    assert 'selected.includes("bangumi") && sendBangumiUsername' in html
    # F4: read the 202 body and render warnings via setInitReason (safe text).
    assert "startBody.warnings" in html
    assert 'setInitReason(startWarnings.join(" "), "warn")' in html


def test_setup_exposes_anonymous_bangumi_bootstrap() -> None:
    html = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert '{ key: "bangumi", label: "Bangumi" }' in html
    assert 'bangumiInput.id = "initBangumiUsername"' in html
    assert "Bangumi \u4f7f\u7528\u516c\u5f00 API，\u4e0d\u9700\u767b\u5f55" in html
    assert 'if (selected.includes("bangumi") && sendBangumiUsername)' in html
    assert "payload.source_options = { bangumi: { username: bangumiUsername } }" in html
    assert "no_profile_signal_sources" in html
    assert (
        'let initBangumiUsername = "", initBangumiUsernameTouched = false, '
        "initBangumiUsernamePrefilled = false;" in html
    )
    assert "bangumiInput.value = initBangumiUsername;" in html
