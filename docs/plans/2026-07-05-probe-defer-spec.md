# Probe Defer Spec — 兴趣/避雷探针「暂时忽略」状态

**Created:** 2026-07-05
**Origin:** PR #82 (external contribution) proposed a "neutral/ignore" probe button but implemented
it as an audit-log-only action — the speculation stayed `active`, so the probe reappeared on every
page reload. This spec keeps the product idea and redesigns it as a real, persisted state
transition in the speculator layer. PR #82 will be closed with credit once this ships.
**Scope:** `soul/speculator.py`, `soul/avoidance_speculator.py`, probe endpoints + chat sentiment
classification in `api/app.py`, sentiment prompt migration into `llm/prompts.py`, desktop web
(`web/desktop/assets/js/app.js`, `css/app.css`), mobile web (`web/js/view-models.js`), tests, docs.
**Out of scope:** delight/surprise-recommendation feedback (different mechanism — feedback on a
concrete item, not on a speculated direction; "do nothing" is already its neutral state), browser
extension popup (it has no probe UI today), "revived probe" special copy
(`revived_from_defer` marker — deliberate v2), any config.toml surface (constants only, see D8).

## Goal

Probes today offer only 喜欢 / 不喜欢 / 多聊聊. A user who is *temporarily* uninterested must
either reject (30-day cooldown + the guess is discarded — too punishing for "meh, not now") or
ignore the card (probe occupies a message slot until its 14-day TTL expires). Add a third action:

> **暂时忽略 (defer)** = "收起这个猜测，过 N 天再来问我一次；连续几次都被搁置，就自动当作不感兴趣放弃。"

Defer is a **reversible, persisted state transition** with an escalation ladder, not a log entry.

## Semantics (the contract)

| Action | Meaning | State effect | Comes back? |
|---|---|---|---|
| confirm | 猜对了 | `status="confirmed"` → promoted | no (promoted into profile) |
| reject | 猜错了 | `status="rejected"` + 30d cooldown | only if re-guessed after cooldown |
| **defer** | 猜得可能没错，现在不想处理 | `status="deferred"` + `deferred_until` | **yes, automatically, N days later** |
| chat | 聊聊再说 | unchanged (sentiment may trigger any of the above) | — |

Escalation ladder: 1st defer hides the probe for **7 days**, 2nd for **14 days**, a **3rd defer
exhausts** it (`status="rejected"` + a 30-day cooldown entry). Three shelvings *is* a soft no;
without this cap defer becomes an infinite nag loop. The ladder is also the honest promise behind
the UI copy: ignoring never turns into 永远的纠缠.

**Exhaustion ≠ user-reject (critical distinction — see D3).** Exhaustion is modeled on
**TTL-expiry**, not on the explicit reject button. Both set `status="rejected"` and add a 30-day
`CooldownEntry`, but exhaustion records the feedback response as `defer_exhausted`, which is
**not** in `HANDLED_PROBE_FEEDBACK_RESPONSES` — so, exactly like a probe that timed out, the domain
becomes re-guessable once the 30-day cooldown lapses (if behavioral signal still supports it).
An explicit user-reject, by contrast, is recorded as `reject` (a handled response) and the novelty
guard blocks that domain **durably**. Exhaustion after three "not now"s is a soft, time-bounded
no; it must not become the permanent block that an explicit "no" earns.

## Data model

### `SpeculativeInterest` (`soul/speculator.py:66`) — and symmetrically `SpeculativeAvoidance` (`soul/avoidance_speculator.py:136`)

```python
status: str = "active"    # active | confirmed | promoted | rejected | deferred   ← new value
deferred_at: str = ""     # ISO timestamp of the most recent defer
deferred_until: str = ""  # ISO timestamp; tick revives the item at/after this instant
defer_count: int = 0      # lifetime count; drives the escalation ladder; NEVER reset
```

