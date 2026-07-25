import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// The mobile-web entry keeps the sibling icon-button treatment (no bespoke
// colors) but carries a phone glyph + 手机版 label — the label, not color,
// is what makes it discoverable (feedback 2026-07-05).
test("popup mobile entry is a labelled phone button in the shared icon style", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");

  const buttonMarkup =
    popupHtml.match(/<button id="mobileQrButton"[\s\S]*?<\/button>/)?.[0] ?? "";
  assert.ok(buttonMarkup, "popup.html must keep the #mobileQrButton entry");
  // Phone body + speaker line + home dot — not the abstract QR squares.
  assert.match(buttonMarkup, /<rect x="6\.5" y="2" width="11" height="20"/);
  assert.match(buttonMarkup, /<circle/);
  assert.match(buttonMarkup, /mobile-button-label/);
  assert.match(buttonMarkup, /手机版/);
  // Label needs intrinsic width; shared style otherwise (no bespoke bg).
  assert.match(popupHtml, /\.mobile-button\s*\{[^}]*width:\s*auto/);
  const pillBlocks = [...popupHtml.matchAll(/\.mobile-button\s*\{[\s\S]*?\}/g)];
  for (const block of pillBlocks) {
    assert.doesNotMatch(block[0], /background:\s*var\(--brand\)/);
  }
});

test("popup mobile entry still opens the QR overlay wiring", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  assert.match(popupHtml, /id="mobileQrOverlay"/);
  assert.match(popupHtml, /id="mobileQrCode"/);
});
