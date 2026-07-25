# Platform-Scoped Recommendations Implementation Plan

> **For the implementation agent:** Work only in the dedicated
> `feat/platform-scoped-recommendations` worktree. Follow TDD task by task, keep the
> global/no-platform behavior backward compatible, and do not commit or merge unless the
> parent explicitly asks.

**Goal:** Turn the PC Web platform chips into real recommendation scopes and show exact
per-platform servable candidate inventory.

**Architecture:** Carry an optional canonical `source_platform` from desktop reshuffle/append
requests through FastAPI and RecommendationEngine into a platform-filtered canonical pool
snapshot. Keep the existing curator/MMR/persistence path after candidate loading. Expose one
read-only availability snapshot for count badges and refresh it independently from card state.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, SQLite, vanilla JavaScript/CSS, pytest, Playwright.

## Global Constraints

- The design contract is
  `docs/superpowers/specs/2026-07-19-platform-scoped-recommendations-design.md`.
- No-platform calls must retain their current argument shape and behavior.
- Never implement platform mode by generating a mixed batch and filtering the response.
- Availability counts and strict candidate loading must share the canonical servability set.
- Reuse the existing curator, MMR, copy, recommendation-history, shown-commit, and replenishment
  paths.
- Preserve cards belonging to other platform tabs during a scoped reshuffle.
- Do not turn an availability read failure into zero.
- Do not change config, source shares, discovery strategies, mobile Web, extension, or CLI UI.
- Update all documentation required by `CLAUDE.md#documentation-requirements`.

---

### Task 1: Lock the platform inventory and strict-storage contract

**Files:**
- Modify: `tests/test_storage.py`
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `docs/modules/storage.md`

- [x] Add failing tests for a mixed canonical pool that cover:
  - exact `total == sum(by_platform)` parity;
  - canonical source-family aliases / legacy strategies;
  - viewed, already-recommended, delight-claimed, unclassified, copy-pending, and non-linkable
    rows excluded from both counts and strict reads;
  - strict rows containing only the requested platform;
  - the same topic-window semantics as canonical available inventory.
- [x] Add a connection-isolated async availability snapshot method returning total and canonical
  per-platform counts from one read transaction.
- [x] Make `get_pool_candidates_for_platform()` select from the same canonical available set and
  return full rows in stable relevance/diversity order.
- [x] Run focused storage tests and `ruff check` for touched Python files.

### Task 2: Thread platform scope through RecommendationEngine

**Files:**
- Modify: `tests/test_recommendation_engine.py`
- Modify: `src/openbiliclaw/recommendation/engine.py`

- [x] Add failing async-snapshot and compatibility-path tests for
  `serve/reshuffle/append(..., source_platform=...)`.
- [x] Prove every returned item matches the requested canonical platform and still passes through
  curator scoring, embeddings/MMR, diversity, persistence, and shown consumption.
- [x] Add default-empty keyword-only parameters across the public serve/reshuffle/append methods.
- [x] Pass the scope into the isolated snapshot loader only when non-empty; skip cross-platform
  floor top-ups in strict mode.
- [x] Keep the no-platform code path byte-for-byte equivalent where practical and keep existing
  platform-floor tests green.
- [x] Run focused recommendation-engine tests.

### Task 3: Add API models, forwarding, availability, and replenishment behavior

**Files:**
- Modify: `tests/test_api_app.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`

- [x] Add failing tests for:
  - append and reshuffle forwarding canonical `source_platform`;
  - omitted platform preserving the old engine call shape;
  - alias normalization and 422 for invalid/unknown values;
  - `GET /api/recommendations/platform-availability`;
  - a short/empty scoped batch waking the existing forced replenishment path without inline
    discovery.
- [x] Add the optional request field and availability response model.
- [x] Implement the isolated read endpoint without using the shared SQLite connection in a worker.
- [x] Forward platform scope to result and legacy engine paths only when present.
- [x] Preserve current response models and schedule the existing exact pool-status publication
  after consumption.
