# OpenBiliClaw Target Architecture

> Status: architecture discussion draft. This document describes the intended target state, not the architecture currently implemented on `main`, an implementation sequence, or a migration plan.

## 1. System overview

```text
┌──────────────────────────────────────────────────────────┐
│                         Hosts                            │
│ Desktop Web · Mobile Web · Extension · CLI · API        │
└───────────────┬────────────────────────────┬─────────────┘
                │                            │
                ▼                            ▼
┌─────────────────────────────┐  ┌─────────────────────────┐
│ Presentation Contract       │  │ Observation Ingress     │
│ cards · views · actions     │  │ events · feedback       │
└───────────────┬─────────────┘  └────────────┬────────────┘
                │                             ▼
                │                 ┌─────────────────────────┐
                │                 │ User Understanding      │
                │                 │ memory · preferences    │
                │                 │ evidence · profile      │
                │                 └────────────┬────────────┘
                │                              │
                ▼                              ▼
┌──────────────────────────────────────────────────────────┐
│                 Application Workflows                    │
│ connect · discover · recommend · feedback · dialogue     │
└───────────────┬────────────────────────────┬─────────────┘
                ▼                            ▼
┌─────────────────────────────┐  ┌─────────────────────────┐
│ Assistant                   │  │ Discovery &             │
│ conversation · native tools │  │ Recommendation          │
└───────────────┬─────────────┘  └────────────┬────────────┘
                │                             ▼
                │                 ┌─────────────────────────┐
                │                 │ Content Integration     │
                │                 │ registry · capabilities │
                │                 │ tools · projections     │
                │                 └────────────┬────────────┘
                │                              ▼
                │                 ┌─────────────────────────┐
                │                 │ Content Providers       │
                │                 │ native models · access  │
                │                 │ tools · presentation    │
                │                 └─────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────┐
│ AI Runtime                                               │
│ PydanticAI · routing · limits · usage · capabilities     │
└───────────────────────────┬──────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────┐
│ Model and Embedding Provider Plugins                     │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│ Core Runtime                                             │
│ tick · resources · lifecycle · config · plugin hosting   │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│ Infrastructure                                           │
│ SQLite · files · HTTP · credentials · events · telemetry │
└──────────────────────────────────────────────────────────┘
```

## 2. Core Runtime

The Core is the minimal operational kernel.

### Owns

- Tick scheduling
- Background-job admission
- Shared concurrency and resources
- Cancellation and timeouts
- Startup, shutdown, pause, drain, and reload
- Configuration activation
- Typed extension registration
- Health reporting
- Secret-access policy

### Does not own

- Content semantics
- User understanding
- Recommendation policy
- Prompts
- Provider authentication details
- Frontend rendering

Plugins register bounded capabilities and jobs; they cannot create unmanaged background loops.

## 3. AI Runtime

The AI Runtime is the single model-execution boundary.

### Owns

- PydanticAI agent execution
- Model-instance routing
- Capability-safe fallback
- Native function calling
- Structured outputs
- Token, request, and tool-call limits
- Shared model concurrency
- Usage and cost attribution
- Provider errors, health, and cooldown

### Capability requirement

Every model instance declares capabilities such as:

```text
native tools
structured output
vision
streaming
reasoning control
context size
```

The router must never silently send an agent to an incompatible fallback model.

Agent definitions remain owned by their product modules and are addressable by the evaluation harness.

## 4. Model and Embedding Providers

Model providers use PydanticAI's `Model` abstraction rather than another custom completion API.

A model-provider plugin contributes:

- Model construction
- Provider configuration
- Capability declaration
- Model discovery
- Provider-specific error conversion

Embedding providers use a separate contract because embeddings have different configuration, dimensionality, caching, and provenance requirements.

## 5. Provider Access

Provider Access separates content retrieval from how credentials are acquired.

```text
User supplies access
        ↓
Access Method
        ↓
Opaque Access Handle
        ↓
Content Provider
```

### Included access methods

- Anonymous access
- Manually supplied API token, PAT, cookie, or session fields
- Provider-specific credential forms
- Credential verification
- Connection status

### Extension boundary

`AccessMethod` is a narrow typed extension point for possible integrations such as:

- Browser-extension sessions
- Managed-browser profiles
- OAuth
- CLI credential import
- OS browser credential import

Browser-session acquisition and managed browsers are not part of the current scope.

### Credential storage

- Use the operating-system credential store where available.
- Otherwise use a dedicated local secret store with restrictive permissions.
- Never store credentials in general application configuration.
- The frontend may set, replace, delete, and verify credentials, but cannot read them back.
- Raw credentials never enter model context, tool results, telemetry, or logs.
- Access handles are scoped to one provider, account, and permission set.

## 6. Content Integration

Content Integration is a thin shared contract and registry layer.