- `to_dict` / `from_dict` gain the three fields with the defaults above → old
  `data/memory/speculative_state.json` / avoidance state files load unchanged. **No migration.**
- Module constants (both speculator modules, shared values):
  `PROBE_DEFER_DAYS: tuple[int, ...] = (7, 14)` and `PROBE_MAX_DEFERS: int = 3`.
  Defer *k* (1-based, k < MAX) hides for `PROBE_DEFER_DAYS[min(k, len(PROBE_DEFER_DAYS)) - 1]` days.

### State machine

```
                ┌── tick maintenance (runs AFTER promote): now ≥ deferred_until → revive ──┐
                │   (status=active, created_at=now → fresh TTL, clamp conf_count<threshold) │
                ▼                                                                           │
  active ──user defer (count<3)──► deferred ─────────────────────────────────────────────────┘
    │                                 │
    │                                 └─ user defer while deferred: impossible (probe not surfaced)
    ├─ user defer (count would be 3) ──► rejected + CooldownEntry   (response=defer_exhausted, NOT handled → re-guessable after cooldown)
    ├─ confirm ──► confirmed ──► promoted
    ├─ reject ──► rejected + 30d CooldownEntry   (response=reject, handled → durable novelty block)
    └─ TTL expiry via expire_stale ──► rejected + cooldown   (each item keeps its OWN persisted ttl_days)
```

TTL note (corrected in review): both interest and avoidance **generated** probes normally carry
`ttl_days=3` — interest generation uses `default_ttl_days=3` (`speculator.py:707`, applied at
`:1071`/`:1355`) and avoidance uses its `ttl_days=3` default (`avoidance_speculator.py:148`). The
dataclass fallback `ttl_days=14` (`speculator.py:77`/`:123`) is only a backcompat default for old
state files, NOT what live probes use. The defer windows (7/14 days) are shared constants and are
deliberately **longer** than the 3-day TTL — that's fine because deferred items are exempt from
`expire_stale` (D4). Revival resets `created_at`, giving the revived item a fresh window of
**its own persisted `ttl_days`** (whatever it was created with, normally 3). So a revived probe of
either kind has ~3 days to be acted on before it TTL-expires again — acceptable and symmetric.

## Design decisions

- **D1 — defer is a status, not a log.** The single root defect of PR #82. Every surface that
  serves probes already filters `status == "active"` — pending endpoints
  (`api/app.py` `pending_interest_probes` / `pending_avoidance_probes`), the WebSocket publishers
  (`runtime/refresh.py:2371` and `:2474`), and `get_active_speculations()`
  (`speculator.py:1084`) — so `deferred` disappears from every delivery channel with **zero
  changes to read paths**, survives restarts, and needs no frontend memory
  (`state.handledProbeKeys` stays a same-session nicety only).
- **D2 — the API verb is `defer`, not `neutral`.** The button is an explicit action; `neutral` is
  a sentiment-classification bucket. Keeping them distinct keeps the chat path honest (see D6).
- **D3 — escalation 7 → 14 → exhausted, on the TTL-expiry contract (NOT the reject contract).**
  See ladder above. Exhaustion sets `status="rejected"` and appends a 30-day `CooldownEntry` —
  the same two mutations `expire_stale` performs on a timed-out probe — but it deliberately does
  **NOT** record a handled feedback response. It records `response="defer_exhausted"`, which is
  absent from `HANDLED_PROBE_FEEDBACK_RESPONSES` (`speculator.py:322`); the novelty guard only
  promotes *handled* responses to durable never-re-guess terms (`speculator.py:494-498`). Net
  effect: an exhausted domain is suppressed for 30 days, then re-guessable — identical to a
  TTL-expired probe, and intentionally softer than an explicit user-reject (which IS handled and
  blocks durably). Rejected earlier draft wording ("identical to a rejection") was wrong: reject
  is a *handled* response and would permanently block; that over-punishes three "not now"s.
