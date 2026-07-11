# Engagement-Stats Completeness — Implementation Plan

> **Spec:** [`2026-07-07-engagement-stats-completeness-spec.md`](./2026-07-07-engagement-stats-completeness-spec.md)
> **Status:** Draft — 2026-07-07. Implement task-by-task, TDD style; do not start a task before the
> previous one's tests are green. Ordered by ROI: **Wave C** (pipeline bugs, all-platform payoff,
> lowest risk) → **Wave B** (per-platform sub-path gaps, highest-value platform first) → **Wave D**
> (docs). Wave A-class absences require **no work** (deliberate zeros).
> **Tech:** Python 3.11+, pytest (`asyncio_mode=auto`), Ruff, MyPy strict, 100-char lines.
> Backend interpreter is `.venv/bin/python` (plain `python`/`python3` has no deps).
> Extension: `cd extension && npm run typecheck && npm run test` (node --test).
> Run per backend task: `.venv/bin/python -m pytest <touched tests> -q`, then
> `.venv/bin/python -m ruff check` / `ruff format --check` on touched files, then
> `.venv/bin/python -m mypy src/openbiliclaw/`.

**Invariants (from Spec — re-read before each task):**
- DTO change is additive only (`share_count: int = 0`); no renames/removals.
- Structural zeros never rendered; keep the `> 0` guard, no placeholders.
- collect→favorite fallback is display-only, never rewrites stored `DiscoveredContent`.
- Extension: omitted source fields stay `undefined`; backend `_safe_int`/`_intish` defaults to 0.
- Edit BOTH `recommendationStats()` copies together (mobile web + desktop), identical logic.
- No scoring/ranking input changes.

---

## Wave C — pipeline bugs (all-platform payoff, ship first)

### Task C1: surface `share_count` end-to-end

**Files:** Modify `src/openbiliclaw/api/models.py`, `src/openbiliclaw/api/app.py`,
`src/openbiliclaw/web/js/view-models.js`, `src/openbiliclaw/web/desktop/assets/js/app.js`;
Tests `tests/test_api_app.py`, `tests/test_mobile_web_view_models.py`,
`tests/test_desktop_web_*.py` (whichever covers `recommendationStats`).

**Steps:**
1. Failing tests:
   - `RecommendationOut` accepts and serializes `share_count`; `_serialize_recommendation_items`
     (`api/app.py:3093-3101`) emits it from `content.share_count`.
   - **Every card-feeding output path emits `share_count`** — add a test asserting the row/dict
     paths at `api/app.py:3889` and `:4604` include `share_count` too. A DTO-only test would let a
     missing `share_count` in those paths pass silently (Spec Verification C1).
   - Both `recommendationStats()` renderers: item with `share_count > 0` produces a `🔁 <count>`
     segment; `share_count == 0` produces none. Fix segment order to
     `view · like · comment · favorite · danmaku · share`.
   - The two view-model normalizers (`view-models.js:325`, `:432`; desktop `app.js:735`, `:4793`)
     carry `share_count: Number(item?.share_count ?? 0)`.
