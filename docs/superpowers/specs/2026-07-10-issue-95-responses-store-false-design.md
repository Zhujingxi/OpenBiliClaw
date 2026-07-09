# Issue #95 Responses `store=false` Design

## Context

OpenBiliClaw routes `[llm.openai]` and `[llm.openai_compatible]` through the
OpenAI Responses API when `api_flavor = "responses"`. The current request body
omits the standard `store` field. Gateways backed by the ChatGPT/Codex Responses
endpoint reject that request with HTTP 400 and `Store must be set to false`.

## Decision

Every Responses API request will explicitly pass `store=False` through the
OpenAI Python SDK. The Chat Completions path remains unchanged.

This is intentionally not configurable. OpenBiliClaw sends the complete input
for every call and does not use `previous_response_id` or another server-side
conversation-state feature, so enabling response storage has no functional
benefit. A fixed false value is compatible with the official Responses API and
with stricter OpenAI-compatible gateways.

## Request flow

`OpenAIProvider.complete()` continues to select `_complete_via_responses()`
only when `api_flavor == "responses"`. That method adds `store=False` to its
top-level SDK keyword arguments. Existing retries that remove an unsupported
`temperature` or an empty-output `text.format` constraint reuse the same
keyword-argument dictionary and therefore retain `store=False`.

## Error handling and scope

No new fallback or error-text matching is introduced. In particular, the
broader retry policy that currently retries deterministic HTTP 400/404 errors
is outside issue #95 and should be handled separately so this compatibility fix
does not alter provider-wide retry behavior.

## Verification

- Add a focused provider test that captures the Responses SDK call and asserts
  that server-side storage is disabled.
- Verify the test fails before the production change and passes afterward.
- Run the complete OpenAI/LLM provider test module, the full Python suite,
  Ruff, and strict MyPy.
- Update the LLM module documentation and current changelog entry.
