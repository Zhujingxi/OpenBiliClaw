# OpenBiliClaw Refactor Implementation Plan

> Authority: [`../architecture.md`](../architecture.md). This directory plans the implementation of that target architecture. It does not implement it.

## Scope and non-negotiable constraints

- Backward compatibility is not required for Python APIs, HTTP routes, configuration shape, database schema, frontend state, or extension messages.
- User data must never be silently discarded. Any destructive schema cutover must stop, create a verified backup, and require an explicit reset or import decision.
- Production source languages are Python and TypeScript only. Handwritten or checked-in `.js`, `.mjs`, and `.cjs` files are forbidden. Browser build output may contain generated JavaScript under ignored `dist/` directories because browsers execute JavaScript.
- Web and extension presentation use Vue 3, Pinia, TypeScript, and Vite.
- Python is checked with MyPy strict mode. Project-owned code may not use untyped dictionaries, implicit `Any`, untyped decorators, blanket `type: ignore`, or `ignore_errors` overrides.
- TypeScript uses `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitOverride`, and `useUnknownInCatchVariables`; `allowJs` is false.
- Critical behavior remains deterministic Python. Agents may interpret, evaluate, and express; they do not own persistence, scheduling, ranking invariants, credential access, or transactions.
- No compatibility wrappers, generic hook bus, service locator, command bus, repository framework, or speculative plugin framework may be introduced.
- The branch is complete only when replaced implementations and dead tests are deleted. New and old implementations may coexist temporarily while the branch is under construction, but production composition must never route through both.

## Target source layout

```text
src/openbiliclaw/
├── core/                         # lifecycle, jobs, resources, config, health
├── infrastructure/               # SQLite, filesystem, HTTP, events, credentials
├── ai/
│   ├── runtime/                  # typed agent execution and routing
│   ├── providers/                # model and embedding construction
│   └── evaluation/               # generic offline run/report mechanics
├── access/                       # access methods, handles, verification
├── content/
│   ├── integration/              # contracts, capabilities, registry, projections
│   └── providers/                # first-party provider packages
├── observations/                 # observation ingress and persistence
├── understanding/                # profile, evidence, analyzers, projections
├── recommendation/
│   ├── discovery/
│   ├── evaluation/
│   ├── selection/
│   └── expression/
├── application/                  # explicit cross-module workflows
├── assistant/                    # PydanticAI conversational facade
├── hosts/
│   ├── api/                      # FastAPI transport
│   └── cli/                      # Typer transport
└── composition/                  # concrete construction and process entrypoints

frontend/
├── package.json                  # npm workspaces
├── tsconfig.base.json
├── packages/
│   ├── api-client/               # generated OpenAPI types + typed transport
│   └── presentation/             # cards, descriptors, shared Vue components
└── apps/
    ├── web/                      # desktop/mobile Vue host
    └── extension/                # extension runtime + Vue popup/sidebar
```

Names may change only if the architecture document is updated in the same change. Package boundaries and ownership may not drift silently.

## Large-module implementation order

| Order | Plan | Requires | Produces |
|---:|---|---|---|
| 1 | [Core Runtime](01-core-runtime.md) | Standard library, Pydantic | lifecycle, configuration, job/resource contracts |
| 2 | [Infrastructure](02-infrastructure.md) | Core contracts | database/session primitives, repositories, HTTP, vault, events |
| 3 | [AI Runtime](03-ai-runtime.md) | Core, infrastructure telemetry | typed PydanticAI execution, routing, budgets |
| 4 | [Model and Embedding Providers](04-model-embedding-providers.md) | AI Runtime | concrete compatible models and embeddings |
| 5 | [Provider Access](05-provider-access.md) | Core, credential infrastructure | anonymous/manual access handles and verification |
| 6 | [Content Integration](06-content-integration.md) | Core, access contracts | content contracts, capabilities, projections, registry |
| 7 | [Content Providers](07-content-providers.md) | Content Integration, Access | first-party provider implementations |
| 8 | [Observation Ingress](08-observation-ingress.md) | Content references, infrastructure | immutable typed observations and producer boundary |
| 9 | [User Understanding](09-user-understanding.md) | AI Runtime, observations | canonical profile, analyzers, evidence, bounded views |
| 10 | [Discovery & Recommendation](10-discovery-recommendation.md) | Content, Understanding, AI Runtime | candidate inventory, evaluation, selection, expression |
| 11 | [Application Workflows](11-application-workflows.md) | Access and all product services | explicit transactional use cases |
| 12 | [Assistant](12-assistant.md) | AI Runtime, application workflows | bounded conversation and native tools |
| 13 | [API and CLI Hosts](13-hosts-api-cli.md) | Application, Assistant, Core | typed FastAPI/OpenAPI and Typer surfaces |
| 14 | [Presentation](14-presentation.md) | Host API contract | Vue/Pinia web and extension shells |
| 15 | [Runtime Composition](15-runtime-composition.md) | Every preceding module | one production graph, lifecycle cutover, legacy deletion |

