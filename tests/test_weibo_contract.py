"""Executable exclusions and field mappings for the frozen Weibo contract."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from openbiliclaw.api.source_auth.forms import build_credential_form
from openbiliclaw.api.source_auth.write import CREDENTIAL_SPECS
from openbiliclaw.config import Config
from openbiliclaw.saved_sync.identity import is_native_save_local_only
from openbiliclaw.sources.weibo import weibo_post_to_content

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = tomllib.loads(
    (ROOT / "docs/platform-source-contract.weibo.toml").read_text(encoding="utf-8")
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _extension_runtime_source() -> str:
    files = sorted((ROOT / "extension/src").rglob("*.js")) + sorted(
        (ROOT / "extension/src").rglob("*.ts")
    )
    return "\n".join(path.read_text(encoding="utf-8") for path in files)


def _mapped_post() -> object:
    return weibo_post_to_content(
        {
            "id": "5023456789012345",
            "text": "公开微博正文",
            "attitudes_count": 17,
            "comments_count": 9,
            "reposts_count": 4,
        }
    )


def test_weibo_profile_signals_are_discovery_only() -> None:
    assert CONTRACT["integration_level"] == "discovery-only"
    assert CONTRACT["profile"]["signals"] is False
    assert "weibo-profile" not in _read("src/openbiliclaw/runtime/weibo_producer.py")


def test_weibo_profile_incremental_is_excluded() -> None:
    assert CONTRACT["profile"]["incremental"] is False
    refresh = _read("src/openbiliclaw/runtime/refresh.py")
    assert "_loop_weibo_producer" in refresh
    assert "weibo_incremental" not in refresh


def test_weibo_profile_refresh_mode_is_none() -> None:
    assert CONTRACT["profile"]["refresh_mode"] == "none"
    assert "weibo_profile_refresh" not in _read("src/openbiliclaw/runtime/refresh.py")


def test_weibo_extension_task_is_excluded() -> None:
    assert CONTRACT["extension"]["task"] == "none"
    assert "weibo" not in _extension_runtime_source().casefold()


def test_weibo_extension_task_marker_is_excluded() -> None:
    assert CONTRACT["extension"]["task_marker"] is False
    assert "weibo-task" not in _extension_runtime_source().casefold()


def test_weibo_extension_background_is_excluded() -> None:
    assert CONTRACT["extension"]["background"] is False
    manifest = json.loads(_read("extension/manifest.json"))
    assert "weibo" not in json.dumps(manifest, ensure_ascii=False).casefold()


def test_weibo_extension_early_response_is_excluded() -> None:
    assert CONTRACT["extension"]["early_response"] is False
    assert "weibo" not in _extension_runtime_source().casefold()


def test_weibo_extension_cookie_sync_is_excluded() -> None:
    assert CONTRACT["extension"]["cookie_sync"] is False
    runtime = _extension_runtime_source().casefold()
    assert "weibo" not in runtime
    assert "sinaimg" not in runtime


def test_weibo_setup_surface_is_excluded() -> None:
    assert CONTRACT["surfaces"]["setup"] is False
    shared = _read("src/openbiliclaw/web/shared/source-status.js")
    assert "weibo: Object.freeze({ guidedInit: false })" in shared
    assert "SOURCE_KEYS.filter((key) => SOURCE_CAPABILITIES[key]?.guidedInit === true)" in shared


def test_weibo_mobile_popup_platform_filter_is_product_level_exclusion() -> None:
    scope = CONTRACT["surface_scope"]
    assert scope["desktop_platform_filter"] is True
    assert scope["mobile_platform_filter"] is False
    assert scope["extension_popup_platform_filter"] is False
    desktop = _read("src/openbiliclaw/web/desktop/assets/js/app.js")
    assert '{ key: "weibo", label: "微博" }' in desktop
    assert "sourceFilter" in desktop
    assert "platform filter" in str(scope["platform_filter_policy"]).casefold()


def test_weibo_credentials_surface_is_excluded() -> None:
    assert CONTRACT["surfaces"]["credentials"] is False
    spec = CREDENTIAL_SPECS["weibo"]
    form = build_credential_form("weibo", cfg=Config())
    assert spec.kinds == ()
    assert form.kind == "none"
    assert form.required_keys == []


def test_weibo_favorite_engagement_is_unavailable() -> None:
    assert CONTRACT["engagement"]["favorite"] == "unavailable"
    content = _mapped_post()
    assert content is not None
    assert content.favorite_count == 0


def test_weibo_danmaku_engagement_is_unavailable() -> None:
    assert CONTRACT["engagement"]["danmaku"] == "unavailable"
    content = _mapped_post()
    assert content is not None
    assert content.danmaku_count == 0


def test_weibo_like_engagement_is_mapped() -> None:
    assert CONTRACT["engagement"]["like"] == "mapped"
    content = _mapped_post()
    assert content is not None
    assert content.like_count == 17


def test_weibo_comment_engagement_is_mapped() -> None:
    assert CONTRACT["engagement"]["comment"] == "mapped"
    content = _mapped_post()
    assert content is not None
    assert content.comment_count == content.reply_count == 9


def test_weibo_deep_link_uses_browser_fallback() -> None:
    assert CONTRACT["media"]["deep_link"] == "browser-fallback"
    launch = _read("src/openbiliclaw/web/js/app-launch.js")
    node_test = _read("tests/js/mobile-app-launch.test.mjs")
    assert "weibo://" not in launch
    assert 'buildAppDeepLink("https://m.weibo.cn/detail/5023456789012345"), ""' in node_test


def test_weibo_native_save_is_excluded() -> None:
    assert CONTRACT["media"]["native_save"] is False
    assert is_native_save_local_only("weibo") is True
    adapters = _read("src/openbiliclaw/saved_sync/adapters/extension.py")
    assert 'ExtensionAdapterDefinition("weibo"' not in adapters
    assert "local_only_source" in _read("src/openbiliclaw/saved_sync/service.py")