2. Add `share_count: int = 0` to `RecommendationOut` (`api/models.py`, after `comment_count`, with a
   one-line comment noting douyin/twitter/bilibili carry it; issue #79 lineage).
3. Emit `share_count=int(getattr(item.content, "share_count", 0) or 0)` in
   `_serialize_recommendation_items` (`api/app.py:3093-3101` block). **Then also add `share_count`
   to the row/dict output paths at `:3889` and `:4604`** (mirror how they already emit
   `view_count`/`favorite_count`) — both feed cards; leaving them out is the exact gap the
   coverage test in step 1 guards.
4. Add the `🔁` branch to both `recommendationStats()` copies + `share_count` to both normalizer
   pairs.
5. Backend + extension test/lint/mypy green.

### Task C2: collect→favorite fallback at the view-model boundary

**Files:** Modify `src/openbiliclaw/web/js/view-models.js`,
`src/openbiliclaw/web/desktop/assets/js/app.js`; same test files as C1.

**Steps:**
1. Failing test: normalizer output for an item with `collect_count > 0`, `favorite_count == 0` has
   `favorite_count` resolved to the collect value; `recommendationStats()` then renders ⭐.
2. In both view-model normalizers set
   `favorite_count: Number(item?.favorite_count ?? 0) || Number(item?.collect_count ?? 0) || 0`
   (mirror the server-side merge at `api/app.py:3096-3099`). Keep it display-only; do not touch
   stored values.
3. Extension test/lint green. Confirm no double-count when both fields are present (favorite wins).

---

## Wave B — per-platform sub-path gaps (highest-value platform first)

> All Wave B work is in the browser extension (`extension/src/content/*`). Each task: add field
> extraction to the normalizers that lack it, matching the discovery-path normalizer's style and
> the source's real field names. Verify with `npm run typecheck && npm run test`.

### Task B1: XHS — fill bootstrap/profile counts + add share to note metadata

> **Corrected diagnosis (r2):** the search/creator task path is **not** URL-only — it already
> returns `XhsNoteMetadata` with view/like/collect/comment via `extractNoteMetadataFromAnchor`
> (`task-executor.ts:682-685`). The two real gaps are: (a) the **bootstrap/profile** scope's
> `XhsBootstrapNote` (`xhs/bootstrap.ts:11-19`) carries **no counts**; (b) `share_count` is
> **absent from the `XhsNoteMetadata` interface** (`passive.ts:46-55`) so no path can carry it.

**Files:** Modify `extension/src/content/xhs/passive.ts` (interface + share scrape),
`extension/src/content/xhs/bootstrap.ts` (interface + extractors), and confirm
`extension/src/content/xhs/task-executor.ts` needs no count-extraction change; Tests under
`extension/` (node --test).

**Steps:**
1. Failing tests:
   - `XhsNoteMetadata` (and any typed note object built from it) accepts `share_count?: number`;
     `pickMetricCount(["分享","转发","share"])` added to `passive.ts` (~:193-206) fills it; a passive
     fixture with a share label surfaces it, and backend `_cache_xhs_notes` (`api/app.py:6936`)
     receives it. **Add `share_count?: number` to `XhsNoteMetadata` first — without the interface
     field the typed return fails typecheck / is silently dropped.**
   - `XhsBootstrapNote` (`bootstrap.ts:11-19`) gains optional
     `view_count?/like_count?/collect_count?/comment_count?/share_count?`; the bootstrap/profile
     extractors (`extractBootstrapNote` ~:569, DOM/state paths) fill them from the note payload
     when present; a bootstrap fixture with counts surfaces them (today: zero counts).
2. Add `share_count?: number` to `XhsNoteMetadata`; add the share `pickMetricCount` in `passive.ts`
   and in `extractNoteMetadataFromAnchor` if it has a share DOM slot; verify the search/creator path
   inherits share automatically once the interface + extractor carry it.
3. Add the five optional count fields to `XhsBootstrapNote` and populate them in its extractors from
   the XHS `initialState`/note JSON (bootstrap has structured payload, not just DOM).
4. Confirm backend `_cache_xhs_notes` (`api/app.py:6922-6946`) already reads all five counts —
   verify `XhsBootstrapNote`-derived items reach the same reader (they may go through a different
   ingest path; trace it) — extend the backend read only if the bootstrap path lands elsewhere.
5. typecheck + test green.

### Task B2: Zhihu — fill sub-path stats + admit zvideo/pin

**Files:** Modify `extension/src/content/zhihu/task-executor.ts`; Tests under `extension/`.

**Steps:**
0. **Fix the pre-existing semantic bug in the discovery normalizer first.**
   `normalizeZhihuDiscoveryObject` **already** back-fills `comment_count` from `answer_count`
   (`task-executor.ts:523-524`) and `favorite_count` from `follower_count` (`:525-526`). Both are
   wrong metrics on answer/article cards (answers-to-question ≠ comments; author followers ≠ content
   favorites) and already ship false ⭐/💬 counts today. **Remove both fallbacks** (keep them only
   if a `question` card legitimately wants an answer tally — but that is a distinct display concept,
   out of scope; default is remove). Do NOT propagate them into the lean normalizers.
1. Failing tests:
   - **Semantic guard (covers the existing bug):** for the discovery path
     (search/hot/feed/creator/related all route through `normalizeZhihuDiscoveryObject`), an
     answer/article fixture with `answer_count` but no `comment_count` produces **no**
     `comment_count`; a fixture with `follower_count` but no `favorite_count` produces **no**
     `favorite_count` (Spec invariant 7). These fail today and pass after step 0.
   - `normalizeZhihuActivity`, `normalizeZhihuCollectionItem`, `normalizeZhihuReadHistory` carry
     `favorite_count`/`comment_count` when the fixture provides them **as those exact fields**
     (today: voteup-only / none).
   - A `zvideo` fixture and a `pin` fixture are admitted (not `null`) with their available counts;
     extend the type whitelist (~:372/:415/:476) and add mapping for the two new types.
2. Add `favorite_count ?? collect_count` (same metric — collect is XHS/Zhihu's word for favorite)
   and `comment_count` (the **real** comment field only) to the three lean normalizers. **Do NOT
   add `answer_count`- or `follower_count`-based fallbacks anywhere** (Spec invariant 7) — mirror
   only the semantically-correct fields of `normalizeZhihuDiscoveryObject` (:517-522), never its
   :523-526 lines (which step 0 deletes).
3. Admit `zvideo`/`pin`: map their like/comment (and view where the pin/zvideo payload exposes it);
   keep `view_count` absent for answer/article (Cause A — Zhihu lists omit it).
4. `ZhihuBootstrapItem` (~:19-40) **already declares** `voteup?`/`favorite_count?`/`comment_count?`
   — do not re-add them. Add only genuinely new fields: `view_count?: number` **iff** step 3 emits
   view for pin/zvideo. In that case also add `view_count=_safe_int(item.get("view_count"))` to
   backend `zhihu_discovery_items_to_contents` (`sources/zhihu_tasks.py:238-257`, currently unmapped
   — otherwise the emitted view is silently dropped).
5. typecheck + test green.

### Task B3 (optional / lower ROI): YouTube search-path like/comment

**Files:** `src/openbiliclaw/youtube/client.py`, `discovery/strategies/youtube.py`; Tests
`tests/test_youtube_*.py`.

**Steps:**
1. Decide: scrapetube keyword search does not return like/comment; only a yt-dlp detail fetch does.
   Filling them requires an extra per-item yt-dlp call → cost. **Default: defer** and document that
   search-path YouTube cards legitimately show view-only (Cause A-ish: not in the cheap API).
2. If pursued: gate an opt-in enrichment behind existing yt-dlp usage; add tests that the enriched
   path fills `like_count`/`comment_count`. Otherwise close this task as "won't do — documented".

---

## Wave D — docs

### Task D: docs sync (mandatory per CLAUDE.md)

**Files:** `docs/modules/<touched>.md` (api, web/desktop, extension zhihu/xhs), `docs/changelog.md`;
plus a mandatory **evaluate-and-decide** pass on `docs/architecture.md`, `docs/spec.md` §3 diagram,
and README CN/EN diagrams (CLAUDE.md rule 3 triggers on data-flow changes — this alters the
engagement-count flow through the DTO into the renderer).

**Steps:**
1. `docs/changelog.md`: add a bullet under the current version — "跨平台卡片互动数据补齐:share
   透出全端、collect→favorite 兜底前移、知乎/小红书子路径与卡片类型补映射".
2. Update the relevant `docs/modules/*.md` "implemented features" / public-API rows: the
   `RecommendationOut.share_count` addition and the extension normalizer coverage.
3. **Architecture-doc decision (do not skip — CLAUDE.md rule 3):** inspect `docs/architecture.md`,
   `docs/spec.md` §3, and the README CN/EN diagrams. If any of them enumerates the engagement/stat
   fields or the extension→backend→card data flow, update it to include `share_count` and the new
   coverage. If none depicts field-level flow (likely — this adds a field to existing wiring, no
   new module/adapter/dependency block), record in the PR description the explicit justification
   for why no diagram changed. Silence is not acceptable; the checklist item must be answered.
4. README 📌 highlights: only if this rides a release — a single user-facing bullet
   ("多平台卡片现在稳定显示点赞/收藏/评论/分享"), CN+EN in sync, ≤1 sentence.

---

## Suggested landing order & PR slicing

- **PR 1 (Wave C):** C1 + C2 together — small, all-platform, zero fetch risk. Ships share
  visibility + favorite/collect consistency.
- **PR 2 (Wave B1):** XHS sub-path — biggest observable gap after C.
- **PR 3 (Wave B2):** Zhihu sub-path + zvideo/pin.
- **PR 4:** B3 decision (likely "documented won't-do") + Wave D docs, or fold docs into each PR.

Each PR carries its own `docs/changelog.md` bullet per CLAUDE.md; the doc-sync checklist item is
non-optional.