The order is dependency order, not permission to defer tests or documentation. Each module's tests and docs ship with that module.

## Dependency rules

```text
hosts → application + assistant + core
assistant → application + ai + understanding projection contracts + content integration tool contracts
application → product modules → contracts
recommendation → content integration + understanding + ai
understanding → observations + ai
content providers → content integration + access
concrete adapters → infrastructure contracts
composition → all concrete modules
```

Forbidden dependencies:

- Core Runtime importing product modules.
- Understanding importing Recommendation or concrete content providers.
- Content providers importing Understanding, Recommendation, Assistant, or host code.
- Assistant importing repositories or credential infrastructure.
- Frontend code importing provider-specific backend internals.
- Domain modules importing FastAPI, Typer, Vue, Pinia, or transport schemas.
- Any model-visible code importing the credential vault.

Add an AST-based architecture test for Python imports and workspace-boundary checks for TypeScript imports. Do not add an architecture-framework dependency for this.

## Shared implementation method

Every module follows the same discipline without sharing a generic framework:

1. Define its owned types, invariants, and narrow inbound/outbound contracts.
2. Add contract and unit tests before connecting external systems.
3. Implement deterministic behavior and persistence.
4. Add external adapters at the boundary.
5. Connect only through explicit constructors or application workflows.
6. Replace callers directly; do not preserve old signatures.
7. Delete superseded code, tests, configuration, and docs.
8. Run repository-wide quality gates.

## Legacy disposition ledger

| Current area | Target owner | Final disposition |
|---|---|---|
| `agent/orchestrator.py`, `agent/skill.py` | None / Assistant tools | Delete; no god orchestrator replacement |
| `llm/` | AI Runtime and provider plugins | Move required behavior, then delete custom completion stack |
| `sources/`, `bilibili/`, `saved_sync/` | Content Integration, Content Providers, Access | Replace protocols and task dispatch; delete duplicated adapters |
| `youtube/` | Content Providers | Move client/Takeout behavior into the YouTube provider, then delete |
| `api/source_auth/`, `auth_core.py` | Provider Access + API Host | Replace directly; no legacy form compatibility |
| `api/auth.py` | API Host | Replace with typed authentication middleware/routes and tested CSRF/rate policy, then delete |
| `memory/`, non-dialogue `soul/` | Observations + User Understanding | Preserve validated domain rules, replace ownership and storage |
| `soul/dialogue.py` and dialogue runtime helpers | Assistant + Application Workflows | Replace custom dialogue/tool plumbing |
| `discovery/`, `recommendation/` | Discovery & Recommendation | Consolidate into one pipeline and delete old engines |
| `eval/{evaluator,loop,optimizer,report,run_logger,human_feedback,agents}.py` | AI Runtime evaluation harness | Retain only generic typed execution/reporting mechanics; delete the rest |
| `eval/{discovery_evaluator,discovery_optimizer,discovery_scenario}.py` | Discovery & Recommendation | Move retained scenarios/rubrics, then delete old files |
| `eval/{event_simulator,persona_generator,persona_judge,persona_pool,speculation_evaluator}.py` | User Understanding | Move retained scenarios/fixtures, then delete old files |
| `storage/{database,migration,maintenance,x_health}.py` | Infrastructure | Split into schema/session and concrete repositories; delete storage monoliths |
| `api/app.py`, `api/models.py`, `cli.py` | API/CLI Hosts | Split by transport capability and delete monoliths |
| `api/runtime_context.py` | Runtime Composition | Replace with explicit construction; delete |
| `runtime/{api_server,activity_feed}.py` | API Host | Move retained delivery behavior, then delete |
| `runtime/{init_coordinator,init_prereqs}.py` | Runtime Composition | Replace with explicit lifecycle construction, then delete |
| `runtime/{refresh,presence,task_registry}.py` | Core Runtime | Retain only generic supervision/health behavior, then delete originals |
| `runtime/{candidate_eval,expression_copy,inspiration_pipeline,keyword_fetch,keyword_planner,pool_gate,producer_cadence,source_policy}.py` | Discovery & Recommendation | Move retained pipeline behavior, then delete |
| `runtime/*_producer.py` | Discovery & Recommendation | Replace producer wrappers with strategies/jobs, then delete |
| `runtime/{dialogue_reply_scheduler}.py` | Assistant | Move retained durable-turn behavior, then delete |
| `runtime/{feedback_scheduler,account_sync,source_incremental_sync}.py` | Application Workflows | Replace cross-module sequencing with explicit workflows, then delete |
| `runtime/{event_ingress}.py` | Observation Ingress | Replace with typed ingress, then delete |
| `runtime/{events}.py` | Infrastructure events | Replace transport publication, then delete |
| `runtime/{embedding_progress,embedding_seed,ollama_supervisor}.py` | Model/Embedding Providers | Move retained diagnostics/bootstrap behavior, then delete |
| `runtime/{image_cache,image_fetch}.py` | Infrastructure | Move bounded file/HTTP behavior, then delete |
| `runtime/{github_stars}.py` | Content Providers | Retain only through an explicit provider capability; otherwise delete |
| `runtime/{updater}.py`, `docker_runtime.py` | Runtime Composition / installer | Move supported process/update behavior to entrypoint/install tooling, then delete |
| `runtime/autostart/` | Infrastructure host-platform adapters | Move supported OS registration behavior behind typed platform adapters, then delete old package |
| `integrations/agent.py`, `integrations/openclaw/` | API/CLI Hosts | Keep only explicit external-host adapters over Application; delete broad facades/service location |
| `network.py`, `tls_proxy.py`, `proc.py`, `logging_setup.py` | Infrastructure | Move typed HTTP/process/telemetry adapters, then delete top-level files |
| `published_time.py` | Content Integration | Retain one shared typed temporal normalization utility only if multiple providers use it |
| `config.py` | Core Runtime | Replace with frozen typed settings and loader, then delete monolith |
| `src/openbiliclaw/web/**/*.js` | Vue web app | Delete after Vue cutover |
| `extension/popup/**/*.js` | Vue extension app | Rewrite in TypeScript/Vue; delete JavaScript sources |
| `extension/scripts/**/*.mjs` | Python packaging/release scripts | Rewrite executable scripts in Python; delete JavaScript sources |

