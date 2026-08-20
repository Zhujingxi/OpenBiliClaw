# Contributing Guide

Thank you for your interest in contributing to OpenBiliClaw!

## Development Setup

```bash
# Clone the project
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw

# Recommended: use uv
uv sync

# Or use pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Code Standards

- Use **ruff** for formatting and linting.
- Use **mypy** for type checking.
- Follow PEP 8 naming conventions.
- Add docstrings to all public APIs.

```bash
ruff format src/ tests/ scripts/
ruff check src/ tests/ scripts/
mypy src/ tests/
```

## Testing

```bash
# Run the offline hermetic suite with branch coverage
ALLOW_MODEL_REQUESTS=False pytest --cov=openbiliclaw --cov-branch --cov-fail-under=90
```

## Commit Convention

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add provider capability
fix: preserve recommendation state transition
docs: update application workflow reference
refactor: simplify composition lifecycle
test: add understanding policy coverage
```

## Browser Extension Development

```bash
npm --prefix frontend ci
npm --prefix frontend run format:check
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
```

## Skill Development

Skills are Markdown files in the form `skills/<skill-name>/SKILL.md`. Keep only skills that match current production capabilities; skills that reference deleted commands, modules, or historical documentation must be deleted or rewritten during the cutover.

The repository's `skills/` directory contains current workspace skills. `.claude/skills/` contains Claude Code project skills, such as the release runbook and writing-specs skill.

A skill file describes that skill's capability boundary and current workflow. See the built-in examples under `skills/`.

## Documentation Update Checklist

After completing a feature and before merging, check whether these documents need updates:

- [ ] `docs/modules/<module>.md` — update "Implemented Features" and "Public API"
- [ ] `docs/changelog.md` — append a change entry
- [ ] `docs/modules/cli.md` — when adding or changing CLI commands
- [ ] `docs/modules/config.md` — when adding configuration fields
- [ ] `docs/architecture.md` — when cross-module interactions change
- [ ] `docs/index.md` — when adding module documentation or changing status

See "Documentation Update Requirements" in [AGENTS.md](../AGENTS.md) for details.

## Acknowledgments

Some features on the main branch originated in community contributions:

- **Multimodal visual recommendation pipeline** — [@wuwafly3](https://github.com/wuwafly3) first contributed the DashScope multimodal embedding provider and cover image-only vectors in [#100](https://github.com/whiteguo233/OpenBiliClaw/pull/100), then implemented user visual profiles (P1), Bilibili danmaku semantics (P2), video keyframes (P3), and the cross-platform visually weighted pipeline in [#135](https://github.com/whiteguo233/OpenBiliClaw/pull/135). Main subsequently hardened its contracts, retries, configuration UI, and real-environment acceptance.
- **Remote extension authentication and optional TLS ingress** — [@RayeLouis](https://github.com/RayeLouis) fixed the extension to treat the server authentication decision as authoritative in [#132](https://github.com/whiteguo233/OpenBiliClaw/pull/132), then implemented the initial opt-in TLS proxy in [#136](https://github.com/whiteguo233/OpenBiliClaw/pull/136). Main subsequently completed security, configuration, Docker, real HTTPS/WebSocket, and extension QR-code hardening.
- **Brand icons across all clients** — [@xiongguixg](https://github.com/xiongguixg) proactively proposed a mobile icon design in [issue #127](https://github.com/whiteguo233/OpenBiliClaw/issues/127). Building on that proposal, v0.3.184 unified brand icons across the browser extension, PWA, desktop and mobile web, website, installers, and system tray.
- **Probe "Ignore for now" state** — [@15515151](https://github.com/15515151) proposed and implemented the neutral/ignored state in [#82](https://github.com/whiteguo233/OpenBiliClaw/pull/82). The main implementation (`83654613`) rewrote it as a cross-session persistent state machine. The PR was not merged directly because the implementation path differed, but both the design and code originated in that contribution.

## Target Frontend and Extension Build

Web and extension source share the strict npm workspace in `frontend/`. Run `npm ci` and the format/lint/typecheck/test/build gates there. Browser release archives are created from generated Vite artifacts with `python scripts/extension_release.py package [--firefox] --no-build`; do not add handwritten `.js`, `.mjs`, or `.cjs` sources.
