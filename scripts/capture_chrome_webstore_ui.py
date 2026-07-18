"""Capture current OpenBiliClaw UI with deterministic local demo data."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from chrome_webstore_demo import DemoServer
from playwright.sync_api import BrowserContext, Page, Route, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = ROOT / "extension"
EXPECTED = (
    "desktop-recommend.png",
    "desktop-settings.png",
    "mobile-recommend.png",
    "extension-recommend.png",
)


def _extension_browser_executable() -> str:
    candidates = sorted(
        Path.home().glob(
            "Library/Caches/ms-playwright/chromium-*/chrome-mac-x64/"
            "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        ),
        reverse=True,
    )
    candidates.extend(
        [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    fallback = shutil.which("google-chrome") or shutil.which("chromium")
    if fallback:
        return fallback
    raise FileNotFoundError("Chrome/Chromium executable not found")


def _install_loopback_guard(context: BrowserContext, blocked: list[str]) -> None:
    def guard(route: Route) -> None:
        parsed = urlsplit(route.request.url)
        if parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
        }:
            blocked.append(route.request.url)
            route.abort("blockedbyclient")
            return
        route.continue_()

    context.route("**/*", guard)


def _prepare_page(page: Page) -> None:
    page.add_init_script(
        """
        localStorage.setItem("obc.theme", "light");
        window.WebSocket = class DemoWebSocket {
          static OPEN = 1;
          constructor() { this.readyState = 1; }
          addEventListener() {}
          removeEventListener() {}
          send() {}
          close() { this.readyState = 3; }
        };
        """
    )


def _wait_for_covers(page: Page, selector: str, minimum: int) -> None:
    page.wait_for_function(
        """({selector, minimum}) => {
          const images = [...document.querySelectorAll(selector)];
          return images.length >= minimum
            && images.every((image) => image.complete && image.naturalWidth > 0);
        }""",
        arg={"selector": selector, "minimum": minimum},
        timeout=15_000,
    )


def _capture_web(origin: str, output_dir: Path, blocked: list[str]) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1
        )
        _install_loopback_guard(context, blocked)
        page = context.new_page()
        _prepare_page(page)
        page.goto(f"{origin}/web/", wait_until="domcontentloaded")
        page.wait_for_function(
            "document.querySelectorAll('#videoGrid .video-card:not(.is-skeleton)').length >= 3",
            timeout=15_000,
        )
        _wait_for_covers(page, "#videoGrid .video-card .cover img", 3)
        _wait_for_covers(page, "#delightThumb img", 1)
        page.screenshot(path=output_dir / "desktop-recommend.png")

        page.locator("#settingsBtn").click()
        page.locator('[data-settings-tab="sources"]').click()
        page.locator('[data-settings-panel="sources"]:not([hidden])').wait_for(state="visible")
        page.wait_for_function(
            "document.querySelector('[data-source-status=reddit] .src-detail')?.textContent.includes('Reddit')"
        )
        page.screenshot(path=output_dir / "desktop-settings.png")
        context.close()
        browser.close()

        mobile = playwright.chromium.launch(channel="chrome", headless=True)
        mobile_context = mobile.new_context(
            viewport={"width": 430, "height": 900},
            device_scale_factor=1,
            is_mobile=True,
            has_touch=True,
        )
        _install_loopback_guard(mobile_context, blocked)
        mobile_page = mobile_context.new_page()
        _prepare_page(mobile_page)
        mobile_page.goto(f"{origin}/m/", wait_until="domcontentloaded")
        mobile_page.wait_for_function(
            "document.querySelectorAll('#app .card').length >= 1",
            timeout=15_000,
        )
        _wait_for_covers(mobile_page, "#app .card-cover-frame img.card-cover", 2)
        mobile_page.screenshot(path=output_dir / "mobile-recommend.png")
        mobile_context.close()
        mobile.close()


def _capture_extension(origin: str, output_dir: Path, blocked: list[str]) -> None:
    service_worker = EXTENSION_ROOT / "dist/background/service-worker.js"
    popup = EXTENSION_ROOT / "popup/popup.html"
    if not service_worker.exists() or not popup.exists():
        raise FileNotFoundError("extension build missing; run `cd extension && npm run build`")

    parsed = urlsplit(origin)
    if parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise RuntimeError(f"refusing non-demo backend origin: {origin}")

    with (
        tempfile.TemporaryDirectory(prefix="obc-store-capture-") as profile_dir,
        sync_playwright() as playwright,
    ):
        context = playwright.chromium.launch_persistent_context(
            profile_dir,
            executable_path=_extension_browser_executable(),
            headless=False,
            args=[
                f"--disable-extensions-except={EXTENSION_ROOT}",
                f"--load-extension={EXTENSION_ROOT}",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=560,940",
            ],
            viewport={"width": 520, "height": 860},
        )
        _install_loopback_guard(context, blocked)
        workers = [
            worker
            for worker in context.service_workers
            if "/dist/background/service-worker.js" in worker.url
        ]
        worker = workers[0] if workers else context.wait_for_event("serviceworker", timeout=15_000)
        extension_id = worker.evaluate("chrome.runtime.id")
        worker.evaluate(
            """async ({host, port}) => {
              await chrome.storage.local.set({
                popup_backend_endpoint: {scheme: "http", host, port}
              });
            }""",
            {"host": parsed.hostname, "port": parsed.port},
        )
        page = context.new_page()
        page.set_viewport_size({"width": 520, "height": 860})
        page.goto(
            f"chrome-extension://{extension_id}/popup/popup.html",
            wait_until="domcontentloaded",
        )
        page.locator("#recommendationList .recommendation-card").first.wait_for(
            state="visible",
            timeout=20_000,
        )
        _wait_for_covers(page, "#recommendationList .recommendation-cover img", 2)
        page.screenshot(path=output_dir / "extension-recommend.png")
        context.close()


def capture(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_names = set(EXPECTED)
    for stale in output_dir.glob("*.png"):
        if stale.name not in expected_names:
            stale.unlink()
    blocked: list[str] = []
    with DemoServer() as origin:
        _capture_web(origin, output_dir, blocked)
        _capture_extension(origin, output_dir, blocked)
    outputs = [output_dir / name for name in EXPECTED]
    missing = [str(path) for path in outputs if not path.exists()]
    if missing:
        raise RuntimeError(f"capture did not produce expected files: {missing}")
    if any(urlsplit(url).hostname in {"127.0.0.1", "localhost"} for url in blocked):
        raise RuntimeError("loopback request was unexpectedly blocked")
    print(f"Captured {len(outputs)} sanitized UI images; blocked {len(blocked)} external requests.")
    for path in outputs:
        print(path)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "docs/images/chrome-web-store/source",
    )
    args = parser.parse_args()
    capture(args.output_dir.resolve())


if __name__ == "__main__":
    main()
