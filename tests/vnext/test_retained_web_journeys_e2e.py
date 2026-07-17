"""Executable browser coverage for retained Task 22 Web and popup journeys."""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

playwright_sync = pytest.importorskip(
    "playwright.sync_api",
    reason="rendered retained-journey checks require the optional Playwright dependency",
)
Page = playwright_sync.Page
expect = playwright_sync.expect
sync_playwright = playwright_sync.sync_playwright

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src/openbiliclaw/web"
POPUP = ROOT / "extension/popup"
CONTENT_ID = "11111111-1111-4111-8111-111111111111"
CONTENT = {
    "id": CONTENT_ID,
    "source_id": "bilibili",
    "external_id": "BV1TASK22",
    "url": "https://www.bilibili.com/video/BV1TASK22",
    "title": "Task 22 retained journey",
    "summary": "Executable browser contract",
    "creator": "OpenBiliClaw",
    "published_at": None,
    "media_type": "video",
    "metadata": {},
}
FEED_ITEM = {
    "entry": {
        "id": "22222222-2222-4222-8222-222222222222",
        "content_id": CONTENT_ID,
        "assessment_id": None,
        "position": 0,
        "admitted_at": "2026-07-17T00:00:00Z",
        "explanation": "Retained recommendation",
    },
    "content": CONTENT,
}


def _settings(onboarding_complete: bool) -> dict[str, Any]:
    tasks = {
        name: {
            "model_alias": "obc-interactive" if name == "chat" else "obc-analysis",
            "semantic_retry_limit": 1,
            "timeout_seconds": 30,
            "request_limit": 2,
            "total_tokens_limit": 4096,
        }
        for name in ("chat", "profile_projection", "candidate_assessment", "expression_copy")
    }
    return {
        "onboarding_complete": onboarding_complete,
        "sources": {"enabled": {}, "weights": {}},
        "schedules": {"source_sync_interval_minutes": 60},
        "feed": {
            "low_watermark": 10,
            "high_watermark": 50,
            "candidate_multiplier": 2,
            "max_batch_candidates": 50,
            "min_score": 0.4,
            "min_novelty": 0.2,
            "max_per_source": 10,
            "max_per_topic": 10,
        },
        "profile": {"minimum_evidence_confidence": 0.5},
        "tasks": tasks,
        "network": {"mode": "direct", "proxy_url": ""},
        "logging": {"console_level": "INFO", "file_level": "INFO"},
        "access_control": {
            "web_password_enabled": False,
            "trust_loopback": False,
            "session_ttl_hours": 24,
            "extension_access_enabled": True,
            "extension_session_ttl_hours": 1,
            "installer_bearer_configured": False,
            "password_configured": False,
        },
        "jobs": {"retention_days": 30},
    }


@dataclass
class StubState:
    onboarding_complete: bool = True
    onboarding_result: str = "succeeded"
    fail_interaction: bool = False
    fail_library_add: bool = False
    interactions: list[dict[str, Any]] = field(default_factory=list)
    collections: dict[str, set[str]] = field(
        default_factory=lambda: {"favorites": set(), "watch_later": set()}
    )
    requests: list[dict[str, Any]] = field(default_factory=list)


