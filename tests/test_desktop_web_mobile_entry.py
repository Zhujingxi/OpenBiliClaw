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
    body = wiring[1].split("safeBind(\"#profileBtn\"", 1)[0]
    assert "ENDPOINTS.health" in body, "must ask the backend for its LAN IP"
    assert "lan_ip" in body
    assert "isLoopbackMobileHost" in body, "must warn when only a loopback address is available"
    assert 'safeBind("#mobileQrBtn"' in _APP_JS
    assert 'safeBind("#mobileQrCopyBtn"' in _APP_JS


def test_first_visit_discovery_affordance() -> None:
    """New visitors must get an unmissable pointer at the mobile entry."""
    for marker in ('id="mobileQrDot"', 'id="mobileQrCallout"', 'id="mobileQrCalloutOpen"',
                   'id="mobileQrCalloutClose"'):
        assert marker in _INDEX, f"missing {marker}"
    assert "mobileQrSeen" in _APP_JS, "seen-state must persist so the callout shows only once"
    assert "initMobileQrDiscovery" in _APP_JS
    assert 'safeBind("#mobileQrCalloutOpen"' in _APP_JS, "callout body must open the QR drawer"


def test_qr_generator_is_self_contained_global() -> None:
    assert "window.OBCMobileQr" in _QR_JS
    for symbol in ("buildMobileWebUrl", "isLoopbackMobileHost", "createQrSvgMarkup"):
        assert symbol in _QR_JS
    assert "import " not in _QR_JS, "desktop web has no module build; keep it dependency-free"
