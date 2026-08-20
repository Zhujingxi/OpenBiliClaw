"""L7: localized Assistant journey through production serve and built Vue assets."""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.e2e.server import DATA_DIR, ROOT, production_server

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l7]

_ASSISTANT_HEADINGS = {"en": "Assistant", "zh-CN": "助手", "zh-TW": "助理"}
_UNSAFE_VISIBLE = re.compile(r"(?:vault:|cred_|conv_[0-9a-f]{16}|msg_[0-9a-f]{16}|\{\s*\")")


def _playwright() -> tuple[Any, Any]:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.fail(
            "Playwright is not installed; install the optional browser extra with "
            'python -m pip install -e ".[browser]"'
        )
    return sync_playwright, Error


def _assert_friendly_tools(cards: Any) -> None:
    for index in range(cards.count()):
        card = cards.nth(index)
        assert card.locator("strong").inner_text().strip()
        assert card.locator(".tool-status-text").inner_text().strip()
        assert not _UNSAFE_VISIBLE.search(card.inner_text())


def test_localized_assistant_stream_hydration_stop_and_mobile_layout() -> None:
    sync_playwright, playwright_error = _playwright()
    frontend = ROOT / "frontend/apps/web/dist"
    screenshot = DATA_DIR / "reports/l7-assistant.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)

    with production_server(  # noqa: SIM117 - fail server prerequisites before Playwright
        log_name="l7-server.log", frontend_dir=frontend
    ) as base_url:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except playwright_error:
                pytest.fail(
                    "Playwright Chromium is missing; run "
                    f"{ROOT / '.venv/bin/python'} -m playwright install chromium"
                )
            context = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
            page = context.new_page()
            console_errors: list[str] = []
            page_errors: list[str] = []
            failed_responses: list[tuple[int, str, str]] = []
            page.on(
                "console",
                lambda message: (
                    console_errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on(
                "response",
                lambda response: (
                    failed_responses.append(
                        (response.status, response.request.method, response.url)
                    )
                    if response.status >= 400
                    else None
                ),
            )

            try:
                page.goto(f"{base_url}/#/assistant", wait_until="networkidle")
                page.get_by_role("heading", name="Assistant", exact=True).wait_for()

                page.get_by_role("button", name="New chat", exact=True).click()
                prompt = "Reply briefly with a safe greeting. Do not use tools."
                composer = page.locator("#assistant-message")
                composer.fill(prompt)
                page.get_by_role("button", name="Send", exact=True).click()
                page.get_by_role("button", name="Stop", exact=True).wait_for(timeout=5_000)
                page.get_by_role("button", name="Stop", exact=True).wait_for(
                    state="hidden", timeout=70_000
                )

                assistant_messages = page.locator(
                    "li.message-assistant:not(.message-failed) .message-content"
                )
                assistant_messages.last.wait_for(timeout=5_000)
                assert assistant_messages.last.inner_text().strip()
                page.get_by_text(re.compile(r"^Context ~\d+%$")).wait_for()
                assert page.locator(".turn-error").count() == 0

                reasoning = page.locator(".reasoning-card")
                if reasoning.count():
                    assert reasoning.locator("summary").inner_text().strip() in {
                        "Reasoning",
                        "Reasoning (live)",
                    }

                live_tools = page.locator(".tool-cards:not(.persisted-tool-cards) .tool-card")
                tool_count = live_tools.count()
                _assert_friendly_tools(live_tools)
                visible = page.locator("body").inner_text()
                assert not _UNSAFE_VISIBLE.search(visible)

                # Switch locales only after the first turn exists so the fresh,
                # unsent conversation has exactly one documented 404 lookup.
                for locale, heading in _ASSISTANT_HEADINGS.items():
                    page.goto(f"{base_url}/#/settings")
                    page.locator("#language").select_option(locale)
                    assert page.locator("html").get_attribute("lang") == locale
                    page.goto(f"{base_url}/#/assistant")
                    page.get_by_role("heading", name=heading, exact=True).wait_for()
                    page.get_by_text(prompt, exact=True).wait_for()
                page.goto(f"{base_url}/#/settings")
                page.locator("#language").select_option("en")
                page.goto(f"{base_url}/#/assistant")

                page.goto(f"{base_url}/#/search")
                page.goto(f"{base_url}/#/assistant")
                page.get_by_text(prompt, exact=True).wait_for()
                hydrated_assistant = page.locator(
                    "li.message-assistant:not(.message-failed) .message-content"
                )
                hydrated_assistant.last.wait_for()
                assert hydrated_assistant.last.inner_text().strip()
                assert page.locator(".reasoning-card").count() == 0
                hydrated_tools = page.locator(".persisted-tool-cards .tool-card")
                assert hydrated_tools.count() == tool_count
                _assert_friendly_tools(hydrated_tools)

                completed_count = hydrated_assistant.count()
                composer.fill("Write a detailed answer about local-first software.")
                page.get_by_role("button", name="Send", exact=True).click()
                stop = page.get_by_role("button", name="Stop", exact=True)
                stop.wait_for(timeout=5_000)
                stop.click()
                page.get_by_role("button", name="Send", exact=True).wait_for()
                assert page.evaluate("document.activeElement?.id") == "assistant-message"
                assert hydrated_assistant.count() == completed_count
                assert page.locator(".turn-error").count() == 0

                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(100)
                overflow = page.evaluate(
                    "Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) "
                    "- window.innerWidth"
                )
                assert overflow <= 0
                mobile_navigation = page.locator("nav.mobile-nav")
                assert mobile_navigation.is_visible()
                assert mobile_navigation.locator("a").count() == 5
                assert (
                    mobile_navigation.locator('a[href="#/assistant"]').get_attribute("aria-current")
                    == "page"
                )
                page.screenshot(path=screenshot, full_page=True)

                unexpected_responses = [
                    item
                    for item in failed_responses
                    if not (
                        item[0] == 404
                        and item[1] == "GET"
                        and "/v1/assistant/conversations/" in item[2]
                    )
                ]
                initial_not_found = [
                    item for item in failed_responses if item not in unexpected_responses
                ]
                assert unexpected_responses == []
                assert len(initial_not_found) == 1
                assert page_errors == []
                assert [
                    message for message in console_errors if "404 (Not Found)" not in message
                ] == []
            finally:
                if not screenshot.exists() and not page.is_closed():
                    page.screenshot(path=screenshot, full_page=True)
                context.close()
                browser.close()
