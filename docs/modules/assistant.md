# Assistant

`src/openbiliclaw/assistant/` is a bounded PydanticAI dialogue facade above Application Workflows. Its stable agent identity is `assistant.dialogue`. It receives only a safe application facade, a versioned and bounded `DialogueProfile`, locale, and local-user/device-scoped conversation metadata. The canonical profile, evidence ledger, credential vault, provider secrets, and repositories never enter agent dependencies.

## Implemented

- four discriminated outputs: message, recommendation presentation, clarification, and pending action;
- interactive `RunPolicy`: 6 tool calls, 12k input / 2k output tokens, 45-second timeout, and one retry;
- eight native workflow tool contracts selected by intent, connected provider, and skill/capability; global exposure is prohibited;
- provider-native read tools reuse the bounded Content Integration tool contract; all results are sanitized and clamped before history;
- `AssistantSkill` contains only a stable ID, tool factory, model requirements, and static instructions; it has no lifecycle, hook, or credential;
- conversation/message/tool-summary/pending-action/usage models, SQLite restart, retention, scope, and deletion; successful validated turns atomically persist the user message, structured Assistant response, friendly sanitized tool summaries, and conversation timestamp, while reasoning and native tool payloads are never stored;
- full-window transcript projection that reconstructs only persisted complete user/Assistant turns, estimates instructions, tool definitions, profile, history, and current input, reserves about 20% of the configured model window for output/tool work, and excludes only the oldest complete turns when needed;
- an approximate context meter reports input-window use and the count of oldest turns excluded; excluded transcript remains persisted and readable, with no automatic summarization;
- exact-effect/expiry presentation for pending actions and replay-safe deterministic confirmation;
- a dialogue observation filter that permits only explicit preferences, explicit feedback, confirmed edits, and defined outcomes; ordinary Assistant messages are not learned;
- a landed profile-correction channel: `propose_profile_revision(field, operation, value, rationale)` accepts only an existing claim ID or `exploration.disabled`, derives a deterministic idempotency key from the effect tuple, and first persists a scoped, expiring pending action without changing the profile. Only approval through the unified confirmation endpoint invokes canonical `EditProfile`; `POST /v1/content/actions/reject` rejects explicitly. SET creates a same-kind, trust-1.0 statement claim from the user-supplied new value; REMOVE only removes. Statement evidence and the accepted claim are best-effort indexed by the shared C2 hook. Assistant exposes no direct-mutation tool.

Provider/tool/profile text is always untrusted data, never instructions. Known secret markers, credential references, and oversized messages/tool results are rejected before model execution or persistence. Reconstructed history projects structured Assistant responses to visible text and includes only persisted sanitized tool summaries, never native tool payloads or reasoning.

## Model compatibility

PydanticAI output tools enforce Assistant's discriminated output. The output tool's `kind` schema enumerates the four valid discriminators—message, recommendations, clarification, and pending_action—so providers cannot generate arbitrary strings that the validator must reject. Provider protocol and capability routing come from the models.dev catalog or a complete custom declaration; catalog-routed `kimi-for-coding` uses its declared Anthropic protocol. `[model.options].disable_thinking` affects only explicit OpenAI-protocol construction and does not alter Anthropic, Google, or OpenRouter providers.

## Turn lifecycle

The canonical Composition controller emits `turn_started`, textual-provider-reasoning start/delta/finish, sanitized tool start/finish, validated visible response delta, `turn_finished`, or safe `error`. Both streaming HTTP and the existing non-streaming call consume this workflow. Cancelling the consumer propagates directly into `AIRuntime.stream`; there is no stop registry or stop endpoint.

## Composition

`composition/assistant.py` constructs the Assistant dependencies and registers the dialogue agent in the single production graph. Hosts reach it through Application/Assistant facades; deleted legacy dialogue, orchestrator, integration, and fake-tool paths have no compatibility surface.
