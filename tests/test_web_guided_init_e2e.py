from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration


ROOT = Path(__file__).resolve().parents[1]

ANALYZE_TIMEOUT_DETAIL = (
    "偏好分析等待 AI 服务超过 6 分钟仍未返回结果，已自动停止，避免继续卡住。"
    "常见原因是 Base URL、模型名或代理配置错误。请到模型设置测试 AI 服务后重试初始化。"
)
DISCOVERY_TIMEOUT_DETAIL = (
    "画像已生成，但首轮内容池等待内容发现超过 10 分钟仍未完成，本次初始化为部分完成。"
    "系统会在后台继续补池；请检查平台登录与网络/代理。"
)


def _model_snapshot(revision: str = "model-rev-1") -> dict[str, Any]:
    return {
        "revision": revision,
        "source": "native",
        "models": {
            "schema_version": 1,
            "chat": {
                "concurrency": 4,
                "timeout_seconds": 300,
                "connections": [
                    {
                        "id": "ollama-main",
                        "name": "Local Ollama",
                        "type": "ollama",
                        "preset": "",
                        "model": "qwen2.5:7b",
                        "base_url": "http://127.0.0.1:11434/v1",
                        "credential": {
                            "source": "none",
                            "configured": False,
                            "env_name": "",
                            "credential_ref": "",
                            "oauth_logged_in": False,
                        },
                        "api_mode": "",
                        "reasoning_effort": "",
                        "http_referer": "",
                        "x_title": "",
                        "num_ctx": 0,
                        "probe": None,
                        "circuit": {"state": "closed"},
                    }
                ],
            },
            "embedding": {
                "enabled": False,
                "settings": {
                    "model": "bge-m3",
                    "output_dimensionality": 1024,
                    "similarity_threshold": 0.82,
                    "multimodal_enabled": False,
                },
                "providers": [],
            },
        },
        "migration": {"state": "none", "confirmed": True, "issues": []},
        "overrides": [],
    }


def _connection_types() -> dict[str, Any]:
    ollama = {
        "id": "ollama",
        "label": "Ollama",
        "category": "local_runtime",
        "capabilities": ["chat", "embedding"],
        "presets": [],
        "preset_definitions": [],
        "help": "Local native service",
        "fields": [
            {
                "name": "model",
                "label": "Model",
                "input_type": "text",
                "required": True,
                "capabilities": ["chat"],
                "presets": [],
                "help": "",
                "placeholder": "qwen2.5:7b",
                "choices": [],
            },
            {
                "name": "base_url",
                "label": "Base URL",
                "input_type": "text",
                "required": True,
                "capabilities": ["chat", "embedding"],
                "presets": [],
                "help": "",
                "placeholder": "http://127.0.0.1:11434/v1",
                "choices": [],
            },
        ],
    }
    return {
        "capability": "chat",
        "connection_types": [ollama],
        "groups": [{"category": "local_runtime", "connection_types": [ollama]}],
    }


def _status(
    *,
    initialized: bool = False,
    running: bool = False,
    current_stage: int = 0,
    can_start: bool = True,
    reason: str = "none",
    detail: str = "",
    partial_success: bool = False,
    enabled_platforms: list[str] | None = None,
    stages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "initialized": initialized,
        "running": running,
        "run_id": "test-run",
        "sequence": current_stage,
        "current_stage": current_stage,
        "total_stages": 4,
        "stages": stages
        or [
            {"n": 1, "label": "拉取数据", "status": "pending", "reason": None},
            {"n": 2, "label": "分析偏好", "status": "pending", "reason": None},
            {"n": 3, "label": "生成画像", "status": "pending", "reason": None},
            {"n": 4, "label": "发现内容池", "status": "pending", "reason": None},
        ],
        "partial_success": partial_success,
        "can_start": can_start,
        "can_manage": True,
        "prerequisites": {
            "bilibili_logged_in": True,
            "bilibili_check": "ok",
            "llm_ready": True,
            "embedding_ready": True,
            "enabled_platforms": enabled_platforms or ["bilibili", "youtube"],
        },
        "reason": reason,
        "detail": detail,
    }


