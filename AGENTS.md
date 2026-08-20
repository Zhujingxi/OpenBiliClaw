# Repository Guidelines

## Project Structure and Module Organization

OpenBiliClaw is currently defined by `docs/architecture.md`, `docs/spec.md`, and `docs/modules/`; do not restore deleted legacy implementations based on historical plans.

- `src/openbiliclaw/`
  - `core/`: settings, resource budgets, lifecycle, task supervision, and health state.
  - `composition/`: the only production composition, lifecycle, reload, and CLI entrypoint; concrete implementations are assembled only here.
  - `infrastructure/`: SQLite/archive, credential vault, HTTP, file, event, and telemetry adapters.
  - `access/`: anonymous, manual-secret, and plugin-assisted access plus verifiers; credentials pass through opaque vault handles.
  - `content/integration/`: cross-source typed contracts, purpose-specific projections, and provider registry; `content/providers/`: provider-owned schemas, transports, and manifests.
  - `observations/`: immutable observation ingress, validation, repository, and service.
  - `understanding/`: profile, evidence, ledger, analyzer, resynthesis, and embedding projection.
  - `recommendation/`: discovery, evaluation, expression, allocation, selection, semantic recall, inspection, policy journal, and reward learning.
  - `application/`: the only sequencing layer for cross-module product workflows.
  - `assistant/`: bounded, typed, propose-only Assistant dialogue and tools.
  - `ai/`: PydanticAI runtime, native provider factory, capability checks, and offline evaluation.
  - `hosts/api/`: FastAPI `/v1` transport; hosts, CLI, and Assistant all work through Application contracts and do not implement business logic in the transport.
- `tests/`: Python tests organized by the modules above; `tests/e2e/` contains explicitly opt-in tests against real services.
- `frontend/`: Vue 3/Pinia/TypeScript workspace; `apps/web/` is the responsive web app, `apps/extension/` is the extension UI, and `packages/api-client/` and `packages/presentation/` are shared packages. The root `extension/` directory contains only declarative manifests and icons.

The current production graph has no legacy/v2 dual track: deleted `runtime/`, `soul/`, old `sources/`, old `storage/`, and compatibility facades must not be rewired. Providers that implement only contracts/projections without live transport must fail closed at the capability boundary rather than copy an old implementation.

## Build, Test, and Development Commands

First create a virtual environment and install development dependencies:

```bash
pip install -e ".[dev]"
```

Common backend gates:

```bash
ruff format src/ tests/ scripts/
ruff check src/ tests/ scripts/
mypy src/
ALLOW_MODEL_REQUESTS=False pytest --cov=openbiliclaw --cov-branch --cov-fail-under=90
```

Run locally:

```bash
openbiliclaw check [--config PATH] [--data-dir PATH]
openbiliclaw serve [--config PATH] [--data-dir PATH]
```

`check` performs the full composition/readiness check without listening on a socket; `serve` is the only production service entrypoint. Product CLI commands must be JSON-only pass-throughs to Application workflows on the in-process composition. They must not use an HTTP loopback or add business logic at the command layer.

When changing the frontend, run:

```bash
npm --prefix frontend run format:check
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
```

After an API schema change, use `npm --prefix frontend run generate:api` to update the generated client; do not edit generated files manually. Real provider/model calls must be explicitly opted into and marked with the appropriate `integration`/`e2e` marker.

## Development Order and Architectural Constraints

- Read the relevant `docs/modules/*.md`, `docs/architecture.md`, and `docs/spec.md` first, then develop in dependency order: Infrastructure/Core → providers/services → Application/Assistant → hosts/frontend. Composition owns concrete assembly.
- Every product operation has exactly one Application workflow owner. API, CLI, and Assistant tools are thin adapters; service locators, command buses, parallel sequencing, and direct domain repository writes are prohibited.
- Content Integration consumes only provider-owned typed payloads and purpose-specific projections. It must not read credentials, scrape websites, rank recommendations, or import Understanding/Recommendation/Assistant/Hosts.
- Observation is the factual ingress. Understanding consumes immutable observations and cannot import Recommendation. Recommendation consumes only the required narrow projections, embedding index, and policy journal.
- Chat/embedding models may be integrated only through `ai.runtime` and the PydanticAI native provider factory. OpenBiliClaw does not host, package, or supervise model runtimes; do not add a second `complete(prompt) -> str` call stack.
- Credentials are visible only at Access/Vault boundaries. Provider recipes are frozen declarative data; the browser extension is a generic grabber with no provider business logic.
- Composition owns validate → ready → swap → drain → close. Core owns background tasks, cancellation, timeouts, resources, and health records. A failed replacement must not replace the active graph.
- The database rejects unversioned or destructive cutovers. Destructive migrations require explicit authorization and a verified backup; never reset user data silently.

## Coding Style and Naming Conventions

Python uses four-space indentation, complete type annotations, and clear module boundaries; public APIs and core data structures require concise docstrings. Ruff manages formatting/linting, and MyPy uses strict configuration. Module filenames use lowercase underscores; tests use `test_<behavior>`. Commit only TypeScript/Vue frontend sources, never handwritten JavaScript.

Prefer existing contracts, repositories, projections, and the standard library. Do not add speculative abstractions, factories, or compatibility layers for one implementation. Never omit protections for security, permissions, input boundaries, idempotency, cancellation, transactions, or data preservation merely to reduce code.

## Testing Requirements

New features normally include unit tests. Split real-site or model flows into mockable unit tests, and keep real calls only in explicitly opt-in integration/e2e tests. Keep aggregate branch coverage at or above 90%; do not let aggregate coverage hide low coverage in a new module. Non-trivial parsers, branches, transactions, and security logic require at least one runnable regression check.

## Documentation Update Requirements (Mandatory)

Every commit, merge to main, and release, as well as every change to interfaces, module boundaries, data flow, configuration, CLI, dependencies, or external integrations, must update the relevant documentation within the current branch scope. A branch with missing documentation must not be merged.

- When changing module code, update the implemented features and public API in the corresponding `docs/modules/<module>.md`.
- Add a concise entry to the current version block in `docs/changelog.md` for every PR; add a new version heading and date for releases.
- For cross-module wiring, module, adapter, dependency block, or data-flow changes, update the system diagrams in `docs/architecture.md` and `docs/spec.md`, plus the architecture diagrams in `README.md`/`README_EN.md`.
- Update `docs/modules/cli.md` for CLI command changes and `docs/modules/config.md` for configuration field changes.
- As applicable, update `docs/index.md`, README files, Chinese and English positioning/installation docs, `scripts/install.sh`, `docs/agent-install.md`, `docs/agent-deployment.md`, `docs/docker-deployment.md`, or the corresponding extension/release docs.
- On release, keep the Chinese and English README version highlights synchronized, with at most four concise user-facing bullets; put full details only in `docs/changelog.md`.

## Commits and Security

Use Conventional Commits, for example `feat: add bilibili auth status command` or `fix: validate missing api key`. PR descriptions include a change summary, test commands and results, and related tasks or documentation. Include terminal output or screenshots for CLI output or extension-page changes.

Never commit a real `config.toml`, Cookie, API key, vault secret, session token, or other local sensitive data; local configuration references only opaque credential refs.
