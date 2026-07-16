# Model Configuration Full-Stack Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the provider-bucket model configuration with schema-driven connection types, one ordered Chat route of up to ten equal peers, and one ordered Embedding route whose providers share a single model/settings block across every supported UI, CLI, setup, installer, and runtime path.

**Architecture:** Introduce a standard-library-only model-config domain package that owns types, descriptors, validation, TOML serialization, legacy migration, revisions, secret actions, and transactional saves. Build Chat and Embedding runtime routes from stable connection IDs through protocol adapters, then expose one dedicated API consumed by desktop Web, mobile Web, the extension, CLI, guided setup, Docker bootstrap, and desktop packaging. Keep legacy [llm] read compatibility for one release, but make [models] authoritative and protect it from old /api/config writers.

**Tech Stack:** Python 3.11+ dataclasses and asyncio, FastAPI/Pydantic v2, TOML via tomllib plus the repository renderer, OpenAI/Anthropic/Google SDKs, SQLite migrations, Typer/Rich, vanilla JavaScript/TypeScript-compatible ES modules, node:test, pytest/pytest-asyncio, Ruff, and strict MyPy.

## Global Constraints

- Work only on branch codex/model-config-refactor.
- [models].schema_version starts at 1. A native [models] table always wins when [models] and [llm] coexist.
- Chat has 1..10 ordered connections. Index 0 is Primary; later indices are Fallback 1..9. No stored role, priority, or fallback_enabled field exists.
- Embedding is disabled with zero providers or enabled with 1..10 ordered providers. model, output_dimensionality, similarity_threshold, and multimodal_enabled exist only at models.embedding.settings, never on a provider.
- Stable IDs survive rename and reorder. IDs are unique across Chat connections and Embedding providers so probe, circuit, usage, and error paths remain unambiguous.
- OpenAI, DeepSeek, OpenRouter, and custom gateways are presets of openai_compatible. Anthropic official and custom gateways are presets of anthropic_compatible. Presets fill untouched defaults only.
- Codex OAuth is its own codex_oauth connection type. Its token is never serialized and can be sent only to the official OpenAI endpoint.
- dashscope_api is Embedding-only. A type or preset appears only when its capability metadata includes the selected route kind.
- Model secrets never appear in GET responses, logs, exceptions, migration reports, probe histories, or diffs. Writes use keep, set, clear, or env actions.
- Internal credential values use repr=False and are converted to public status through explicit serializers; never pass an internal ModelConfig through dataclasses.asdict for logging or API output.
- A model PUT must carry the current revision. A stale revision returns 409 without disk or runtime mutation.
- timeout_seconds is the whole Chat route deadline. Provider retries consume that budget before fallback; a new attempt receives only the remaining time.
- Ordinary save_config calls preserve the on-disk [models] or [llm] section. Only ModelConfigService may authoritatively rewrite model configuration or perform the first legacy migration.
- Legacy startup and unrelated config writes never migrate the file. The first explicit model save creates a permission-preserving pre-model-refactor backup before writing [models].
- Candidate runtime construction happens before persistence. Disk write uses a same-directory temporary file, flush, fsync, and os.replace. A failed swap restores the old file and old runtime bundle.
- In-flight requests keep their old immutable route reference; new requests observe the swapped bundle. One lock serializes model saves and swaps.
- Module-specific model overrides are removed. Every Soul, discovery, recommendation, evaluation, API, OpenClaw, and CLI LLM call uses the same ordered route.
- The four product surfaces are mandatory: extension popup, desktop Web, mobile Web, and CLI. Guided setup, agent bootstrap, Docker, and desktop packaging are also in scope.
- Default tests use fake transports only. Do not call a real API, Codex OAuth, or local Ollama in the automated suite.
- Every public interface/config/data-flow change updates the documentation required by CLAUDE.md before the branch is considered complete.
- Before every commit, inspect git status and stage only the exact files named by that task; never sweep unrelated user changes into a broad directory add.

## File Map

- Create src/openbiliclaw/model_config/ for domain types, the descriptor registry, validation, serialization, migration, revisions, and ModelConfigService.
- Create src/openbiliclaw/llm/connection_factory.py, route.py, and embedding_route.py for adapter construction and ordered execution.
- Create src/openbiliclaw/api/model_config_models.py and model_config_routes.py for the dedicated API.
- Create src/openbiliclaw/web/shared/model-config-state.js for desktop/mobile draft operations.
- Create desktop, mobile, and extension model-editor modules; remove their hard-coded provider forms.
- Create src/openbiliclaw/cli_models.py and register the openbiliclaw models command group.
- Modify config.py, runtime_context.py, LLM providers/service, health/setup paths, usage storage, installers, agent bootstrap, packaging entry, and all required docs.

---

### Task 1: Define the Domain Model and Connection-Type Registry

**Files:**
- Create: src/openbiliclaw/model_config/__init__.py
- Create: src/openbiliclaw/model_config/types.py
- Create: src/openbiliclaw/model_config/registry.py
- Create: src/openbiliclaw/model_config/validation.py
- Create: tests/test_model_config_types.py
- Create: tests/test_model_connection_types.py
- Modify: docs/modules/config.md

**Interfaces:**
- Produce immutable CredentialConfig, ChatConnection, ChatRouteConfig, EmbeddingModelSettings, EmbeddingProviderConfig, EmbeddingRouteConfig, and ModelConfig dataclasses.
- Produce ConnectionTypeDefinition, PresetDefinition, and FieldDefinition metadata with a JSON-safe public descriptor.
- Produce validate_model_config(config, registry) -> list[ModelConfigIssue].
- Produce default_model_config() with one editable DeepSeek Chat connection and disabled Embedding.

- [ ] **Step 1: Write failing shape and invariant tests**

~~~python
def test_chat_roles_are_derived_only_from_order() -> None:
    config = model_config(chat_ids=("first", "second", "third"))
    assert [config.chat.role_at(i) for i in range(3)] == [
        "primary",
        "fallback_1",
        "fallback_2",
    ]
    assert "priority" not in asdict(config.chat.connections[0])
    assert "fallback_enabled" not in asdict(config.chat.connections[0])


@pytest.mark.parametrize("count,valid", [(0, False), (1, True), (10, True), (11, False)])
def test_chat_route_size_is_one_through_ten(count: int, valid: bool) -> None:
    issues = validate_model_config(model_config(chat_count=count), connection_type_registry())
    has_count_issue = any(issue.code == "chat_connection_count" for issue in issues)
    assert has_count_issue is not valid


def test_embedding_provider_has_no_model_slot() -> None:
    fields = {field.name for field in dataclasses.fields(EmbeddingProviderConfig)}
    assert "model" not in fields
~~~

- [ ] **Step 2: Write failing descriptor tests**

~~~python
def test_registry_groups_protocol_local_and_oauth_types() -> None:
    descriptors = connection_type_registry().public_descriptors()
    by_id = {item["id"]: item for item in descriptors}
    assert by_id["openai_compatible"]["presets"] == [
        "openai", "deepseek", "openrouter", "custom"
    ]
    assert by_id["anthropic_compatible"]["presets"] == ["anthropic", "custom"]
    assert by_id["codex_oauth"]["category"] == "oauth"
    assert by_id["dashscope_api"]["capabilities"] == ["embedding"]


def test_deepseek_preset_is_not_an_embedding_choice() -> None:
    registry = connection_type_registry()
    assert registry.presets_for("openai_compatible", "chat") == (
        "openai", "deepseek", "openrouter", "custom"
    )
    assert registry.presets_for("openai_compatible", "embedding") == ("openai", "custom")
~~~

- [ ] **Step 3: Verify RED**

Run: pytest tests/test_model_config_types.py tests/test_model_connection_types.py -q

Expected: FAIL during collection because openbiliclaw.model_config does not exist.

- [ ] **Step 4: Implement the internal dataclasses**

Use tuples for ordered immutable collections:

~~~python
CredentialSource = Literal["none", "inline", "env", "oauth"]


@dataclass(frozen=True)
class CredentialConfig:
    source: CredentialSource = "none"
    value: str = field(default="", repr=False)


@dataclass(frozen=True)
class ChatConnection:
    id: str
    name: str
    type: str
    model: str
    preset: str = ""
    base_url: str = ""
    credential: CredentialConfig = field(default_factory=CredentialConfig)
    api_mode: str = ""
    reasoning_effort: str = ""
    http_referer: str = ""
    x_title: str = ""
    num_ctx: int = 0


@dataclass(frozen=True)
class EmbeddingModelSettings:
    model: str
    output_dimensionality: int = 1024
    similarity_threshold: float = 0.82
    multimodal_enabled: bool = False
~~~

ModelConfigIssue carries path, code, message, severity, and optional connection_id. Validation rejects unknown type/preset combinations, blank/duplicate IDs, illegal type-specific fields, invalid credential sources, Chat counts outside 1..10, enabled Embedding counts outside 1..10, and model fields inside raw Embedding providers.

