# Agentic Recommendation Implementation Plan

Status: approved direction, not started. Source of truth for *what* is `docs/spec.md`
(#12–#22 + vision block); this plan sequences the *how*. Module contracts live in
`docs/modules/recommendation-target.md` (exploration strategy, provider channels),
`understanding.md` (two planes, trust tiers), `provider-access.md` (plugin-assisted
access), `cli.md` (CLI doctrine).

## Vision anchor

Get more from what you care about — enjoy / deepen / connect / discover, user-directed.
Invariant: no single provider algorithm or inferred profile defines the user's reachable
discovery space. Every phase must serve this or justify itself.

## Phase A — Foundations (trust + observability)

Goal: every later phase is auditable and the app is operable. No behavior change yet.

Progress: `- [ ]` pending · `- [~]` in progress · `- [x]` done (TDD: red → green → review)

- [x] 1. **Two-plane storage**: `policy_journal` SQLite tables (briefs, hypotheses, lessons,
  outcomes) strictly separate from user evidence; cross-reference by ID only.
  (understanding/recommendation-target: two-plane rule) — landed: schema V7 +
  `recommendation/policy_journal.py`; composition wiring lands with the first consumer (B2)
- [x] 2. **Decision trace + deterministic replay**: record per decision — candidate snapshot ID,
  evidence/profile version IDs, model/prompt/schema versions, typed proposal, compiled
  result, selection/exposure/outcome provenance. Replay validates that the same snapshot
  produces a valid auditable decision (not counterfactual user reaction). — landed:
  `recommendation/trace.py` assembles existing pipeline provenance and replays the full
  persisted seed cohort model-free; brief/model/prompt/schema fields join it in B2.
- [x] 3. **Channel registry + provenance**: provider manifests declare per-`feed_id` bias class
  (`platform-popularity | platform-personalized | subscription-graph | editorial`) and
  auth requirement; candidates carry `(provider, channel)` provenance end to end.
  (spec #18; authoring contract rule) — landed: manifest validation + current anonymous
  feed declarations + durable provider/channel candidate provenance
- [x] 4. **Auth**: password login for local web/desktop clients; generated token for
  extension/agent (`openbiliclaw ext-token`). (spec #20) — landed: backend (schema V9,
  login route, set-password/ext-token CLI) + web login page/token flow; bearer gates
  /v1 only, static SPA shell stays public; browser e2e 5/5
- [x] 5. **Image proxy**: `/v1` route serving provider images locally; no CDN hotlink leaks.
  — landed: declarative provider `image_hosts`/`image_headers`, HTTPS allowlist + 10 MiB
  bounded `/v1/media`, and same-origin card/detail rendering; caching intentionally deferred
- [x] 6. **Export/import**: versioned SQLite + config dump/restore. CLI `export`/`import`.
  — landed: format-v1 zip archive with SQLite backup snapshot, manifest/table counts,
  optional redacted config, non-empty destination guard, and migration-forward restore
- [x] 7. **Full thin CLI**: pass-through commands over Application workflows for
  sources/feed/feedback/profile/assistant/search; zero business logic in commands.
  (spec #19) — landed: in-process graph, one workflow call and one JSON document per
  invocation; expected errors are typed JSON on stderr

Acceptance: replay a recorded feed decision offline with no provider/model calls;
`openbiliclaw export` → fresh data dir → `import` reproduces profile/ledger state;
password + token gates enforced on `/v1`; CLI covers the same surface as `/v1` routers.

## Phase B — Agentic core (spec #15–#17)

Goal: the feed is steered by intent-conditioned, evidence-cited agent strategy with
learned magnitudes.

- [x] 1. **Hypothesis registry**: typed hypotheses — evidence IDs, embedding/query seed,
  falsification criterion, expiry, attempt/outcome counts. Seed arms: weak-signal,
  dormant-interest (pure ledger SQL), source-novel. Adjacent/bridge land with Phase C/D
  substrate. Registry lives in the policy journal, never the user evidence ledger.
- [x] 2. **RecommendationBrief**: agent compiles typed brief on material context change —
  intent (`enjoy | accomplish | deepen | explore | uncertain`, ephemeral + expiry),
  hypotheses, retrieval/keyword plans, inspection targets + quality rubric, slate
  guidance, ask/abstain, stop condition. Deterministic compiler validates against
  capabilities/budget/privacy and logs the replayable trace. Landed in **shadow mode**:
  every configured-model replenishment attempts and journals the typed proposal,
  diagnostics, compiler inputs, and agent provenance while the current uncertain-intent
  allocator executes unchanged. Live switching waits for shadow evidence.
- [x] 3. **Statistical allocation**: Thompson sampling with Beta posteriors over arms
  (strategy-family prior → per-hypothesis posterior when data exists); reward =
  viewed-and-engaged per the reward contract; unseen impressions ignored. Landed as a
  pure replayable allocator: intent deterministically conditions eligible strategies,
  uncertain intent lets exploit and active hypotheses compete, and uniform priors make
  cold-start exploration emerge without a policy percentage. Pipeline wiring remains B4.
- [x] 4. **Constrained slate + allocation wiring**: generalized the existing
  provider(2)/creator(1)/topic(2) quotas with hard exploration-slot reservation and
  supply-missing soft degradation. Production replenishment journals seeded Thompson
  decisions with temporary `intent="uncertain"`, acquires anonymous provider feeds, and
  preserves hypothesis/channel attribution through delivery and feedback. B2 supplies
  real intent next; embedding-based MMR waits for Phase C, and dormant-interest supply
  waits for ledger SQL.
- [ ] 5. **Evidence discipline**: exploration provenance + arm on observations; dismissals
  count only after viewport exposure; like-on-explore → low-confidence (~0.2)
  corroboration-gated proposal via the existing Understanding path; dismiss decays that
  arm only. Directional chat statements = decaying claims; only explicit statements can
  zero exploration (residual = optionality invariant). Cold start: elevated exploration
  for first N engagements.
- [x] 6. **Reward contract before learning**: multi-objective outcome vocabulary (explicit
  satisfaction/correction, meaningful consumption, voluntary return to a new area,
  repetition fatigue) — defined and logged before bandits train on it. Landed: typed
  vocabulary and deterministic feedback mapping; voluntary-return and repetition-fatigue
  derivation remain deferred until longitudinal/viewport evidence exists.

Acceptance: shadow briefs validate against the compiler for two weeks of feeds (or e2e
fixtures); live mode shows intent-conditioned allocation shifting with revealed
preference; replay reproduces any slate; explicit "stop exploring" zeroes exploration
and passive disengagement only shrinks it.

## Phase C — Understanding depth (spec #13–#14)

Goal: the profile becomes a living, evidence-grounded document and matching goes semantic.

- [ ] **Event-triggered re-synthesis**: bounded job re-synthesizes profile claims from
   evidence on triggers (explicit correction, contradictory evidence, drift); never
   schedule-driven full rebuilds. Trust-tier weighting: explicit user statements always
   outrank inference.
- [ ] **Embedding index + semantic retrieval**: durable index over evidence/profile
   fragments/candidates; vector recall feeds LLM rerank. Unlocks the adjacent arm.
   `RecommendationProfile` gains embedding-backed views.
- [ ] **Correction channel hardening**: assistant `propose_profile_revision` tool → pending
   action → `user_statement` evidence; dual-write to ledger + index.

Acceptance: a chat correction measurably shifts the next feed (e2e); re-synthesis fires
on triggers only; index recall quality reported against text-query baseline.

## Phase D — Rich acquisition (spec #12, #18)

Goal: credentialed depth and content-level quality judgment.

- [ ] **Plugin-assisted access**: provider-declared declarative credential recipes (domain,
   artifact list, warmup URL — data only); `GET /sources/{id}/access-recipe` +
   `POST /sources/{id}/access-material`; extension = generic grabber, one token,
   zero per-source logic; verification reuses the existing verifier/vault boundary.
   Must also fix vault-backed access rehydration on startup (found in A7: connections
   are in-memory only, so one-shot CLI commands and server restarts lose them).
- [ ] **Personalized feeds**: credentialed channels (Bilibili `rcmd` flagship) land as
   exploit-class supply; per-channel yield learned via the Phase B machinery.
- [ ] **Saves/history ingestion**: `Saved`/`History` capabilities feed the ledger as
   high-trust observations through the plugin path. `openbiliclaw import <provider>`
   for Takeout-style archives.
- [ ] **Multimodal inspection**: `recommendation.inspect` agent, shortlist-only, per-
   candidate, cached; sampled frames first (any vision model via `ImageUrl`/
   `BinaryContent`), native `VideoUrl` route when a configured model justifies it;
   modality-aware routing via the models.dev catalog; rubric from the brief; structured
   output (`actual_topic, quality, title_mismatch, summary`) feeds evaluation and the
   embedding index. Fail-open to metadata-only evaluation.

Acceptance: plugin connects Bilibili end-to-end without manual cookie paste; personalized
channel yield is measured per user; inspection runs only on shortlist and its judgments
visibly affect ranking; e2e covers recipe → grab → verify → personalized feed.

## Deferred (recorded, do not build without new evidence)

- Control arms / dual veto / learned quota-envelope shifts / reflexion machinery
  (shadow + replay first; single-user data is too sparse for clean experiments)
- Temporal admission lifecycle (needs pool depth evidence)
- Native video-input plumbing (frames suffice until a configured model justifies it)
- Browser-executed fetch / in-page signing (Douyin X-Bogus class) — only when a real
  source proves cookie + backend transport insufficient
- Push notification machinery (vision-approved, pull-first until product call)
- Per-item contextual bandits (LinUCB), collaborative filtering, RL — wrong regime for
  single-user sparse data

## Metrics that gate phases (ledger SQL only)

- Frontier expansion rate (headline): new neighborhoods first-engaged, attributed by
  exploration provenance
- Exposure/consumption gap: system-not-showing vs user-not-clicking
- Exploration vs exploit funnels; exploration slot position bias check
- Exploit-slot satisfaction guardrail (diversity must not be bought with relevance)
- Channel yield per (provider, feed_id); provider-algorithm dependence concentration
