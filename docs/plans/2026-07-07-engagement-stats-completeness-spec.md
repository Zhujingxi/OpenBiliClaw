# Engagement-Stats Completeness Spec — cross-platform card stats parity

**Created:** 2026-07-07
**Scope:** the engagement-stats row (`▶ view · 👍 like · 💬 comment · ⭐ favorite · 弹幕 · 🔁 share`)
on recommendation cards across **all** content platforms — the field mapping in each platform's
fetch/normalize path (backend `sources/*`, `discovery/strategies/*`, and browser-extension
`extension/src/content/*`), the `RecommendationOut` DTO, and the two `recommendationStats()`
renderers (mobile web + desktop).
**Out of scope:** any new metric beyond the six existing `DiscoveredContent` counts; ranking /
scoring use of engagement counts; changing which platforms are enabled; the LLM eval prompts;
`danmaku` for non-bilibili platforms (structurally N/A everywhere else).

## Problem

Users see inconsistent stat rows: bilibili cards stably show the full row, while Zhihu (and, less
visibly, XHS / YouTube) cards often show a partial row or nothing at all. Investigation across
every platform adapter found the gaps split into **three structurally different causes** that must
be treated differently — a blanket "map everything" is wrong because some fields are genuinely
absent from a platform's API.

### Cross-platform mapping matrix (as-is)

Legend: ✅ full mapping · ⚠️ only some fetch sub-paths / DOM-hit dependent · ❌ never mapped
(platform API structural gap or N/A).

| Platform | view | like | favorite | comment | share | danmaku |
|---|---|---|---|---|---|---|
| **bilibili** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **twitter/x** | ✅ | ✅ | ✅ bookmarks | ✅ | ✅ | ❌ N/A |
| **douyin** | ✅ | ✅ | ✅ collect | ✅ | ✅ | ❌ N/A |
| **youtube** | ✅ | ⚠️ yt-dlp only | ❌ deprecated | ⚠️ yt-dlp only | ❌ | ❌ N/A |
| **xiaohongshu** | ⚠️ feed+search; bootstrap 0 | ⚠️ feed+search; bootstrap 0 | ⚠️ collect, same | ⚠️ feed+search; bootstrap 0 | ❌ never scraped | ❌ N/A |
| **zhihu** | ❌ | ⚠️ some paths | ⚠️ some paths | ⚠️ some paths | ❌ | ❌ N/A |
| **reddit** | ❌ API n/a | ✅ score | ❌ | ✅ | ❌ | ❌ N/A |
| **web (llm)** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Cause classification

**Cause A — platform structural absence (NOT a bug, do not "fix").**
`danmaku` for every non-bilibili platform; reddit `view/favorite/share`; youtube `favorite`
(deprecated by the platform); zhihu list-endpoint `view` (Zhihu's list APIs generally do not
return read counts). These fields legitimately stay 0. The correct treatment is **render nothing**
for them (current behavior) — never a placeholder that implies missing data.

**Cause B — fetch sub-path coverage gaps (the real, fixable defect).**
Within one platform, different fetch entry points populate different subsets, so the *same content*
carries stats from one path and none from another:
- **Zhihu** (`extension/src/content/zhihu/task-executor.ts`): only the discovery normalizer
  (`normalizeZhihuDiscoveryObject`, ~:466-528) fills `voteup`/`favorite_count`/`comment_count`;
  `normalizeZhihuActivity` (~:363-406) fills only `voteup`; `normalizeZhihuCollectionItem`
  (~:408-450) only `voteup`; `normalizeZhihuReadHistory` (~:328-361) fills nothing. `zvideo`/`pin`
  card types are dropped (`return null`, type whitelist ~:372/:415/:476).
- **XHS**: both the passive feed path (`extension/src/content/xhs/passive.ts:193-206`) **and** the
  search/creator task path carry view/like/collect/comment — the task path already calls
  `extractNoteMetadataFromAnchor` and returns `XhsNoteMetadata` notes
  (`task-executor.ts:682-685`), so it is **not** URL-only. The genuine gaps are two:
  (1) the **bootstrap/profile** scope returns `XhsBootstrapNote` (`xhs/bootstrap.ts:11-19`) which
  carries **no counts at all** (url/title/author/cover/note_id/xsec_token only); (2) `share_count`
  is **structurally absent from `XhsNoteMetadata`** (`passive.ts:46-55`) — the backend reads it
  (`api/app.py:6936`) but no extension path ever scrapes it.
- **YouTube**: `like`/`comment` are present only on the yt-dlp detail/trending path
  (`youtube/client.py:526-535`); the scrapetube keyword-search path leaves them 0.