- [x] Run focused API tests.

### Task 4: Turn desktop platform chips into real scoped actions

**Files:**
- Modify: `tests/test_desktop_web_pool_status.py`
- Modify: `tests/test_desktop_web_load_more.py`
- Add: `tests/test_desktop_web_platform_recommendations_e2e.py`
- Modify: `src/openbiliclaw/web/desktop/index.html`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css`

- [x] Add source-contract tests for:
  - the availability endpoint and state;
  - stable enabled/inventory/loaded tab union;
  - per-platform count rendering and accessible selected state;
  - active-platform auto-load inventory gating;
  - scoped request payloads;
  - platform-only replacement that retains other cached platform cards;
  - response handling based on the request-start scope, not the current tab.
- [x] Add a real Chromium test with a deterministic local server:
  - render mixed first page and count badges;
  - choose Zhihu;
  - assert reshuffle and append bodies contain `source_platform: "zhihu"`;
  - assert all visible cards are Zhihu;
  - switch back and prove Bilibili cards were retained;
  - exercise “全部” and prove the platform field is absent/empty.
- [x] Add availability hydration plus a debounced/single-flight refresh after inventory stream
  events and recommendation actions. Preserve the last successful snapshot on failure.
- [x] Capture the active platform at request start. Scoped reshuffle replaces only that platform;
  scoped append deduplicates and appends only matching rows. Treat a backend cross-platform leak as
  an error, not a silent client filter.
- [x] Render compact count spans inside the existing chips. Add tabular numerals, no layout-shifting
  hover effect, visible focus, `aria-selected`/accessible labels, and stable responsive overflow.
- [x] Use current-platform availability for auto-load; keep manual load available at zero.
- [x] Run the static desktop tests and the new Playwright E2E.

### Task 5: Synchronize architecture and module documentation

**Files:**
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/storage.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/changelog.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Inspect/update when the diagram text requires it: `README.md`, `README_EN.md`
- Update checkboxes in this plan

- [x] Document the new request field, availability endpoint, canonical count definition,
  strict-loading invariant, and PC-Web-only surface scope.
- [x] Add a concise bullet under the current changelog version; do not create a release or bump
  versions.
- [x] Update the existing recommendation data-flow diagram/description to show optional platform
  scope and per-platform inventory. Keep Chinese/English README diagrams synchronized if touched.
- [x] Explicitly state why mobile Web, extension, and CLI are unchanged.

### Task 6: Verification and handoff

- [x] Run formatting:

```bash
ruff format src/ tests/
ruff check src/ tests/
```

- [x] Run strict typing:

```bash
mypy src/
```

- [x] Run focused tests first, then the full suite:

```bash
pytest -q \
  tests/test_storage.py \
  tests/test_recommendation_engine.py \
  tests/test_api_app.py \
  tests/test_desktop_web_pool_status.py \
  tests/test_desktop_web_load_more.py
pytest -q tests/test_desktop_web_platform_recommendations_e2e.py -m integration -s
pytest -q -m "not integration"
```

- [x] Inspect:

```bash
git diff --check
git status --short
git diff --stat
```

- [x] Return a concise implementation summary, exact tests and results, remaining risks, and the
  final `git status --short`. Do not commit, push, merge, or remove the worktree.

Final acceptance results:

- `ruff format --check src/ tests/` — 452 files already formatted.
- `ruff check src/ tests/` — passed.
- `mypy src/` — passed for 203 source files.
- Focused storage + recommendation-engine suites — 241 passed.
- Full API suite — 436 passed.
- Desktop Web non-integration suites — 164 passed.
- Real Chromium platform-tab E2E — 2 passed.
- Full credential-free suite — 5320 passed, 74 integration cases deselected.
- Independent production-chain smoke (`API → RecommendationEngine → SQLite`) — scoped Zhihu
  response stayed Zhihu-only and persisted inventory decremented without changing Bilibili stock.
