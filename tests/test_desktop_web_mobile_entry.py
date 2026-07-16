"""Static contract tests for the desktop web mobile-page entry.

The desktop web must keep a *labelled* 手机版 entry in the top bar (users
missed icon-only entries) that opens a QR drawer pointing at the /m/ mobile
web, built from the backend-reported LAN IP rather than the page host.
"""

from __future__ import annotations

from pathlib import Path

_DESKTOP_DIR = Path(__file__).resolve().parent.parent / "src" / "openbiliclaw" / "web" / "desktop"
_INDEX = (_DESKTOP_DIR / "index.html").read_text(encoding="utf-8")
_APP_JS = (_DESKTOP_DIR / "assets" / "js" / "app.js").read_text(encoding="utf-8")
_QR_JS = (_DESKTOP_DIR / "assets" / "js" / "mobile-qr.js").read_text(encoding="utf-8")
_CSS = (_DESKTOP_DIR / "assets" / "css" / "app.css").read_text(encoding="utf-8")


def test_topbar_has_labelled_mobile_entry() -> None:
    assert 'id="mobileQrBtn"' in _INDEX
    button = _INDEX.split('id="mobileQrBtn"', 1)[1].split("</button>", 1)[0]
    assert "手机版" in button, "the entry must carry a visible text label, not just an icon"
    assert ".top-mobile-btn" in _CSS
    top_btn_block = _CSS.split(".top-mobile-btn {", 1)[1].split("}", 1)[0]
    assert "background: var(--fg)" in top_btn_block, (
        "the entry pill must use the house solid-dark treatment so it stands out in the top bar"
    )


def test_mobile_qr_drawer_contract() -> None:
    for marker in (
        'id="mobileQrDrawer"',
        'id="mobileQrCanvas"',
        'id="mobileQrUrl"',
        'id="mobileQrCopyBtn"',
        'id="mobileQrHint"',
        'data-close="mobileQrDrawer"',
    ):
        assert marker in _INDEX, f"missing {marker}"
    assert "/web/assets/js/mobile-qr.js" in _INDEX, "QR generator script must be loaded"


def test_app_js_builds_url_from_backend_lan_ip() -> None:
    wiring = _APP_JS.split("async function openMobileQrDrawer", 1)
    assert len(wiring) == 2, "app.js must define openMobileQrDrawer"
    body = wiring[1].split('safeBind("#profileBtn"', 1)[0]
    assert "ENDPOINTS.qrInfo" in body, "must ask the lightweight QR endpoint for its LAN IP"
    assert "ENDPOINTS.health" not in body, "QR drawer must not trigger readiness/embedding probes"
    assert "lan_ip" in body
    assert "isLoopbackMobileHost" in body, "must warn when only a loopback address is available"
    assert 'safeBind("#mobileQrBtn"' in _APP_JS
    assert 'safeBind("#mobileQrCopyBtn"' in _APP_JS


def test_first_visit_discovery_affordance() -> None:
    """New visitors must get an unmissable pointer at the mobile entry."""
    for marker in (
        'id="mobileQrDot"',
        'id="mobileQrCallout"',
        'id="mobileQrCalloutOpen"',
        'id="mobileQrCalloutClose"',
    ):
        assert marker in _INDEX, f"missing {marker}"
    assert "mobileQrSeen" in _APP_JS, "seen-state must persist so the callout shows only once"
    assert "initMobileQrDiscovery" in _APP_JS
    assert 'safeBind("#mobileQrCalloutOpen"' in _APP_JS, "callout body must open the QR drawer"


def test_qr_generator_is_self_contained_global() -> None:
    assert "window.OBCMobileQr" in _QR_JS
    for symbol in ("buildMobileWebUrl", "isLoopbackMobileHost", "createQrSvgMarkup"):
        assert symbol in _QR_JS
    assert "import " not in _QR_JS, "desktop web has no module build; keep it dependency-free"


def test_desktop_settings_selects_survive_browser_page_translation() -> None:
    """Every <option> must carry an explicit value attribute and code-like
    selects must opt out of translation. Chrome/Edge page translation
    rewrites option TEXT nodes; without a value attribute select.value falls
    back to the translated text and garbage like '奥拉玛' lands in
    config.toml (field log 2026-07-05)."""
    import re
    from pathlib import Path

    html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    valueless = re.findall(r"<option(?:\s+selected=\"\")?>[^<]*</option>", html)
    assert valueless == [], f"value-less <option> elements: {valueless}"
    for select_id in ("logLevel", "logFileLevel"):
        m = re.search(rf'<select id="{select_id}"[^>]*>', html)
        assert m, select_id
        assert 'translate="no"' in m.group(0), f"{select_id} missing translate=no"

    model_js = Path("src/openbiliclaw/web/desktop/assets/js/model-settings.js").read_text(
        encoding="utf-8"
    )
    assert "field.choices" in model_js
    assert "preset_definitions" in model_js
    assert '<option value="${escapeHtml(' in model_js


def test_desktop_settings_allows_same_type_instances_with_distinct_ids() -> None:
    """Fallback identity is now the stable record ID, not provider type."""
    model_js = Path("src/openbiliclaw/web/desktop/assets/js/model-settings.js").read_text(
        encoding="utf-8"
    )

    assert 'id="llmFallbackSameWarning"' not in _INDEX
    assert "syncLlmFallbackSameState" not in _APP_JS
    assert "data-model-record-id" in model_js
    assert "record.id" in model_js