- **D4 — revival resets the TTL window and MUST run after promotion.** On revive:
  `status="active"`, `created_at=now`, `deferred_at`/`deferred_until` cleared, `defer_count`
  **kept**, and `confirmation_count` **clamped to `min(confirmation_count, confirmation_threshold
  - 1)`** (see D5 for why). Without the `created_at` reset a revived item would be past its TTL and
  `expire_stale` would kill it on the next tick. Revival runs inside the existing `_prepare`
  maintenance closure of **both** `tick()` (`speculator.py:849`) and `force_tick()`
  (`speculator.py:919`) — `force_tick` runs at process startup, so a daemon restart is also a
  revival opportunity. **Ordering within `_prepare` is fixed: `expire_stale` → `promote_ready` →
  `revive_deferred`.** Reviving *after* `promote_ready` guarantees a freshly-revived probe is not
  promoted in the same maintenance pass, so it always resurfaces to the user as a probe first
  (the reject alternative — reviving before promotion — could silently absorb it into the profile,
  violating D5). `expire_stale` (`speculator.py:601`) already skips non-`active` items, so a
  snoozed item can never TTL-expire; no change needed there.
  - **Interest `_prepare` order:** `expire_stale` → `promote_ready` → `revive_deferred` (interest
    has no compaction step, `speculator.py:849-853`).
  - **Avoidance `_prepare` order (found in review):** avoidance runs an *extra*
    `compact_redundant_active_avoidances` step **after** promotion (`avoidance_speculator.py:1027`,
    `:1091`), and compaction can reject an active item. `revive_deferred` for avoidance must
    therefore be the **last** step: `expire_stale_avoidances` → `promote_ready_avoidances` →
    `compact_redundant_active_avoidances` → `revive_deferred`. Reviving before compaction would let
    a freshly-revived avoidance be compacted/rejected in the same pass, breaking the
    "resurfaces first" guarantee.
- **D5 — deferred items are frozen while snoozed, and cannot silently auto-promote on revival.**
  `observe_events` (`speculator.py:527`) and `promote_ready` (`speculator.py:580`) both filter on
  `active`/`confirmed` and stay untouched: a deferred speculation accrues no behavioral
  confirmations and cannot be promoted while snoozed. Rationale: the user explicitly asked to
  shelve it; the "I said ignore but it promoted itself anyway" surprise must not happen.
  **The subtle leak (found in review):** an item could already sit at `confirmation_count >=
  confirmation_threshold` at defer time (e.g. behavioral confirmations landed in the same tick, or
  between ticks, before `promote_ready` ran). On revival that item would be promotion-eligible
  immediately. Two guards close this: (1) revive-after-promote ordering (D4) prevents same-pass
  promotion; (2) clamping `confirmation_count` to `threshold - 1` on revival forces the revived
  probe to earn at least one **fresh** confirmation before it can promote — so a revived probe is
  always shown to the user again, never absorbed silently. Clamping is a no-op for the normal case
  (`count < threshold`); it only bites the rare threshold-ready-at-defer edge.
- **D6 — chat classification adds exactly one label.** PR #82's 5-way split
  (`neutral_deferred`/`neutral_ambiguous`) mapped **both** to the same no-op, which is the worst
  of both worlds. Correct split:
  - `neutral_deferred` (「先放着吧」「稍后再看」) = an explicit shelve request → **runs the same
    defer transition as the button**.
  - Ambiguous (「不确定」「再看看」) = the user has *not* decided → stays plain `neutral`:
    feedback-history log only, probe stays live. No new label; the raw message is already stored
    (`raw_text_excerpt`) so any future analytics can re-classify offline.
  - Keyword fallback (`_keyword_judge_sentiment`, `api/app.py:4803`): add a conservative
    `deferred_terms` set (「暂时忽略」「先放着」「稍后再看」「以后再说」「回头再看」「过段时间再说」),
    checked **after** `negative_terms` — an utterance containing an explicit negative
    (「不想再看看这个了」) must classify negative; mis-reading a true negative as defer re-asks in
    7 days (annoying), mis-reading a defer as negative costs a 30-day cooldown (worse, but only
    happens when the user actually used negative words). 「先不看」 is deliberately NOT a deferred
    term (too close to rejection phrasing).