A legacy file has exactly one target owner. If behavior spans modules, extract the domain rule once and let callers depend on its owner.

## Global quality gates

Run at the end of every internal phase that changes the relevant language:

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/ tests/
pytest --cov=openbiliclaw --cov-report=term-missing

npm --prefix frontend run format:check
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
```

Additional repository gates:

- New Python modules maintain at least 90% branch coverage; repository coverage must not fall below 80%.
- `ALLOW_MODEL_REQUESTS=False` is enforced for normal tests. Real provider tests are opt-in integration tests.
- MyPy enables `disallow_any_explicit = true` for project-owned code; external dynamic libraries are isolated behind typed adapters or local stubs.
- Ruff enables `PGH003` and `PGH004`; every unavoidable targeted suppression includes an error code and an adjacent rationale. CI scans project-owned source for disallowed blanket `cast`, `# type: ignore`, and `# noqa` forms.
- No source or test `.js`, `.mjs`, or `.cjs` files remain outside ignored generated output.
- OpenAPI generation and generated TypeScript types are reproducible and clean in `git diff`.
- SQLite foreign keys, unique constraints, and transactional invariants have direct tests.
- Secret redaction tests inspect logs, API responses, tool outputs, and model messages.
- All background tasks have cancellation, timeout, ownership, and health tests.
- No production module has two implementations of the same responsibility after cutover.

## Documentation gate

Every implementation change follows `CLAUDE.md`'s documentation checklist. Depending on scope, update in the same change:

- `docs/modules/<module>.md`
- `docs/changelog.md`
- `docs/architecture.md`
- `docs/spec.md`
- `README.md` and `README_EN.md` diagrams
- `docs/modules/api.md`, `docs/modules/cli.md`, or `docs/modules/config.md`
- Installer and deployment documentation when dependencies or build steps change

The target-state document remains under `docs/refactor/`; current-state documents must describe only code that has actually landed.

## Whole-refactor completion criteria

- All 15 module plans satisfy their completion criteria.
- Runtime Composition creates one explicit production component graph.
- All Python passes strict typing with no project-owned suppressions hiding errors.
- All frontend and extension source is TypeScript/Vue; no handwritten JavaScript remains.
- Desktop, mobile, and extension hosts consume the same presentation and API contracts.
- Credentials remain inaccessible to agents and frontend reads.
- Normal recommendation feeds operate without the Assistant.
- Proactive jobs stop cleanly and cannot exceed resource budgets.
- Legacy modules in the disposition ledger are deleted.
- Tests, current architecture docs, configuration docs, and build/install docs match the final code.