class GuidedInitStub:
    def __init__(self) -> None:
        self.init_posts: list[dict[str, Any]] = []
        self.model_puts: list[dict[str, Any]] = []
        self.model_probes: list[dict[str, Any]] = []
        self.model_operations: list[str] = []
        self.model_snapshot = _model_snapshot()
        self.current_status = _status()
        self.post_init_error: tuple[int, dict[str, Any]] | None = None
        self.fail_next_status = False
        self.runtime_status = {
            "initialized": False,
            "pool_available_count": 0,
            "pool_size": 0,
            "pool_refresh_state": "idle",
            "pool_source_shares": {"bilibili": 1.0},
            "configured_sources": {"bilibili": {"enabled": True}},
            "unread_count": 0,
        }

    def status(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.current_status))

    def start_response(self) -> dict[str, Any]:
        status = self.status()
        return {
            "running": status["running"],
            "run_id": status["run_id"],
            "sequence": status["sequence"],
            "current_stage": status["current_stage"],
            "total_stages": status["total_stages"],
            "stages": status["stages"],
            "partial_success": status["partial_success"],
            "status": "running" if status["running"] else "idle",
            "reason": status["reason"],
        }

    def set_running(self) -> None:
        self.current_status = _status(
            running=True,
            current_stage=1,
            stages=[
                {"n": 1, "label": "拉取数据", "status": "running", "reason": None},
                {"n": 2, "label": "分析偏好", "status": "pending", "reason": None},
                {"n": 3, "label": "生成画像", "status": "pending", "reason": None},
                {"n": 4, "label": "发现内容池", "status": "pending", "reason": None},
            ],
        )

    def set_initialized(self) -> None:
        self.current_status = _status(
            initialized=True,
            stages=[
                {"n": 1, "label": "拉取数据", "status": "ok", "reason": None},
                {"n": 2, "label": "分析偏好", "status": "ok", "reason": None},
                {"n": 3, "label": "生成画像", "status": "ok", "reason": None},
                {"n": 4, "label": "发现内容池", "status": "ok", "reason": None},
            ],
        )

    def set_analyze_timeout(self) -> None:
        self.current_status = _status(
            can_start=True,
            reason="analyze_failed",
            detail=ANALYZE_TIMEOUT_DETAIL,
            stages=[
                {"n": 1, "label": "拉取数据", "status": "ok", "reason": None},
                {"n": 2, "label": "分析偏好", "status": "failed", "reason": "analyze_failed"},
                {"n": 3, "label": "生成画像", "status": "failed", "reason": "analyze_failed"},
                {"n": 4, "label": "发现内容池", "status": "failed", "reason": "analyze_failed"},
            ],
        )

    def set_discovery_timeout(self) -> None:
        self.current_status = _status(
            initialized=True,
            can_start=False,
            reason="discovery_timeout",
            detail=DISCOVERY_TIMEOUT_DETAIL,
            partial_success=True,
            stages=[
                {"n": 1, "label": "拉取数据", "status": "ok", "reason": None},
                {"n": 2, "label": "分析偏好", "status": "ok", "reason": None},
                {"n": 3, "label": "生成画像", "status": "ok", "reason": None},
                {
                    "n": 4,
                    "label": "发现内容池",
                    "status": "warning",
                    "reason": "discovery_timeout",
                },
            ],
        )

    def set_bilibili_blocked(self) -> None:
        self.current_status = _status(
            can_start=False,
            reason="bilibili_not_logged_in",
        )
        self.current_status["prerequisites"]["bilibili_logged_in"] = False
        self.current_status["prerequisites"]["bilibili_check"] = "failed"

    def set_enabled_platforms(self, platforms: list[str]) -> None:
        self.current_status = _status(enabled_platforms=platforms)


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, Any],
    status: int = 200,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


