#!/usr/bin/env python3
"""Render deterministic social preview images from the checked-in HTML source."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/images/social-preview.src.html"
OUTPUTS = {
    "#card-en": ROOT / "docs/images/social-preview-en.png",
    "#card-zh": ROOT / "docs/images/social-preview-zh.png",
}
EXPECTED_SIZE = (1280, 640)


def build() -> list[Path]:
    """Render both localized cards and verify their published dimensions."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(
            viewport={"width": 1360, "height": 1400},
            device_scale_factor=1,
        )
        page.goto(SOURCE.as_uri(), wait_until="load")
        page.wait_for_function(
            """() => [...document.images].every(
              (image) => image.complete && image.naturalWidth > 0
            )"""
        )
        for selector, output in OUTPUTS.items():
            page.locator(selector).screenshot(path=output)
        browser.close()

    for output in OUTPUTS.values():
        with Image.open(output) as image:
            if image.size != EXPECTED_SIZE:
                raise RuntimeError(f"{output}: expected {EXPECTED_SIZE}, got {image.size}")
        print(output.relative_to(ROOT))
    return list(OUTPUTS.values())


if __name__ == "__main__":
    build()