### Owns

- Content-provider registration
- Capability discovery
- Provider availability
- Shared content references
- Schema and version validation
- Provider tool selection
- Common projections
- Provider action routing

It does not flatten every provider into one universal content schema.

## 7. Content Providers

Each content provider is an independent integration package.

```text
Content Provider
├── manifest
├── native content schemas
├── access implementation
├── capabilities
├── typed service API
├── native agent tools
├── recommendation projection
├── presentation descriptors
└── optional observation producer
```

### Native content structures

Providers retain their own models:

```text
BilibiliVideo
BilibiliArticle
RedNoteImagePost
RedNoteVideoPost
RedditThread
YouTubeVideo
```

A shared envelope identifies provider-native records:

```text
provider
content kind
content ID
schema version
canonical URL
native payload
```

### Purpose-specific projections

Cross-provider modules consume small projections:

```text
ContentPreview
RecommendationCandidate
SearchDocument
CardData
```

### Provider access

The provider exposes a typed programmatic API for:

- Search
- Feed
- Fetch
- Related content
- Creator content
- History
- Saved content
- Provider-specific actions

Native agent tools are thin wrappers over this API. Tools are not the only way to access providers.

## 8. Observation Ingress

Observation Ingress is the explicit producer boundary for user-understanding inputs.

```text
Observation Provider
        ↓
Validate · normalize · deduplicate
        ↓
Immutable observation record
        ↓
User Understanding
```

### Observation sources

- Explicit recommendation feedback
- Assistant dialogue
- Profile edits
- Content opens and saves reported by a host
- Imported provider history
- Future browser-extension observation provider
- Future custom observation plugins

### Observation contract

Every observation carries:

- Source
- Event type
- Account identity when applicable
- Content reference
- Timestamp
- Provenance
- Idempotency identity
- Trust level

Observation providers cannot modify the profile directly.

A future browser extension can implement both `AccessMethod` and `ObservationProvider` without changing Understanding or Recommendation.

## 9. User Understanding

User Understanding is the persistent owner of the user model.

### Owns

- Observations
- Preferences and avoidance
- Awareness and insights
- Evidence provenance
- User overrides
- Canonical profile
- Learning ledger
- Profile projections

### Processing model

```text
Observations
    ↓
Understanding analyzers
    ↓
Profile update proposals
    ↓
Validation and conflict handling
    ↓
Ledger and canonical commit
```

Understanding analyzers may be configurable or extensible, but they only propose updates.

User Understanding remains the only canonical profile writer.

Other modules receive bounded projections:

```text
DiscoveryProfile
RecommendationProfile
DialogueProfile
```

The persistent element is the stored user model, not a permanent LLM conversation.

## 10. Discovery & Recommendation

This module owns the complete content-supply-to-presentation pipeline.

### Owns

- Proactive discovery
- Interactive discovery
- Query planning
- Provider selection
- Discovery strategies
- Candidate acquisition
- Deterministic prefiltering
- Agent evaluation
- Candidate-pool admission
- Seen-item exclusion
- Negative-preference enforcement
- Cross-provider balancing
- Diversity and ranking
- Personalized expression
- Recommendation history
- Feedback state

### Main workflow

```text
Tick or user request
        ↓
Load compact Understanding projection
        ↓
Choose discovery strategies and providers
        ↓
Call typed provider APIs
        ↓
Project native records to RecommendationCandidate
        ↓
Deduplicate and cheaply prefilter
        ↓
One-shot batch Evaluation Agent
        ↓
Persist evaluated candidate pool
        ↓
Deterministic exclusion, ranking, and diversity
        ↓
Optional Expression Agent
        ↓
Persist final recommendations
        ↓
Presentation contract
```

### Responsibility split

Agents handle:

- Query generation
- Semantic relevance
- Topic interpretation
- Recommendation wording

Deterministic Python handles:

- Deduplication
- Exclusions
- Seen history
- Quotas
- Diversity
- Ranking constraints
- Transactions
- Scheduling
- Retry policy

Provider APIs are used for deterministic workflows. Native provider tools are used for Assistant-driven ad hoc exploration.

## 11. Assistant

The Assistant is a conversational facade over application capabilities.

### Owns

- Main dialogue agent
- Conversation history
- Intent interpretation
- Native application tools
- Conversational result presentation
- Recommendation explanations

Tools include:

```text
get_recommendations
search_content
get_content_details
record_feedback
show_profile
edit_profile
list_sources
connect_source
```

Assistant tools call Application Workflows. They do not access databases, provider credentials, or internal repositories directly.

Normal recommendation feeds work without invoking the Assistant.

## 12. Application Workflows

Application Workflows make cross-module sequencing explicit.

Examples:

```text
ConnectContentProvider
RecordObservation
RecordFeedback
RefreshRecommendations
GetRecommendations
HandleDialogue
ApplyProfileEdit
```

