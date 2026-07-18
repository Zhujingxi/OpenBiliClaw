import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
WEB = ROOT / "src/openbiliclaw/web"
EXT = ROOT / "extension"

PROTECTED_TERMS = ("Chat", "Embedding", "Runtime", "Primary", "Fallback")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _frontend_sources() -> list[Path]:
    files: list[Path] = []
    for base, suffixes in (
        (WEB, {".js", ".html", ".css"}),
        (EXT / "popup", {".js", ".html"}),
        (EXT / "src", {".ts", ".js"}),
    ):
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in suffixes:
                files.append(path)
    return files


def test_no_frontend_surface_requests_revealed_credentials() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _frontend_sources()
        if "reveal_keys=true" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_frontend_consumes_legacy_saved_or_cognition_paths() -> None:
    legacy_route = re.compile(r"[\"'`]/watch-later|[\"'`]/favorites")
    offenders = []
    for path in _frontend_sources():
        text = path.read_text(encoding="utf-8")
        if legacy_route.search(text):
            offenders.append(f"{path.relative_to(ROOT)}: legacy saved route")
        if path.is_relative_to(WEB / "js") and "cognition-updates" in text:
            offenders.append(f"{path.relative_to(ROOT)}: cognition route")
    assert offenders == []


def test_extension_has_no_debug_relay_and_no_web_shared_dependency() -> None:
    for path in _frontend_sources():
        if not path.is_relative_to(EXT):
            continue
        text = path.read_text(encoding="utf-8")
        assert "/sources/_debug/log" not in text, path
        assert "debugLog(" not in text, path
        assert "/web/shared/" not in text, path


def test_desktop_shared_core_loads_before_app_and_is_cache_versioned() -> None:
    html = _read("src/openbiliclaw/web/desktop/index.html")
    assert 'src="/web/shared/saved-sync-core.js"' in html
    assert html.index('src="/web/shared/saved-sync-core.js"') < html.index(
        'src="/web/assets/js/app.js"'
    )
    app_py = _read("src/openbiliclaw/api/app.py")
    assert '"shared/saved-sync-core.js"' in app_py
    # The served HTML rewrites the shared-core URL with the same asset digest
    # as the other desktop assets, so a stale cached core cannot pair with a
    # fresh app.js.
    assert 'src="/web/shared/saved-sync-core.js?v={version}"' in app_py


def test_desktop_app_js_starts_only_after_shared_core_is_ready() -> None:
    # The core is an ES module while app.js is a classic deferred script; the
    # dependency must be explicit in code, never assumed from tag ordering.
    js = _read("src/openbiliclaw/web/desktop/assets/js/app.js")
    head = js.split("const DEFAULT_API_BASE", 1)[0]
    assert "OBCSavedSyncCore" in head
    assert "const startDesktopApp = () => {" in head
    assert "coreReadyTimer" in js
    assert "setInterval" in js
    assert js.index("const startDesktopApp = () => {") < js.index("coreReadyTimer")


def test_desktop_e2e_fixture_serves_shared_modules() -> None:
    e2e = _read("tests/test_web_guided_init_e2e.py")
    assert 'path.startswith("/web/shared/")' in e2e
    assert '"src/openbiliclaw/web/shared"' in e2e
    assert (ROOT / "src/openbiliclaw/web/shared/saved-sync-core.js").is_file()


def test_shared_saved_sync_core_stays_surface_neutral() -> None:
    core = _read("src/openbiliclaw/web/shared/saved-sync-core.js")
    assert "globalThis.document" not in core
    assert "globalThis.window" not in core
    assert not any("一" <= character <= "鿿" for character in core)


def test_setup_wizard_single_csrf_helper_and_no_config_reads() -> None:
    html = _read("src/openbiliclaw/web/setup/index.html")
    assert "async function apiFetch(" in html
    assert html.count("fetch(") == 1
    assert '"/api/config"' not in html
    assert "X-OBC-Auth" in html


def test_model_editors_use_chinese_first_labels_and_preserve_technical_terms() -> None:
    surfaces = {
        "desktop": _read("src/openbiliclaw/web/desktop/index.html")
        + _read("src/openbiliclaw/web/desktop/assets/js/model-settings.js"),
        "mobile": _read("src/openbiliclaw/web/js/views/model-settings.js"),
        "popup": _read("extension/popup/popup.html")
        + _read("extension/popup/popup-model-settings.js"),
    }
    for name, text in surfaces.items():
        assert "上移" in text, name
        assert "下移" in text, name
        assert "未命名连接" in text, name
        for term in PROTECTED_TERMS:
            assert term in text, f"{name}: {term}"
    for editor in (
        "src/openbiliclaw/web/desktop/assets/js/model-settings.js",
        "extension/popup/popup-model-settings.js",
    ):
        assert "拖拽排序" in _read(editor), editor
        assert "未设置模型" in _read(editor), editor


@pytest.mark.parametrize(
    "mount_path",
    ["/web/shared/saved-sync-core.js", "/m/shared/saved-sync-core.js"],
)
def test_shared_saved_sync_core_is_served_on_desktop_and_mobile(mount_path: str) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app

    client = TestClient(create_app())
    response = client.get(mount_path)
    assert response.status_code == 200, mount_path
    assert "createSavedSubmissionFence" in response.text