- **D7 — sentiment prompt moves to `llm/prompts.py`.** The system instruction currently lives
  inline in `_llm_judge_sentiment` (`api/app.py:4835`, via `complete_with_core_memory`). We must
  edit it anyway (5th label), so migrate it to a static module-level constant + builder in
  `llm/prompts.py` per the prompt-cache convention (CLAUDE.md), register the builder in
  `tests/test_llm_prompts.py::_builder_test_inputs()`. `max_tokens` bumps 8 → 16 so the longest
  label (`neutral_deferred`) can never truncate. The accepted-output set gains `neutral_deferred`
  (`neutral_ambiguous` is NOT accepted — the LLM is not offered it).
- **D8 — constants, not config.** `PROBE_DEFER_DAYS`/`PROBE_MAX_DEFERS` are module constants.
  No `config.toml` field until someone actually asks; avoids docs/modules/config.md churn.
- **D9 — both `defer` and `defer_exhausted` stay OUT of `HANDLED_PROBE_FEEDBACK_RESPONSES`**
  (`speculator.py:322`). That set feeds the novelty guard's *durable* "never re-guess" terms
  (`speculator.py:494-498`). A snoozed domain must not be permanently blocked (it has to be able
  to revive), and an exhausted domain must stay merely cooldown-blocked (re-guessable after 30
  days per D3) — neither earns the permanent block that an explicit `reject` does. Deferred
  domains are excluded from *re-generation while snoozed* through the existing active-list
  mechanisms instead:
  - `ProbeNoveltyGuard.from_profile_and_state` (`speculator.py:486`) iterates **all**
    `state.active` entries regardless of status → deferred already covered, no change.
  - The `existing_domains` status-set dedup in candidate selection (`speculator.py:~806`,
    `avoidance_speculator.py:919`) must add `"deferred"`; implementation must audit every
    `existing_domains` / `item.status in {...}` site in both speculator modules
    (`grep -n 'status in {' src/openbiliclaw/soul/*.py`).
  - `defer_exhausted` records `response="defer_exhausted"` — also NOT added to the handled set;
    the 30-day cooldown entry provides a time-bounded block, and the domain is re-guessable after
    it lapses, exactly like a TTL-expired probe (D3).
