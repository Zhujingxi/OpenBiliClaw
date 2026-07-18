# Event Satisfaction Signal Design

## Goal

Break the passive-engagement feedback loop where a clickbait-baited click is indistinguishable from a deliberate interest signal. Attach a deterministic `inferred_satisfaction` field to every behavior event at ingestion time, and gate preference analysis on it so the profile only learns from events the user appears to have actually wanted. This is "Direction C — minimum version" from the discovery-quality investigation: smallest reachable change that severs the self-poisoning loop without depending on LLM judgment.

## Current Gaps

- `ProfileSignal` (in `src/openbiliclaw/soul/pipeline.py`) carries a `confidence: float` field, but the value is set by the producer of the signal, not derived from observable behavior; for the bulk of `behavior_event` ingestions it stays at 0.0 and is not used downstream as a quality gate.
- `preference_analyzer.analyze_events` consumes the raw event list without any "did the user actually like this" filter. A user who was baited by a clickbait title and bounced in 2 seconds contributes the same positive interest weight as a user who watched 8 minutes and liked the video.
- Recommendation click-throughs are persisted as normal `click` events with `metadata.source = "recommendation_click"` (`api/app.py:2277+`). They do not carry dwell, watch-completion, or quick-return data, so the system cannot distinguish "user opened my recommendation and stayed" from "user opened my recommendation, regretted it, and closed the tab in 3 seconds".
- The current explicit-positive vocabulary (like / coin / favorite / comment via `_ENGAGEMENT_TYPES`) is correct but coarse. There is no symmetric explicit-negative vocabulary (quick-exit or explicit `feedback_type=dislike`), so the preference layer only learns from positive signals — exactly the structural shape that causes engagement-bait collapse on incumbent platforms.
- The extension content collector is implemented in `extension/src/content/kernel.ts`, not a `collector.ts` file. It already emits video `view`/`pause` events with `currentTime` and `duration`, but it does not emit a final dwell summary when the user leaves a video page, so the data needed to infer quick-exit / meaningful dwell is still partially uncaptured.

## Chosen Approach

Treat `inferred_satisfaction` as a **first-class field on the event record** that every event must carry, classified by a small, deterministic rule table at the moment the event is ingested or finalized. This is rule-based on purpose: it must be auditable, cheap, and explainable. The downstream filter is one new helper that the preference analyzer (and later, the negative-anchor builder from the eval-batch plan) calls before reading events.

Three pieces, all behind a single feature flag for safe rollout:

1. **Event-side classification.** Add `inferred_satisfaction: Literal["positive", "neutral", "negative", "unknown"]` and `satisfaction_reason: str` to the event record. A new helper in `src/openbiliclaw/sources/event_format.py` computes the value from the event's type and payload using a small rule table:
   - Explicit positive (`like`, `coin`, `favorite`, `comment`, or `feedback` with `metadata.feedback_type in {"like", "comment"}` / thumbs-up reaction) → `positive` / `reason="explicit_engagement"`.
   - Explicit negative (`feedback` with `metadata.feedback_type == "dislike"` or thumbs-down reaction) → `negative` / `reason="explicit_negative"`.
   - `click` with payload `watch_seconds >= max(15, 0.3 * video_duration)` → `positive` / `reason="meaningful_dwell"`. Recommendation click-throughs are detected as `event_type == "click"` plus `metadata.source == "recommendation_click"`, not a separate DB event type.
   - `click` with payload `watch_seconds < 5` → `negative` / `reason="quick_exit"`. v1 is intentionally one-pass and does not retroactively reconcile a later positive engagement against an earlier quick-exit row.
   - Passive events already accepted by memory (`snapshot`, `scroll`, `hover`, `search`, `pause`, `seek`) → `neutral` or `unknown` depending on whether enough dwell data exists. Do not introduce unsupported event types like `dismiss`, `hide`, `not_interested`, `browse`, or `history_sync` unless `_EVENT_TYPES` and API producers are updated in the same PR.

2. **Capture the missing inputs.** Extend the extension collector kernel and the `/api/events` / `/api/recommendation-click` ingestion models to carry `watch_seconds` and `video_duration_seconds` on relevant click events. For events that already exist in the database without those fields, classification falls back to `unknown` — no migration of historical row values.