def _json(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    return json.loads(handler.rfile.read(length) or b"{}")


def _library_item(collection: str) -> dict[str, Any]:
    return {
        "collection_item": {
            "id": "33333333-3333-4333-8333-333333333333",
            "collection": collection,
            "content_id": CONTENT_ID,
            "added_at": "2026-07-17T00:00:00Z",
            "note": "",
        },
        "content": CONTENT,
    }


def _wait_until(predicate: Any, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.fixture()
def retained_server() -> tuple[str, StubState]:
    state = StubState()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _static(self, path: str) -> bool:
            roots = {
                "/m/": WEB,
                "/web/": WEB / "desktop",
                "/extension/popup/": POPUP,
            }
            for prefix, root in roots.items():
                if not path.startswith(prefix):
                    continue
                relative = path[len(prefix) :] or "index.html"
                candidate = (root / relative).resolve()
                if root.resolve() not in candidate.parents and candidate != root.resolve():
                    self.send_error(404)
                    return True
                if not candidate.is_file():
                    self.send_error(404)
                    return True
                content = candidate.read_bytes()
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
                )
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return True
            return False

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if self._static(path):
                return
            if path == "/api/v1/auth/status":
                return _json(self, {"enabled": False, "authenticated": True})
            if path == "/api/v1/system/readiness":
                return _json(self, {"ready": True, "version": "test", "checks": []})
            if path == "/api/v1/settings":
                return _json(self, _settings(state.onboarding_complete))
            if path == "/api/v1/system/ai-health":
                return _json(self, {"aliases": [], "admin_url": None})
            if path == "/api/v1/sources":
                return _json(self, [])
            if path == "/api/v1/sources/status":
                return _json(self, [])
            if path == "/api/v1/feed":
                return _json(self, [FEED_ITEM])
            if path.startswith("/api/v1/library/"):
                collection = path.rsplit("/", 1)[-1]
                items = (
                    [_library_item(collection)]
                    if CONTENT_ID in state.collections.get(collection, set())
                    else []
                )
                return _json(self, items)
            if path.startswith("/api/v1/chat/"):
                return _json(
                    self,
                    {
                        "conversation_id": path.rsplit("/", 1)[-1],
                        "items": [],
                        "limit": 100,
                        "offset": 0,
                        "has_more": False,
                    },
                )
            if path.startswith("/api/v1/onboarding/") and path.endswith("/events"):
                result = state.onboarding_result
                if result == "succeeded":
                    state.onboarding_complete = True
                frames = (
                    'event: progress\ndata: {"stage":"source_sync","run":'
                    '{"status":"running","progress":0.5}}\n\n'
                    f'event: done\ndata: {{"status":"{result}"}}\n\n'
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(frames)))
                self.end_headers()
                self.wfile.write(frames)
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            payload = _body(self)
            state.requests.append(
                {"path": path, "body": payload, "auth": self.headers.get("X-OBC-Auth")}
            )
            if path == "/api/v1/interactions":
                if state.fail_interaction:
                    return _json(
                        self,
                        {
                            "error": {
                                "code": "interaction_unavailable",
                                "message": "feedback failed",
                            }
                        },
                        503,
                    )
                state.interactions.append(payload)
                return _json(self, {"signal": {"id": "44444444-4444-4444-8444-444444444444"}}, 201)
            if path.startswith("/api/v1/library/"):
                collection = path.rsplit("/", 1)[-1]
                if state.fail_library_add:
                    return _json(
                        self,
                        {"error": {"code": "library_unavailable", "message": "save failed"}},
                        503,
                    )
                state.collections[collection].add(payload["content_id"])
                return _json(self, _library_item(collection)["collection_item"], 201)
            if path == "/api/v1/onboarding":
                return _json(self, {"id": "55555555-5555-4555-8555-555555555555"}, 202)
            if path == "/api/v1/auth/login":
                return _json(self, {"authenticated": True})
            self.send_error(404)

        def do_DELETE(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            parts = path.split("/")
            if len(parts) == 7 and parts[3] == "library":
                collection, content_id = parts[4], parts[5]
                state.collections[collection].discard(content_id)
                self.send_response(204)
                self.end_headers()
                return
            if path.startswith("/api/v1/library/"):
                collection, content_id = path.removeprefix("/api/v1/library/").split("/", 1)
                state.collections[collection].discard(content_id)
                self.send_response(204)
                self.end_headers()
                return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def browser_page() -> Page:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        yield page
        browser.close()


def _install_popup_chrome(page: Page, base_url: str) -> None:
    port = int(base_url.rsplit(":", 1)[-1])
    page.add_init_script(
        """
        ({port}) => {
          const values = {
            popup_backend_endpoint: {scheme: "http", host: "127.0.0.1", port},
            obc_extension_device_key: "device-key",
            obc_auth_session: {token: "session", expires_at: 2000000000},
          };
          window.chrome = {
            storage: {
              local: {
                get(keys, callback) {
                  const selected = Array.isArray(keys) ? keys : [keys];
                  const entries = selected
                    .filter(key => key in values)
                    .map(key => [key, values[key]]);
                  callback(Object.fromEntries(entries));
                },
                set(items, callback) { Object.assign(values, items); callback?.(); },
                remove(keys, callback) {
                  for (const key of (Array.isArray(keys) ? keys : [keys])) delete values[key];
                  callback?.();
                },
              },
              onChanged: { addListener() {} },
            },
            tabs: { async create() {} },
            permissions: {
              contains(_details, callback) { callback(true); },
              request(_details, callback) { callback(true); },
            },
          };
        }
        """,
        arg={"port": port},
    )


@pytest.mark.parametrize(
    ("surface", "url", "feedback_selector", "save_selector"),
    [
        ("desktop", "/web/", '[data-feedback="positive"]', '[data-save="favorites"]'),
        ("mobile", "/m/#/recommend", '[data-kind="positive"]', '[data-save="favorites"]'),
    ],
)
def test_failed_retained_writes_never_mark_controls_successful(
    retained_server: tuple[str, StubState],
    browser_page: Page,
    surface: str,
    url: str,
    feedback_selector: str,
    save_selector: str,
) -> None:
    base_url, state = retained_server
    state.fail_interaction = True
    state.fail_library_add = True
    browser_page.goto(base_url + url)
    card = browser_page.locator(".video-card" if surface == "desktop" else ".rec-card")
    expect(card).to_have_count(1)

    feedback = card.locator(feedback_selector)
    feedback.click()
    browser_page.wait_for_timeout(100)
    expect(feedback).not_to_have_attribute("aria-pressed", "true")
    assert feedback.evaluate("button => !button.classList.contains('active')")

    save = card.locator(save_selector)
    save.click()
    browser_page.wait_for_timeout(100)
    expect(save).not_to_have_attribute("aria-pressed", "true")
    assert save.evaluate("button => !button.classList.contains('active')")
    assert state.collections["favorites"] == set()


@pytest.mark.parametrize(
    ("url", "card_selector", "tab_selector", "list_selector"),
    [
        ("/web/", ".video-card", "#favoritesBtn", "#favoritesList"),
        ("/m/#/recommend", ".rec-card", '[data-tab="favorites"]', "#mobileLibrary"),
    ],
)
def test_favorites_add_list_and_remove_round_trip(
    retained_server: tuple[str, StubState],
    browser_page: Page,
    url: str,
    card_selector: str,
    tab_selector: str,
    list_selector: str,
) -> None:
    base_url, state = retained_server
    browser_page.goto(base_url + url)
    browser_page.locator(card_selector).locator('[data-save="favorites"]').click()
    _wait_until(lambda: CONTENT_ID in state.collections["favorites"])

    browser_page.locator(tab_selector).click()
    saved = browser_page.locator(list_selector).locator(card_selector)
    expect(saved).to_have_count(1)
    expect(saved).to_contain_text(CONTENT["title"])
    saved.locator("[data-remove]").click()
    _wait_until(lambda: CONTENT_ID not in state.collections["favorites"])


def test_popup_saved_round_trip_and_retry_after_failed_add(
    retained_server: tuple[str, StubState], browser_page: Page
) -> None:
    base_url, state = retained_server
    _install_popup_chrome(browser_page, base_url)
    browser_page.goto(base_url + "/extension/popup/popup.html")
    card = browser_page.locator("#recommendationList .recommendation-card")
    expect(card).to_have_count(1)

    state.fail_library_add = True
    favorite = card.get_by_role("button", name="收藏")
    favorite.click()
    expect(favorite).to_be_enabled()
    assert state.collections["favorites"] == set()

    state.fail_library_add = False
    favorite.click()
    _wait_until(lambda: CONTENT_ID in state.collections["favorites"])
    browser_page.locator("#tabFavorites").click()
    saved = browser_page.locator("#favoritesList .recommendation-card")
    expect(saved).to_have_count(1)
    saved.get_by_role("button", name="移除").click()
    _wait_until(lambda: CONTENT_ID not in state.collections["favorites"])


def test_popup_onboarding_error_is_retryable_and_success_enters_product(
    retained_server: tuple[str, StubState], browser_page: Page
) -> None:
    base_url, state = retained_server
    state.onboarding_complete = False
    state.onboarding_result = "failed"
    _install_popup_chrome(browser_page, base_url)
    browser_page.goto(base_url + "/extension/popup/popup.html")
    start = browser_page.locator("#onboardingStart")
    expect(start).to_be_visible()

    # The stub has no source manifests, so inject one selected source into the
    # real form while leaving submission and SSE handling in production code.
    browser_page.locator("#onboardingSources").evaluate(
        """host => {
          const input = document.createElement('input');
          input.type = 'checkbox';
          input.value = 'bilibili';
          input.checked = true;
          host.append(input);
        }"""
    )
    start.click()
    expect(start).to_be_enabled()
    expect(browser_page.locator("#toast")).to_contain_text("失败")

    state.onboarding_result = "succeeded"
    start.click()
    expect(browser_page.locator(".tabs-shell")).to_be_visible()
    assert state.onboarding_complete is True


def test_ids_used_by_browser_contract_are_valid_uuids() -> None:
    """Keep fixture drift from turning browser failures into schema noise."""

    UUID(CONTENT_ID)
    UUID(FEED_ITEM["entry"]["id"])