- [ ] **Step 5: Implement safe registry metadata**

The registry is code-defined and exposes labels, category, capabilities, field descriptors, presets, defaults, and help copy. Public serialization contains no Python callable/class names. Keep preset default application pure:

~~~python
def apply_preset_defaults(
    connection: ChatConnection,
    definition: PresetDefinition,
    touched_fields: frozenset[str],
) -> ChatConnection:
    updates = {
        key: value
        for key, value in definition.defaults.items()
        if key not in touched_fields and not str(getattr(connection, key, "")).strip()
    }
    return replace(connection, **updates)
~~~

- [ ] **Step 6: Verify GREEN, update the config module contract, and commit**

Run:

~~~bash
pytest tests/test_model_config_types.py tests/test_model_connection_types.py -q
ruff check src/openbiliclaw/model_config tests/test_model_config_types.py tests/test_model_connection_types.py
mypy src/openbiliclaw/model_config
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/model_config/__init__.py src/openbiliclaw/model_config/types.py src/openbiliclaw/model_config/registry.py src/openbiliclaw/model_config/validation.py tests/test_model_config_types.py tests/test_model_connection_types.py docs/modules/config.md
git commit -m "feat: define model connection schema"
~~~

---

### Task 2: Add Native [models] Parsing, Rendering, and Revisions Without Silent Migration

**Files:**
- Create: src/openbiliclaw/model_config/serialization.py
- Create: src/openbiliclaw/model_config/revision.py
- Create: tests/test_model_config_serialization.py
- Modify: src/openbiliclaw/config.py
- Modify: tests/test_config.py
- Modify: docs/modules/config.md

**Interfaces:**
- Produce parse_model_config(raw: Mapping[str, object]) -> ModelConfig.
- Produce render_model_config(config: ModelConfig) -> list[str].
- Produce compute_model_revision(config: ModelConfig) -> str using normalized values and secret fingerprints.
- Add Config.models and non-persisted ModelConfigMeta(source, migration, override_paths).
- Extend save_config with models_authoritative=False; normal callers preserve the on-disk model section.

- [ ] **Step 1: Write native round-trip and strict-field tests**

