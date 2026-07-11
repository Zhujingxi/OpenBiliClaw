# Chrome Web Store Screenshot Refresh V2 Design

**Date:** 2026-07-12

## Goal

Replace the current five pale, copy-heavy Chrome Web Store images with three
clear 1280×800 product screenshots. Every recommendation shown in the store
assets must have a visible content cover, including the delight hero.

## Visual Direction

- Use locally generated, editorial-style 16:9 covers for sanitized demo
  content. Covers should feel like real videos or posts without copying an
  identifiable creator, platform asset, trademarked artwork, or user data.
- Render covers through the real desktop, extension, and mobile UI data path.
  Do not paste covers over screenshots during composition.
- Make the product UI the visual subject. Use one short headline per asset,
  strong contrast, fewer decorative panels, and readable crops.
- Use the real OpenBiliClaw extension icon. Remove the synthetic `B` mark and
  the inaccurate `openbiliclaw.com` footer.
- Keep all capture traffic on loopback. External platform requests remain
  blocked.

## Asset Set

### 01 — Seven-platform recommendation hero

- One large desktop recommendation view with visible card covers and a
  populated delight hero cover.
- Copy: `七平台内容推荐，数据默认留在本机`.
- A compact seven-platform label row may remain, but no separate privacy box or
  long feature list.

### 02 — Three surfaces

- Show PC, extension, and mobile recommendations using the same sanitized
  content set.
- Crop each surface around its recommendation cards, covers, and feedback
  controls. Avoid showing large navigation or empty areas.
- Copy: `PC、插件、手机，一套推荐体验`.

### 03 — Truthful connection status

- Use a close crop of the real settings page showing the distinction between
  source enablement and connection status.
- Copy: `登录状态说人话，数据默认在本机`.
- Keep only the minimum legend needed to explain `凭据已就绪`, `状态待验证`,
  and `无需登录`.

## Demo Data and Covers

- Add one local cover for each of the seven sanitized recommendation topics:
  system design, research workflow, cognitive science, local-first software,
  recommendation systems, knowledge-base data flow, and agent memory.
- Add one local delight hero item and cover so the large hero area never shows
  an empty gradient placeholder.
- The demo server exposes cover files from a dedicated loopback-only asset
  route and includes those URLs in recommendation and delight payloads.
- No production API behavior, user database, `config.toml`, Cookie, or external
  content is used.

## Composition

- Retain deterministic PIL-based 1280×800 composition.
- Reduce headings and chrome so source UI remains legible at Chrome Web Store
  thumbnail sizes.
- Prefer one dominant screenshot per slide. The three-surface slide is the only
  approved multi-frame composition.
- Use a higher-contrast neutral background; avoid pale UI on an equally pale
  canvas.

## Verification

- Demo tests require all seven recommendations and the delight hero to have
  loopback-local cover URLs.
- Capture blocks every non-loopback request and reports zero permitted external
  traffic.
- Asset tests require exactly three ordered PNG files, each RGB/RGBA at
  1280×800.
- Visual verification checks that covers are visible, crops contain no empty
  media blocks, text is readable, and no private or external data appears.

## Out of Scope

- No Chrome Web Store upload automation; screenshots still require Developer
  Dashboard upload.
- No extension runtime or production recommendation changes.
- No reuse of real platform thumbnails or copyrighted creator artwork.
- No separate profile screenshot, duplicate recommendation explainer, ratings,
  testimonials, QR code, or additional marketing panels.