@pytest.fixture()
def guided_init_server() -> tuple[str, GuidedInitStub]:
    state = GuidedInitStub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/setup/", "/setup/index.html"}:
                return self._serve_file(ROOT / "src/openbiliclaw/web/setup/index.html", "text/html")
            if path in {"/web", "/web/"}:
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/desktop/index.html",
                    "text/html",
                )
            if path.startswith("/web/assets/"):
                rel = path.removeprefix("/web/assets/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/desktop/assets" / rel)
            if path == "/api/model-config":
                return _json_response(self, state.model_snapshot)
            if path == "/api/model-connection-types":
                return _json_response(self, _connection_types())
            if path == "/api/config":
                return _json_response(
                    self,
                    {"config": {"bilibili": {"cookie": "SESSDATA=test-session"}}},
                )
            if path == "/api/init-status":
                if state.fail_next_status:
                    state.fail_next_status = False
                    return _json_response(self, {"error": "temporary"}, 500)
                return _json_response(self, state.status())
            if path == "/api/runtime-status":
                return _json_response(self, state.runtime_status)
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/recommendations":
                return _json_response(self, {"items": [], "runtime": state.runtime_status})
            if path == "/api/delight/pending-batch":
                return _json_response(self, {"items": []})
            if path == "/api/activity-feed":
                return _json_response(self, {"items": [], "has_more": False, "next_cursor": ""})
            if path == "/api/notifications/pending":
                return _json_response(self, {"items": []})
            if path == "/api/profile-summary":
                return _json_response(
                    self,
                    {"profile": None, "memory_items": [], "has_more": False},
                )
            if path == "/api/profile/edit-state":
                return _json_response(self, {"busy": False, "draft": ""})
            if path in {"/api/watch-later", "/api/favorites"}:
                return _json_response(self, {"items": [], "total": 0})
            return _json_response(self, {}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if path == "/api/init":
                state.init_posts.append(payload)
                if state.post_init_error is not None:
                    status_code, body = state.post_init_error
                    return _json_response(self, body, status_code)
                state.set_running()
                return _json_response(self, state.start_response(), 202)
            if path == "/api/model-config/probe":
                state.model_probes.append(payload)
                state.model_operations.append("probe")
                connection = payload.get("connection") or {}
                return _json_response(
                    self,
                    {
                        "ok": True,
                        "connection_id": connection.get("id", ""),
                        "capability": "chat",
                        "observed_dimension": 0,
                        "error_code": "",
                        "message": "Connection ready.",
                        "probed_at": "2026-07-16T00:00:00+00:00",
                        "revision": payload.get("revision", ""),
                    },
                )
            return _json_response(self, {"ok": True})

        def do_PUT(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if path == "/api/model-config":
                state.model_puts.append(payload)
                state.model_operations.append("put")
                snapshot = _model_snapshot("model-rev-2")
                snapshot["models"] = payload["models"]
                return _json_response(
                    self,
                    {
                        "ok": True,
                        "revision": "model-rev-2",
                        "reloaded": True,
                        "rollback_applied": False,
                        "snapshot": snapshot,
                    },
                )
            return _json_response(self, {"ok": True})

        def _serve_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists():
                return _json_response(self, {"error": "not_found", "path": str(path)}, 404)
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
def chromium_page():
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            yield page
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip(
                "Playwright Chromium is not installed; "
                "run `uv run --extra browser playwright install chromium`"
            )
        raise


def _install_fake_runtime_stream(page: Any, *, fast_watchdog: bool = False) -> None:
    watchdog_setup = (
        "window.__OBC_TEST_INIT_POLL_MS = 50;"
        "window.__OBC_TEST_INIT_START_POLL_MS = 50;"
        "window.__OBC_TEST_INIT_WATCHDOG_MS = 50;"
        if fast_watchdog
        else ""
    )
    script = """
        (() => {
          __WATCHDOG_SETUP__
          window.__obcSockets = [];
          window.__obcInitPosted = false;
          const realFetch = window.fetch.bind(window);
          window.fetch = (input, init) => {
            const url = String(input && input.url ? input.url : input);
            const method = String((init && init.method) || "GET").toUpperCase();
            const isInitPost = method === "POST" && /\\/api\\/init(?:$|[?#])/.test(url);
            return realFetch(input, init).then((response) => {
              if (isInitPost) window.__obcInitPosted = true;
              return response;
            });
          };
          window.WebSocket = class FakeWebSocket {
            constructor(url) {
              this.url = String(url);
              this.readyState = 1;
              this.listeners = new Map();
              window.__obcSockets.push(this);
              window.setTimeout(() => this.__dispatch("open", { type: "open" }), 0);
            }
            addEventListener(type, handler) {
              const list = this.listeners.get(type) || [];
              list.push(handler);
              this.listeners.set(type, list);
            }
            removeEventListener(type, handler) {
              const list = this.listeners.get(type) || [];
              this.listeners.set(type, list.filter((item) => item !== handler));
            }
            __dispatch(type, event) {
              const attr = this[`on${type}`];
              if (typeof attr === "function") attr.call(this, event);
              for (const handler of this.listeners.get(type) || []) {
                handler.call(this, event);
              }
            }
            close() {
              this.readyState = 3;
              this.__dispatch("close", { type: "close" });
            }
          };
          window.__emitRuntimeEvent = (payload) => {
            const event = { type: "message", data: JSON.stringify(payload) };
            for (const socket of window.__obcSockets) {
              socket.__dispatch("message", event);
            }
          };
        })();
        """
    page.add_init_script(script.replace("__WATCHDOG_SETUP__", watchdog_setup))


def _save_setup_model(page: Any) -> None:
    page.wait_for_selector('#connectionTypeList [data-connection-type="ollama"]')
    page.locator('#connectionTypeList [data-connection-type="ollama"]').click()
    page.locator("#saveModel").click()
    page.wait_for_selector('[data-panel="1"].active')


def test_setup_wizard_e2e_starts_guided_init_and_finishes_on_runtime_event(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    _save_setup_model(chromium_page)
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("label.init-source-row", has_text="YouTube").locator("input").check()
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_function("() => window.__obcSockets.length === 1")
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgress')?.hidden === false"
    )
    assert stub.init_posts == [{"sources": ["bilibili", "youtube"]}]
    assert stub.model_operations == ["probe", "put"]
    assert stub.model_probes[0]["connection"]["id"] == "ollama-main"
    assert stub.model_puts[0]["revision"] == "model-rev-1"
    assert "llm" not in stub.model_puts[0]
    socket_url = chromium_page.evaluate("() => window.__obcSockets[0].url")
    assert socket_url.endswith("/api/runtime-stream")

    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_progress" })""")
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgressLabel')?.innerText.includes('1/4')"
    )
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_selector('[data-panel="3"].active')
    assert "首轮初始化" in chromium_page.locator('[data-panel="3"]').inner_text()


