# Issues #101–#109 Web Reliability and State Consistency Design

**Date:** 2026-07-13
**Status:** Approved for implementation
**Scope:** GitHub items #101 through #109 in `whiteguo233/OpenBiliClaw`

## 1. Context

The numbered range contains seven open issues, one merged pull request, and one
open issue whose code fix is already on `main`:

| Item | Current repository state | Design disposition |
| --- | --- | --- |
| #101 | Open; desktop reload can hide fast data behind slow backend reads | Implement progressive desktop hydration and bound cold-cover pressure |
| #102 | Merged PR; drawer animation and Delight drag dead zone are on `main` | Preserve behavior, add missing real-browser regression coverage and module documentation |
| #103 | Open; mobile probe buttons lose their pending state when the message overlay is rebuilt | Introduce keyed pending probe state and derive every render from it |
| #104 | Open; mobile Delight hides all actions after a positive response | Separate status visibility from action visibility |
| #105 | Open; `AUTO_LOAD_ROOT_MARGIN_PX = 50` and a Chromium regression are already on `main` | Do not reimplement; add missing module documentation and keep the existing test in the verification gate |
| #106 | Open; an expanded 312px drawer can make Delight overflow at medium viewport widths | Make Delight respond to its available content container, not only the viewport |
| #107 | Open; chat failures collapse into one generic successful-looking reply | Propagate typed failures, sanitize them at the boundary, and persist failed turns as failed |
| #108 | Open; rehydrated liked Delight cards do not expose pressed state | Project persisted liked state into `aria-pressed` on every graphical surface |
| #109 | Open; desktop probe ToastNotice drops the probe subject | Generate domain-aware result and toast copy |

The current targeted baseline is green: 76 relevant tests pass. These issues are
therefore missing-state and missing-browser-coverage defects, not failures in the
existing test suite.

## 2. Goals

1. Make useful desktop content visible as soon as its own request completes.
2. Ensure rebuilt DOM is always derived from retained interaction state.
3. Give persisted Delight feedback the same visual and accessibility meaning on
   mobile Web, desktop Web, and the extension side panel.
4. Prevent failed LLM calls from looking like successful dialogue or entering
   the learning pipeline.
5. Make Delight responsive to the width it actually receives after the desktop
   drawer participates in flex layout.
6. Lock already-landed #102 and #105 behavior with appropriate tests and docs.

## 3. Non-goals

- No service worker, localStorage recommendation snapshot, or offline Web cache.
- No direct-to-CDN image path or relaxation of `/api/image-proxy` validation.
- No new batch API for favorite/watch-later state.
- No redesign of the desktop drawer or rollback of PR #102's flex-flow behavior.
- No new Delight API schema; the existing `state="liked"` contract remains the
  source of truth.
- No GitHub issue closing, commenting, pushing, or PR publication as part of the
  local implementation. Those are separate external actions.

## 4. Options Considered

### Option A — Superficial per-symptom patches

Filter the rebuilt probe list, change one Delight conditional, replace the chat
fallback string, and add a CSS media query. This is the smallest diff, but it
keeps the root problem: state remains encoded in whichever DOM nodes happen to
exist, `handled` retains conflicting meanings, and chat failures still enter the
successful learning path.

### Option B — Focused state and boundary corrections (chosen)

Keep the existing APIs and visual system while making four focused corrections:

- progressive resource application at desktop boot;
- explicit keyed pending/projection state for probes and Delight;
- typed dialogue failure propagation with sanitized boundary mapping;
- component-width responsive CSS for Delight.

This resolves the root causes without introducing a new shared frontend
framework or backend schema.

### Option C — Shared frontend store plus typed UI API

Introduce a cross-surface state package and a typed batch API that directly
returns available actions, selected states, and failure codes. This could reduce
future drift, but it would require coordinated migration of three independently
bundled clients and materially expand the release risk. It is deferred until a
broader frontend consolidation is planned.

## 5. Detailed Design

### 5.1 #101: Progressive desktop hydration

#### Root cause

`hydrateFromBackend()` currently awaits eleven requests in one `Promise.all`,
then applies recommendations. `/api/health` and `/api/init-status` can perform a
cold embedding readiness probe with a 15-second timeout. A controlled browser
reproduction with only `/api/health` delayed by four seconds kept a fast
recommendation response hidden for the same four seconds.

