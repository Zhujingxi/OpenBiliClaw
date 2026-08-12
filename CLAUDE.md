# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenBiliClaw is a local-first typed content discovery and recommendation application. Current architecture is authoritative in `docs/architecture.md`; Runtime Composition is the sole concrete production graph.

## Build & Development Commands

```bash
ruff format src/ tests/ scripts/
ruff check src/ tests/ scripts/
mypy src/
ALLOW_MODEL_REQUESTS=False pytest --cov=openbiliclaw
npm --prefix frontend run format:check
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
openbiliclaw check
openbiliclaw serve
```

## Documentation Requirements

**Every commit/merge to main, and every release, must keep docs and architecture diagrams in sync with the code.** Not optional. Branches without doc updates should not merge. Scope is not limited to "todolist tasks" — any change that touches interfaces, module boundaries, data flow, config, CLI, dependencies, or external integrations triggers this rule.

Mandatory updates (apply whichever match the PR's scope):

1. `docs/modules/<module>.md` — update "implemented features" table and "public API" section for any module whose code changed
2. `docs/changelog.md` — every release adds a top entry (`## vX.Y.Z: theme (YYYY-MM-DD)`); every PR also adds a short bullet under the current version block
3. **Architecture diagrams** — when a PR changes cross-module wiring, adds modules / adapters, alters data flow, or introduces a new dependency block (e.g. embedding service, xhs path):
   - `docs/architecture.md` (text layers + module roles)
   - `docs/spec.md` §3 system architecture ASCII diagram
   - `README.md` and `README_EN.md` top-of-page architecture diagrams
   The architecture diagram is not decorative — it MUST reflect what's on main.
4. `docs/modules/cli.md` — when CLI commands are added / removed / renamed
5. `docs/modules/config.md` — when `config.toml` fields are added / renamed / removed

Update on demand based on PR type:

6. `docs/index.md` — new module docs, module-status changes, highlighted docs
7. `README.md` / `README_EN.md` — positioning changes, tagline changes, core feature list changes, install flow changes, version releases
8. GitHub About (`gh repo edit --description`) — when project positioning shifts
9. `scripts/install.sh` post-install summary, `docs/agent-install.md`, `docs/docker-deployment.md` — installer flow / dependencies / opt-in steps changing
10. `README.md` / `README_EN.md` 📌 vX.Y.Z highlights callout — keep it a **teaser, not a mini-changelog**. Hard rules:
    - **At most 4 bullets**, each one tight sentence (~60 字 / ~40 words max).
    - Surface only the release's biggest **user-facing** wins: new platform, behaviour change, perf jump, breaking config. Skip internal smokes, test coverage, refactor, default-value tweaks, observability-only changes — those live only in `docs/changelog.md`.
    - When releasing, **replace** the previous version's callout entirely; never stack two version headers, and never append the new version's bullets onto the old list.
    - Both `README.md` (中文) and `README_EN.md` (英文) callouts must stay in sync — same bullet count, same items, same order.
    - The bullet ends with a one-liner "完整变更详见 [docs/changelog.md](docs/changelog.md)。" (CN) / "Full changelog: [docs/changelog.md](docs/changelog.md)." (EN). The full detail is *always* in changelog, never in README.

Pre-merge checklist:

- [ ] `docs/modules/<modules touched>.md` updated
- [ ] `docs/changelog.md` has a new entry
- [ ] Architecture changed → `docs/architecture.md` + `docs/spec.md` diagram + README diagrams synced
- [ ] CLI / config changed → corresponding module doc synced
- [ ] Installer flow changed → `install.sh` output + agent-install.md + docker-deployment.md synced
- [ ] Positioning / tagline changed → README CN/EN + GitHub About synced
- [ ] New release → README CN/EN 📌 highlights callout **replaced** (not appended), ≤4 bullets, ≤1 sentence each, CN/EN in sync, no internal smokes/test coverage entries

## Development Order

Follow `docs/v0.1-todolist.md` roadmap: Connect -> Understand -> Discover -> Recommend -> Learn -> Extension -> Stable Delivery. Do not skip lower layers to build upper-layer features.
