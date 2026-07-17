"""Executable browser coverage for retained Task 22 Web and popup journeys."""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
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
    auth_enabled: bool = False
    authenticated: bool = True
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


_STATIC_ROOTS = {
    "/m/": WEB,
    "/web/": WEB / "desktop",
    "/setup/": WEB / "setup",
    "/extension/popup/": POPUP,
}
_NOT_FOUND = object()


def _get_payload(path: str, state: StubState) -> object:
    if path == "/api/v1/auth/status":
        return {"enabled": state.auth_enabled, "authenticated": state.authenticated}
    if path == "/api/v1/system/readiness":
        return {"ready": True, "version": "test", "checks": []}
    if path == "/api/v1/settings":
        return _settings(state.onboarding_complete)
    if path == "/api/v1/system/ai-health":
        return {
            "aliases": [
                {"alias": alias, "state": "available", "available": True, "reason": None}
                for alias in ("obc-interactive", "obc-analysis", "obc-embedding")
            ],
            "admin_url": None,
        }
    if path == "/api/v1/sources":
        return [
            {
                "source_id": "bilibili",
                "display_name": "Bilibili",
                "capabilities": ["history"],
            }
        ]
    if path == "/api/v1/sources/status":
        return []
    if path == "/api/v1/feed":
        return [FEED_ITEM]
    if path.startswith("/api/v1/library/"):
        collection = path.rsplit("/", 1)[-1]
        return (
            [_library_item(collection)]
            if CONTENT_ID in state.collections.get(collection, set())
            else []
        )
    if path.startswith("/api/v1/chat/"):
        return {
            "conversation_id": path.rsplit("/", 1)[-1],
            "items": [],
            "limit": 100,
            "offset": 0,
            "has_more": False,
        }
    return _NOT_FOUND


class _RetainedServer(ThreadingHTTPServer):
    state: StubState