Cold cover misses amplify the problem: desktop recommendation cards currently
mark every cover eager, and image proxy requests share the backend origin with
interactive JSON requests. The proxy itself is already disk-cache-first and
returns a one-day browser cache header, so another cache layer is not the fix.

#### Boot flow

The desktop page will keep its immediate static/skeleton render, then apply
resources independently:

1. Bound `/auth/status` so a stalled backend cannot leave the public shell in an
   indefinite authentication wait. A real unauthenticated response still opens
   the login overlay before protected data is requested.
2. Use lightweight `/api/ping` for the initial connected/unavailable indicator.
   Do not use `/api/health` as liveness.
3. Start recommendation and runtime reads together after authentication.
4. As soon as recommendations settle successfully, normalize them, update
   `state.videos`, and render the recommendation area. A failure starts the
   existing bounded recovery loop without waiting for secondary resources.
5. Apply the first runtime snapshot independently. Preserve the existing
   post-recommendation runtime reread because recommendation serving consumes
   inventory, but run that reconciliation without holding back cards.
6. Load health, init status, activity, profile, Delight, notifications, chat
   history, and config as secondary resources. Each resource updates only its
   owned state and view when it settles; one failure cannot discard successful
   siblings.
7. Preserve debounce, single-flight, generation guards, init polling, and
   recommendation/runtime recovery behavior.

The final hydration promise may still be used for bookkeeping, but no renderable
primary resource may be gated on the slowest unrelated request.

#### Cover scheduling

- Delight and the first visible desktop recommendation row remain eager/high
  priority to preserve the no-white-flash intent.
- Recommendation covers after the first four use native lazy loading and normal
  or low fetch priority.
- Existing decode-before-insert and image-proxy fallback behavior remains.
- Favorite/watch-later batching and proxy transport changes remain out of scope.

### 5.2 #102 and #105: Landed behavior closeout

#102 receives no behavior rewrite. Browser tests will lock:

- drawer open/closed geometry and `aria-expanded`/`aria-hidden`;
- smooth flex-flow ownership of the remaining content width;
- pointer movement below the 10px drag activation threshold;
- activation at the threshold without navigation below 50px;
- card navigation at the 50px switch threshold.

The documentation will record why 10px is an interaction dead zone while 50px
is the navigation threshold.

#105 remains exactly `AUTO_LOAD_ROOT_MARGIN_PX = 50`. Its existing Chromium test
continues to prove that a sentinel around 150px below the viewport does not
trigger while a sentinel around 20px does. Only the missing module documentation
is added.

### 5.3 #103: Retained pending state across message-overlay rebuilds

The mobile chat module will maintain a module-scoped keyed collection such as
`pendingProbeActions`, keyed by normalized probe type plus domain. It represents
only an in-flight submission; it is distinct from the terminal handled-key set.

The state transition is:

1. On a non-chat probe action, derive the key and ignore duplicate submission if
   that key is already pending.
2. Add `{response}` to `pendingProbeActions` before starting the request.
3. Every `renderOverlay()` derives `disabled`, processing class, and `aria-busy`
   from that collection. Closing and reopening the overlay therefore recreates
   the same disabled presentation.
4. Do not add the terminal handled key before the server settles.
5. On accepted or terminal no-op response, remove pending state, remember the
   handled key, remove the notification, and update the badge.
6. On transport/server failure, remove pending state, retain the notification,
   and rerender enabled actions so the user can retry.

Interest and avoidance probes remain isolated because the normalized key
contains the probe type. A full page reload during an in-flight non-durable
request is not made durable by this change; the backend's domain transition
remains authoritative after reload.

### 5.4 #104 and #108: Explicit Delight UI projection

`handled` currently conflates at least three questions: whether to show status
copy, whether to show actions, and whether feedback is selected. The Delight UI
projection will express those independently. Exact field names may follow local
naming conventions, but the projection must provide equivalents of:

- `showStatus`
- `showActions`
- `likePressed`
- `likeDisabled`

Required liked-state behavior on mobile Web, desktop Web, and extension:

- the card remains in the active queue;
- “好，这类多来点。” is visible;
- the action group remains visible;
- the like control has `aria-pressed="true"` and cannot submit a duplicate like;
- view, watch-later, favorite, dislike, and chat retain their existing behavior;
- a queue reload containing `state="liked"` and a `delight.liked` stream event
  produce the same DOM state as a successful local click.

Mobile rendering will no longer choose between result text and the entire action
group with one `if/else`. Desktop's static like button gains an initial
`aria-pressed="false"`, and `setActiveDelight()` synchronizes it from the active
candidate. The extension's generated like control receives the same state
projection. Existing viewed, rejected, chatted, saved-toggle, and negative-removal
semantics remain unchanged unless a test demonstrates they are inseparable from
the liked-state correction.

### 5.5 #106: Component-width responsive Delight

Viewport media queries continue to own true mobile navigation and topbar
behavior. Delight layout will additionally use an inline-size container on the
desktop main content area:

- wide available container: existing two-column media/copy layout;
- compact available container: one-column Delight layout;
- narrower component thresholds: wrap and compact the action groups using the
  same intent as the current 620px/430px viewport rules.

Opening the 312px drawer at an 860px viewport currently leaves about 533px for
the layout while the Delight grid still demands roughly 697px. The acceptance
criterion is based on geometry, not a class name: both open and closed drawer
states must keep the Delight right edge within the main layout and must not add
horizontal document overflow. Wide desktop must retain the two-column design.

### 5.6 #107: Typed, non-learning chat failures

#### Dialogue boundary

`SocraticDialogue.respond()` will append the user turn only provisionally. On a
successful model response it appends the agent turn and starts background
learning exactly as today. On failure it removes the provisional user turn,
does not append synthetic assistant text, does not call
`learn_from_dialogue()`, logs the failure, and re-raises while preserving the
exception chain.

This prevents provider outages from becoming false dialogue events or inferred
preferences.

#### Safe failure classification

The existing `describe_llm_failure()` mapping remains the source for user-safe
LLM explanations and is extended to recognize service-layer empty-response
errors. It distinguishes:

- content moderation/compliance refusal;
- authentication/API key failure;
- exhausted quota or rate limiting;
- timeout/network slowness;
- no available primary/fallback provider;
- empty or unparseable response;
- unknown failure, which receives a generic safe message while details remain
  in logs.

Raw provider exception strings, credentials, request bodies, and upstream
payloads must never be returned to a client.

#### Durable turns and other callers

The durable turn worker will stop converting failures into ordinary reply
strings. It will:

1. complete a turn only after a genuine dialogue reply;
2. mark timeout or classified failure as `status="failed"`, `reply=""`, and a
   sanitized actionable `error`;
3. preserve retry by `turn_id` and existing polling behavior.

All direct `SocraticDialogue.respond()` callers are audited. Contextual probe
endpoints retain their current response shape but return `ok=false` with safe
copy on failure. CLI dialogue catches the propagated failure, prints the safe
reason, and keeps the interactive session usable. Mobile, desktop, and extension
durable-turn renderers must show `turn.error` rather than a false success state.

### 5.7 #109: Domain-aware probe feedback copy

Desktop probe feedback copy will use one domain-aware helper for the card result
and ToastNotice. It receives probe type, response, domain, and server outcome.
Examples:

- `已确认兴趣「系统设计」`
- `已搁置兴趣「短视频热点」，过阵子可能再提`
- `已确认避雷「标题党」`
- `已排除避雷「长视频」`

The subject is bounded to a reasonable toast length with an ellipsis and is
always assigned through `textContent` or escaped output. Inbox and profile
surfaces use the same helper so their wording cannot drift.

## 6. Error Handling and Recovery

- A failed secondary boot resource updates only its owned failure state.
- Recommendation/runtime failures continue through the existing 1/2/4/8-second
  bounded single-flight recovery loops.
- Failed probe submissions restore actions and preserve the card.
- Failed Delight feedback restores the previous pressed/disabled state and does
  not fabricate a liked state.
- Chat failures are stored as failed durable turns with safe retryable copy;
  they are not learned.
- Container-query support is CSS-only and degrades to existing viewport rules
  in unsupported engines; supported Chromium is the release target tested here.

## 7. Testing Strategy