3. **Filter at consumption.** Add a `filter_events_by_satisfaction(events, *, modes)` helper in `src/openbiliclaw/soul/event_filters.py`. Add dataclass config models for `[soul.preference] satisfaction_filter_enabled = false`, then pass the flag into `SoulEngine` / `PreferenceAnalyzer` construction. `PreferenceAnalyzer.analyze_events` calls the helper with `modes={"positive", "unknown"}` only when the flag is enabled. When disabled, behavior remains unchanged.

## Data Flow

1. Extension kernel tracks `dwell_started_at` on Bilibili video-page sessions; on SPA route change / `pagehide`, it emits a final click-like dwell event for the previous video page with `metadata.watch_seconds`, `metadata.video_duration_seconds`, and `metadata.dwell_source = "video_page_exit"`.
2. `/api/events` and `/api/recommendation-click` preserve those dwell fields in metadata. They do not classify independently; the single classification owner is the persistence path.
3. `MemoryManager.propagate_event` passes the existing event fields to `Database.insert_event`; `insert_event` reconstructs the full event dict from `event_type` + kwargs, calls `classify_event_satisfaction(event)` once, and writes `inferred_satisfaction` / `satisfaction_reason` to the event row.
4. When `preference_analyzer.analyze_events` consumes events, it calls `filter_events_by_satisfaction(events, modes=...)` only if the config-backed flag on the analyzer is enabled. Disabled means pass all events through.
5. The filtered list is then passed to the LLM exactly as before — no prompt-builder change needed.

## Error Handling

- A classifier helper that hits an unexpected event shape returns `("unknown", "fallback")` and logs at `DEBUG`. Classification must never raise; an event with no satisfaction is still a valid event.
- A missing `inferred_satisfaction` column on a freshly migrated database is tolerated by the consumer side (treat as `unknown`) so the upgrade does not require a hot-restart in lockstep.
- The config flag defaults to `False`. Until the operator flips it, the filter is a no-op; behavior is byte-identical to today. This is the rollout safety: classification can run, be observed via the cost / event dashboards for a release cycle, and only then be turned on.

## Testing

- Unit tests for `classify_event_satisfaction` covering every rule branch (each explicit positive type, `feedback_type=dislike`, meaningful_dwell threshold edges, quick_exit threshold, missing fields → unknown, scroll/snapshot/search → neutral).
- Unit tests for `filter_events_by_satisfaction`: empty modes returns empty, `{"positive"}` drops neutrals, `{"positive", "unknown"}` keeps unclassified rows, deterministic ordering preserved.
- A `preference_analyzer` integration test that runs `analyze_events` twice on the same event list — once with the flag off, once with the flag on — and asserts the second call's LLM input excludes the quick-exit and explicit-negative events.
- Storage and memory-manager tests verifying the new columns exist after first boot, pre-migration events get `inferred_satisfaction = NULL` (treated as `unknown` by the consumer), and `query_events(satisfaction_modes=...)` passes through `MemoryManager`.
- Extension tests verifying that on a navigation-away event the kernel emits a dwell event with `watch_seconds` populated and that the service worker forwards it without stripping metadata.
- A live integration test (manual or marked `@pytest.mark.integration`) that ingests one quick-exit + one long-dwell event and asserts the preference layer's resulting `interests` weight is **higher** for the long-dwell topic than for the quick-exit topic.

## Documentation

Update `docs/modules/soul.md` (event-filter contract and the flag-driven rollout), `docs/modules/storage.md` (new `inferred_satisfaction` and `satisfaction_reason` columns), `docs/modules/extension.md` (collector kernel now emits dwell + completion data), `docs/modules/api.md` (event ingest payload extended), `docs/modules/config.md` (`soul.preference.satisfaction_filter_enabled` flag and the default value), `docs/architecture.md`, `docs/spec.md` §3, `README.md`, `README_EN.md`, and `docs/changelog.md` (`feat(soul): inferred_satisfaction signal — preference layer can be gated to ignore quick-exit / explicit-negative events`).
