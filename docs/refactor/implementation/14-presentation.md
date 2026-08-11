# Module Plan 14: Presentation Contract and Host Shells

## Outcome

Replace handwritten web/popup JavaScript with one TypeScript workspace using Vue 3, Pinia, and Vite. Desktop, mobile, and extension shells consume shared API and presentation contracts while retaining host-specific navigation and responsive behavior.

## Target workspace

```text
frontend/
├── package.json                 # npm workspaces and shared scripts
├── tsconfig.base.json
├── packages/
│   ├── api-client/
│   │   ├── generated/           # generated OpenAPI types
│   │   └── src/                 # typed fetch/stream client
│   └── presentation/
│       └── src/
│           ├── cards/
│           ├── descriptors/
│           ├── actions/
│           └── components/
└── apps/
    ├── web/
    │   └── src/
    │       ├── app/
    │       ├── stores/
    │       ├── views/
    │       └── components/
    └── extension/
        └── src/
            ├── popup/
            ├── background/
            ├── content/
            └── shared/
```

Use npm workspaces, not a heavier monorepo framework. Use Vite for web and extension multi-entry builds. Executable release, signing, upload, and packaging utilities are Python; Vite/workspace configuration remains TypeScript, avoiding a separate TypeScript script runner.

## TypeScript policy

- `strict: true`, `allowJs: false`, `checkJs: false`.
- Enable `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitOverride`, and `useUnknownInCatchVariables`.
- Use `vue-tsc --noEmit`, ESLint with TypeScript/Vue rules, and Prettier.
- No `any`, non-null assertion without local proof, unchecked JSON parsing, or manually duplicated backend schemas.
- Generated JavaScript exists only in ignored build directories.

## State ownership

Use small Pinia stores by durable UI concern:

- session/auth status
- provider connections
- recommendations/feed
- profile projection/edit state
- Assistant conversations
- runtime/job status
- host-local UI preferences

Do not create a universal store, mirror the whole backend, or put transient component state in Pinia.

## Internal phases

### Phase 1 — Workspace and contracts

- Create npm workspaces, strict shared TS config, Vite, Vue, Pinia, Vitest, Vue Test Utils, ESLint, and formatting scripts.
- Generate API types from deterministic FastAPI OpenAPI.
- Implement one typed HTTP/stream client with runtime handling for network/unknown payload failures.
- Add a CI gate banning checked-in `.js`, `.mjs`, and `.cjs` source/test files.

### Phase 2 — Presentation contract

- Define TypeScript representations of provider views, card descriptors, generic `CardData`, actions, pagination, and availability.
- Build generic video, image, article, discussion, and fallback cards.
- Keep the shell-owned outer frame, feedback controls, accessibility semantics, and responsive behavior consistent.
- Treat URLs and media as untrusted; sanitize protocols and render text through Vue escaping.
- Reject arbitrary provider HTML, CSS, component names, or executable code.

### Phase 3 — Shared API and Pinia services

- Implement stores that call the typed API client and expose explicit loading/success/empty/error states.
- Use `AbortController` for view/request cancellation.
- Implement reconnecting event streams with bounded backoff and one owner per connection.
- Keep server state authoritative; optimistic updates are allowed only where rollback behavior is defined.
- Test store behavior without mounting complete applications.

### Phase 4 — Web shell

- Build Vue routes/views for recommendations, provider tabs, search/content detail, profile, Assistant, source connection, settings, and runtime health.
- Use one responsive web app with desktop/mobile layout components rather than two duplicated applications where behavior is shared.
- Preserve distinct desktop/mobile navigation and density through host layout components.
- Implement keyboard navigation, focus management, reduced motion, contrast, labels, and screen-reader announcements.

### Phase 5 — Extension shell

- Move existing TypeScript background/content code into the workspace after auditing whether it belongs to target Presentation or future Observation/Access scope.
- Rewrite popup/sidebar UI in Vue + Pinia.
- Keep extension device/backend connection and host presentation behavior that remains in target scope.
- Remove browser-session provider execution, cookie extraction, and task dispatch that the architecture excludes.
- Define extension messages as discriminated TypeScript unions with runtime validation at browser-message boundaries.

### Phase 6 — Provider renderers and fallbacks

- Register trusted first-party renderers by provider/content kind at build time.
- Require every provider content type to render through generic fallback data.
- Test missing renderer, unknown card version, missing media, long text, deleted content, and provider unavailability.
- Verify unified feed visual consistency across provider variants.

### Phase 7 — JavaScript removal and packaging

- Delete all current files under `src/openbiliclaw/web/**/*.js`, `extension/popup/**/*.js`, extension `.mjs` scripts, and JavaScript tests.
- Rewrite required tests as `.test.ts`; rewrite executable release, signing, upload, and packaging scripts in Python. Keep only declarative Vite/workspace build configuration in TypeScript.
- Build web assets into an ignored artifact copied into the Python package during packaging.
- Build extension artifacts separately; do not commit generated JavaScript.
- Remove old static HTML fragments/styles that are no longer referenced.

## Tests and quality gates

- Vitest component/store tests for all states and actions.
- Accessibility assertions for shared components and keyboard paths.
- Browser E2E smoke tests for desktop, mobile viewport, and extension popup using built artifacts.
- Contract tests against generated API schemas and provider card fixtures.
- `vue-tsc`, ESLint, formatting, tests, and production builds must pass.
- Repository scan proves no handwritten JavaScript remains.

## Documentation updates during implementation

Update frontend/extension module docs, build and install docs, API contract docs, screenshots where required, architecture diagrams, README/README_EN, and `docs/changelog.md`.

## Completion criteria

- Web and extension presentation source is entirely TypeScript/Vue.
- Vue 3 and Pinia own application UI/state; no global mutable script state remains.
- Desktop/mobile/extension consume shared contracts and generic fallbacks.
- No arbitrary backend-supplied executable presentation is possible.
- All legacy JavaScript source and tests are deleted.