Implementation follows test-driven development: add each failing regression
before changing production behavior.

### #101

- Playwright boot fixture: recommendations respond immediately while health is
  delayed; a real recommendation card must appear before health settles.
- Assert runtime reconciliation still updates inventory after recommendation
  consumption.
- Browser request/viewport test: first visible covers are eager; below-fold
  covers do not all start before scroll.

### #102 and #105

- Real-browser drawer geometry/ARIA and Delight drag threshold coverage.
- Rerun the existing auto-load margin source contract and Chromium geometry
  regression unchanged.

### #103

- Delay both interest and avoidance response APIs.
- Click, close, and reopen the message overlay before settlement.
- Assert all actions on that probe remain disabled/`aria-busy`, no duplicate POST
  is sent, success removes the card, and rejection restores enabled controls.

### #104 and #108

- Mobile: like a pending Delight and assert status plus actions remain.
- Mobile: assert local click, queue reload with `state="liked"`, and stream liked
  event all yield `aria-pressed="true"`.
- Desktop and extension: assert generated/static like controls expose and restore
  the same pressed state.
- Preserve negative removal, chat, watch-later, and favorite regressions.

### #106

- Playwright at 860px with drawer open and closed: no Delight or document
  overflow.
- Threshold-adjacent compact widths and composer/action wrapping.
- Wide desktop retains a two-column Delight.

### #107

- Unit tests for each safe classification, including service-layer empty reply.
- Dialogue test proves failure rolls back provisional history and never learns.
- API tests prove durable turn status/error/reply fields for success, timeout,
  classified failure, and unknown failure without raw detail leakage.
- Client contracts prove `failed` turns render error state across graphical
  surfaces; CLI test proves the session remains usable.

### #109

- Existing desktop issue-98 browser fixture commits an inbox interest response
  and asserts the resulting toast contains the domain.
- Cover interest/avoidance plus confirm/defer/reject wording.

### Verification commands

```bash
.venv/bin/ruff format --check src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
.venv/bin/pytest
.venv/bin/pytest --cov=openbiliclaw
```

Relevant real-browser suites are run explicitly even if the full suite selects
them indirectly. Extension changes also receive the repository's available
extension tests plus a documented manual side-panel check because `extension/`
does not declare a standalone package script in the repository guidelines.

## 8. Documentation Impact

The implementation updates, as applicable:

- `docs/changelog.md`
- `docs/modules/runtime.md` for progressive desktop hydration, drawer/drag
  contracts, the calibrated 50px auto-load margin, and container-responsive
  Delight
- `docs/modules/recommendation.md` for positive Delight retention and pressed
  state
- `docs/modules/soul.md` and `docs/modules/llm.md` for non-learning classified
  dialogue failures
- `docs/modules/extension.md` for extension liked-state projection
- `docs/modules/cli.md` if the CLI catch path changes
- `docs/mobile-web-spec.md` for pending probe and mobile Delight behavior
- `docs/architecture.md`, `docs/spec.md`, `README.md`, and `README_EN.md` for
  progressive Web hydration and the success-only dialogue-to-learning path
- `docs/diagrams/web-architecture.html` so the detailed Web flow matches the
  textual architecture

No endpoint, module boundary, dependency block, or configuration field is added.
The successful recommendation/dialogue paths remain the same, but boot ordering
and failure termination are data-flow changes, so the architecture surfaces are
updated under the repository's mandatory documentation policy. Config and
installer docs do not change.

## 9. Acceptance Criteria

The work is complete when:

1. A slow health/readiness probe cannot delay an already-returned desktop
   recommendation card.
2. Medium-width drawer layouts have no Delight horizontal overflow.
3. Reopening a mobile message overlay cannot re-enable an in-flight probe.
4. Liking a Delight retains the card and other actions and exposes pressed state
   after click, reload, and stream synchronization on all three graphical
   clients.
5. Dialogue provider failures are actionable, stored as failed turns, safe from
   detail leakage, and excluded from learning.
6. Probe success toasts identify the affected domain.
7. #102 and #105 landed behavior has browser/module-documentation coverage.
8. Relevant browser regressions, full pytest, Ruff, and MyPy pass, with any
   environment-only limitation explicitly reported.