- **D10 — probe slots free up during a snooze.** `_available_probe_slots` counts `active` only;
  a deferred item releases its near/challenge slot so a *different* guess can surface. Accepted:
  revival can therefore temporarily exceed the nominal slot count by design (slots gate
  *generation*, not the active list size; same as today's confirm flow).

## API surface

`POST /api/interest-probes/respond` and `POST /api/avoidance-probes/respond` each gain one
`response` value:

```
{ "domain": "...", "response": "defer" }
→ 200 { "ok": true, "action": "deferred",        "domain": "...", "deferred_until": "...", "defer_count": 1 }
→ 200 { "ok": true, "action": "defer_exhausted", "domain": "...", "defer_count": 3 }
→ 200 { "ok": false, ... }        # domain not found among active speculations (matches reject semantics)
```

- New speculator methods: `user_defer_speculation(domain) -> DeferResult` /
  `user_defer_avoidance(domain) -> DeferResult` where `DeferResult` carries
  `outcome: "deferred" | "exhausted" | "not_found"`, `deferred_until`, `defer_count`. Only
  `status == "active"` items can transition (same rule as confirm/reject). All escalation logic
  lives in the speculator, not the API layer.
- Endpoint side effects (mirroring confirm/reject):
  - `_record_probe_feedback_history(domain, "defer", ...)` with
    `resulting_action="deferred"|"defer_exhausted"`, plus `defer_count` and `deferred_until` in
    the metadata entry; metadata is captured **before** the state transition
    (`_probe_metadata_from_active_speculation`, same ordering as the reject branch).
    `state_key="avoidance_probe_feedback_history"` on the avoidance endpoint.
    - **Sanitizer gap (found in review):** `normalize_probe_feedback_history`
      (`speculator.py:348-386`) rebuilds each entry from a **string-only whitelist** that omits
      `defer_count`/`deferred_until`, so they are silently dropped from persisted history today.
      To make the analytics fields actually survive, extend the normalizer to preserve
      `deferred_until` (string) and `defer_count` (int — needs int handling; the current loop is
      `_string_field`-only). This is the ONLY change to the shared normalizer; existing fields are
      untouched. If we decide the state file
      (`speculative_state.json`) is a sufficient source of truth for analytics, the alternative is
      to drop these two from the history entry entirely — but the spec chooses to preserve them,
      so the normalizer MUST be extended (invariant 11) rather than leave a doc that lies.
  - `_record_probe_cognition` — deferred: 「你把「X」先放一放，7 天后再提。」 / exhausted:
    「「X」已被多次搁置，不再提了。」(avoidance copy mirrors with 避雷 wording).
  - `_publish_probe_event("interest.deferred" | "avoidance.deferred", ...)` for the deferred
    outcome; exhaustion lands in rejected+cooldown state (TTL-style, re-guessable per D3) and
    reuses the existing `interest.rejected` / `avoidance.rejected` WS event purely for UI
    notification — the event choice is cosmetic, the state contract is D3's, not the durable
    user-reject one.
- Chat paths — all **four** sentiment-branch sites gain a `neutral_deferred` branch that calls the
  defer method and emits the same history/cognition records with `classifier` = `llm`/`keyword`:
  durable interest chat + durable avoidance chat (`_generate_durable_chat_reply`,
  `api/app.py:5078` / `:5129`) and the two synchronous respond-chat branches (`:5457` / `:5682`).
  Plain `neutral` behavior is byte-identical to today. Chat-triggered exhaustion behaves exactly
  like button-triggered exhaustion.
- Validation error text for `response` updates to mention `defer` on both endpoints.

## Frontend

- **Desktop web message cards** (`web/desktop/assets/js/app.js:3174` renderer): third icon button
  `data-probe="defer"` between confirm and reject (minus-in-circle icon, `is-neutral` gray style),
  for both interest and avoidance cards. Resolution copy keyed off the response's `action` field:
  - `deferred` → toast 「已搁置，7 天后可能再提」 (14 on second defer — use returned
    `deferred_until` is overkill; static copy 「过阵子可能再提」 is acceptable), card result
    「已搁置，稍后可能再提。」
  - `defer_exhausted` → toast 「好，不再提了」, card result 「已多次搁置，不再提这个方向。」
  The honest copy is part of the contract: never say "已忽略" as if it were permanent.
- **Desktop web profile speculation list** (`app.js:2533` spec-actions row): add 「暂时忽略」
  button (`data-spec-response="defer"`) between confirm and reject, same result copy.
- **Mobile web** (`web/js/view-models.js:479` / `:487`): insert
  `{ label: "暂时忽略", action: "defer", primary: false }` after the confirm entry in
  `getProbeMessageActions` and `getAvoidanceProbeMessageActions`.
- **CSS** (`web/desktop/assets/css/app.css`): `.feedback-icon-btn.is-neutral` and
  `.spec-actions .probe-btn.is-neutral` — muted gray (`var(--meta)`), hover to `var(--muted)`;
  PR #82's rules were fine and can be lifted as-is.
- **WS runtime-event handling** (`web/desktop/assets/js/app.js` `handleRuntimeEvent`, ~`:4895`):
  the handler triggers a **profile refresh** for `interest.confirmed|rejected|chat` +
  avoidance equivalents (~`:4920`). `interest.deferred` / `avoidance.deferred` are deliberately
  **NOT** added to that profile-refresh set — defer does not mutate the profile, so refreshing
  would be a wasted round-trip. The event's human message ("你把「X」先放一放…") still surfaces:
  `applyRuntimeStatus(...)` runs **unconditionally** at the top of `handleRuntimeEvent` for every
  event, so the live-summary banner updates regardless. Card dismissal is handled by the local
  optimistic click handler, not the WS event. **A source-inspection test must assert
  `interest.deferred` is absent from the profile-refresh branch** — this pins the decision so a
  future edit doesn't "helpfully" add it and reintroduce needless refreshes. (This is the
  opposite of the naïve "add deferred to the refresh path" fix; the review flagged the omission,
  and the correct resolution is an explicit, tested exclusion.)