~~~python
def test_native_models_round_trip_keeps_order_and_secret_sources(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(NATIVE_MODELS_TOML, encoding="utf-8")
    loaded = load_config(path)
    assert [item.id for item in loaded.models.chat.connections] == ["deepseek-main", "router"]
    save_config(loaded, path, models_authoritative=True)
    reloaded = load_config(path)
    assert reloaded.models == loaded.models


def test_unknown_connection_field_is_blocking() -> None:
    raw = native_models_raw()
    raw["chat"]["connections"][0]["stale_provider_field"] = "must-not-survive"
    with pytest.raises(ModelConfigParseError, match="stale_provider_field"):
        parse_model_config(raw)
~~~

- [ ] **Step 2: Pin revision and secret behavior**

~~~python
def test_revision_changes_when_inline_secret_changes_without_exposing_it() -> None:
    left = model_config(secret="sk-left")
    right = model_config(secret="sk-right")
    assert compute_model_revision(left) != compute_model_revision(right)
    assert "sk-left" not in compute_model_revision(left)


def test_reorder_changes_revision_but_not_connection_ids() -> None:
    config = model_config(chat_ids=("a", "b"))
    moved = replace(config, chat=replace(config.chat, connections=tuple(reversed(config.chat.connections))))
    assert compute_model_revision(config) != compute_model_revision(moved)
    assert {item.id for item in config.chat.connections} == {item.id for item in moved.chat.connections}
~~~

- [ ] **Step 3: Pin unrelated-save preservation**

Write cases for native [models], legacy [llm], unknown legacy keys, inline secrets, array order, and an explicit config.local.toml layer:

~~~python
def test_unrelated_save_does_not_migrate_or_drop_legacy_model_data(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(LEGACY_WITH_UNKNOWN_PROVIDER_KEY, encoding="utf-8")
    config = load_config(path)
    config.saved_sync.auto_sync_enabled = True
    save_config(config, path)
    text = path.read_text(encoding="utf-8")
    assert "[llm]" in text
    assert "[models]" not in text
    assert 'vendor_extension = "keep-me"' in text
~~~

- [ ] **Step 4: Verify RED**

Run: pytest tests/test_model_config_serialization.py tests/test_config.py -q

Expected: FAIL because Config has no models field and save_config always renders [llm].

- [ ] **Step 5: Implement strict parsing and deterministic rendering**

The parser accepts only schema_version=1, preserves array order, normalizes string enums, and maps flattened TOML credential keys into CredentialConfig:

- api_key -> source inline
- api_key_env -> source env
- credential_ref -> source oauth
- more than one source -> blocking parse error

The renderer performs the inverse and never renders empty inline secrets as placeholders.

- [ ] **Step 6: Make model persistence explicitly authoritative**

Before rendering, read the raw on-disk top-level models/llm tables. With models_authoritative=False, re-render that raw section through a model-scoped generic TOML emitter, including unknown scalar/list/nested keys and arrays of tables. With models_authoritative=True, render only Config.models and omit legacy [llm].

Do not use text regex replacement and do not introduce a TOML writer dependency. Add tests proving comments outside the model section, auth provenance, and autostart authority continue to follow existing semantics.

- [ ] **Step 7: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_model_config_serialization.py tests/test_config.py -q
ruff check src/openbiliclaw/model_config src/openbiliclaw/config.py tests/test_model_config_serialization.py tests/test_config.py
mypy src/openbiliclaw/model_config src/openbiliclaw/config.py
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/model_config/serialization.py src/openbiliclaw/model_config/revision.py src/openbiliclaw/model_config/types.py src/openbiliclaw/model_config/__init__.py src/openbiliclaw/config.py tests/test_model_config_serialization.py tests/test_config.py docs/modules/config.md
git commit -m "feat: persist versioned model routes"
~~~

---

### Task 3: Implement Deterministic Legacy Migration and Resolution Reports

**Files:**
- Create: src/openbiliclaw/model_config/migration.py
- Create: tests/test_model_config_migration.py
- Modify: src/openbiliclaw/config.py
- Modify: tests/test_config.py
- Modify: docs/modules/config.md

**Interfaces:**
- Produce migrate_legacy_llm(raw_llm, env) -> LegacyMigrationResult.
- Produce MigrationReport and MigrationIssue with safe resolution choices.
- Produce apply_migration_resolutions(result, choices) -> ModelConfig.
- Generate deterministic legacy IDs so startup and repeated GET calls do not churn revisions.

- [ ] **Step 1: Write the complete mapping table as parameterized failing tests**

~~~python
@pytest.mark.parametrize(
    ("legacy", "expected_type", "expected_preset"),
    [
        (legacy_provider("openai"), "openai_compatible", "openai"),
        (legacy_provider("openai", base_url="https://relay.example/v1"), "openai_compatible", "custom"),
        (legacy_provider("deepseek"), "openai_compatible", "deepseek"),
        (legacy_provider("openrouter"), "openai_compatible", "openrouter"),
        (legacy_provider("openai_compatible"), "openai_compatible", "custom"),
        (legacy_provider("claude"), "anthropic_compatible", "anthropic"),
        (legacy_provider("claude", base_url="https://relay.example"), "anthropic_compatible", "custom"),
        (legacy_provider("gemini"), "gemini_api", ""),
        (legacy_provider("ollama"), "ollama", ""),
    ],
)
def test_legacy_provider_mapping(legacy, expected_type, expected_preset) -> None:
    result = migrate_legacy_llm(legacy, {})
    connection = result.models.chat.connections[0]
    assert (connection.type, connection.preset) == (expected_type, expected_preset)
~~~

Add Codex OAuth as a separate test and assert credential_ref=codex with no inline token.

- [ ] **Step 2: Pin ordering and unresolved data**

~~~python
def test_only_explicit_default_and_fallback_enter_chat_route() -> None:
    result = migrate_legacy_llm(legacy_with_three_configured_providers(), {})
    assert [item.preset for item in result.models.chat.connections] == ["deepseek", "openrouter"]
    assert result.report.issue_codes == {"unrouted_credential"}


def test_incompatible_embedding_fallback_is_reported_not_mapped() -> None:
    result = migrate_legacy_llm(legacy_embedding(primary_model="bge-m3", fallback_model="other"), {})
    assert len(result.models.embedding.providers) == 1
    assert "embedding_space_mismatch" in result.report.issue_codes


def test_module_overrides_require_explicit_global_route_acknowledgement() -> None:
    result = migrate_legacy_llm(legacy_with_module_override("evaluation", "openai"), {})
    issue = next(item for item in result.report.issues if item.code == "module_override_removed")
    assert issue.field == "llm.evaluation"
    assert issue.allowed_actions == ("accept_global_route", "cancel")
~~~

- [ ] **Step 3: Pin startup and precedence**

Verify legacy-only load builds Config.models in memory without changing bytes. Verify both sections choose [models], emit a diagnostic that [llm] is ignored, and never mix credentials from [llm] into [models].

- [ ] **Step 4: Verify RED**

Run: pytest tests/test_model_config_migration.py tests/test_config.py -q

Expected: FAIL because no legacy compatibility adapter or migration report exists.

- [ ] **Step 5: Implement migration from raw data**

Inspect raw dictionaries rather than only legacy dataclasses so unknown providers, translated values, stale auth modes, and unused credentials remain reportable. Report only field names, provider names, booleans such as credential_configured, and safe reasons; never copy secret values.

Use deterministic IDs:

~~~python
def legacy_connection_id(kind: str, provider: str, used: set[str]) -> str:
    base = slugify_id("legacy-" + kind + "-" + provider)
    return unique_id(base, used)
~~~

Resolution actions are closed enums. An unknown issue/action pair is blocking.

- [ ] **Step 6: Integrate loader provenance**

Set ModelConfigMeta.source to native, legacy, or default. Store the report in memory only. Add diagnostics for:

- legacy configuration loaded read-only
- [llm] ignored because [models] exists
- pending migration decisions
- config.local model override paths

- [ ] **Step 7: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_model_config_migration.py tests/test_model_config_serialization.py tests/test_config.py -q
ruff check src/openbiliclaw/model_config src/openbiliclaw/config.py tests/test_model_config_migration.py
mypy src/openbiliclaw/model_config src/openbiliclaw/config.py
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/model_config/migration.py src/openbiliclaw/model_config/types.py src/openbiliclaw/model_config/__init__.py src/openbiliclaw/config.py tests/test_model_config_migration.py tests/test_config.py docs/modules/config.md
git commit -m "feat: migrate legacy model config safely"
~~~

---

### Task 4: Build Protocol Adapters From Connection Records

**Files:**
- Create: src/openbiliclaw/llm/connection_factory.py
- Modify: src/openbiliclaw/llm/openai_provider.py
- Create: src/openbiliclaw/llm/anthropic_provider.py
- Modify: src/openbiliclaw/llm/gemini_provider.py
- Modify: src/openbiliclaw/llm/ollama_provider.py
- Modify: src/openbiliclaw/llm/codex_auth.py
- Modify: src/openbiliclaw/llm/registry.py
- Modify: tests/test_llm_providers.py
- Create: tests/test_llm_connection_factory.py
- Modify: docs/modules/llm.md

**Interfaces:**
- Produce build_chat_adapter(connection, runtime_options) -> LLMProvider.
- Produce build_embedding_adapter(provider, settings, runtime_options) -> SupportsEmbedding.
- Resolve credentials at build time without placing values into descriptors or public config.
- Use one OpenAIProtocolProvider implementation for OpenAI, DeepSeek, OpenRouter, and custom presets.
- Use one AnthropicCompatibleProvider for official and custom Anthropic endpoints.

- [ ] **Step 1: Write failing adapter-selection tests**

~~~python
@pytest.mark.parametrize("preset", ["openai", "deepseek", "openrouter", "custom"])
def test_openai_presets_use_one_protocol_adapter(preset: str) -> None:
    adapter = build_chat_adapter(openai_connection(preset), runtime_options())
    assert type(adapter) is OpenAIProtocolProvider


def test_anthropic_official_and_custom_use_one_adapter() -> None:
    assert type(build_chat_adapter(anthropic_connection("anthropic"), runtime_options())) is AnthropicCompatibleProvider
    assert type(build_chat_adapter(anthropic_connection("custom"), runtime_options())) is AnthropicCompatibleProvider
~~~

- [ ] **Step 2: Pin preset hooks and OAuth containment**

Use fake SDK clients to assert DeepSeek body fields, OpenRouter headers, OpenAI api_mode, custom base URLs, Anthropic base URLs, Ollama num_ctx, and Gemini native construction. Assert a codex_oauth connection with any non-official base URL fails before token lookup.

- [ ] **Step 3: Verify RED**

Run: pytest tests/test_llm_connection_factory.py tests/test_llm_providers.py -q

Expected: FAIL because provider construction is keyed by legacy Config.llm buckets.

- [ ] **Step 4: Refactor OpenAI protocol hooks**

Replace mutable subclass state with immutable constructor options:

~~~python
@dataclass(frozen=True)
class OpenAIProtocolOptions:
    connection_id: str
    preset: str
    api_mode: Literal["chat_completions", "responses"]
    default_reasoning_effort: str = ""
    extra_headers: Mapping[str, str] = field(default_factory=dict)


class OpenAIProtocolProvider(LLMProvider):
    def _extra_body(self, reasoning_effort: str | None) -> dict[str, object]:
        effort = self.options.default_reasoning_effort if reasoning_effort is None else reasoning_effort
        return {} if not effort else {"thinking": {"type": "enabled"}, "reasoning_effort": effort}
~~~

This removes the concurrency-unsafe DeepSeek mutation and makes each connection instance independent.

- [ ] **Step 5: Implement credential resolution and safe labels**

Credential source behavior:

- inline: use the stored value
- env: read exactly the configured environment variable
- oauth: resolve credential_ref=codex only
- none: allowed only for local Ollama or endpoints whose descriptor explicitly permits it

Adapter names use connection IDs. Route metadata, not exception text, supplies type/preset labels.

- [ ] **Step 6: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_llm_connection_factory.py tests/test_llm_providers.py tests/test_network_proxy_isolation.py -q
ruff check src/openbiliclaw/llm tests/test_llm_connection_factory.py tests/test_llm_providers.py
mypy src/openbiliclaw/llm
~~~

Expected: PASS, including proxy isolation.

Commit:

~~~bash
git add src/openbiliclaw/llm/connection_factory.py src/openbiliclaw/llm/openai_provider.py src/openbiliclaw/llm/anthropic_provider.py src/openbiliclaw/llm/gemini_provider.py src/openbiliclaw/llm/ollama_provider.py src/openbiliclaw/llm/codex_auth.py src/openbiliclaw/llm/registry.py tests/test_llm_connection_factory.py tests/test_llm_providers.py docs/modules/llm.md
git commit -m "refactor: build llm protocol connections"
~~~

---

### Task 5: Replace Single Fallback With Ordered Chat Routing and Circuits

**Files:**
- Create: src/openbiliclaw/llm/route.py
- Modify: src/openbiliclaw/llm/base.py
- Modify: src/openbiliclaw/llm/service.py
- Create: tests/test_llm_ordered_route.py
- Modify: tests/test_llm_registry.py
- Modify: tests/test_llm_service.py
- Modify: docs/modules/llm.md

**Interfaces:**
- Produce OrderedLLMRoute(connections, revision, timeout_seconds, clock).
- Produce complete(messages, temperature, max_tokens, json_mode, reasoning_effort, model) and complete_connection(connection_id, messages, temperature, max_tokens, json_mode, reasoning_effort, model, ignore_circuit=False).
- Produce RouteAttempt and LLMRouteExhaustedError with safe structured attempts.
- Produce CircuitTable keyed by connection ID and config revision.
- Extend LLMResponse with connection_id, connection_type, preset, and route_position while preserving provider/model for pricing compatibility.

- [ ] **Step 1: Write exact-order and duplicate-type tests**

~~~python
async def test_route_treats_same_type_connections_as_distinct_ordered_peers() -> None:
    route = fake_route(
        ("openai-primary", "openai-second", "openai-third"),
        outcomes=(RateLimit(), Timeout(), success("ok")),
    )
    response = await route.complete([{"role": "user", "content": "hi"}])
    assert route.calls == ["openai-primary", "openai-second", "openai-third"]
    assert response.connection_id == "openai-third"
    assert response.route_position == 2
~~~

- [ ] **Step 2: Write deadline and retry-order tests**

Pin that provider transport retries finish before the next connection, remaining route time is passed to each attempt, and no new fallback begins after deadline exhaustion. Caller cancellation, request-shape errors, and internal programming errors must propagate immediately.

- [ ] **Step 3: Write circuit matrix tests**

Parameterize:

- rate_limited/quota: Retry-After or 60 seconds
- auth_failed/model_not_found: until revision change or exact-probe success
- timeout/connection/server_error: 15, 30, 60, 120, 240, then 300 seconds
- invalid_response/moderation: fallback for this call, no cross-request circuit

Exact probe ignores an open circuit and closes it on success.

- [ ] **Step 4: Verify RED**

Run: pytest tests/test_llm_ordered_route.py tests/test_llm_registry.py tests/test_llm_service.py -q

Expected: FAIL because LLMRegistry supports only default plus one fallback.

- [ ] **Step 5: Implement route attempts and total deadline**

~~~python
deadline = self._clock() + self.timeout_seconds
for position, connection in enumerate(self.connections):
    if self.circuits.should_skip(connection.id, self.revision):
        continue
    remaining = deadline - self._clock()
    if remaining <= 0:
        break
    try:
        async with asyncio.timeout(remaining):
            response = await connection.adapter.complete(messages, **kwargs)
    except Exception as exc:
        kind = classify_llm_failure_kind(exc)
        if not should_fallback(exc, kind):
            raise
        attempts.append(RouteAttempt.safe(connection, position, kind))
        self.circuits.record_failure(connection.id, self.revision, kind, exc)
        continue
    self.circuits.record_success(connection.id)
    return stamp_route_metadata(response, connection, position)
raise LLMRouteExhaustedError(attempts)
~~~

Parse Retry-After into LLMRateLimitError.retry_after_seconds at adapter boundaries. Safe attempt summaries contain no raw upstream body, URL userinfo, or credential.

- [ ] **Step 6: Keep LLMService caller behavior but remove module selection**

LLMService delegates every normal, structured, multimodal, and tool path to the same OrderedLLMRoute. Remove ModuleOverride, module_overrides_from_config, caller-prefix route selection, and per-module model overrides. Caller tags remain for concurrency and usage only.

- [ ] **Step 7: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_llm_ordered_route.py tests/test_llm_registry.py tests/test_llm_service.py tests/test_llm_module_routing_e2e.py -q
ruff check src/openbiliclaw/llm tests/test_llm_ordered_route.py
mypy src/openbiliclaw/llm
~~~

Expected: PASS after replacing test_llm_module_routing_e2e with a global ordered-route assertion.

Commit:

~~~bash
git add src/openbiliclaw/llm/route.py src/openbiliclaw/llm/base.py src/openbiliclaw/llm/service.py tests/test_llm_ordered_route.py tests/test_llm_registry.py tests/test_llm_service.py tests/test_llm_module_routing_e2e.py docs/modules/llm.md
git commit -m "feat: route chat through ordered connections"
~~~

---

### Task 6: Build the Shared-Settings Ordered Embedding Route

**Files:**
- Create: src/openbiliclaw/llm/embedding_route.py
- Modify: src/openbiliclaw/llm/embedding.py
- Modify: src/openbiliclaw/llm/registry.py
- Create: tests/test_embedding_route.py
- Modify: tests/test_embedding_service.py
- Modify: tests/test_dashscope_embedding.py
- Modify: docs/modules/llm.md

**Interfaces:**
- Produce OrderedEmbeddingRoute(providers, settings, revision, circuits).
- Produce embed, embed_image, and probe_provider methods.
- Every adapter call receives the same immutable EmbeddingModelSettings.
- Cache namespace derives only from shared settings, not active provider ID.

- [ ] **Step 1: Write shared-model and fallback tests**

~~~python
async def test_every_embedding_provider_receives_identical_shared_settings() -> None:
    first, second = fake_embedding_provider(empty=True), fake_embedding_provider(vector=[1.0, 0.0])
    settings = EmbeddingModelSettings(
        model="bge-m3",
        output_dimensionality=2,
        similarity_threshold=0.82,
        multimodal_enabled=False,
    )
    route = OrderedEmbeddingRoute((first, second), settings=settings, revision="r1")
    assert await route.embed("text") == [1.0, 0.0]
    assert first.calls[0].settings is settings
    assert second.calls[0].settings is settings
~~~

- [ ] **Step 2: Pin invalid output behavior**

Test empty vector, non-numeric vector, dimension mismatch, missing image capability, provider retry before fallback, and all-provider failure. Dimension mismatch opens a config-error circuit until revision change or exact probe success. Empty vectors are never cached.

- [ ] **Step 3: Pin cache namespace**

~~~python
def test_cache_namespace_ignores_provider_reorder_but_changes_with_vector_space() -> None:
    settings = embedding_settings(model="bge-m3", dims=1024)
    assert cache_namespace(settings, providers=("a", "b")) == cache_namespace(settings, providers=("b", "a"))
    assert cache_namespace(settings, providers=("a",)) != cache_namespace(
        embedding_settings(model="other", dims=1024), providers=("a",)
    )
~~~

- [ ] **Step 4: Verify RED**

Run: pytest tests/test_embedding_route.py tests/test_embedding_service.py tests/test_dashscope_embedding.py -q

Expected: FAIL because EmbeddingService owns one primary and at most one provider-derived fallback.

- [ ] **Step 5: Implement route and exact probes**

The fixed image-only probe uses a repository-owned tiny PNG byte constant. A probe:

- calls only the requested provider
- ignores its current circuit
- does not write config or cache
- validates nonempty output
- validates configured dimension, unless dimension is zero
- runs image-only validation when multimodal is enabled
- reports observed dimension without claiming the remote weight is proven

- [ ] **Step 6: Integrate EmbeddingService cache and graceful degradation**

EmbeddingService keeps its public embed/are_similar/find_similar_cluster API, but its provider is OrderedEmbeddingRoute and cache_model is settings.cache_namespace(). All-provider failure returns the existing product-level unavailable behavior with a safe reason.

- [ ] **Step 7: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_embedding_route.py tests/test_embedding_service.py tests/test_dashscope_embedding.py -q
ruff check src/openbiliclaw/llm tests/test_embedding_route.py
mypy src/openbiliclaw/llm
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/llm/embedding_route.py src/openbiliclaw/llm/embedding.py src/openbiliclaw/llm/registry.py tests/test_embedding_route.py tests/test_embedding_service.py tests/test_dashscope_embedding.py docs/modules/llm.md
git commit -m "feat: route embeddings with shared settings"
~~~

---

### Task 7: Implement ModelConfigService, Secret Actions, and Atomic Transactions

**Files:**
- Create: src/openbiliclaw/model_config/service.py
- Create: tests/test_model_config_service.py
- Modify: src/openbiliclaw/config.py
- Modify: src/openbiliclaw/api/runtime_context.py
- Modify: tests/test_api_config_transactional.py
- Modify: docs/modules/config.md
- Modify: docs/modules/runtime.md

**Interfaces:**
- Produce ModelConfigService.read(), save(), probe(), add(), edit(), remove(), and move().
- Produce CredentialAction(action, value) with keep/set/clear/env.
- Produce ModelConfigSaveRequest, ModelConfigSaveResult, ModelConfigSnapshot, and ModelRuntimeCoordinator protocol.
- Add RuntimeContext.build_model_candidate and swap_model_candidate hooks.

- [ ] **Step 1: Write secret-action tests**

~~~python
@pytest.mark.parametrize("action", ["keep", "set", "clear", "env"])
async def test_secret_actions_have_explicit_semantics(action: str, service: ModelConfigService) -> None:
    result = await service.save(request_for_secret_action(action))
    assert result.ok is True
    public_credential = service.read().public.models.chat.connections[0].credential
    assert not hasattr(public_credential, "raw_value")


async def test_mask_or_empty_string_cannot_replace_existing_secret(service: ModelConfigService) -> None:
    with pytest.raises(ModelConfigValidationError):
        await service.save(request_with_secret_action("set", "********"))
~~~

- [ ] **Step 2: Write revision and migration tests**

Assert stale revision returns a conflict result without candidate build. Assert a pending migration issue blocks save until a valid closed resolution is supplied. Assert first legacy save creates config.toml.pre-model-refactor.bak, retains mode bits, does not overwrite an existing backup, and returns the backup path only in the direct response.

- [ ] **Step 3: Write transaction and rollback tests**

~~~python
async def test_candidate_failure_changes_neither_disk_nor_runtime(service, coordinator) -> None:
    before_file = service.path.read_bytes()
    before_bundle = coordinator.current
    coordinator.fail_build = True
    result = await service.save(valid_request())
    assert result.ok is False
    assert service.path.read_bytes() == before_file
    assert coordinator.current is before_bundle


async def test_swap_failure_restores_file_and_bundle(service, coordinator) -> None:
    before_file = service.path.read_bytes()
    before_bundle = coordinator.current
    coordinator.fail_swap = True
    result = await service.save(valid_request())
    assert result.rollback_applied is True
    assert service.path.read_bytes() == before_file
    assert coordinator.current is before_bundle
~~~

- [ ] **Step 4: Pin config.local read-only overrides**

Load base and local model configs, expose overridden paths in the snapshot, reject a save attempting to mutate a shadowed path, and permit edits outside those paths. The UI-facing error must name the field and source without revealing values.

- [ ] **Step 5: Verify RED**

Run: pytest tests/test_model_config_service.py tests/test_api_config_transactional.py -q

Expected: FAIL because no dedicated service, revision guard, or model runtime coordinator exists.

- [ ] **Step 6: Implement service operations and validation**

All list mutations operate by stable ID and return a full candidate ModelConfig. move(id, position) uses 1-based CLI/UI positions, validates 1..route length, and never changes IDs or credentials. Removing the final Chat connection is blocking.

- [ ] **Step 7: Implement atomic save ordering**

Inside one per-path asyncio lock:

1. Re-read disk and compare revision.
2. Merge keep actions from persisted credentials.
3. Apply migration resolutions.
4. Validate structure and credentials.
5. Build a complete candidate runtime bundle.
6. Create the one-time migration backup when applicable.
7. Write a same-directory temp file, preserve file mode, flush, fsync, os.replace, and fsync the directory where supported.
8. Swap the runtime bundle.
9. On swap failure, atomically restore the prior bytes and coordinator bundle.

Do not log backup contents/path or secrets. Return fieldized errors.

- [ ] **Step 8: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_model_config_service.py tests/test_api_config_transactional.py tests/test_config.py -q
ruff check src/openbiliclaw/model_config src/openbiliclaw/config.py src/openbiliclaw/api/runtime_context.py tests/test_model_config_service.py
mypy src/openbiliclaw/model_config src/openbiliclaw/config.py src/openbiliclaw/api/runtime_context.py
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/model_config/service.py src/openbiliclaw/model_config/types.py src/openbiliclaw/model_config/__init__.py src/openbiliclaw/config.py src/openbiliclaw/api/runtime_context.py tests/test_model_config_service.py tests/test_api_config_transactional.py docs/modules/config.md docs/modules/runtime.md
git commit -m "feat: save model routes transactionally"
~~~

---

### Task 8: Cut Runtime Composition and Usage Observability Over to Ordered Routes

**Files:**
- Modify: src/openbiliclaw/api/runtime_context.py
- Modify: src/openbiliclaw/api/app.py
- Modify: src/openbiliclaw/llm/service.py
- Modify: src/openbiliclaw/llm/usage_recorder.py
- Modify: src/openbiliclaw/storage/database.py
- Modify: src/openbiliclaw/runtime/ollama_supervisor.py
- Modify: src/openbiliclaw/soul/engine.py
- Modify: src/openbiliclaw/soul/dialogue.py
- Modify: src/openbiliclaw/integrations/openclaw/bootstrap.py
- Modify: src/openbiliclaw/cli.py
- Modify: tests/test_llm_module_routing_e2e.py
- Modify: tests/test_llm_usage.py
- Create: tests/test_runtime_model_bundle.py
- Modify: docs/modules/runtime.md
- Modify: docs/modules/storage.md
- Modify: docs/modules/soul.md

**Interfaces:**
- Produce RuntimeModelBundle(revision, chat_route, llm_service, embedding_service).
- RuntimeContext owns one current immutable bundle and one model swap lock.
- Existing service consumers retain LLMService APIs but no longer receive module overrides.
- Add connection metadata columns to llm_usage without breaking existing rows/queries.

- [ ] **Step 1: Write composition and in-flight swap tests**

~~~python
async def test_all_callers_share_the_same_global_route(runtime_context) -> None:
    route = runtime_context.model_bundle.chat_route
    assert runtime_context.llm_service.registry is route
    assert runtime_context.soul_engine._llm_service.registry is route
    assert runtime_context.recommendation_engine._llm.registry is route


async def test_in_flight_call_finishes_on_old_bundle_and_next_call_uses_new(runtime_context) -> None:
    old_call = asyncio.create_task(
        runtime_context.llm_service.complete_structured_task(
            system_instruction="Return json.",
            user_input="old route call",
            caller="soul.preference",
        )
    )
    await old_adapter.entered.wait()
    await runtime_context.swap_model_candidate(new_bundle)
    old_adapter.release.set()
    assert (await old_call).connection_id == "old"
    next_response = await runtime_context.llm_service.complete_structured_task(
        system_instruction="Return json.",
        user_input="new route call",
        caller="recommendation.write_expression",
    )
    assert next_response.connection_id == "new"
~~~

- [ ] **Step 2: Replace module-routing E2E**

Use four former buckets and assert recommendation, evaluation, discovery, and Soul caller tags all traverse the same route order and configured per-connection models. Delete module override constructor arguments and private attributes where they exist only for routing.

- [ ] **Step 3: Migrate health, Ollama, and setup reads**

Replace every config.llm embedding/default-provider access in app.py, ollama_supervisor.py, CLI composition, packaging-facing runtime helpers, and OpenClaw with Config.models. Ollama is needed when any Chat connection or Embedding provider has type=ollama.

- [ ] **Step 4: Add usage-schema migration tests**

Existing llm_usage rows receive safe defaults. New rows store connection_id, connection_type, preset, route_position, provider, and model. Cost lookup uses preset when it maps to a price table, otherwise the connection type/custom provider behavior.

- [ ] **Step 5: Verify RED**

Run:

~~~bash
pytest tests/test_runtime_model_bundle.py tests/test_llm_module_routing_e2e.py tests/test_llm_usage.py -q
~~~

Expected: FAIL because runtime composition still builds legacy LLMRegistry/module overrides and the database lacks connection columns.

- [ ] **Step 6: Implement immutable bundle construction and swap**

Build every adapter, route, embedding service, usage recorder, and dependent engine before acquiring the short swap lock. Under the lock, replace RuntimeContext.model_bundle and references used by new requests. Keep the existing concurrency gate object and reconfigure its capacity from models.chat.concurrency.

Publish config_reloaded only after a successful swap and include revision.

- [ ] **Step 7: Implement usage migration**

Add idempotent ALTER TABLE support for:

- connection_id TEXT NOT NULL DEFAULT ''
- connection_type TEXT NOT NULL DEFAULT ''
- preset TEXT NOT NULL DEFAULT ''
- route_position INTEGER NOT NULL DEFAULT 0

Keep provider/model indexes and add connection_id/timestamp indexing. Update Rich cost output to show connection name/ID without losing provider totals.

- [ ] **Step 8: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_runtime_model_bundle.py tests/test_llm_module_routing_e2e.py tests/test_llm_usage.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_cli.py -q
ruff check src/openbiliclaw/api src/openbiliclaw/llm src/openbiliclaw/runtime src/openbiliclaw/soul src/openbiliclaw/storage tests/test_runtime_model_bundle.py tests/test_llm_usage.py
mypy src/openbiliclaw
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/api/runtime_context.py src/openbiliclaw/api/app.py src/openbiliclaw/llm/service.py src/openbiliclaw/llm/usage_recorder.py src/openbiliclaw/storage/database.py src/openbiliclaw/runtime/ollama_supervisor.py src/openbiliclaw/soul/engine.py src/openbiliclaw/soul/dialogue.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/cli.py tests/test_runtime_model_bundle.py tests/test_llm_module_routing_e2e.py tests/test_llm_usage.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_cli.py docs/modules/runtime.md docs/modules/storage.md docs/modules/soul.md
git commit -m "refactor: run all model calls through global routes"
~~~

---

### Task 9: Expose the Dedicated Model Configuration API and Protect Legacy /api/config

**Files:**
- Create: src/openbiliclaw/api/model_config_models.py
- Create: src/openbiliclaw/api/model_config_routes.py
- Modify: src/openbiliclaw/api/app.py
- Modify: src/openbiliclaw/api/models.py
- Create: tests/test_api_model_config.py
- Modify: tests/test_api_config_guards.py
- Modify: tests/test_api_config_probe.py
- Modify: tests/test_api_config_transactional.py
- Modify: docs/modules/config.md
- Modify: docs/modules/runtime.md

**Interfaces:**
- GET /api/model-config
- PUT /api/model-config
- GET /api/model-connection-types
- POST /api/model-config/probe
- One-release read-only legacy llm projection on GET /api/config.
- PUT /api/config ignores llm when native [models] is active and returns model_config_not_updated.

- [ ] **Step 1: Define strict Pydantic request/response tests**

Public credential state:

~~~json
{
  "source": "inline",
  "configured": true,
  "env_name": "",
  "credential_ref": "",
  "oauth_logged_in": false
}
~~~

Write requests use:

~~~json
{
  "credential": {
    "action": "keep",
    "value": ""
  }
}
~~~

Assert extra fields are forbidden, raw secret fields are absent from OpenAPI responses, and API model conversion preserves ordered arrays.

- [ ] **Step 2: Write endpoint behavior tests**

Cover:

- GET snapshot/revision/migration/probe/circuit summaries
- descriptors grouped and capability-filterable
- PUT success
- stale PUT 409 with latest revision
- fieldized 400/422 validation
- exact Chat probe
- exact Embedding provider probe with shared settings
- probe draft is not persisted and does not fallback
- config_reloaded carries the new revision
- API never emits raw secrets, even with reveal_keys=true

- [ ] **Step 3: Pin old-client protection**

~~~python
def test_legacy_config_put_cannot_overwrite_native_model_route(client, native_config) -> None:
    before = client.get("/api/model-config").json()
    response = client.put("/api/config", json={"llm": {"default_provider": "ollama"}})
    after = client.get("/api/model-config").json()
    assert response.status_code == 200
    assert "model_config_not_updated" in response.json()["warnings"]
    assert after["revision"] == before["revision"]
    assert after["models"] == before["models"]
~~~

Projection returns only representable primary/first fallback legacy fields and labels itself non-authoritative. It never reveals secrets. Duplicate types or later fallbacks do not get collapsed into shared provider buckets.

- [ ] **Step 4: Verify RED**

Run: pytest tests/test_api_model_config.py tests/test_api_config_guards.py tests/test_api_config_probe.py tests/test_api_config_transactional.py -q

Expected: FAIL because the new routes/models do not exist.

- [ ] **Step 5: Implement a route installer around ModelConfigService**

Keep model route code out of the 11k-line app.py. model_config_routes.py receives RuntimeContext, event publisher, init-active guard, and config path dependencies. It maps service conflict/error results to stable HTTP shapes.

Probe accepts either:

- kind=chat, revision, connection draft
- kind=embedding, revision, provider draft, shared settings

A keep action resolves only against the matching existing stable ID at the supplied revision.

- [ ] **Step 6: Narrow the legacy endpoint**

Remove llm and embedding mutations from _apply_llm_update when Config.models is native. Retain non-model config save/reload behavior and normal persistence protection from Task 2. Keep network_proxy probe on the legacy probe endpoint; move llm, llm_fallback, and embedding probes to the new endpoint.

- [ ] **Step 7: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_api_model_config.py tests/test_api_config_guards.py tests/test_api_config_probe.py tests/test_api_config_transactional.py -q
ruff check src/openbiliclaw/api tests/test_api_model_config.py
mypy src/openbiliclaw/api
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/api/model_config_models.py src/openbiliclaw/api/model_config_routes.py src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py tests/test_api_model_config.py tests/test_api_config_guards.py tests/test_api_config_probe.py tests/test_api_config_transactional.py docs/modules/config.md docs/modules/runtime.md
git commit -m "feat: add model configuration api"
~~~

---

### Task 10: Replace the Desktop Model Form With Ordered List and Side Inspector

**Files:**
- Create: src/openbiliclaw/web/shared/model-config-state.js
- Create: src/openbiliclaw/web/desktop/assets/js/model-settings.js
- Modify: src/openbiliclaw/web/desktop/index.html
- Modify: src/openbiliclaw/web/desktop/assets/css/app.css
- Modify: src/openbiliclaw/web/desktop/assets/js/app.js
- Modify: src/openbiliclaw/api/app.py
- Create: tests/web_model_config_state.test.mjs
- Create: tests/test_desktop_web_model_settings.py
- Modify: tests/test_desktop_web_config_probe.py
- Modify: tests/test_desktop_web_multimodal_settings.py

**Interfaces:**
- Desktop model page has Chat route, Embedding route, and Runtime tabs.
- Chat/Embedding use an ordered left list and selected-item right inspector.
- Connection type selection is a grouped, vertical, searchable list driven by GET /api/model-connection-types.
- Model saves use only PUT /api/model-config; the general settings form no longer includes llm.

- [ ] **Step 1: Write pure draft-state tests**

~~~javascript
test("reorder changes order only", () => {
  const before = draft(["a", "b", "c"]);
  const after = moveItem(before, "c", 0);
  assert.deepEqual(after.chat.connections.map((item) => item.id), ["c", "a", "b"]);
  assert.equal(after.chat.connections[0].credential.action, "keep");
});

test("chat caps at ten and cannot delete the last item", () => {
  assert.throws(() => appendChat(draftWithCount(10)), /maximum 10/);
  assert.throws(() => removeChat(draftWithCount(1), "only"), /at least one/);
});
~~~

Add tests for preset fill-only-untouched, incompatible-field clearing confirmation, stable selection after reorder, server field error mapping by connection ID, and a remote revision arriving while dirty.

- [ ] **Step 2: Write markup/contract tests**

Assert:

- top three tabs exist
- route list and inspector landmarks exist
- no horizontal connection-type tabs or hard-coded provider option list remains
- type search and grouped list exist
- drag handles plus Move Up/Move Down controls exist
- module override, default provider, and single fallback IDs are absent
- narrow layout switches to list/detail

- [ ] **Step 3: Verify RED**

Run:

~~~bash
node --test tests/web_model_config_state.test.mjs
pytest tests/test_desktop_web_model_settings.py tests/test_desktop_web_config_probe.py tests/test_desktop_web_multimodal_settings.py -q
~~~

Expected: FAIL because the desktop still has default/fallback subtabs and hard-coded selects.

- [ ] **Step 4: Implement the shared reducer**

model-config-state.js is DOM-free and owns clone, select, append, remove, move, touched fields, preset application, dirty tracking, revision conflict state, and API payload conversion. It never stores a raw existing secret; hydrated credentials default to action=keep.

- [ ] **Step 5: Implement desktop rendering**

The model panel contains:

- route header with add and save
- rows showing derived role, name, type/preset, model, and safe health
- inspector with name/model and descriptor-driven conditional fields
- vertical chooser with category headings and text search
- explicit secret source/actions
- exact probe status with timestamp and observed dimension
- migration issue resolution panel
- Runtime tab for concurrency, timeout, and circuit summaries

Use HTML drag/drop on wide screens plus buttons/keyboard for accessible reorder. Restore focus to the moved row. Beforeunload and settings-tab navigation guard only when the model draft is dirty.

- [ ] **Step 6: Separate model and general saves**

When the Models settings tab is active, show Save model route and hide the general form submit. app.js buildConfigUpdate excludes llm. model-settings.js owns its fetch/PUT/probe lifecycle and listens for config_reloaded events; it auto-hydrates only when clean.

Update _desktop_asset_version to hash model-settings.js and the shared state module.

- [ ] **Step 7: Verify GREEN and commit**

Run:

~~~bash
node --test tests/web_model_config_state.test.mjs
pytest tests/test_desktop_web_model_settings.py tests/test_desktop_web_config_probe.py tests/test_desktop_web_multimodal_settings.py tests/test_desktop_web_mobile_entry.py -q
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/web/shared/model-config-state.js src/openbiliclaw/web/desktop/assets/js/model-settings.js src/openbiliclaw/web/desktop/index.html src/openbiliclaw/web/desktop/assets/css/app.css src/openbiliclaw/web/desktop/assets/js/app.js src/openbiliclaw/api/app.py tests/web_model_config_state.test.mjs tests/test_desktop_web_model_settings.py tests/test_desktop_web_config_probe.py tests/test_desktop_web_multimodal_settings.py
git commit -m "feat: redesign desktop model routes"
~~~

---

### Task 11: Replace Extension Model Settings With the Same List/Detail Contract

**Files:**
- Create: extension/popup/popup-model-config-state.js
- Create: extension/popup/popup-model-settings.js
- Modify: extension/popup/popup-api.js
- Modify: extension/popup/popup.js
- Modify: extension/popup/popup.html
- Create: extension/tests/popup-model-settings.test.ts
- Modify: extension/tests/popup-settings.test.ts
- Modify: extension/tests/popup-api.test.ts
- Modify: docs/modules/extension.md

**Interfaces:**
- Extension model tab uses sequential list/detail at popup width.
- The editor is descriptor-driven and uses the same API payload/revision/secret semantics.
- General extension settings saves exclude model fields.

- [ ] **Step 1: Write failing state and API tests**

Cover append/remove/move, ten-item cap, first-item protection, touched preset defaults, keep/set/clear/env, exact probe payload, 409 handling, dirty remote update, and migration resolutions.

- [ ] **Step 2: Replace the old settings contract test**

Assert old cfgLlmProvider, cfgLlmFallbackProvider, cfgEmbeddingFallbackProvider, provider field blocks, and module override IDs are absent. Assert route list, detail back button, type search, descriptor list, shared-settings block, per-provider inspector, and model save controls exist and are wired.

- [ ] **Step 3: Verify RED**

Run:

~~~bash
cd extension
npm run test -- --test-name-pattern="model settings|settings page|model-config api"
~~~

Expected: FAIL because the popup still posts the legacy llm object.

- [ ] **Step 4: Implement API helpers**

Add fetchModelConfig, updateModelConfig, fetchModelConnectionTypes, and probeModelConnection through the existing authenticated request helper and deadline. Never use reveal_keys.

- [ ] **Step 5: Implement popup list/detail**

The list page shows route tabs, order, derived role, type, model, and health. Selecting a row opens detail; Back returns without discarding draft. Connection-type selection is a searchable grouped vertical list. Add/remove/move and save match desktop semantics.

Do not duplicate provider rules: render fields from descriptors and let server validation remain authoritative.

- [ ] **Step 6: Exclude models from legacy settings saves**

Remove model hydration/collection/probe code from popup.js. A model save failure does not block saving unrelated platform/general settings, and vice versa; the two buttons state their scope.

- [ ] **Step 7: Verify GREEN, typecheck, build, and commit**

Run:

~~~bash
cd extension
npm run test
npm run typecheck
npm run build
~~~

Expected: PASS and dist build succeeds.

Commit:

~~~bash
git add extension/popup/popup-model-config-state.js extension/popup/popup-model-settings.js extension/popup/popup-api.js extension/popup/popup.js extension/popup/popup.html extension/tests/popup-model-settings.test.ts extension/tests/popup-settings.test.ts extension/tests/popup-api.test.ts docs/modules/extension.md
git commit -m "feat: redesign extension model routes"
~~~

---

### Task 12: Add Full Mobile Web Model Route Editing

**Files:**
- Create: src/openbiliclaw/web/js/views/model-settings.js
- Modify: src/openbiliclaw/web/js/api.js
- Modify: src/openbiliclaw/web/js/app.js
- Modify: src/openbiliclaw/web/css/app.css
- Create: tests/test_mobile_web_model_settings.py
- Modify: tests/test_mobile_web_app_launch.py
- Modify: docs/modules/runtime.md

**Interfaces:**
- Mobile settings overlay gains Saved sync and Models sections.
- Models uses Chat/Embedding/Runtime tabs followed by list then detail.
- It imports the same web/shared/model-config-state.js reducer as desktop.

- [ ] **Step 1: Write failing mobile contract tests**

Assert model API functions, tabs, list/detail navigation, detail Back, descriptor search, add/remove/move, shared Embedding settings, save/probe, revision conflict, dirty close confirmation, and focus restoration. Assert no legacy llm object is sent through updateConfig.

- [ ] **Step 2: Verify RED**

Run: pytest tests/test_mobile_web_model_settings.py tests/test_mobile_web_app_launch.py -q

Expected: FAIL because mobile settings currently exposes only saved-sync.

- [ ] **Step 3: Implement same-origin model API calls**

Use requestJson with the existing CSRF/session behavior and a 60-second write timeout. Export fetchModelConfig, fetchModelConnectionTypes, updateModelConfig, and probeModelConnection.

- [ ] **Step 4: Implement sequential list/detail**

Keep the draft object alive while navigating. Closing or switching away prompts only when dirty. A clean config_reloaded event rehydrates; a dirty editor shows a remote-update banner and keeps the local draft until reload/discard.

Use touch-friendly Move Up/Move Down rather than drag as the primary mobile reorder action.

- [ ] **Step 5: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_mobile_web_model_settings.py tests/test_mobile_web_app_launch.py tests/test_mobile_web_view_models.py -q
node --test tests/web_model_config_state.test.mjs
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/web/js/views/model-settings.js src/openbiliclaw/web/js/api.js src/openbiliclaw/web/js/app.js src/openbiliclaw/web/css/app.css tests/test_mobile_web_model_settings.py tests/test_mobile_web_app_launch.py docs/modules/runtime.md
git commit -m "feat: add mobile model route settings"
~~~

---

### Task 13: Add the Unified Models CLI and Remove Module-Override Setup

**Files:**
- Create: src/openbiliclaw/cli_models.py
- Modify: src/openbiliclaw/cli.py
- Create: tests/test_cli_models.py
- Modify: tests/test_cli.py
- Modify: docs/modules/cli.md
- Modify: docs/modules/config.md

**Interfaces:**
- openbiliclaw models list
- openbiliclaw models add --kind chat|embedding
- openbiliclaw models edit ID
- openbiliclaw models remove ID
- openbiliclaw models move ID --position 1..10
- openbiliclaw models probe ID
- config-show displays the new structure with secrets reduced to status/source.

- [ ] **Step 1: Write failing command tests**

Use CliRunner and a temporary project root. Cover ordered output, add two same-type connections, editing by stable ID, removal guard, movement, exact probe, stale revision retry behavior, environment credential source, migration warning/resolution, and no raw secret in stdout/stderr.

- [ ] **Step 2: Pin CLI help and setup cleanup**

Assert the models group and six subcommands appear. Assert init/setup no longer asks for per-module overrides and setup-embedding delegates to the Embedding route editor rather than writing [llm.embedding].

- [ ] **Step 3: Verify RED**

Run: pytest tests/test_cli_models.py tests/test_cli.py -q

Expected: FAIL because no models Typer group exists.

- [ ] **Step 4: Implement a thin Typer layer**

cli_models.py calls ModelConfigService operations; it does not duplicate validation or TOML editing. Non-interactive flags include type, preset, name, model, base-url, api-mode, api-key, api-key-env, and credential-ref where applicable. Missing required values prompt only on a TTY.

list renders position, role/kind, ID, name, type/preset, model/shared model, credential status, and circuit status.

- [ ] **Step 5: Update guided CLI setup**

First select connection type, then preset/OAuth, then only descriptor fields. Create one Chat route connection. Embedding setup creates/edits shared settings plus one provider. Remove _save_module_overrides and _interactive_module_overrides.

- [ ] **Step 6: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_cli_models.py tests/test_cli.py -q
ruff check src/openbiliclaw/cli.py src/openbiliclaw/cli_models.py tests/test_cli_models.py tests/test_cli.py
mypy src/openbiliclaw/cli.py src/openbiliclaw/cli_models.py
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/cli.py src/openbiliclaw/cli_models.py tests/test_cli_models.py tests/test_cli.py docs/modules/cli.md docs/modules/config.md
git commit -m "feat: add ordered model route cli"
~~~

---

### Task 14: Cut Guided Setup, Agent Bootstrap, Docker, and Desktop Packaging to [models]

**Files:**
- Modify: src/openbiliclaw/web/setup/index.html
- Modify: tests/test_web_guided_init.py
- Modify: tests/test_web_guided_init_e2e.py
- Modify: config.example.toml
- Modify: src/openbiliclaw/docker_runtime.py
- Modify: tests/test_docker_runtime.py
- Modify: scripts/agent_bootstrap.py
- Modify: tests/test_agent_bootstrap.py
- Modify: scripts/install.sh
- Modify: scripts/install.ps1
- Modify: tests/test_install_contract_docs.py
- Modify: docker-compose.yml
- Modify: packaging/entry.py
- Modify: tests/test_packaging_entry.py
- Modify: docs/agent-install.md
- Modify: docs/docker-deployment.md
- Modify: docs/modules/init.md

**Interfaces:**
- Fresh setup chooses connection type, then preset/OAuth, and creates a Chat connection through the model API.
- Docker/bundled Ollama seed one Embedding provider under shared settings.
- Agent/bootstrap writers emit [models], preserve existing native routes, and use exact probes.
- Desktop packaging stops regex-editing [llm.embedding].

- [ ] **Step 1: Write fresh-install and setup tests**

Assert config.example.toml has schema_version=1, one starter Chat connection, shared Embedding settings, and no [llm] or module overrides. Assert setup loads descriptors, writes /api/model-config with revision, and probes the exact new connection before init.

- [ ] **Step 2: Rewrite agent-bootstrap tests around connection types**

Keep existing human choices/flags source-compatible where practical, but map:

- --provider deepseek -> type openai_compatible, preset deepseek
- --provider openai/openrouter/openai_compatible -> type openai_compatible with matching preset
- --provider claude -> type anthropic_compatible, preset anthropic
- --provider gemini/ollama -> native types
- Codex login -> codex_oauth

Remove --module-override. Add --connection-type and --preset as canonical flags; legacy --provider is a deprecated input alias only.

Pin that bootstrap can configure multiple Embedding providers only through repeated --embedding-endpoint values while sharing one --embedding-model/settings block.

- [ ] **Step 3: Rewrite Docker and packaging tests**

Assert OPENBILICLAW_EMBEDDING_MODEL and the compose sidecar produce:

~~~toml
[models.embedding]
enabled = true

[models.embedding.settings]
model = "bge-m3"

[[models.embedding.providers]]
id = "ollama-docker"
type = "ollama"
base_url = "http://ollama:11434/v1"
~~~

Bundled desktop helpers parse/update ModelConfig through serialization helpers, never regex-select [llm.embedding], and preserve remote provider choices.

- [ ] **Step 4: Verify RED**

Run:

~~~bash
pytest tests/test_web_guided_init.py tests/test_web_guided_init_e2e.py tests/test_agent_bootstrap.py tests/test_docker_runtime.py tests/test_packaging_entry.py tests/test_install_contract_docs.py -q
~~~

Expected: FAIL because every path still reads/writes legacy provider buckets.

- [ ] **Step 5: Implement fresh config and setup**

The setup page uses the same descriptor-driven vertical type chooser in a single-column layout. It may create the first connection or edit the selected existing connection, but it never posts the whole legacy config. Saved inline secrets hydrate as configured status only.

- [ ] **Step 6: Implement bootstrap/packaging model writes**

Keep pre-install question collection standard-library-only. Defer authoritative model persistence until the project files exist, then use the standard-library model_config types/validation/serialization functions. All existing unrelated config reuse remains intact.

Replace packaging _enable_ollama_embedding_default, _set_embedding_field, and _default_ollama_to_embedding_only with typed model-config transformations.

- [ ] **Step 7: Update installer copy and commands**

Install output says connection type/preset and ordered Chat/Embedding routes, not default/fallback provider buckets. Recovery commands use openbiliclaw models. Docker, git, and frozen desktop modes retain their existing paths.

- [ ] **Step 8: Verify GREEN and commit**

Run:

~~~bash
pytest tests/test_web_guided_init.py tests/test_web_guided_init_e2e.py tests/test_agent_bootstrap.py tests/test_docker_runtime.py tests/test_packaging_entry.py tests/test_install_contract_docs.py -q
ruff check scripts/agent_bootstrap.py packaging/entry.py src/openbiliclaw/docker_runtime.py
mypy src/openbiliclaw/docker_runtime.py
~~~

Expected: PASS.

Commit:

~~~bash
git add src/openbiliclaw/web/setup/index.html config.example.toml src/openbiliclaw/docker_runtime.py scripts/agent_bootstrap.py scripts/install.sh scripts/install.ps1 docker-compose.yml packaging/entry.py tests/test_web_guided_init.py tests/test_web_guided_init_e2e.py tests/test_agent_bootstrap.py tests/test_docker_runtime.py tests/test_packaging_entry.py tests/test_install_contract_docs.py docs/agent-install.md docs/docker-deployment.md docs/modules/init.md
git commit -m "refactor: configure model routes during setup"
~~~

---

### Task 15: Remove Legacy Runtime Dead Code, Synchronize Architecture Docs, and Run the Full Matrix

**Files:**
- Modify/Delete: legacy-only code in src/openbiliclaw/config.py
- Modify/Delete: legacy LLMRegistry and provider subclasses no longer used in src/openbiliclaw/llm/
- Modify: tests/test_config.py
- Modify: tests/test_llm_registry.py
- Modify: docs/changelog.md
- Modify: docs/architecture.md
- Modify: docs/spec.md
- Modify: README.md
- Modify: README_EN.md
- Modify: docs/modules/config.md
- Modify: docs/modules/llm.md
- Modify: docs/modules/cli.md
- Modify: docs/modules/extension.md
- Modify: docs/modules/runtime.md
- Modify: docs/modules/storage.md
- Modify: docs/modules/init.md
- Modify: docs/agent-install.md
- Modify: docs/docker-deployment.md

**Interfaces:**
- Runtime and product code have no Config.llm, LLMConfig, ModuleLLMConfig, default_provider, fallback_provider, or module-override dependency.
- Legacy parsing remains isolated inside model_config/migration.py for one compatibility release.
- /api/config legacy llm projection remains read-only and clearly deprecated.
- Architecture diagrams show descriptor registry -> ModelConfigService -> ordered routes -> all callers/surfaces.

- [ ] **Step 1: Add structural no-legacy tests**

~~~python
def test_runtime_source_no_longer_reads_legacy_llm_buckets() -> None:
    runtime_files = [
        ROOT / "src/openbiliclaw/api/runtime_context.py",
        ROOT / "src/openbiliclaw/llm/registry.py",
        ROOT / "src/openbiliclaw/llm/service.py",
        ROOT / "src/openbiliclaw/cli.py",
    ]
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        assert ".llm." not in text
        assert "module_overrides_from_config" not in text
~~~

Also scan desktop/mobile/extension/setup for the removed IDs and legacy llm write payloads.

- [ ] **Step 2: Delete or privatize compatibility-only classes**

Move any legacy shapes needed by migration into model_config/migration.py under Legacy-prefixed private types. Delete OpenRouterProvider and DeepSeekProvider subclass construction once factory tests prove the unified adapter. Remove old single-fallback code and model probe kinds from /api/config/probe-service.

Do not remove the one-release /api/config projection or [llm] loader.

- [ ] **Step 3: Update documentation and diagrams**

Update:

- config reference with complete [models] examples, invariants, revisions, secret sources, legacy migration, and config.local behavior
- LLM module with connection factory, ordered routes, circuits, exact probes, and aggregate errors
- CLI docs with all models commands
- runtime/storage docs with atomic bundle swap and usage metadata
- extension/init/install/Docker docs with list/detail editors and setup mappings
- architecture.md, spec.md section 3, and both README diagrams
- changelog current version block with a concise branch change entry

README release highlights are changed only if this branch includes a release/version bump; otherwise keep the existing release teaser.

- [ ] **Step 4: Run focused backend suites**

Run:

~~~bash
pytest tests/test_model_config_types.py tests/test_model_connection_types.py tests/test_model_config_serialization.py tests/test_model_config_migration.py tests/test_model_config_service.py tests/test_llm_connection_factory.py tests/test_llm_ordered_route.py tests/test_embedding_route.py tests/test_api_model_config.py tests/test_runtime_model_bundle.py tests/test_cli_models.py -q
~~~

Expected: PASS.

- [ ] **Step 5: Run every UI/install suite**

Run:

~~~bash
node --test tests/web_model_config_state.test.mjs
pytest tests/test_desktop_web_model_settings.py tests/test_mobile_web_model_settings.py tests/test_web_guided_init.py tests/test_web_guided_init_e2e.py tests/test_agent_bootstrap.py tests/test_docker_runtime.py tests/test_packaging_entry.py tests/test_install_contract_docs.py -q
cd extension
npm run test
npm run typecheck
npm run build
cd ..
~~~

Expected: PASS.

- [ ] **Step 6: Run the repository verification matrix**

Run:

~~~bash
ruff format src/ tests/ scripts/agent_bootstrap.py packaging/entry.py
ruff check src/ tests/ scripts/agent_bootstrap.py packaging/entry.py
mypy src/
pytest
pytest --cov=openbiliclaw --cov-report=term-missing --cov-fail-under=70
~~~

Expected: PASS with at least 70 percent coverage.

- [ ] **Step 7: Run final safety and artifact checks**

Run:

~~~bash
git diff --check
rg -n 'sk-[A-Za-z0-9]|Bearer [A-Za-z0-9]|api_key = "[^"]+"' src tests extension docs config.example.toml scripts packaging -g '!*.map' -g '!fixtures/**'
rg -n 'cfgLlmProvider|llmFallbackProvider|moduleSoulProvider|module_overrides_from_config|fallback_enabled' src/openbiliclaw/web extension/popup src/openbiliclaw/cli.py src/openbiliclaw/llm
OPENBILICLAW_PROJECT_ROOT="$(mktemp -d)" openbiliclaw config-show
git status --short
~~~

Expected:

- diff check is clean
- secret scan has only intentional test placeholders/documented empty examples
- removed UI/runtime identifiers have no production matches
- config-show starts, prints [models]-equivalent route information, and reveals no secret
- only intentional branch files are modified

- [ ] **Step 8: Commit the cleanup and synchronized docs**

~~~bash
git add -A -- src/openbiliclaw/config.py src/openbiliclaw/llm/base.py src/openbiliclaw/llm/registry.py src/openbiliclaw/llm/openai_provider.py src/openbiliclaw/llm/openrouter_provider.py src/openbiliclaw/llm/claude_provider.py src/openbiliclaw/llm/service.py tests/test_config.py tests/test_llm_registry.py docs/changelog.md docs/architecture.md docs/spec.md README.md README_EN.md docs/modules/config.md docs/modules/llm.md docs/modules/cli.md docs/modules/extension.md docs/modules/runtime.md docs/modules/storage.md docs/modules/init.md docs/agent-install.md docs/docker-deployment.md
git commit -m "docs: complete model route refactor"
~~~

## Plan Self-Review

- [ ] Every acceptance criterion in the approved design has at least one implementation task and one automated assertion.
- [ ] Chat primary/fallback equality is represented only by ordered arrays; no role or priority field was reintroduced.
- [ ] Embedding providers cannot carry model/settings overrides at type, parser, API, UI, CLI, or installer layers.
- [ ] Connection types and presets are protocol/auth descriptors, not new hard-coded frontend tabs.
- [ ] Codex OAuth containment is tested before token resolution.
- [ ] Revision, secret, backup, config.local, atomic write, runtime swap, and rollback paths are all explicit.
- [ ] Ordinary config saves cannot accidentally migrate or overwrite model configuration.
- [ ] Legacy ambiguity is surfaced through safe resolution reports, never silently dropped.
- [ ] Desktop, mobile, extension, CLI, setup, agent bootstrap, Docker, and desktop packaging all use the same backend contract.
- [ ] The plan contains no placeholder file names, TODO implementation steps, unbounded fallback behavior, or real-provider automated tests.
- [ ] All added dataclasses, Pydantic models, protocols, and JavaScript payloads use consistent field names: type, preset, model, api_mode, timeout_seconds, output_dimensionality, similarity_threshold, and multimodal_enabled.