def test_setup_wizard_e2e_waits_for_first_pool_before_finishing(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)

    chromium_page.goto(f"{base_url}/setup/")
    _save_setup_model(chromium_page)
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgress')?.hidden === false"
    )

    stub.set_discovery_timeout()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 0})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_timeout(100)

    assert chromium_page.locator('[data-panel="2"]').evaluate(
        "el => el.classList.contains('active')"
    )
    assert chromium_page.locator('[data-panel="3"]').evaluate(
        "el => !el.classList.contains('active')"
    )
    assert "首轮内容" in chromium_page.locator("#initProgressLabel").inner_text()
    assert "超过 10 分钟" in chromium_page.locator("#initReason").inner_text()
    assert "后台继续补池" in chromium_page.locator("#initReason").inner_text()
    assert chromium_page.locator("#initReason").get_attribute("role") == "status"

    stub.runtime_status.update({"pool_available_count": 12, "last_replenished_count": 12})
    chromium_page.evaluate(
        """() => window.__emitRuntimeEvent({
            type: "refresh.pool_updated",
            pool_available_count: 12
        })"""
    )
    chromium_page.wait_for_selector('[data-panel="3"].active')


def test_setup_wizard_e2e_save_llm_does_not_start_guided_init(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server

    chromium_page.goto(f"{base_url}/setup/")
    _save_setup_model(chromium_page)

    assert stub.init_posts == []
    assert len(stub.model_probes) == 1
    assert len(stub.model_puts) == 1
    assert stub.model_operations == ["probe", "put"]


def test_setup_wizard_e2e_creates_first_stable_chat_connection(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.model_snapshot["models"]["chat"]["connections"] = []

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.wait_for_selector('#connectionTypeList [data-connection-type="ollama"]')
    chromium_page.locator('[data-model-field="model"]').fill("qwen2.5:7b")
    chromium_page.locator('[data-model-field="base_url"]').fill("http://127.0.0.1:11434/v1")
    chromium_page.locator("#saveModel").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')

    assert stub.model_operations == ["probe", "put"]
    assert stub.model_probes[0]["connection"]["id"] == "chat-primary"
    assert stub.model_puts[0]["models"]["chat"]["connections"][0]["id"] == "chat-primary"


def test_setup_wizard_e2e_selected_sources_do_not_require_prior_settings_enable(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.set_enabled_platforms(["bilibili"])
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    _save_setup_model(chromium_page)
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("label.init-source-row", has_text="小红书").locator("input").check()
    chromium_page.locator("label.init-source-row", has_text="抖音").locator("input").check()
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    assert stub.init_posts == [{"sources": ["bilibili", "xiaohongshu", "douyin"]}]


def test_desktop_web_e2e_shows_init_cta_and_starts_same_init_endpoint(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    assert chromium_page.locator(".video-card").count() == 0
    assert chromium_page.locator("#loadMoreBtn").is_hidden()

    chromium_page.locator("label.init-source-row", has_text="YouTube").locator("input").check()
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")

    assert stub.init_posts == [{"sources": ["bilibili", "youtube"]}]
    chromium_page.wait_for_function(
        "() => document.querySelector('.init-progress')?.innerText.includes('1/4')"
    )
    assert "✗" not in chromium_page.locator(".init-checklist").inner_text()
    fill_width = chromium_page.locator(".init-progress-fill").evaluate(
        "el => Number.parseFloat(el.style.width)"
    )
    assert fill_width > 0
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_function("() => document.querySelector('.init-onboarding') === null")
    assert chromium_page.locator("#loadMoreBtn").is_visible()


def test_desktop_web_e2e_waits_for_pool_before_init_completed_state(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")

    stub.set_discovery_timeout()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 0})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_timeout(100)

    assert chromium_page.locator('.init-onboarding[data-init-phase="completed"]').count() == 0
    assert chromium_page.locator(".init-onboarding").is_visible()
    assert "首轮内容" in chromium_page.locator(".init-progress").inner_text()
    assert "超过 10 分钟" in chromium_page.locator(".init-reason").inner_text()
    assert "后台继续补池" in chromium_page.locator(".init-reason").inner_text()

    stub.runtime_status.update({"pool_available_count": 12, "last_replenished_count": 12})
    chromium_page.evaluate(
        """() => window.__emitRuntimeEvent({
            type: "refresh.pool_updated",
            pool_available_count: 12
        })"""
    )
    chromium_page.wait_for_function("() => document.querySelector('.init-onboarding') === null")
    assert chromium_page.locator("#loadMoreBtn").is_visible()


@pytest.mark.parametrize("surface", ["setup", "desktop"])
def test_web_e2e_surfaces_timeout_cause_and_recovery_actions(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
    surface: str,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)

    if surface == "setup":
        chromium_page.goto(f"{base_url}/setup/")
        _save_setup_model(chromium_page)
        chromium_page.locator("#next1").click()
        chromium_page.wait_for_selector('[data-panel="2"].active')
        chromium_page.locator("#startInit").click()
        label = chromium_page.locator("#initProgressLabel")
        retry = chromium_page.locator("#startInit")
    else:
        chromium_page.goto(f"{base_url}/web/")
        chromium_page.wait_for_selector(".init-onboarding", state="attached")
        chromium_page.locator('[data-init-action="start"]').click()
        label = chromium_page.locator(".init-progress p")
        retry = chromium_page.locator('[data-init-action="start"]')

    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    stub.set_analyze_timeout()
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_failed" })""")
    chromium_page.wait_for_function(
        "() => document.body.innerText.includes('超过 6 分钟') && "
        "document.body.innerText.includes('Base URL')"
    )

    text = label.inner_text()
    assert "偏好分析" in text
    assert "超过 6 分钟" in text
    assert "Base URL" in text
    assert "模型设置" in text
    assert label.get_attribute("role") == "alert"
    assert retry.inner_text() == "重试初始化"
    if surface == "desktop":
        assert chromium_page.locator('[data-init-action="settings"]').is_visible()


def test_desktop_web_e2e_matches_popup_when_runtime_has_post_init_signals(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.runtime_status.update(
        {
            "initialized": False,
            "recommendation_count": 4,
            "pool_available_count": 12,
            "pool_pending_count": 3,
            "last_discovered_count": 9,
            "last_replenished_count": 5,
        }
    )
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/web/")

    chromium_page.wait_for_selector(".empty-state")
    assert chromium_page.locator(".init-onboarding").count() == 0
    assert chromium_page.locator("#loadMoreBtn").is_visible()


def test_setup_wizard_e2e_watchdog_polls_when_runtime_stream_is_silent(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)

    chromium_page.goto(f"{base_url}/setup/")
    _save_setup_model(chromium_page)
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_function("() => window.__obcSockets.length === 1")
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.wait_for_selector('[data-panel="3"].active')


def test_setup_wizard_e2e_default_watchdog_polls_when_runtime_stream_is_silent(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    _save_setup_model(chromium_page)
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_function("() => window.__obcSockets.length === 1")
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.wait_for_selector('[data-panel="3"].active', timeout=30000)


def test_setup_wizard_e2e_blocks_missing_bilibili_without_post(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.set_bilibili_blocked()
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    _save_setup_model(chromium_page)
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_selector("#initReason.msg.show")
    assert stub.init_posts == []
    assert "还没检测到 B站 登录" in chromium_page.locator("#initReason").inner_text()
    assert "✗" in chromium_page.locator("#initChecklist").inner_text()


def test_desktop_web_e2e_surfaces_init_start_conflict(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.post_init_error = (409, {"error": "already_running"})
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")

    assert stub.init_posts == [{"sources": ["bilibili"]}]
    chromium_page.wait_for_function(
        "() => document.querySelector('.init-reason')?.innerText.includes('初始化正在进行中')"
    )
    assert chromium_page.locator('[data-init-action="start"]').is_enabled()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("bilibili_not_logged_in", "还没检测到 B 站登录"),
        ("llm_not_ready", "AI 服务还没配好"),
        ("unsupported_runtime", "docker exec -it openbiliclaw-backend openbiliclaw init"),
        ("already_initialized", "已经初始化过了"),
    ],
)
def test_desktop_web_e2e_surfaces_post_init_prereq_race_errors(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
    code: str,
    expected: str,
) -> None:
    base_url, stub = guided_init_server
    stub.post_init_error = (409, {"error": code})
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")

    assert stub.init_posts == [{"sources": ["bilibili"]}]
    chromium_page.wait_for_function(
        "(expected) => document.querySelector('.init-reason')?.innerText.includes(expected)",
        arg=expected,
    )
    assert chromium_page.locator('[data-init-action="start"]').is_enabled()


def test_desktop_web_e2e_retries_status_after_terminal_event_fetch_failure(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    chromium_page.wait_for_function(
        "() => document.querySelector('.init-progress')?.innerText.includes('1/4')"
    )

    stub.fail_next_status = True
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_function("() => document.querySelector('.init-onboarding') === null")


def test_setup_wizard_e2e_resumes_running_init_on_page_load(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """A reload mid-init must land on the live progress, not silently on step 0."""
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)
    stub.set_running()

    chromium_page.goto(f"{base_url}/setup/")

    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgress')?.hidden === false"
    )
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgressLabel')?.innerText.includes('1/4')"
    )
    assert chromium_page.locator("#startInit").is_disabled()
    # Re-attach only observes: no second POST /api/init.
    assert stub.init_posts == []


def test_setup_wizard_e2e_waiting_state_offers_escape_to_web(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """Initialized backend + empty first pool → waiting state with a /web way out."""
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 0})

    chromium_page.goto(f"{base_url}/setup/")

    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgressLabel')?.innerText.includes('整理首轮内容池')"
    )
    escape = chromium_page.locator("#initEscape")
    chromium_page.wait_for_function(
        "() => document.querySelector('#initEscape')?.classList.contains('show')"
    )
    assert escape.locator("a").get_attribute("href") == "/web"
    assert chromium_page.locator("#startInit").is_disabled()
