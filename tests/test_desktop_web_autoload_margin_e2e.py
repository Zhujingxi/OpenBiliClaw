"""Desktop web 自动加载预载边距的真实浏览器契约（AUTO_LOAD_ROOT_MARGIN_PX）。

用真实 chromium 驱动 /web/，量测「自动加载触发时哨兵距视口底部的实际距离」，
证明 50px 的预载边距只在最后一行基本滚进视口后才追加新卡片——即哨兵还在视口下方
约 150px 时不触发、逼近视口底部(约 20px)时才触发。用于回归 300→50 的 UX 调整，
避免「最后一行永远看不全就被追加」的强迫症体验回退。
"""

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

playwright_api = pytest.importorskip("playwright.sync_api")
Page = playwright_api.Page
expect = playwright_api.expect
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
CARD_COUNT = 30


def _recommendations(prefix: str, count: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"{prefix}{index}",
            "bvid": f"BV1MARGIN{prefix}{index}",
            "content_id": f"BV1MARGIN{prefix}{index}",
            "content_url": f"https://www.bilibili.com/video/BV1MARGIN{prefix}{index}",
            "source_platform": "bilibili",
            "title": f"边距卡片 {prefix}-{index}",
            "up_name": f"UP {index}",
            "topic_label": "自动加载",
            "expression": f"第 {prefix}-{index} 张卡片的推荐理由，占位文案撑起卡片高度。",
        }
        for index in range(1, count + 1)
    ]


class MarginStub:
    def __init__(self) -> None:
        self.append_posts: list[dict[str, Any]] = []
        self.append_received = threading.Event()


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    with suppress(BrokenPipeError):
        handler.wfile.write(body)


@pytest.fixture()
def margin_server() -> tuple[str, MarginStub]:
    state = MarginStub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/web", "/web/", "/web/index.html"}:
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/desktop/index.html", "text/html"
                )
            if path.startswith("/web/assets/"):
                rel = path.removeprefix("/web/assets/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/desktop/assets" / rel)
            if path == "/api/ping":
                return _json_response(self, {"ok": True})
            if path == "/api/health":
                return _json_response(self, {"ok": True, "embedding_ready": True})
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/recommendations":
                return _json_response(self, {"items": _recommendations("A", CARD_COUNT)})
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
            if path == "/api/profile-summary":
                return _json_response(self, {"initialized": True})
            if path == "/api/activity-feed":
                return _json_response(self, {"items": [], "has_more": False, "next_cursor": ""})
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
            if path == "/api/recommendations/append":
                state.append_posts.append(payload)
                state.append_received.set()
                return _json_response(self, {"items": _recommendations("B", 10)})
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
            window.WebSocket = class FakeWebSocket {
              static OPEN = 1;
              constructor() {
                this.readyState = FakeWebSocket.OPEN;
                setTimeout(() => {
                  if (typeof this.onopen === 'function') this.onopen({type:'open'});
                }, 0);
              }
              addEventListener() {}
              removeEventListener() {}
              close() { this.readyState = 3; }
            };
            """
        )
        yield page
        browser.close()


def _sentinel_gap(page: Page) -> float:
    """哨兵顶部距视口底部的距离（正数=还在视口下方）。"""
    return page.evaluate(
        """() => {
          const s = document.getElementById('loadMoreSentinel');
          const r = s.getBoundingClientRect();
          return r.top - window.innerHeight;
        }"""
    )


def _scroll_sentinel_to_gap(page: Page, gap: float) -> float:
    """滚动使哨兵顶部落在视口底部下方约 gap px 处；返回滚动后同步量到的实际 gap
    （在异步追加/重渲染改变布局之前）。"""
    return page.evaluate(
        """(gap) => {
          const s = document.getElementById('loadMoreSentinel');
          const r = s.getBoundingClientRect();
          const delta = r.top - window.innerHeight - gap;  // 需要额外下滚的量
          window.scrollTo({ top: window.scrollY + delta, behavior: 'instant' });
          const after = s.getBoundingClientRect();
          return after.top - window.innerHeight;
        }""",
        gap,
    )


def test_autoload_fires_only_when_last_row_nearly_in_view(
    margin_server: tuple[str, MarginStub],
    chromium_page: Page,
) -> None:
    base_url, stub = margin_server
    chromium_page.goto(f"{base_url}/web/")

    grid = chromium_page.locator("#videoGrid .video-card:not(.is-skeleton)")
    expect(grid).to_have_count(CARD_COUNT, timeout=8000)

    # 自动加载默认开启（storageGet(...) !== "0"），后端 pool_available_count>0。
    # 未滚动：哨兵远在视口下方，不应触发追加。
    assert _sentinel_gap(chromium_page) > 300
    time.sleep(0.4)
    assert not stub.append_received.is_set(), "首屏未滚动就触发了自动加载"

    # 哨兵停在视口底部下方约 150px：50px 边距不该触发（300px 旧值会触发）。
    gap_far = _scroll_sentinel_to_gap(chromium_page, 150)
    assert 110 < gap_far < 190, f"定位失败，实际 gap={gap_far}"
    assert not stub.append_received.wait(timeout=1.2), (
        f"哨兵在视口下方 {gap_far:.0f}px（>50 边距）时不应自动加载——"
        "若这里触发说明边距又变回了接近 300px 的大预载"
    )

    # 哨兵逼近视口底部（约 20px）：进入 50px 边距，必须触发追加。
    gap_near = _scroll_sentinel_to_gap(chromium_page, 20)
    assert gap_near < 50, f"定位失败，实际 gap={gap_near}"
    assert stub.append_received.wait(timeout=2.0), (
        f"哨兵已逼近视口底部 {gap_near:.0f}px（<50 边距）却没有自动加载"
    )

    # 追加成功后新卡片入列。
    expect(chromium_page.locator("#videoGrid .video-card:not(.is-skeleton)")).to_have_count(
        CARD_COUNT + 10, timeout=4000
    )
