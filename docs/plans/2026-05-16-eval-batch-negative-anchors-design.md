# Eval Batch Negative Anchors Design

## Goal

Stop `eval_batch` from rubber-stamping clickbait / course-pitch candidates by feeding it concrete in-context examples of recent content the user clearly didn't enjoy. This is "Direction A" from the discovery-quality investigation — a fast, model-agnostic tightening of the LLM's evaluation prompt that does not depend on regex keyword lists. It complements the satisfaction-signal plan: the satisfaction work makes the upstream profile cleaner; this work makes the downstream evaluation skeptical, even before the profile has been re-cleaned by the next preference cycle.

## Current Gaps

- `build_batch_content_evaluation_prompt` (in `src/openbiliclaw/llm/prompts.py:1025`) currently passes only `profile_summary`, `source_platform`, `source_context`, and `content_batch` to the LLM. There is no field carrying recent **negative exemplars** that the model can pattern-match against.
- The user's `disliked_topics` is a small list of abstract labels (`"低质标题党"` etc.). LLMs treat such abstract labels as soft, low-priority advisory text and apply them inconsistently to concrete candidates like *"【吴恩达】2026年公认最好的【Claude Code】教程！…一套全解决！"*.
- A 6-hour log capture showed a course-pitch candidate (*"Codex (APP) 保姆级全攻略，海量实战教程，一期精通Codex"*) flowing all the way through `eval_batch` → `recommendation.expression` and being pushed to the user as a "惊喜推荐". The evaluator scored it positively; the expression layer had no veto power and dutifully wrote a reason for it. The structural fix in this plan is to give the evaluator concrete negative-pattern anchors so it never scores such candidates positively in the first place.
- The system has no first-class store of "negative exemplars". The satisfaction-signal plan creates the necessary raw material (events classified as `negative` with reason `quick_exit` or `explicit_negative`), so this plan can depend on it.

## Chosen Approach

Add a new optional `negative_examples` block to the batch evaluation prompt — a short, deterministic, recency-weighted list of the titles the user actually quick-exited or explicitly disliked, with the disqualifying reason attached. The LLM is instructed to **pattern-match candidates against these examples** and downscore on resemblance (in title shape, claim structure, or commercial-intent language), not on keyword overlap.

The block is omitted when the user has no negative exemplars (cold start), keeping the **user-message variable prefix** byte-identical to the current shape for cold-start users. The system prompt will change once when the two permanent rules are added, but it must remain call-invariant after that change. When examples are present, the block sits in the user message between `<source_context>` and `<content_batch>` — stable enough to extend the cache prefix across the lifetime of an exemplar epoch.

Three pieces:

1. **A small store of negative exemplars** built from the satisfaction-classified event stream. A new helper `recent_negative_exemplars(event_store, *, limit=8, half_life_days=14)` accepts a `Database` or any object exposing `query_events(satisfaction_modes=..., limit=...)`, queries events with `inferred_satisfaction = "negative"`, sorts by recency-weighted score using `created_at`, dedupes by normalized title prefix, and returns up to `limit` records carrying `{title, reason, age_days}`. No LLM call.

2. **Prompt builder change.** `build_batch_content_evaluation_prompt` accepts a new optional kwarg `negative_examples: list[dict] | None = None`. When provided and non-empty, the user message includes a `<negative_examples>` block before `<content_batch>`; when `None` or empty, the block is omitted entirely (no empty tag emitted) so the user-message variable prefix is identical to the no-examples path. System prompt picks up two permanent rules about how to use the block and stays call-invariant after that one-time template change.

3. **Caller change.** `discovery/engine.py`'s eval-batch call site fetches the exemplars via the new helper from `self._database` (read-through with a 5-minute TTL cache keyed by `Database.get_latest_event_id()`, to keep I/O flat) and forwards them to the prompt builder.

This plan is **gated on the satisfaction-signal plan landing first**, because without classified negative events there is nothing meaningful to put in the block. If the satisfaction filter flag is still off in production, the negative store will be small or empty and the block will simply be omitted — graceful degradation by construction.

## Data Flow

1. Discovery cycle starts an eval_batch for a candidate set.
2. Eval-batch caller asks `recent_negative_exemplars(self._database)` for up to 8 normalized exemplars from the recent event store, recency-weighted.
3. Caller passes those exemplars to `build_batch_content_evaluation_prompt(..., negative_examples=exemplars)`.
4. The prompt builder emits the `<negative_examples>` block (when non-empty) after `<source_context>` and before `<content_batch>`, while the two new rule lines live permanently in the system block.
5. The LLM returns its per-candidate scores; candidates resembling the exemplars get downscored.
6. Existing diversity, caps, and dedup logic apply downstream; no other call site changes.

## Error Handling

- A failure inside `recent_negative_exemplars` (storage glitch, missing column on a stale DB) returns `[]` and logs at `DEBUG`. The eval batch proceeds with no `<negative_examples>` user block.
- The prompt builder treats `None` and `[]` identically: block omitted from the user message. The system prompt remains byte-identical across calls because the new rules are permanent additions to the constant, not call-conditional text.
- The helper caps `limit` at 8 even if a caller asks for more, to bound prompt size growth.
- Exemplar title text is truncated to 80 chars per row to bound worst-case prompt cost; truncation marker is `…` on the right.

## Testing

- Unit tests for `recent_negative_exemplars`: empty store → `[]`; correct recency weighting (newer rows ranked higher); dedup by normalized title prefix (so two near-identical clickbait variants don't both occupy a slot); cap at `limit`; survives a DB without the satisfaction columns by returning `[]`.
- Prompt-builder tests proving:
  - `build_batch_content_evaluation_prompt(..., negative_examples=None)` produces the same user-message bytes as the no-examples path.
  - `build_batch_content_evaluation_prompt(..., negative_examples=[])` likewise produces the same user-message bytes as `None`.
  - With non-empty examples, the user message contains `<negative_examples>` between `<profile_summary>` and `<content_batch>` in that order; the system prompt is **still byte-identical to the constant** (the new rules must be permanent additions to the constant, not call-conditional).
  - `test_prompt_builder_system_messages_are_call_invariant` continues to pass.
- A discovery-engine integration test that runs an eval batch with two synthetic candidates — one resembling a real captured negative exemplar (e.g. `"被微电子男朋友的学识震惊到"`-style title) and one neutral — and asserts the LLM scores the resembling candidate lower. Marked `@pytest.mark.integration` since it needs a live or replayed LLM; ship a fallback that asserts the prompt **contained** both the exemplar and the candidate even if the LLM client is stubbed.

## Documentation

Update `docs/modules/discovery.md` (eval-batch now consumes recent negative exemplars), `docs/modules/llm.md` (prompt builder gained an optional kwarg and two new system-prompt rules; user block omitted when examples are absent), `docs/modules/soul.md` (cross-reference: the satisfaction signal is the upstream producer for this consumer), `docs/architecture.md` / `docs/spec.md` if their discovery data-flow diagrams include eval-batch inputs, and `docs/changelog.md` (`feat(discovery): eval_batch evaluator now anchored on recent quick-exit / negative exemplars to suppress clickbait look-alikes`).