class _RetainedHandler(BaseHTTPRequestHandler):
    @property
    def state(self) -> StubState:
        return cast("_RetainedServer", self.server).state

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _static(self, path: str) -> bool:
        for prefix, root in _STATIC_ROOTS.items():
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
        payload = _get_payload(path, self.state)
        if payload is not _NOT_FOUND:
            _json(self, payload)
            return
        if path.startswith("/api/v1/onboarding/") and path.endswith("/events"):
            self._onboarding_events()
            return
        self.send_error(404)

    def _onboarding_events(self) -> None:
        result = self.state.onboarding_result
        if result == "succeeded":
            self.state.onboarding_complete = True
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

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        payload = _body(self)
        self.state.requests.append(
            {"path": path, "body": payload, "auth": self.headers.get("X-OBC-Auth")}
        )
        if path == "/api/v1/interactions":
            self._interaction(payload)
            return
        if path.startswith("/api/v1/library/"):
            self._library_add(path, payload)
            return
        if path == "/api/v1/onboarding/start":
            _json(self, {"id": "55555555-5555-4555-8555-555555555555"}, 202)
            return
        if path == "/api/v1/auth/login":
            self._login(payload)
            return
        self.send_error(404)

    def _interaction(self, payload: dict[str, Any]) -> None:
        if self.state.fail_interaction:
            _json(
                self,
                {"error": {"code": "interaction_unavailable", "message": "feedback failed"}},
                503,
            )
            return
        self.state.interactions.append(payload)
        _json(self, {"signal": {"id": "44444444-4444-4444-8444-444444444444"}}, 201)

    def _library_add(self, path: str, payload: dict[str, Any]) -> None:
        collection = path.rsplit("/", 1)[-1]
        if self.state.fail_library_add:
            _json(
                self,
                {"error": {"code": "library_unavailable", "message": "save failed"}},
                503,
            )
            return
        if payload["content_id"] in self.state.collections[collection]:
            _json(
                self,
                {"error": {"code": "already_saved", "message": "already saved"}},
                409,
            )
            return
        self.state.collections[collection].add(payload["content_id"])
        _json(self, _library_item(collection)["collection_item"], 201)

    def _login(self, payload: dict[str, Any]) -> None:
        if payload.get("password") != "correct horse":
            _json(
                self,
                {"error": {"code": "invalid_password", "message": "密码不正确"}},
                401,
            )
            return
        self.state.authenticated = True
        _json(self, {"authenticated": True})

    def do_DELETE(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/v1/library/"):
            collection, content_id = path.removeprefix("/api/v1/library/").split("/", 1)
            self.state.collections[collection].discard(content_id)
            self.send_response(204)
            self.end_headers()
            return
        self.send_error(404)


@pytest.fixture()
def retained_server() -> tuple[str, StubState]:
    state = StubState()
    server = _RetainedServer(("127.0.0.1", 0), _RetainedHandler)
    server.state = state
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
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        yield page
        browser.close()


def _install_popup_chrome(page: Page, base_url: str) -> None:
    port = int(base_url.rsplit(":", 1)[-1])
    page.add_init_script(
        script=f"""
        (() => {{
          const port = {json.dumps(port)};
          const values = {{
            popup_backend_endpoint: {{scheme: "http", host: "127.0.0.1", port}},
            obc_extension_device_key: "device-key",
            obc_auth_session: {{token: "session", expires_at: 2000000000}},
          }};
          window.chrome = {{
            storage: {{
              local: {{
                get(keys, callback) {{
                  const selected = Array.isArray(keys) ? keys : [keys];
                  const entries = selected
                    .filter(key => key in values)
                    .map(key => [key, values[key]]);
                  callback(Object.fromEntries(entries));
                }},
                set(items, callback) {{ Object.assign(values, items); callback?.(); }},
                remove(keys, callback) {{
                  for (const key of (Array.isArray(keys) ? keys : [keys])) delete values[key];
                  callback?.();
                }},
              }},
              onChanged: {{ addListener() {{}} }},
            }},
            tabs: {{ async create() {{}} }},
            permissions: {{
              contains(_details, callback) {{ callback(true); }},
              request(_details, callback) {{ callback(true); }},
            }},
          }};
        }})()
        """,
    )


@pytest.mark.parametrize(
    ("surface", "url", "feedback_selector", "collection"),
    [
        ("desktop", "/web/", '[data-feedback="positive"]', "favorites"),
        ("desktop", "/web/", '[data-feedback="positive"]', "watch_later"),
        ("mobile", "/m/#/recommend", '[data-kind="positive"]', "favorites"),
        ("mobile", "/m/#/recommend", '[data-kind="positive"]', "watch_later"),
    ],
)
def test_failed_writes_and_partial_saves_keep_truthful_retryable_state(
    retained_server: tuple[str, StubState],
    browser_page: Page,
    surface: str,
    url: str,
    feedback_selector: str,
    collection: str,
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

    save = card.locator(f'[data-save="{collection}"]')
    save.click()
    expect(save).to_be_enabled()
    expect(save).not_to_have_attribute("aria-pressed", "true")
    assert save.evaluate("button => !button.classList.contains('active')")
    assert state.collections[collection] == set()

    state.fail_library_add = False
    save.click()
    _wait_until(lambda: CONTENT_ID in state.collections[collection])
    expect(save).to_have_attribute("aria-pressed", "true")
    expect(save).to_have_attribute("data-library-persisted", "true")
    expect(save).to_have_attribute("data-interaction-pending", "true")
    add_count = sum(
        request["path"] == f"/api/v1/library/{collection}" for request in state.requests
    )

    state.fail_interaction = False
    save.click()
    interaction_kind = "save_favorite" if collection == "favorites" else "save_watch_later"
    _wait_until(lambda: any(item["kind"] == interaction_kind for item in state.interactions))
    expect(save).not_to_have_attribute("data-interaction-pending", "true")
    expect(save).to_be_disabled()
    assert (
        sum(request["path"] == f"/api/v1/library/{collection}" for request in state.requests)
        == add_count
    )
    interaction_count = len(state.interactions)
    save.evaluate("button => button.click()")
    browser_page.wait_for_timeout(50)
    assert len(state.interactions) == interaction_count

    browser_page.reload()
    rerendered = browser_page.locator(
        ".video-card" if surface == "desktop" else ".rec-card"
    ).locator(f'[data-save="{collection}"]')
    rerendered.click()
    expect(rerendered).to_have_attribute("aria-pressed", "true")
    expect(rerendered).to_be_disabled()
    assert len(state.interactions) == interaction_count


@pytest.mark.parametrize(
    ("url", "card_selector", "collection", "tab_selector", "list_selector"),
    [
        ("/web/", ".video-card", "favorites", "#favoritesBtn", "#favoritesList"),
        ("/web/", ".video-card", "watch_later", "#watchLaterBtn", "#watchLaterList"),
        ("/m/#/recommend", ".rec-card", "favorites", '[data-tab="favorites"]', "#mobileLibrary"),
        ("/m/#/recommend", ".rec-card", "watch_later", '[data-tab="watchLater"]', "#mobileLibrary"),
    ],
)
def test_library_add_list_and_remove_round_trip(
    retained_server: tuple[str, StubState],
    browser_page: Page,
    url: str,
    card_selector: str,
    collection: str,
    tab_selector: str,
    list_selector: str,
) -> None:
    base_url, state = retained_server
    browser_page.goto(base_url + url)
    browser_page.locator(card_selector).locator(f'[data-save="{collection}"]').click()
    _wait_until(lambda: CONTENT_ID in state.collections[collection])

    if url == "/web/":
        browser_page.locator("#sideDrawerBtn").click()
        expect(browser_page.locator("#sideDrawer")).to_have_attribute("aria-hidden", "false")
    browser_page.locator(tab_selector).click()
    saved = browser_page.locator(list_selector).locator(card_selector)
    expect(saved).to_have_count(1)
    expect(saved).to_contain_text(CONTENT["title"])
    saved.locator("[data-remove]").click()
    _wait_until(lambda: CONTENT_ID not in state.collections[collection])


@pytest.mark.parametrize(
    ("collection", "button_name", "tab_selector", "list_selector", "interaction_kind"),
    [
        ("favorites", "收藏", "#tabFavorites", "#favoritesList", "save_favorite"),
        ("watch_later", "稍后看", "#tabWatchLater", "#watchLaterList", "save_watch_later"),
    ],
)
def test_popup_saved_round_trip_and_retry_after_failed_add(
    retained_server: tuple[str, StubState],
    browser_page: Page,
    collection: str,
    button_name: str,
    tab_selector: str,
    list_selector: str,
    interaction_kind: str,
) -> None:
    base_url, state = retained_server
    _install_popup_chrome(browser_page, base_url)
    browser_page.goto(base_url + "/extension/popup/popup.html")
    card = browser_page.locator("#recommendationList .recommendation-card")
    expect(card).to_have_count(1)

    state.fail_library_add = True
    save = card.get_by_role("button", name=button_name)
    save.click()
    expect(save).to_be_enabled()
    assert state.collections[collection] == set()

    state.fail_library_add = False
    state.fail_interaction = True
    save.click()
    _wait_until(lambda: CONTENT_ID in state.collections[collection])
    expect(save).to_have_attribute("aria-pressed", "true")
    expect(save).to_have_attribute("data-interaction-pending", "true")
    add_count = sum(
        request["path"] == f"/api/v1/library/{collection}" for request in state.requests
    )

    state.fail_interaction = False
    save.click()
    _wait_until(lambda: any(item["kind"] == interaction_kind for item in state.interactions))
    expect(save).to_be_disabled()
    interaction_count = len(state.interactions)
    assert (
        sum(request["path"] == f"/api/v1/library/{collection}" for request in state.requests)
        == add_count
    )
    save.evaluate("button => button.click()")
    browser_page.wait_for_timeout(50)
    assert len(state.interactions) == interaction_count

    browser_page.locator("#refreshRecommendationsButton").click()
    rerendered = browser_page.locator("#recommendationList .recommendation-card").get_by_role(
        "button", name=button_name
    )
    rerendered.click()
    expect(rerendered).to_be_disabled()
    assert len(state.interactions) == interaction_count

    browser_page.locator(tab_selector).click()
    saved = browser_page.locator(f"{list_selector} .recommendation-card")
    expect(saved).to_have_count(1)
    saved.get_by_role("button", name="移除").click()
    _wait_until(lambda: CONTENT_ID not in state.collections[collection])


def test_setup_onboarding_error_is_retryable_and_success_reaches_ready_step(
    retained_server: tuple[str, StubState], browser_page: Page
) -> None:
    base_url, state = retained_server
    state.onboarding_complete = False
    state.onboarding_result = "failed"
    browser_page.goto(base_url + "/setup/")
    next_ai = browser_page.locator("#nextAi")
    expect(next_ai).to_be_enabled()
    next_ai.click()
    browser_page.locator("#next1").click()
    expect(browser_page.locator("#initSources input:checked")).to_have_count(1)

    start = browser_page.locator("#startInit")
    start.click()
    expect(start).to_be_enabled()
    expect(browser_page.locator("#initProgressLabel")).to_have_text("初始化失败")

    state.onboarding_result = "succeeded"
    start.click()
    expect(browser_page.locator('[data-panel="3"]')).to_have_class("panel active")
    expect(browser_page.locator("#runMessage")).to_contain_text("初始化完成")


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


@pytest.mark.parametrize(
    ("url", "gate_selector", "password_selector", "submit_selector", "error_selector"),
    [
        ("/web/", "#loginGate", "#loginPassword", "#loginForm button", "#loginError"),
        (
            "/m/#/recommend",
            ".login-view",
            "#mobilePassword",
            "#mobileLogin button",
            "#mobileLoginError",
        ),
    ],
)
def test_password_login_recovers_the_retained_surface(
    retained_server: tuple[str, StubState],
    browser_page: Page,
    url: str,
    gate_selector: str,
    password_selector: str,
    submit_selector: str,
    error_selector: str,
) -> None:
    base_url, state = retained_server
    state.auth_enabled = True
    state.authenticated = False
    browser_page.goto(base_url + url)
    expect(browser_page.locator(gate_selector)).to_be_visible()

    browser_page.locator(password_selector).fill("wrong")
    browser_page.locator(submit_selector).click()
    expect(browser_page.locator(error_selector)).to_contain_text("密码不正确")

    browser_page.locator(password_selector).fill("correct horse")
    browser_page.locator(submit_selector).click()
    expect(browser_page.locator(".video-card" if url == "/web/" else ".rec-card")).to_have_count(1)
    assert state.authenticated is True


def test_ids_used_by_browser_contract_are_valid_uuids() -> None:
    """Keep fixture drift from turning browser failures into schema noise."""

    UUID(CONTENT_ID)
    UUID(FEED_ITEM["entry"]["id"])