**Cause C — two pipeline-level bugs that silently drop an already-mapped field on ALL platforms.**
- **C1 — `share_count` never surfaces.** `RecommendationOut` (`api/models.py:102-133`) has **no
  `share_count` field**, and `_serialize_recommendation_items` (`api/app.py:3093-3101`) does not
  emit it. Both `recommendationStats()` renderers (`web/js/view-models.js:348-356`;
  `web/desktop/assets/js/app.js:1934-1942`) have no share branch. So douyin / twitter / bilibili
  all map `share_count` end-to-end into the DB, and it is dropped at the DTO + view layers. Nobody
  ever sees a share count.
- **C2 — favorite/collect fallback is only in the API output layer, not the client.** The
  `favorite_count OR collect_count` merge is done three times server-side
  (`api/app.py:3096-3099`, `:3892-3894`, `:4608-4610`) but the two view-model normalizers read
  `favorite_count` only. Any client-side or non-serialized path that forwards `collect_count`
  without the merge shows no ⭐. The merge should also exist once at the view-model boundary so the
  renderer has a single, consistent source.

## Goal / desired end state

1. **C-class bugs fixed once, all platforms benefit** — `share_count` travels DTO → view →
   rendered row (new `🔁` segment); favorite display consistently falls back to collect at the
   view-model boundary.
2. **B-class gaps closed per platform, highest-value first** — every fetch sub-path of a platform
   fills the same engagement subset its discovery path already proves is available; `zvideo`/`pin`
   Zhihu cards are admitted with their counts.
3. **A-class absences left as deliberate zeros** — no fake placeholders; structurally-absent
   fields render nothing, exactly as today.
4. Verified end-to-end: a Zhihu answer card shows 👍/⭐/💬 when the source returns them; an XHS
   search-path note shows the same row as a passive-feed note; a douyin/twitter card shows 🔁.

## Invariants (MUST hold)

1. **Additive, backward-compatible DTO** — new `share_count: int = 0` on `RecommendationOut` only;
   older extension popups ignore unknown keys (same contract as the issue #75/#79 additive fields,
   `api/models.py:123-133`). No renames, no field removals.
2. **Structural zeros are never rendered** — the `> 0` guard in `recommendationStats()` stays; the
   share branch obeys it too. No em-dash / "N/A" placeholder for any count.
3. **Fallback is a display concern, not a stored value** — `collect_count → favorite_count` merge
   never rewrites stored `DiscoveredContent`; it happens only at read/serialize/view boundaries.
   The stored counts remain each platform's raw values.
4. **Extension changes stay within the existing normalizer contracts** — new field extraction
   reuses the same optional-chaining + alias-fallback style already in each normalizer; a field
   that the source genuinely omits stays `undefined` and is not forced to 0 in the extension (the
   backend `_safe_int`/`_intish` already defaults to 0).
5. **Both `recommendationStats()` copies stay byte-identical in logic** — mobile web and desktop
   renderers must be edited together and produce the same segment set/order.
6. **No scoring/ranking behavior change** — engagement counts feeding richer values must not alter
   admission or ranking; if any ranker reads these counts, its inputs are unchanged in
   distribution-breaking ways (spot-check ranker call sites before merge).
7. **Field semantics must match, not just field presence** — a fallback alias may only be used when
   it is the *same metric*. Two mismatches exist **today** in `normalizeZhihuDiscoveryObject` and
   ship false counts: `answer_count → comment_count` (`task-executor.ts:523-524`; answers-to-a-
   question ≠ comments) and `follower_count → favorite_count` (`:525-526`; author followers ≠
   content favorites). Both MUST be removed, not propagated. `favorite_count ?? collect_count` is
   the one legitimate alias (collect is the same metric as favorite). Every alias added in Wave B is
   justified against the source's field meaning; a `question` card's answer tally is a separate
   display concept and out of scope here.

## Verification

- **C1:** seed a douyin/twitter/bilibili recommendation with `share_count > 0`; assert
  `RecommendationOut.share_count` is populated **on every card-feeding output path** — the DTO
  serializer (`api/app.py:3093-3101`) *and* the row/dict paths (`:3889`, `:4604`) that also render
  cards — and that the rendered stats string contains the `🔁` segment (unit test on both
  `recommendationStats()` + a serialization test per output path). A DTO-only test would let a
  missing `share_count` in the row/dict paths pass silently.
- **C2:** view-model unit test — item with `collect_count > 0` and `favorite_count == 0` renders a
  ⭐ segment.
- **B (Zhihu):** extension `task-executor` unit tests — activity/collection/read_history
  normalizers now carry `favorite_count`/`comment_count` when the fixture provides them; a `zvideo`
  and a `pin` fixture are admitted (not `null`) with their counts.
- **B (XHS):** extension test — a search/creator/profile task item carries view/like/collect/comment
  when the DOM fixture exposes them; `share` scraped by passive path reaches the backend field.
- **A:** regression test — reddit item (no view/favorite/share) and a zhihu answer with no view
  render exactly the segments that are `> 0`, nothing more.
- **E2E manual:** drive mobile web + desktop with mixed-platform pool; eyeball that each card's row
  matches the source's real counts and no card shows a lone/empty row where data exists.
