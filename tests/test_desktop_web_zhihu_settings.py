"""Static regressions for desktop Zhihu source settings."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_round_trips_zhihu_source_modes() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in (
        "zhihuModeSearch",
        "zhihuModeHot",
        "zhihuModeFeed",
        "zhihuModeCreator",
        "zhihuModeRelated",
    ):
        assert f'id="{element_id}"' in html
        assert f'"{element_id}"' in js

    assert "setZhihuSourceModes(config.sources?.zhihu?.source_modes)" in js
    assert "source_modes: collectZhihuSourceModes()" in js


def test_desktop_source_status_rows_separate_source_and_access_state() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    assert 'id="sourceStatusList"' in html
    for source_key in (
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
    ):
        assert f'data-source-status="{source_key}"' in html

    assert 'class="source-source-badge"' in html
    assert 'class="source-access-badge"' in html
    assert "来源：" in html
    assert "接入：" in html


def test_desktop_source_status_js_has_pending_and_unsaved_states() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'unverified: { tone: "pending"' in js
    assert "状态待验证" in js
    assert "SOURCE_ENABLE_SELECT_IDS" in js
    assert "source-row-unsaved" in js
    assert "保存后生效" in js


def test_desktop_source_status_labels_distinguish_local_readiness() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'ready: { tone: "ready", label: "凭据已就绪" }' in js
    assert 'login_required: { tone: "warning", label: "需要登录" }' in js
    assert 'error: { tone: "danger", label: "检查失败" }' in js


def test_desktop_cookie_fields_are_override_only() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in ("biliCookie", "douyinCookie", "twitterCookie"):
        assert f'id="{element_id}"' in html
        assert f'setCookieOverrideInput("{element_id}"' in js

    assert 'setInput("biliCookie", config.bilibili?.cookie)' not in js
    assert 'setInput("douyinCookie", config.sources?.douyin?.cookie)' not in js
    assert 'setInput("twitterCookie", config.sources?.twitter?.cookie)' not in js
    assert "留空保存不会覆盖" in js
    assert "需要更换时粘贴新的 Cookie" in js


def test_desktop_credentials_are_write_only_without_raw_secret_reads() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    # The collapsed read-only credential panels and their copy buttons are
    # gone; config reads are masked and no credential endpoint is called.
    assert 'id="sourceCredentialList"' not in html
    assert "source-credential" not in html
    assert "reveal_keys" not in js
    assert "sources/credentials" not in js
    assert "CURRENT_CREDENTIAL_KEYS" not in js
    assert "renderSourceCredentials" not in js
