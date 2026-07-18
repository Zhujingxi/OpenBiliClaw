# Douyin Bootstrap Import Design

## Goal

Extend OpenBiliClaw's first-run initialization so the soul-engine pipeline gets fed by Douyin (douyin.com) account signals in addition to the existing Bilibili and Xiaohongshu sources. Douyin data must be collected by the browser extension inside the user's logged-in Douyin web session, not by backend crawling and not by reading Chrome history. The runtime should never need to compute X-Bogus / msToken / `_signature` payloads itself — we observe what Douyin's own JS already signed.

## Scope

The first version imports four Douyin signal scopes, in confidence order:

- `dy_post`: videos the user has posted (always reachable on `/user/<sec_uid>`)
- `dy_collect`: videos in the favorites tab (`/user/self?showTab=favorite_collection`) — confirmed self-cookie-only by jiji262/douyin-downloader, which is exactly our use case
- `dy_like`: videos in the "喜欢过" tab — works for self-cookie always; may be empty if the user kept their like history private (we log + skip silently in that case)
- `dy_follow`: creators the user follows — analogous to bilibili `following`

`dy_history` (browse history equivalent) is **out of scope for v1**. The f2 library exposes a `fetch_user_history_read` endpoint, but Douyin web has no documented UI surface that scrolls watch history. A 1-day spike (see §7 Risks) decides whether v2 can ship it via direct in-tab fetch.

All four scopes are best-effort. If Douyin hides a tab, changes its DOM/state shape, or redirects to login/risk pages, initialization continues with the remaining Bilibili / XHS / Douyin signals.

## Architecture

The backend owns orchestration and profile building. The extension owns Douyin page access. The split mirrors the existing XHS pipeline 1:1, with one important deviation in the data-capture layer.

