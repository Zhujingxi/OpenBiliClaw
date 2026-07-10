from __future__ import annotations

import json
import mimetypes
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect, sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]


def _recommendations() -> list[dict[str, Any]]:
    return [
        {
            "id": index,
            "bvid": f"BV1ISSUE98{index}",
            "content_id": f"BV1ISSUE98{index}",
            "content_url": f"https://www.bilibili.com/video/BV1ISSUE98{index}",
            "source_platform": "bilibili",
            "title": f"稳定卡片 {index}",
            "up_name": f"UP {index}",
            "topic_label": "交互测试",
            "expression": f"第 {index} 张卡片的推荐理由。",
        }
        for index in range(1, 4)
    ]


class Issue98Stub:
    def __init__(self) -> None:
        self.feedback_posts: list[dict[str, Any]] = []
        self.feedback_received = threading.Event()
        self.feedback_delay_seconds = 0.0
        self.feedback_status = 200
        self.probe_posts: list[dict[str, Any]] = []
        self.probe_received = threading.Event()
        self.probe_status = 200


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
def issue_98_server() -> tuple[str, Issue98Stub]:
    state = Issue98Stub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/web", "/web/", "/web/index.html"}:
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/desktop/index.html",
                    "text/html",
                )
            if path.startswith("/web/assets/"):
                rel = path.removeprefix("/web/assets/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/desktop/assets" / rel)
            if path == "/api/health":
                return _json_response(self, {"ok": True, "embedding_ready": True})
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/recommendations":
                return _json_response(self, {"items": _recommendations()})
            if path == "/api/runtime-status":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "pool_available_count": 30,
                        "pool_size": 30,
                        "pool_refresh_state": "idle",
                        "pool_source_shares": {"bilibili": 1.0},
                        "configured_sources": {"bilibili": {"enabled": True}},
                        "unread_count": 0,
                    },
                )
            if path == "/api/init-status":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "running": False,
                        "can_start": False,
                        "reason": "already_initialized",
                        "stages": [],
                        "prerequisites": {
                            "bilibili_logged_in": True,
                            "llm_ready": True,
                            "embedding_ready": True,
                            "enabled_platforms": ["bilibili"],
                        },
                    },
                )
            if path == "/api/profile-summary":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "core_traits": [],
                        "likes": [],
                        "dislikes": [],
                        "speculative_interests": [
                            {
                                "domain": "系统设计",
                                "reason": "你常看复杂系统的拆解。",
                                "status": "active",
                                "confidence": 0.8,
                            }
                        ],
                        "speculative_avoidances": [
                            {
                                "domain": "标题党",
                                "reason": "你会快速退出夸张标题内容。",
                                "status": "active",
                                "confidence": 0.7,
                            }
                        ],
                    },
                )
            if path == "/api/config":
                return _json_response(
                    self,
                    {
                        "config": {
                            "sources": {"bilibili": {"enabled": True}},
                            "scheduler": {},
                            "llm": {"default_provider": "ollama", "ollama": {}},
                        }
                    },
                )
            if path == "/api/activity-feed":
                return _json_response(
                    self,
                    {"items": [], "has_more": False, "next_cursor": ""},
                )
            if path in {"/api/delight/pending-batch", "/api/notifications/pending"}:
                return _json_response(self, {"items": []})
            if path == "/api/chat/turns":
                return _json_response(self, {"items": []})
            if path == "/api/qr-info":
                return _json_response(self, {"lan_ip": "127.0.0.1"})
            return _json_response(self, {}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if path == "/api/feedback":
                state.feedback_posts.append(payload)
                state.feedback_received.set()
                if state.feedback_delay_seconds:
                    time.sleep(state.feedback_delay_seconds)
                return _json_response(
                    self,
                    {"ok": state.feedback_status < 400},
                    state.feedback_status,
                )
            if path in {"/api/interest-probes/respond", "/api/avoidance-probes/respond"}:
                state.probe_posts.append({"path": path, **payload})
                state.probe_received.set()
                return _json_response(
                    self,
                    {
                        "ok": state.probe_status < 400,
                        "action": (
                            "confirmed" if payload.get("response") == "confirm" else "deferred"
                        ),
                    },
                    state.probe_status,
                )
            return _json_response(self, {"ok": True})

        def _serve_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists():
                return _json_response(self, {"error": "not_found"}, 404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def chromium_page() -> Page:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script(
            """
            window.__OBC_TEST_UNDO_WINDOW_MS = 400;
            window.WebSocket = class FakeWebSocket {
              static OPEN = 1;
              constructor() {
                this.readyState = FakeWebSocket.OPEN;
                this.listeners = new Map();
                setTimeout(() => this.dispatch("open", { type: "open" }), 0);
              }
              addEventListener(type, handler) {
                const handlers = this.listeners.get(type) || [];
                handlers.push(handler);
                this.listeners.set(type, handlers);
              }
              removeEventListener(type, handler) {
                const handlers = this.listeners.get(type) || [];
                this.listeners.set(type, handlers.filter((item) => item !== handler));
              }
              dispatch(type, event) {
                if (typeof this[`on${type}`] === "function") this[`on${type}`](event);
                for (const handler of this.listeners.get(type) || []) handler(event);
              }
              close() { this.readyState = 3; }
            };
            """
        )
        yield page
        browser.close()


def _card_position(card: Any) -> tuple[float, float]:
    position = card.evaluate(
        """element => {
          const rect = element.getBoundingClientRect();
          return [rect.x + window.scrollX, rect.y + window.scrollY];
        }"""
    )
    return float(position[0]), float(position[1])


def _assert_position_stable(actual: tuple[float, float], expected: tuple[float, float]) -> None:
    assert actual[0] == pytest.approx(expected[0], abs=2.0)
    assert actual[1] == pytest.approx(expected[1], abs=2.0)


def test_recommendation_feedback_is_immediate_stable_and_undoable(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    chromium_page.goto(f"{base_url}/web/")
    cards = chromium_page.locator("#videoGrid .video-card")
    expect(cards).to_have_count(3)
    chromium_page.evaluate("() => document.fonts.ready")
    chromium_page.wait_for_timeout(500)
    second = cards.nth(1)
    second_identity = second.get_attribute("data-bvid")
    second_position = _card_position(second)

    first = cards.first
    first.locator('[data-action="like"]').click()

    expect(first.locator(".status-line")).to_contain_text("撤销")
    assert stub.feedback_posts == []
    assert second.get_attribute("data-bvid") == second_identity
    _assert_position_stable(_card_position(second), second_position)

    first.locator("[data-feedback-undo]").click()
    chromium_page.wait_for_timeout(500)
    assert stub.feedback_posts == []
    expect(first.locator('[data-action="like"]')).to_be_enabled()
    expect(first.locator(".status-line")).to_have_text("")

    stub.feedback_delay_seconds = 0.8
    first.locator('[data-action="dislike"]').click()
    assert stub.feedback_received.wait(timeout=2)
    expect(first.locator(".status-line")).to_contain_text("正在保存")
    second.locator('[data-action="like"]').click()
    expect(second.locator(".status-line")).to_contain_text("撤销")
    assert second.get_attribute("data-bvid") == second_identity
    _assert_position_stable(_card_position(second), second_position)
    second.locator("[data-feedback-undo]").click()
    assert len(stub.feedback_posts) == 1


def test_recommendation_feedback_failure_rolls_back_current_card(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    stub.feedback_status = 500
    chromium_page.goto(f"{base_url}/web/")
    first = chromium_page.locator("#videoGrid .video-card").first
    like = first.locator('[data-action="like"]')

    like.click()
    assert stub.feedback_received.wait(timeout=2)

    expect(like).to_be_enabled(timeout=3000)
    expect(like).not_to_have_class("is-active")
    expect(first.locator(".status-line")).to_have_text("")
    expect(chromium_page.locator("#toast")).to_contain_text("已恢复")


def test_interest_and_avoidance_probe_actions_are_immediate_and_undoable(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    chromium_page.goto(f"{base_url}/web/")

    chromium_page.locator("#messagesBtn").click()
    drawer = chromium_page.locator("#messagesDrawer")
    expect(drawer).to_be_visible()
    interest = drawer.locator(".message-item.is-interest-probe")
    expect(interest).to_have_count(1)
    expect(interest.locator('[data-probe="confirm"]')).to_have_text("确认喜欢")
    expect(interest.locator('[data-probe="defer"]')).to_have_text("暂时搁置")
    expect(interest.locator('[data-probe="reject"]')).to_have_text("确认不喜欢")
    avoidance_message = drawer.locator(".message-item.is-avoidance-probe")
    expect(avoidance_message).to_have_count(1)
    expect(avoidance_message.locator('[data-probe="confirm"]')).to_have_text("确认避雷")
    expect(avoidance_message.locator('[data-probe="defer"]')).to_have_text("搁置避雷")
    expect(avoidance_message.locator('[data-probe="reject"]')).to_have_text("不是雷点")
    interest.locator('[data-probe="confirm"]').click()
    expect(interest).to_contain_text("撤销")
    assert stub.probe_posts == []
    interest.locator("[data-probe-undo]").click()
    expect(interest.locator('[data-probe="confirm"]')).to_be_visible()
    chromium_page.wait_for_timeout(500)
    assert stub.probe_posts == []

    drawer.locator('[data-close="messagesDrawer"]').first.click()
    chromium_page.evaluate("() => document.querySelector('#profileBtn').click()")
    profile = chromium_page.locator("#profilePage")
    expect(profile).to_be_visible()
    avoidance = profile.locator('[data-spec-domain="标题党"]')
    avoidance.locator('[data-spec-response="confirm"]').click()
    expect(avoidance).to_contain_text("撤销")
    assert stub.probe_received.wait(timeout=2)
    assert stub.probe_posts == [
        {
            "path": "/api/avoidance-probes/respond",
            "domain": "标题党",
            "response": "confirm",
            "message": "",
        }
    ]
    expect(avoidance).to_contain_text("作为避雷方向")

    stub.probe_status = 500
    stub.probe_received.clear()
    interest_row = profile.locator('[data-spec-domain="系统设计"]')
    interest_row.locator('[data-spec-response="reject"]').click()
    assert stub.probe_received.wait(timeout=2)
    expect(interest_row.locator('[data-spec-response="reject"]')).to_be_visible()
    expect(chromium_page.locator("#toast")).to_contain_text("已恢复")
