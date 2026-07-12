from __future__ import annotations

import json
import mimetypes
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
Page = playwright_api.Page
expect = playwright_api.expect
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]


class MobileWebStub:
    def __init__(self) -> None:
        self.notifications = [
            {
                "type": "interest.probe",
                "domain": "系统设计",
                "message": "你似乎常看系统设计。",
            },
            {
                "type": "avoidance.probe",
                "domain": "标题党",
                "message": "你似乎会避开标题党。",
            },
        ]
        self.post_counts = {"interest.probe": 0, "avoidance.probe": 0}
        self.post_received = {
            "interest.probe": threading.Event(),
            "avoidance.probe": threading.Event(),
        }
        self.response_gates = {
            "interest.probe": threading.Event(),
            "avoidance.probe": threading.Event(),
        }
        self.response_status = {"interest.probe": 200, "avoidance.probe": 200}
        self.lock = threading.Lock()

    def release(self, probe_type: str, *, status: int) -> None:
        self.response_status[probe_type] = status
        self.response_gates[probe_type].set()

    def release_all(self) -> None:
        for gate in self.response_gates.values():
            gate.set()


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, Any] | list[dict[str, Any]],
    status: int = 200,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    with suppress(BrokenPipeError):
        handler.wfile.write(body)


@pytest.fixture()
def mobile_web_server() -> tuple[str, MobileWebStub]:
    state = MobileWebStub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/m", "/m/", "/m/index.html"}:
                return self._serve_file(ROOT / "src/openbiliclaw/web/index.html", "text/html")
            if path.startswith("/m/css/"):
                relative = path.removeprefix("/m/css/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/css" / relative)
            if path.startswith("/m/js/"):
                relative = path.removeprefix("/m/js/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/js" / relative)
            if path == "/m/manifest.json":
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/manifest.json",
                    "application/manifest+json",
                )
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/health":
                return _json_response(self, {"ok": True})
            if path == "/api/notifications/pending":
                return _json_response(self, {"items": []})
            if path == "/api/interest-probes/pending":
                return _json_response(
                    self,
                    {"items": [state.notifications[0]]},
                )
            if path == "/api/avoidance-probes/pending":
                return _json_response(
                    self,
                    {"items": [state.notifications[1]]},
                )
            if path == "/api/delight/pending-batch":
                return _json_response(self, {"items": []})
            if path == "/api/chat/turns":
                return _json_response(self, {"items": []})
            return _json_response(self, {}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            probe_type = {
                "/api/interest-probes/respond": "interest.probe",
                "/api/avoidance-probes/respond": "avoidance.probe",
            }.get(path)
            if probe_type is None:
                return _json_response(self, {"ok": True})

            with state.lock:
                state.post_counts[probe_type] += 1
            state.post_received[probe_type].set()
            if not state.response_gates[probe_type].wait(timeout=10):
                return _json_response(self, {"ok": False, "error": "gate_timeout"}, 504)
            status = state.response_status[probe_type]
            return _json_response(
                self,
                {"ok": status < 400, "action": "confirmed"},
                status,
            )

        def _serve_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.is_file():
                return _json_response(self, {"error": "not_found"}, 404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with suppress(BrokenPipeError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        state.release_all()
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def chromium_page() -> Page:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script(
            """
            window.WebSocket = class FakeWebSocket {
              static OPEN = 1;
              constructor() {
                this.readyState = FakeWebSocket.OPEN;
                setTimeout(() => this.onopen?.({ type: "open" }), 0);
              }
              close() { this.readyState = 3; }
            };
            """
        )
        yield page
        browser.close()


def _probe_card(page: Page, domain: str) -> Any:
    return page.locator(f'[data-probe-domain="{domain}"]')


def _open_messages(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/m/#/chat")
    expect(page.locator(".badge-count")).to_have_attribute("data-count", "2")
    page.locator(".badge-btn").click()
    expect(page.locator(".messages-overlay")).to_have_class("messages-overlay open")


def _assert_all_probe_actions_disabled(card: Any, disabled: bool) -> None:
    buttons = card.locator("button")
    expect(buttons).to_have_count(4)
    for index in range(4):
        if disabled:
            expect(buttons.nth(index)).to_be_disabled()
        else:
            expect(buttons.nth(index)).to_be_enabled()


@pytest.mark.parametrize(
    ("probe_type", "domain", "button_action"),
    [
        ("interest.probe", "系统设计", "confirm"),
        ("avoidance.probe", "标题党", "confirm"),
    ],
)
def test_mobile_probe_stays_busy_when_overlay_is_rebuilt(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
    probe_type: str,
    domain: str,
    button_action: str,
) -> None:
    base_url, stub = mobile_web_server
    _open_messages(chromium_page, base_url)
    card = _probe_card(chromium_page, domain)
    card.locator(f'[data-probe="{button_action}"]').click()
    assert stub.post_received[probe_type].wait(timeout=2)

    chromium_page.locator(".messages-close").click()
    chromium_page.locator(".badge-btn").click()
    rebuilt = _probe_card(chromium_page, domain)
    expect(rebuilt).to_be_visible()
    _assert_all_probe_actions_disabled(rebuilt, True)
    expect(rebuilt).to_have_attribute("aria-busy", "true")

    rebuilt.locator(f'[data-probe="{button_action}"]').click(force=True)
    chromium_page.wait_for_timeout(200)
    assert stub.post_counts[probe_type] == 1

    stub.release(probe_type, status=200)
    expect(_probe_card(chromium_page, domain)).to_have_count(0)


@pytest.mark.parametrize(
    ("probe_type", "domain", "button_action"),
    [
        ("interest.probe", "系统设计", "confirm"),
        ("avoidance.probe", "标题党", "confirm"),
    ],
)
def test_mobile_probe_failure_restores_rebuilt_card_for_retry(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
    probe_type: str,
    domain: str,
    button_action: str,
) -> None:
    base_url, stub = mobile_web_server
    page_errors: list[str] = []
    chromium_page.on("pageerror", lambda error: page_errors.append(str(error)))
    _open_messages(chromium_page, base_url)
    _probe_card(chromium_page, domain).locator(f'[data-probe="{button_action}"]').click()
    assert stub.post_received[probe_type].wait(timeout=2)

    chromium_page.locator(".messages-close").click()
    chromium_page.locator(".badge-btn").click()
    rebuilt = _probe_card(chromium_page, domain)
    expect(rebuilt).to_have_attribute("aria-busy", "true")

    stub.release(probe_type, status=500)
    expect(rebuilt).to_be_visible()
    expect(rebuilt).to_have_attribute("aria-busy", "false")
    _assert_all_probe_actions_disabled(rebuilt, False)
    assert stub.post_counts[probe_type] == 1
    assert page_errors == []
