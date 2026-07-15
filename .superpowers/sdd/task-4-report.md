# Task 4 report — protocol adapters from connection records

## Result

Implemented the explicit construction boundary requested by
`.superpowers/sdd/task-4-brief.md`:

- `build_chat_adapter(connection, runtime_options) -> LLMProvider`
- `build_embedding_adapter(provider, settings, runtime_options) -> SupportsEmbedding`

The factory builds one adapter from one immutable model-configuration record. It
does not assemble an ordered route, change the active runtime registry, add a
circuit breaker, or expose a new API/UI/CLI configuration surface.

## Production changes

- Added frozen, secret-safe `AdapterRuntimeOptions`. Its optional environment
  mapping is the exact source for `env` credentials; proxy and `trust_env` are
  intentionally not caller-controlled.
- Added `OpenAIProtocolProvider` with frozen `OpenAIProtocolOptions`. OpenAI,
  DeepSeek, OpenRouter, custom OpenAI-compatible, and Codex OAuth records use
  this exact class. API mode, reasoning body, and attribution headers are
  per-instance immutable hooks.
- Removed the legacy `DeepSeekProvider`'s temporary mutation of
  `_reasoning_effort`; concurrent calls now carry their effective effort as a
  local per-request value while retaining the public constructor/API.
- Added one `AnthropicCompatibleProvider` for official and custom Anthropic
  Messages endpoints, including prompt-cache system blocks, normalized usage,
  retry, timeout, rate-limit, and secret-safe error handling.
- Extended native Gemini and Ollama providers with optional stable provider
  names. Factory-created adapters use connection IDs in responses and errors.
  Ollama retains `num_ctx` and is forced direct in the new factory.
- Added strict Codex OAuth endpoint validation. The endpoint must be exactly
  `https://api.openai.com/v1` (or blank, which canonicalizes to it) before any
  token loader is called; scheme, host, explicit port, extra path, query,
  fragment, and userinfo variants fail closed.
- Added `EmbeddingProtocolAdapter`, which retains the exact shared
  `EmbeddingModelSettings` object supplied by the caller and passes its model
  and output dimensionality to the selected native provider.
- Re-exported the construction API from `llm.registry` without changing
  `build_llm_registry()` or `build_embedding_service()` behavior.

## Credential and transport contract

- `inline` uses only the stored value.
- `env` reads only the configured variable name from the exact supplied mapping
  or the process environment when no mapping is supplied.
- `oauth` accepts only the Codex credential reference on a `codex_oauth`
  connection.
- `none` is accepted only when the code-defined connection descriptor omits the
  credential field (currently local Ollama).
- Missing or invalid values raise fixed, secret-safe factory errors and do not
  include friendly names, type/preset labels, references, or secret values.
- The factory resolves endpoint-aware proxy policy internally through
  `openbiliclaw.network`; Ollama bypasses inherited proxies unconditionally.

## TDD evidence

Initial RED, after adding adapter-selection, hook, credential, OAuth, proxy,
embedding, and concurrency tests before production code:

```text
.venv/bin/pytest tests/test_llm_connection_factory.py tests/test_llm_providers.py -q
35 failed, 62 passed, 1 warning in 19.22s
```

All new failures were caused by the absent connection factory/protocol adapter
classes. A final self-review found one hard-coded Gemini 5xx label. Its focused
regression first failed with:

```text
Expected: gemini-a server error: 503
Actual:   gemini server error: 503
```

After the one-line provider-name fix, that regression passed.

One compatibility regression was also caught during GREEN: constructing a
legacy `OllamaProvider` with the new default forced an explicit HTTP client and
broke its existing constructor test. Root cause was a changed legacy default;
the fix restored legacy `trust_env=True` while the new factory explicitly
passes `trust_env=False` for Ollama. Both the legacy regression and factory
direct-transport test then passed.

## Final verification

Required focused matrix, rerun after the final review fix:

```text
.venv/bin/pytest tests/test_llm_connection_factory.py tests/test_llm_providers.py tests/test_network_proxy_isolation.py -q
121 passed, 1 warning in 18.87s
```

Static and formatting checks:

```text
.venv/bin/ruff check src/openbiliclaw/llm tests/test_llm_connection_factory.py tests/test_llm_providers.py
All checks passed!

.venv/bin/mypy src/openbiliclaw/llm
Success: no issues found in 22 source files

.venv/bin/ruff format --check src/openbiliclaw/llm tests/test_llm_connection_factory.py tests/test_llm_providers.py
24 files already formatted

git diff --check
clean
```

The single requested full-suite run completed successfully:

```text
.venv/bin/pytest -q
5086 passed, 41 skipped, 2752 warnings in 150.04s
```

Warnings are existing dependency/framework deprecations (Google GenAI,
Starlette/FastAPI); no Task 4 failure remains.

## Test coverage and isolation

Fake SDK/client surfaces cover:

- exact adapter types for all four OpenAI-compatible presets and both
  Anthropic presets;
- DeepSeek body/max-token hooks, OpenRouter attribution headers, Responses API
  selection, custom base URLs, native Gemini construction, and Ollama
  `num_ctx`/direct transport;
- deeply immutable options and concurrent calls with different reasoning
  overrides, headers, and API modes, including the legacy DeepSeek regression;
- exact inline/environment credential selection, fixed missing-credential
  errors, descriptor-gated no-credential behavior, and secret-free repr/error
  paths;
- malicious Codex endpoint variants rejected before token lookup;
- endpoint-aware proxy resolution inside the factory;
- every registry embedding capability (OpenAI/custom, Gemini, DashScope,
  Ollama), exact shared-settings identity, shared model propagation, and OpenAI
  output dimensionality.

No test contacts a model service, a local Ollama daemon, or the real Codex
credential store.

## Documentation and boundary review

Updated the LLM module feature table, public construction API, credential and
network contracts, design decisions, changelog, architecture/spec diagrams,
and matching README diagrams. No CLI/config/installer docs changed because the
task adds no such surface.

Legacy provider constructors and registry/service builders remain available.
No ordered route, fallback orchestration, circuit state, runtime cutover,
migration transaction/backup, model configuration API, or UI was added.

## Commit

Planned commit message: `refactor: build llm protocol connections`.