Example feedback flow:

```text
Feedback
├── Discovery & Recommendation stores feedback
├── Observation Ingress records the observation
├── User Understanding processes it
└── affected recommendation inventory is invalidated
```

Critical behavior remains visible in normal Python rather than hidden in a generic event-hook system.

## 13. Presentation Contract and Host Shells

There is one content-presentation contract consumed by multiple frontend shells.

```text
Presentation Contract
├── Desktop Web shell
├── Mobile Web shell
├── Extension shell
└── Generic external client
```

### Shared contract owns

- Provider views and tabs
- Card data
- Card variants
- Provider actions
- Unified-feed representation
- Generic fallback rendering

### Each host shell owns

- Navigation
- Responsive layout
- Loading and errors
- Pagination
- Accessibility
- Theme
- Host-specific interaction behavior

### Provider contribution

Providers may declare:

- Views/tabs
- Content kinds
- Layout preference
- Card projection
- Safe card variant
- Provider actions
- Optional trusted renderer

Every provider must provide generic `CardData`, ensuring it works without specialized frontend code.

Backend providers cannot inject arbitrary HTML, CSS, or JavaScript.

## 14. Infrastructure

Infrastructure provides concrete technical implementations:

- SQLite repositories
- Filesystem storage
- Credential storage
- HTTP clients
- Event transport
- WebSocket delivery
- Provider network access
- Telemetry

Product modules define the required ports; Infrastructure implements them.

## 15. Runtime Composition

Runtime Composition is the only place that knows all concrete implementations.

It:

- Loads configured providers and extensions
- Builds model instances
- Builds content providers
- Injects repositories and clients
- Connects workflows
- Registers bounded jobs
- Coordinates reload and shutdown

It owns composition, not product behavior.

## 16. State ownership

| State | Owner |
|---|---|
| Runtime jobs and health | Core Runtime |
| Model routes and usage | AI Runtime |
| Provider credentials | Provider Access |
| Provider-native cache | Content Provider |
| Raw observations | Observation Ingress |
| User profile and evidence | User Understanding |
| Candidate pool and evaluations | Discovery & Recommendation |
| Recommendation/shown history | Discovery & Recommendation |
| Dialogue history | Assistant |
| UI preferences | Individual host shell |
| Concrete persistence | Infrastructure |

No module writes another module's state directly.

## 17. Extension boundaries

Typed extension points:

- `ModelProvider`
- `EmbeddingProvider`
- `ContentProvider`
- `AccessMethod`
- `ObservationProvider`
- `UnderstandingAnalyzer`
- `DiscoveryStrategy`
- `AssistantSkill`
- Presentation descriptor or trusted renderer

These are separate typed contracts, not one generic plugin or hook interface.

Runtime lifecycle, canonical profile commits, recommendation correctness, transactions, and secret policy are not extensible hooks.

## 18. Token efficiency

- Agents receive compact profile projections.
- Provider toolsets are exposed only when relevant.
- Search returns previews and content references.
- Details are fetched only for selected records.
- Deterministic and embedding prefilters run before model evaluation.
- Candidate evaluation is batched.
- Expression agents receive only selected candidates.
- Dialogue history is bounded and summarized.
- Tool schemas and results have explicit size limits.
- Stable instructions remain separate from volatile context.

## 19. Testability

Every module must be independently testable without external services.

- AI agents use PydanticAI `TestModel` or `FunctionModel`.
- Production model requests are disabled by default in tests.
- Content providers have contract tests and fake implementations.
- Access methods use fake credential handles.
- Observation providers use recorded typed events.
- Understanding uses in-memory repositories and deterministic clocks.
- Discovery & Recommendation uses fake providers and fixed evaluator outputs.
- Presentation descriptors are validated against every host shell.
- Application workflows run without network access.
- Small opt-in provider tests verify real model capabilities.

## 20. Evaluation compatibility

Agent definitions expose stable identities for the evaluation system:

```text
understanding.preference
understanding.insight
recommendation.query
recommendation.evaluate
recommendation.expression
assistant.dialogue
```

The evaluation harness can select:

- Agent instructions
- Output schema
- Context projection version
- Model route
- Recorded dataset

This preserves prompt and model regression testing without coupling evaluation logic to runtime execution.

## 21. Explicit non-goals

- Browser-extension session acquisition implementation
- Managed backend browsers
- Automatic cookie extraction
- Arbitrary executable frontend plugins
- Plugin marketplace
- Generic service locator
- Generic event-hook bus
- Persistent autonomous LLM sessions
- LLM-controlled critical scheduling
- LangGraph
- LiteLLM gateway
- Backward-compatibility adapters

The interfaces allow future browser access and observation integrations without including their implementation in the present target architecture.