1. `openbiliclaw init` runs the existing Bilibili + XHS bootstrap.
2. The backend enqueues a `dy_tasks` row of type `bootstrap_profile` (separate table from `xhs_tasks` to keep per-platform task state isolated).
3. The extension's `dy-task-dispatcher.ts` opens an **active** Douyin tab in the user's browser. Init-time bootstrap is foreground for the same two reasons as XHS: transparency for the user running `init`, and Douyin's virtualised list paginates only when the tab is active.
4. The content script reads `<script id="RENDER_DATA">` from the page (URL-encoded JSON; React-based, no Vue ref-unwrap), pulls `sec_uid` and the user's profile root, then navigates the same tab to each scope's URL.
5. **Data capture (the divergence from XHS)**: the content script injects a MAIN-world `dy-fetch-tap.ts` script that wraps `window.fetch` and `XMLHttpRequest.prototype.send`. Whenever Douyin's React app fires `/aweme/v1/web/aweme/{post,favorite,collection,like,...}/`, we clone the response body and `postMessage` it back to the isolated content script. **We never re-issue these requests ourselves**, so we never need to compute X-Bogus / msToken / `_signature` — Douyin's own `webmssdk.js` already signed the outgoing call before our hook saw it. (Verified 2026-05-07 via chrome-devtools MCP: signing lives in URL params `a_bogus` / `verifyFp` / `fp` / `uifid` / `webid` and in headers `uifid` / `x-secsdk-csrf-token`, all auto-applied by `window.byted_acrawler`.) This sidesteps the entire anti-bot maintenance burden that hits pure-HTTP scrapers (yt-dlp issue #9667 etc.).

   **Critical injection-timing detail (verified empirically, do not skip)**: Douyin's page bundle re-wraps `window.fetch` *after* `document_start`. A naive `chrome.scripting.executeScript({world: "MAIN", runAt: "document_start"})` install gets shadowed by the page bundle's later wrapper and captures **zero** responses. The fix: defer hook installation until `window.byted_acrawler` (the SDK sentinel) appears, then wrap the bundle's already-installed wrapper. This preserves both their signing (which lives inside their wrapper) and our observation (which is the outermost layer). See Task 3 implementation notes in the plan doc for the polling shape.
6. The scroll executor (`dy/task-executor.ts`) drives pagination by scrolling the active tab. Stagnant-round detection is **Douyin-native**, not borrowed from XHS: a round counts as stagnant when **no new `/aweme/v1/web/aweme/<scope>/` JSON page arrived from the fetch-tap since the last scroll**, with `MAX_STAGNANT_ROUNDS = 5` before bailing. This is a strictly more reliable signal than XHS's DOM-card-count delta, because it doesn't rely on the virtual list keeping cards in the DOM. The XHS helper `bootstrapScrollShouldContinue` is **deliberately not reused** — different data-capture models warrant different stagnant signals, and sharing the helper would create a coupling point that nobody owns when the two platforms diverge over time.
7. The extension posts task results to the backend via the standard `/api/xhs/...`-style endpoint pattern (renamed `/api/dy/...`).
8. The backend converts returned items into event-layer payloads:
   - `dy_collect` → `favorite`
   - `dy_like` → `like`
   - `dy_post` → `view` (with `signal_strength=0.4`; the user posting it isn't a strong taste signal but is still one)
   - `dy_follow` → `follow` (event type to be added to `event_format.py`; XHS doesn't currently use it)
9. `init` analyzes the combined Bilibili + XHS + Douyin event batch through the same preference / awareness / soul layers.

This keeps the existing rule: Douyin content enters through the extension; the backend never directly logs into or crawls Douyin.

## Data Shape

Extension task result (mirrors XHS shape; `notes` → `videos`):

```json
{
  "task_id": "uuid",
  "status": "ok",
  "videos": [
    {
      "url": "https://www.douyin.com/video/<aweme_id>",
      "title": "视频描述/标题",
      "author": "作者昵称",
      "author_sec_uid": "...",
      "cover_url": "https://...",
      "scope": "dy_collect",
      "aweme_id": "...",
      "duration_ms": 18500
    }
  ],
  "scope_counts": {
    "dy_post": 12,
    "dy_collect": 47,
    "dy_like": 86,
    "dy_follow": 23
  }
}
```

Backend event conversion:

```json
{
  "event_type": "favorite",
  "title": "视频描述/标题",
  "url": "https://www.douyin.com/video/<aweme_id>",
  "metadata": {
    "source_platform": "douyin",
    "aweme_id": "...",
    "author_sec_uid": "...",
    "author": "作者昵称",
    "import_source": "dy_bootstrap_collect",
    "signal_strength": 1.0
  }
}
```

Signal strengths (matched to XHS conventions where possible):

- `dy_collect`: **1.0** (most deliberate)
- `dy_like`: **0.85**
- `dy_follow`: **0.6** (interest in a creator's catalog as a whole)
- `dy_post`: **0.4** (weak taste signal — user posted it, may or may not reflect what they consume)

## Module Isolation from XHS

This is a deliberate design rule, not an accident:

- **Douyin code lives in its own tree.** `src/openbiliclaw/sources/dy_*.py`, `extension/src/content/dy/`, `extension/src/main/dy-fetch-tap.ts`, `extension/src/background/dy-task-dispatcher.ts`. **No imports from `xhs_*` or `xhs/`** — not even "obviously generic" helpers like scroll-round logic. As demonstrated above, the stagnant-round signal Douyin needs is fundamentally different from XHS's, so what looks reusable usually isn't.
- **Database isolation.** Separate `dy_tasks` table; XHS budget exhaustion never blocks Douyin and vice versa.
- **API isolation.** Separate `/api/sources/dy/*` endpoints. No shared dispatcher route.

The **one** intentional shared layer is the **event taxonomy in `src/openbiliclaw/sources/event_format.py`**. Douyin events emit `event_type` values from the same vocabulary (`favorite`, `like`, `view`, `follow`) and `source_platform="douyin"`, because the soul-engine pipeline must be able to reason across all three sources uniformly. This isn't "code reuse" — it's the cross-source contract that makes the whole multi-source profile work. If we per-platform-typed events, soul-engine would degenerate into three siloed analyses, defeating the architecture.

## Open-Source Prior Art and What We Reuse

Per research, no existing browser extension or userscript exports a Douyin user's own behavior data. All Greasyfork extant scripts are video-downloaders. We are first to ship this use case via extension. The Python projects below are reference-only — we deliberately do **not** consume their HTTP+signing runtime code, since piggybacking on `webmssdk.js` from inside an extension makes that code unnecessary and would re-introduce the maintenance burden we're trying to avoid.

| Project | License | Reuse target |
|---|---|---|
| [Johnserf-Seed/f2](https://github.com/Johnserf-Seed/f2) | Apache-2.0 | URL / endpoint catalog (`fetch_user_post`, `fetch_user_collect`, `fetch_user_history_read`). Crucial reference for which paths Douyin's React app calls — we listen for the same paths in our fetch-tap. |
| [jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader) | MIT | Confirms `collect/collectmix` self-cookie semantics. Not consuming code; using its README as authoritative on which scopes are accessible to a logged-in user. |
| [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) | Apache-2.0 | `RENDER_DATA` script-tag format reference. |
| [erma0/douyin](https://github.com/erma0/douyin) | (n/a) | Cross-check of endpoint coverage. |
| [5ime/Tiktok_Signature](https://github.com/5ime/Tiktok_Signature) | (n/a) | **NOT used.** Listed only to document that we considered the signature-generator path and rejected it. |

Verdict: no upstream library can be `pip install`'d and dropped in. The wins from prior art are pure documentation: knowing which `/aweme/v1/web/aweme/<scope>/` paths to listen for, and which scopes work for self-cookie. Implementation is fresh code structured as a `dy/` mirror of the existing `xhs/` modules.

## User Experience

`openbiliclaw init` reports a new data-fetch line (replaces the existing two-source line):

```text
1/4 拉取数据
  B站   浏览历史 300 条 / 收藏 128 个 / 关注 43 人
  小红书 收藏 47 个 / 点赞 86 个 / 浏览记录 0 个(未暴露)
  抖音   收藏 47 个 / 点赞 86 个 / 关注 23 人 / 发布 12 条
```

If the extension is not running or Douyin is not logged in, init continues with whatever sources did work, in line with the existing XHS-not-logged-in messaging.

## Risks

1. **Watch history (`dy_history`) is unverified on web.** No documented UI surface. f2 has the API but it may be app-only or require headers we can't synthesize from a content script. **Mitigation**: out of scope for v1. v2 spike (1 person-day) decides whether to ship.
2. **Like-tab privacy toggle.** Some users hide their "喜欢过" page. Self-cookie always works *in principle*, but I could not find authoritative 2025-2026 documentation that confirms the per-user privacy toggle is still surfaced. **Mitigation**: scope is best-effort; empty result triggers `status=ok scope_counts.dy_like=0` and a logged-only warning, not a hard failure.
3. **`webmssdk.js` rotation.** Douyin can change the SDK URL or the signing algorithm. Pure-HTTP scrapers eat this constantly; *we don't*, because we never call the signer ourselves. The only thing that breaks for us is the SDK loading reliably in the user's tab — which would also break douyin.com normal browsing for the user, so it's not a long-term risk. **Mitigation**: none needed beyond observability — log which `/aweme/v1/...` paths fired in each scope.
4. **Risk-control / captcha during foreground tab nav.** Suddenly clicking through 4 tab switches in 30s in an automated way may trigger captcha. **Mitigation**: own per-tab settle delay constant `DY_TAB_SETTLE_MS` (~1.5–2s) — chosen independently from XHS's `XHS_TAB_SETTLE_MS`, even though both happen to land on the same value. If captcha rate differs between platforms later, Douyin can tune its own value.
5. **Endpoint shape drift.** `/aweme/v1/web/aweme/post/` response field names can change. **Mitigation**: extractor reads only a small set of fields (`aweme_id`, `desc`, `author.nickname`, `author.sec_uid`, `video.cover.url_list[0]`, `duration`); add tolerance via `_safe_get` helpers, mirror what `xhs/bootstrap.ts` does for note shape variance. **E2E validation 2026-05-07** against the home-feed `/aweme/v2/web/module/feed/` endpoint (same `aweme_list[]` shape as `/aweme/v1/web/aweme/post/`): 10/10 entries parsed with complete metadata (aweme_id, title, author, author_sec_uid 76 chars, cover_url) — no drift detected.

7. **Soft anti-bot via empty-200**: `/aweme/v1/web/aweme/post/?sec_user_id=...` returns **HTTP 200 with `content-type: text/plain` and an empty body** when called from a non-logged-in session, instead of 401/403. The SDK sees a "successful" call but no data flows. **Mitigation**: existing fetch-tap code already handles this — `await resp.clone().json()` raises SyntaxError on empty body, caught silently, no items captured. Tests in `dy-fetch-tap.test.ts` cover the empty-aweme-list path. This means we cannot detect "unauthenticated bootstrap" by HTTP status; we detect it by `scope_counts` all being zero after the executor's full sweep, and surface that to the user as "抖音 signals not imported: not logged in".
6. **Single-platform daily budget conflict.** XHS already uses `daily_budget=10` on bootstrap to avoid hammering. Douyin gets its own `dy_tasks` table and its own `daily_budget`, so XHS budget exhaustion never blocks Douyin and vice versa.

## Testing

- Unit-test backend conversion `dy_bootstrap_videos_to_events` for all four scopes (mirror of `xhs_bootstrap_notes_to_events`).
- Unit-test `DyTaskQueue.enqueue_with_id` for `bootstrap_profile`.
- Unit-test extension fetch-tap message format (priority is shape correctness, not interception of a live douyin.com — that's an integration concern).
- Unit-test `dy/task-executor.ts` scope-routing: given a sequence of captured `/aweme/v1/web/aweme/{post,favorite,collection,like}/` JSON pages, it groups them correctly and respects `max_items_per_scope`.
- Reuse the existing `bootstrapScrollShouldContinue` test fixture; verify integration with new dy fetch-tap.
- One `@pytest.mark.integration` test for `_enqueue_dy_bootstrap_task` env-var overrides (mirror of the existing XHS env-override test at `tests/test_cli.py::test_enqueue_xhs_bootstrap_task_uses_env_overrides`).
- Manual smoke: `openbiliclaw init --yes-douyin` on the maintainer's own logged-in Chrome with seeded saves/likes. Check that scope_counts match what the user actually sees in their profile UI.

## Phasing

The plan breaks into three phases. The phase doc (`docs/plans/2026-05-06-douyin-bootstrap-import.md`, to be written after this spec lands) will detail tasks per phase.

- **Phase A — bootstrap MVP (M, ~5–8 person-days)**: ships dy_post + dy_collect + dy_like + dy_follow via the architecture above. Goal: an `openbiliclaw init` run with `--yes-douyin` produces a scope_counts line and writes the events into the soul pipeline.
- **Phase B — discovery integration (deferred)**: mirror what `xhs.search` / `xhs.creator` did for XHS — let the discovery loop run continuous queries against Douyin. Out of scope for the bootstrap spec but should not be blocked by Phase A choices.
- **Phase C — `dy_history` spike (1 day)**: investigate whether `/aweme/v1/web/aweme/history_read/` responds to in-tab fetches. If yes, ship as a fifth scope; if no, mark permanently deferred and document why in this file.

## Out of Scope (explicit)

- Standalone HTTP scraping of Douyin from the Python backend. Forever out of scope; that's the maintenance trap this whole architecture exists to avoid.
- Background-tab Douyin scraping. The virtual list doesn't paginate without the tab being active. If the user demands silent operation later, that's a separate v2 design problem (probably involving an offscreen document or a hidden window with explicit `chrome.windows.create({focused:false})`, which itself has its own quirks).
- TikTok (tiktok.com) parity. Same architecture in principle, but anti-bot rotation cadence on TikTok is faster than Douyin's; treat as a separate future spec.