## Invariants (review checklist — every one needs a test or an E2E check)

1. A `deferred` item is absent from `pending_interest_probes` / `pending_avoidance_probes`, both
   WS publishers, and `get_active_speculations()` until revival. Survives process restart.
2. Revival happens in `tick`/`force_tick` maintenance only when `now >= deferred_until`, restores
   `status="active"`, resets `created_at`, clears `deferred_at`/`deferred_until`, and is the
   **last** maintenance step in `_prepare` (D4): interest = after `promote_ready`; avoidance =
   after `promote_ready_avoidances` AND `compact_redundant_active_avoidances`. A revived item is
   neither promoted nor compacted in the same pass.
3. `defer_count` is monotonically non-decreasing across defer→revive cycles; the 3rd defer yields
   `rejected` + a 30-day cooldown entry, `response="defer_exhausted"`, and no `deferred` state.
   The exhausted domain is re-guessable after the cooldown lapses (NOT durably blocked).
4. Neither `"defer"` nor `"defer_exhausted"` appears in `HANDLED_PROBE_FEEDBACK_RESPONSES`; an
   explicit `reject` still IS handled (durable block) — the two contracts stay distinct.
5. Generation never proposes a domain that currently sits in `deferred` (dedup + novelty guard);
   both `speculator.py:806` and `avoidance_speculator.py:919` status-allowlists include
   `"deferred"`.
6. `expire_stale` never touches a `deferred` item (no TTL expiry while snoozed).
7. A deferred item that was threshold-ready at defer time does NOT silently auto-promote on
   revival: `confirmation_count` is clamped to `threshold - 1` on revival and the probe resurfaces
   to the user (D5). confirm / reject / plain-neutral / TTL behavior is otherwise byte-identical to
   pre-change behavior.
8. Old state JSON files (without the new fields) load cleanly; new files round-trip; the three new
   fields default to `""`/`""`/`0`.
9. The migrated sentiment system prompt is a static constant registered in the
   `test_llm_prompts.py` invariance test; user message carries all variables.
10. Keyword classification: deferred terms are checked after negative terms; a message containing
    both classifies negative.
11. `normalize_probe_feedback_history` preserves `deferred_until` (string) and `defer_count` (int)
    through a persist→reload round-trip; all pre-existing whitelisted fields are unchanged.

## Verification

Unit/integration: `tests/test_speculator.py`, `tests/test_avoidance_speculator.py`,
`tests/test_api_app.py`, `tests/test_llm_prompts.py`, `tests/test_mobile_web_view_models.py`,
plus a `tests/test_desktop_web_*`-style DOM/regex test for the new buttons.

End-to-end (real environment, after implementation): restart `serve-api` (routes are fixed at
process start), then in the live desktop web UI: defer a probe → card resolves with the honest
copy → hard reload → probe stays gone; `data/memory/speculative_state.json` shows
`status="deferred"` + timestamps; hand-edit `deferred_until` into the past → restart daemon
(`force_tick` runs revival) → probe reappears; drive defer ×3 → state shows cooldown entry;
chat 「先放着吧」 on a probe → same deferred state. Avoidance probe spot-check for symmetry.
