# Module Plan 12: Assistant

## Outcome

Replace custom Socratic dialogue and fake-tool plumbing with `src/openbiliclaw/assistant/`, a bounded PydanticAI conversational facade over Application Workflows. The Assistant helps users search, understand recommendations, inspect/edit their profile, and propose actions; it does not orchestrate proactive product behavior.

## Target package

```text
assistant/
├── agent.py             # PydanticAI Agent definition
├── dependencies.py      # safe workflow facade and dialogue profile
├── tools.py             # native typed tools
├── skills.py            # narrow AssistantSkill extension contract
├── history.py           # conversation repository port and compaction
├── actions.py           # pending-action presentation/confirmation
├── service.py           # turn execution and persistence
├── models.py            # typed turn/result models
└── repository.py
```

## Agent contract

- Stable `AgentId`: `assistant.dialogue`.
- Typed dependencies contain an application facade, bounded `DialogueProfile`, locale, and conversation metadata.
- Typed output distinguishes message, recommendation presentation, clarification request, and pending action.
- Run policy limits history, tools, tool calls, tokens, retries, and elapsed time.
- Instructions treat provider content and tool results as untrusted data, never instructions.

## Native tools

Expose only relevant tools per turn:

- `get_recommendations`
- `search_content`
- `get_content_details`
- `record_feedback`
- `show_profile`
- `edit_profile`
- `list_sources`
- `connect_source`
- provider-native read tools selected by provider/capability

Tools call Application Workflows. Mutation tools create/confirm pending actions as policy requires.

## Internal phases

### Phase 1 — Conversation model and persistence

- Define conversation, turn, message, tool-call summary, pending action, and usage models.
- Store model/provider-neutral message history needed by PydanticAI without storing secrets or oversized raw tool payloads.
- Scope conversations to local user/device identities.
- Add retention/deletion controls and restart tests.

### Phase 2 — Agent and safe dependencies

- Create the PydanticAI agent with typed dependencies and output.
- Supply only `DialogueProfile`, never the canonical profile/evidence ledger.
- Add prompt-injection boundaries around content/tool data.
- Configure model requirements and bounded run policy through AI Runtime.
- Add deterministic tests for normal response, clarification, unavailable model, and invalid output.

### Phase 3 — Native workflow tools and skills

- Implement tools as thin typed functions over application commands/queries.
- Define `AssistantSkill` as a stable ID plus bounded tool factories, capability requirements, and optional static instructions; skills receive only the safe application facade and cannot own lifecycle, persistence, credentials, or arbitrary hooks.
- Register skills through Core's typed extension registry and reject duplicate tool names or incompatible capability requirements at startup.
- Select toolsets based on intent hints, connected providers, registered skills, and capability availability; do not expose all provider tools globally.
- Return previews and opaque references first; fetch details on demand.
- Bound and sanitize every tool result before it enters history.
- Remove source tool dispatch and prompt-described tool emulation.

### Phase 4 — Actions and confirmation

- Render safe pending actions with exact effects and expiry.
- Require deterministic confirmation for external mutations, credential replacement, profile destructive edits, and sensitive operations.
- Revalidate action scope when confirmed.
- Never let model text become a provider request without typed workflow validation.

### Phase 5 — History compaction

- Keep a bounded recent message window plus typed summary state.
- Compact only when limits require it; do not summarize every turn.
- Preserve unresolved actions, user corrections, and references explicitly.
- Record compaction model/version and test that summaries cannot introduce confirmed facts.

### Phase 6 — Dialogue observation and cleanup

- Emit typed observations only for explicit user preference statements, feedback, confirmed edits, and defined dialogue outcomes.
- Do not treat every assistant message as learning evidence.
- Replace `soul/dialogue.py`, dialogue schedulers/queues/settlement helpers, `integrations/agent.py`, and old fake-tool paths.
- Delete dead generic agent orchestrator/skill scaffolding.

## Tests and quality gates

- PydanticAI test models cover every output variant and tool.
- Injection tests place hostile text in provider content, profile labels, and tool results.
- Secret canaries confirm Assistant messages, dependencies, history, and errors contain no secrets.
- Tool/skill registration and selection tests enforce duplicate rejection, scoped exposure, capability checks, and result budgets.
- Action tests cover confirmation, expiry, replay, authorization, and changed provider state.
- MyPy preserves typed dependencies and outputs end to end.

## Documentation updates during implementation

Update dialogue/assistant, API, integrations, privacy, tool, model, and architecture docs plus `docs/changelog.md`.

## Completion criteria

- The Assistant has native typed tools only.
- It calls Application Workflows and imports no repository or credential implementation.
- Conversation context is bounded and secret-free.
- Proactive recommendations function independently of Assistant availability.
- Old Socratic/fake-tool/orchestrator paths are deleted.
