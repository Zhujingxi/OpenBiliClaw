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
    shared = (ROOT / "src/openbiliclaw/web/shared/source-status.js").read_text(encoding="utf-8")

    # The state -> tone table moved into the module the extension side panel and
    # the setup wizard load too; the page keeps only its own scheduling states.
    assert 'unverified: { tone: "pending"' in shared
    assert "状态待验证" in shared
    assert "SOURCE_ENABLE_SELECT_IDS" in js
    assert "source-row-unsaved" in js
    assert "保存后生效" in js


def test_desktop_source_status_labels_distinguish_local_readiness() -> None:
    shared = (ROOT / "src/openbiliclaw/web/shared/source-status.js").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'ready: { tone: "ready", label: "凭据已就绪" }' in shared
    assert 'login_required: { tone: "warning", label: "需要登录" }' in shared
    assert 'error: { tone: "danger", label: "检查失败" }' in shared

    # This page must not re-declare the table it just started sharing — a second
    # copy is what let it drift from the side panel in the first place (D6).
    assert "const SOURCE_ACCESS_STATE" not in js
    assert "globalThis.OpenBiliClawSourceStatus" in js


def test_desktop_page_loads_the_shared_source_status_module() -> None:
    """The page is a classic script, so the shared module must precede it."""
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    shared_at = html.find('src="/shared/source-status.js"')
    app_at = html.find('src="/web/assets/js/app.js"')
    assert shared_at > 0, "desktop page does not load the shared module"
    assert shared_at < app_at, "the shared module must load before app.js"


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


def test_desktop_current_credentials_render_in_collapsed_panels() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'id="sourceCredentialList"' in html
    for source_key in (
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
    ):
        assert f'data-source-credential="{source_key}"' in html

    assert 'sourceCredentials: "/sources/credentials"' in js
    assert "reveal_keys=true" not in js
    # Status rows and credential rows now walk the same roster from the shared
    # module; they used to be two identical hand-kept arrays in this one file.
    assert "SOURCE_STATUS_KEYS = SourceStatus.SOURCE_KEYS" in js
    assert "renderSourceCredentials" in js
    assert "source-credential-value" in html
    assert "后端不会把原始 Cookie、令牌或 API Key 回传到页面" in html
    assert "source-credential-copy" not in html
    assert "已复制当前凭据" not in js
