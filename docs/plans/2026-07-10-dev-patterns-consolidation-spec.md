# Dev-Patterns Consolidation Spec — encode 1840 commits of lessons into rules, skills, and tooling

**Created:** 2026-07-10 (r3 — post adversarial review rounds 1-2)
**Scope:** `CLAUDE.md` (new pitfall-rules section), `AGENTS.md` (de-duplication),
new `.claude/skills/` (release, spec/plan authoring), `.gitignore` (unignore
`.claude/skills/` — the current `.gitignore:72` ignores the whole `.claude/` tree, which
would silently exclude the new skills from version control), new `scripts/release.py` +
tests, `docs/changelog.md`, one clarifying line in `docs/contributing.md`.
**Out of scope:** any product runtime code path (`src/openbiliclaw/` behavior), CI release
automation, modifications to third-party gsd-*/superpowers skills, retroactive rewriting of
existing `docs/plans/` documents.

## Goal

A git-history audit shows **fix commits at ~35% of all commits** and still ~34% over the last
month (queries in the Audit-provenance note below). The dominant fix families are *repeats of
the same root causes* — proxy inheritance, poisoned caches, uncalibrated thresholds, silent
failures, multi-surface drift, multi-install-mode drift — none of which are encoded anywhere
an agent or contributor reads. Separately, every release hand-edits **11 files** (repair
commits like `b42c584a` "complete 0.3.158 version sync" prove the failure mode; a stale
`uv.lock` in a tag once permanently blocked auto-update on installed machines), and the
mature third-generation spec/plan workflow exists only by example, not as a template. Target
outcomes:

- The seven recurring bug families become **short imperative rules in `CLAUDE.md`**, each
  traceable to at least one historical commit, so every future session carries them in
  context.
- Version consistency becomes **mechanically checkable and mechanically fixable**
  (`scripts/release.py --check` / `--bump`), and the full release runbook (version bump →
  changelog → README callout discipline → per-tag push) becomes a **release skill**.
- The third-gen spec/plan structure (invariants dual-write, quantified acceptance gates,
  Wave grouping, file:line diagnosis, TDD task scaffold, superpowers execution handoff)
  becomes a **spec/plan authoring skill** so future plans start from the proven skeleton.
- `AGENTS.md` stops duplicating `CLAUDE.md`'s documentation checklist while **retaining a
  self-contained statement of the obligation** (AGENTS.md serves non-Claude agents that may
  never load CLAUDE.md): one canonical checklist, one enforcing pointer.

Verification metric: `scripts/release.py --check` exits 0 on a consistent tree and exits 1
naming every offending file on any single-file version mutation; `pytest
tests/test_release_script.py` green; every CLAUDE.md rule cites a commit hash resolvable by
`git cat-file -t <hash>`.

**Audit provenance (as of 2026-07-10, HEAD `92843113` at audit time; the tree moves fast —
re-run, don't trust the absolute numbers):** total commits `git log --oneline | wc -l` →
1840; type distribution `git log --pretty=%s | sed -E 's/^([a-z]+)(\(.*\))?:.*/\1/' | sort |
uniq -c` → fix 641 / feat 535 / docs 304; recent ratio `git log --pretty=%s
--since=2026-06-01 | grep -cE '^fix'` → 155 of 457 ≈ 34%; version-sync repair commits found
by `git log --pretty=%s | grep -iE 'version sync|re-lock|resync'` (3 direct matches incl.
`b42c584a`, plus the adjacent uv.lock/auto-update incident chain and stale-README fixes that
the query's wording misses).

## Design invariants (MUST hold in every phase)

1. **Context budget:** the new `CLAUDE.md` pitfall section is ≤ 70 lines total. CLAUDE.md is
   loaded into every session; rules must be imperative one-liners with a one-line rationale,
   not essays. Anything longer belongs in a skill body.
2. **Traceability:** every pitfall rule cites ≥ 1 real commit hash (or issue number) as
   evidence. A rule that cannot point at a historical cost does not go in.
3. **Single source of truth for meta-docs:** after this work, the documentation-discipline
   **checklist** exists in full only in `CLAUDE.md`; `AGENTS.md` keeps a short
   self-contained statement of the obligation (mandatory, what triggers it, the five target
   areas in one sentence) plus a link — but never a second copy of the checklist items.
4. **Release tooling is read-only by default:** `scripts/release.py` with no flags (or
   `--check`) never writes — proven by a test that hashes every fixture file before/after a
   CLI `--check` run. Mutation requires an explicit `--bump` / `--extension`. The script
   never runs `git commit`, `git tag`, or `git push`. Mutation is **fail-closed**: every
   target is pre-validated (file exists, expected match count) before the first write; any
   ambiguity aborts with zero files modified.
5. **Tags are pushed one at a time** (skill instruction): pushing > 3 tags in one `git push`
   emits no GitHub events and all release workflows stay silent (incident: 4-tag push, all
   four workflows dead; recovered by deleting remote tags and re-pushing individually).
6. **No product runtime behavior change:** nothing under `src/openbiliclaw/` or `extension/`
   changes; the full existing test suite passes untouched. `CLAUDE.md` and skill files ARE
   agent-visible input, so they are *not* "zero surface": each such change carries an
   explicit compatibility check (no contradiction with the existing prompt-cache convention
   section; the authoring skill preserves the superpowers/GSD execution conventions) instead
   of a quality gate.
7. **Version-file list is defined once in code** — a module-level constant in
   `scripts/release.py`. Operational documents (the release skill, AGENTS.md, module docs)
   reference the script and never restate the list. This spec's own enumeration below is the
   design-time source and is superseded by the code once merged.
8. **Mechanical vs content edits:** "never hand-edit" applies to *mechanical version fields*
   (the `VERSION_FILES` entries). The changelog heading and README 📌 callout are deliberate
   *content* edits made by hand — and `--check` verifies them afterwards (warn on missing
   `## v<version>` changelog heading or stale README version header).

## Current diagnosis

### D1. Seven recurring bug families are institutional knowledge, not rules

Evidence from `git log --pretty=%s` (counts are lower bounds; queries per family recorded in
the audit session):

| Family | ~Commits | Representative | Root cause |
| --- | --- | --- | --- |
| Proxy inheritance | 8 | `df626f3f`, `6f951597`, `620ee9c8` | httpx `trust_env=True` inherits system proxy → CN-site risk control / localhost breakage |
| Poisoned caches | 5+ | "never cache empty vectors" chain | failed provider results (`[]`) written to cache, corrupting downstream scoring |
| Uncalibrated thresholds | 6+ | delight threshold 0.70→0.35→0.65 (`70259c23`, `5b36a8ba`, `804afc9b`) | magic constants with no calibration provenance; model swap invalidates them silently |
| Unvalidated LLM output | 5 | `1eef1cd2` (schema-defying persisted) | LLM dicts merged verbatim; `"unknown"` coerced to 0.0 not field default |
| Multi-surface drift | 20+ | `a03f8f92`, `6a5acd41` ("all three surfaces") | popup / desktop web / mobile web / CLI implemented independently |
| Multi-install-mode drift | 7 | auto-update chain `ec9db8b7`, `721d5a99`, `d6fbb68f` | git / docker / desktop each fixed separately, one at a time |
| Silent failure | 15+ | `88c9aab0`, `bc2dc983`, `fd4b9390` (surface/honest/diagnosable) | errors swallowed into `internal_error` / silent disable; every family above took longer to debug because of this |

### D2. Release is an 11-file hand edit with only partial mechanical guards

`git show --stat 754f2b44` (release 0.3.157): `pyproject.toml`, `uv.lock`,
`src/openbiliclaw/__init__.py`, `extension/manifest.json`, `extension/package.json`,
`extension/package-lock.json`, `packaging/openbiliclaw.iss`, `docs/index.html`, `README.md`,
`README_EN.md`, `docs/changelog.md`.

**Existing partial guard:** `tests/test_release_consistency.py` (uv.lock + pyproject vs
`__version__`) and `tests/test_docs_index.py` (`softwareVersion` vs `__version__`) already
cover three of the eleven at test time. The script does not replace them — it extends
coverage to the rest (extension trio, README CN/EN 📌 version headers) and adds what tests
cannot: a **bump** operation and a standalone pre-commit check. During this audit a
mid-release working tree was observed with `uv.lock` / `docs/index.html` two versions behind
`pyproject.toml` — exactly the hand-edit window the tool closes. Note
`packaging/openbiliclaw.iss` carries the version only in build-command comment examples
(`:4` and `:6`; the real value arrives via `iscc /DMyAppVersion=`), so it is a warn-only
entry: bumped for accuracy, never a `--check` failure. Desktop release versions track the
backend version (release-topology convention: all channel versions stay aligned), so no
separate desktop group is needed — the `.iss` warn-only treatment plus the aligned backend
group covers the desktop channel; `--extension` exists because the extension historically
lagged the backend and may legitimately differ.

### D3. Tag-push and README-callout rules live only in memory

The > 3-tags-per-push GitHub silent-failure and the README 📌 callout discipline (≤ 4 bullets,
replace-not-append, CN/EN lockstep) are known but recorded nowhere a fresh contributor or
agent session would find them at release time.

### D4. `AGENTS.md` duplicates `CLAUDE.md` (~80% of its doc-discipline section)

Two near-identical checklists (`AGENTS.md:31-59` vs CLAUDE.md "Documentation Requirements").
Edits land in one and miss the other — the exact multi-surface drift pattern from D1, applied
to the repo's own meta-docs.

### D5. The third-gen spec/plan workflow is example-only

`docs/plans/` (~270 files) shows a clear three-generation evolution; the third generation
(e.g. the 2026-07-05 llm-token-diet pair, `git show 1754019b^ --stat` era) carries the
high-value practices — numbered design invariants restated at plan top, quantified acceptance
gates with reproduction commands, Wave A/B risk grouping, file:line diagnosis, TDD red-green
task steps, and the superpowers execution handoff
(`> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans`, checkbox steps). No
template or skill captures this; each new plan re-derives it by imitation.

### D6. No project-level `.claude/skills/` directory exists

Project skills are the natural home for the release runbook and the spec/plan template
(loaded on demand, not per-session — invariant 1's counterpart). Note the repo already has a
**different** `skills/` directory (OpenClaw adapter skills, `docs/contributing.md:71-75`);
the two must not be confused — Task 5 adds one clarifying line there.

## Priority classification

| Task | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | `scripts/release.py` version-consistency tool + tests | **MUST** | Converts D2 from a recurring repair-commit generator into a mechanical check; everything else in the release skill leans on it |
| 1 | Release skill (`.claude/skills/release/SKILL.md`) | **MUST** | Encodes D2 + D3 (tag-per-push, README callout rules, uv.lock re-lock) as an executable runbook |
| 2 | CLAUDE.md pitfall-rules section | **MUST** | Encodes D1's seven families; highest leverage against the ~34% fix ratio |
| 3 | Spec/plan authoring skill (`.claude/skills/writing-specs/SKILL.md`) | RECOMMENDED | Codifies D5 including the superpowers handoff; additive |
| 4 | AGENTS.md de-duplication (keep obligation, drop checklist copy) | RECOMMENDED | Fixes D4 without weakening non-Claude enforcement |
| 5 | Docs sync (changelog bullet + contributing.md skills-dir clarification) | **MUST** | CLAUDE.md documentation requirement |

Dependencies: Task 1 references Task 0's script (script must exist first). Task 4 depends on
Task 2 (CLAUDE.md must contain the canonical text before AGENTS.md points at it). Tasks 0/2/3
are mutually independent.

**Recommended implementation order:** Task 0 → 1 → 2 → 4 → 3 → 5. No task changes product
runtime behavior; agent-visible doc changes (Tasks 1-4) carry the compatibility checks from
invariant 6. Ordering is purely dependency-driven.

## Phase designs

### Task 0 — `scripts/release.py`

A stdlib-only (argparse, re, json, pathlib, subprocess, hashlib) script following
`scripts/run_*.py` conventions:

- `VERSION_FILES`: module-level tuple of `(relative_path, kind, group, policy)` entries:
  - backend group, enforced: `pyproject.toml` (`version = "X.Y.Z"`),
    `src/openbiliclaw/__init__.py` (`__version__`), `docs/index.html` (`softwareVersion`
    JSON-LD field), `uv.lock` (the `name = "openbiliclaw"` package block's `version` —
    **checked** always; **fixed** by shelling out to `uv lock` with `cwd=<repo root>` when
    `uv` is on PATH, otherwise reported as requiring manual re-lock),
    `README.md` + `README_EN.md` (the 📌 version header, `📌 最新版本：**vX.Y.Z` /
    `📌 Latest: **vX.Y.Z` — checked so a stale user-facing version fails `--check`; bumped
    textually by `--bump`, with callout *content* remaining a hand edit per invariant 8).
  - backend group, warn-only: `packaging/openbiliclaw.iss` comment-example versions (both
    occurrences, `:4` and `:6`).
  - extension group, enforced: `extension/manifest.json`, `extension/package.json`,
    `extension/package-lock.json` (root `version` **and** `packages[""].version` only —
    never dependency `version` fields).
- `--root PATH` (default: the script's own repository root, i.e.
  `Path(__file__).resolve().parent.parent`): all operations resolve `VERSION_FILES` against
  this root — this is also the injection point that lets CLI-level tests run against fixture
  trees.
- `--check` (default): parse every entry, report per group, exit 0 iff each group is
  internally consistent (backend and extension may differ from each other); exit 1 listing
  every disagreeing file. Warn (not fail) when `docs/changelog.md` lacks a
  `## v<backend-version>` heading.
- `--bump X.Y.Z` (backend group) / `--extension X.Y.Z` (extension group; either flag may be
  given alone or both together): first **strictly validate the requested version strings**
  (`^\d+\.\d+\.\d+$` — reject anything else before touching any file), then **pre-validate
  all targets** (invariant 4 fail-closed semantics), then rewrite with minimal targeted
  substitution (JSON files keep their existing formatting), run `uv lock` if bumping backend
  and `uv` is available, then run the `--check` logic and exit accordingly. If the `uv lock`
  runner fails (`FileNotFoundError` **or** `CalledProcessError`), the already-applied text
  bumps stand (they are individually valid), the report flags `uv.lock` as requiring manual
  re-lock with the failure reason, and the exit code is 1 (tree not yet consistent). Never
  touches git.

Acceptance: unit tests (fixture trees under `tmp_path`, no LLM/network/git; `uv lock` runner
monkeypatched and asserted to receive `cwd=root`) cover: per-kind parsers against
real-format fixtures; group-independence semantics; package-lock mutation detection limited
to the two project fields; both `.iss` occurrences bumped but never failing `--check`;
fail-closed abort (one broken target → zero files modified anywhere); CLI-level `--check`
read-only proof (sha256 of every fixture file identical before/after) and exit codes; bump →
check convergence; missing-`uv` degradation (bump succeeds, uv.lock flagged, exit 1).
On the real repo, `.venv/bin/python scripts/release.py --check` output is recorded in the
PR; expected exit 0 on a settled tree (concurrent release sessions can transiently hold a
mid-bump tree — record whatever is observed, do not "fix" another session's in-flight state).

### Task 1 — Release skill

`.claude/skills/release/SKILL.md` (project skill, standard frontmatter: `name`, `description`
with trigger conditions), plus the `.gitignore` change that makes project skills trackable:
replace the blanket `.claude/` entry with `.claude/*` + `!.claude/skills/` so skills are
versioned while session-local files (`settings.local.json`, worktrees, locks) stay ignored.
Runbook content:

1. Pre-flight: clean worktree, on `main`, `git pull`; run full test suite.
2. Version bump via `scripts/release.py --bump X.Y.Z` (+ `--extension` when the extension
   changed). Explicitly: **never hand-edit mechanical version fields** (invariant 8 —
   changelog heading and README callout content are the deliberate hand edits, and `--check`
   verifies both afterwards).
3. `docs/changelog.md`: new `## vX.Y.Z: theme (YYYY-MM-DD)` top entry.
4. README 📌 callout discipline (per CLAUDE.md rule 10, linked not restated; the skill
   restates only the three hard limits: ≤ 4 bullets / replace-not-append / CN-EN lockstep).
5. Commit `chore: release X.Y.Z`, then tags: `openbiliclaw-v*` aggregate plus per-channel
   tags as applicable (`backend-v*`, `extension-v*`, `desktop-v*`) — **pushed one per
   `git push` invocation**, with the D3 4-tag incident stated as the reason (invariant 5).
6. Post-push: verify each pushed tag's workflow actually triggered —
   `gh run list --limit 10` filtered by the release workflow names — plus the GHCR package
   visibility note (must be set public manually).

### Task 2 — CLAUDE.md pitfall rules

New section "## Hard-Won Pitfall Rules (from the commit history)" placed after "Code
Conventions". Content = seven imperative rules (one per D1 family) + the three-question
meta-rule ("will this failure be swallowed? does this constant/cache survive a provider
swap? did the other surfaces/install modes keep up?"). Format per rule: one bold imperative
line, one rationale line with commit citation. Total ≤ 70 lines (invariant 1). Compatibility
check (invariant 6): the section must not contradict or restate the existing "LLM
Prompt-Cache Convention" and "Documentation Requirements" sections — cross-reference only.
The rules:

1. HTTP clients for CN sites (bilibili/douyin/xhs/zhihu/CN CDN) and local services
   (Ollama/localhost) set `trust_env=False`; proxy use is explicit opt-in config.
2. Never cache empty/failed provider results; validate before every cache write.
3. Every algorithmic threshold documents its calibration provenance in a comment; a
   provider/model swap reopens calibration as a task, never inherits the constant.
4. LLM structured output passes a validation layer before persistence: enum whitelist,
   numeric clamp to field default, placeholder strings treated as missing, coercions logged
   at WARNING. Silent persistence is forbidden.
5. User-visible features are a four-surface contract (extension popup, desktop web, mobile
   web, CLI): confirm all four or state the exclusion in the PR; shared logic sinks to the
   backend.
6. Install/update-adjacent changes are verified per install mode (git, docker, desktop).
7. Failures propagate their real cause; invalid configs are rejected at save time, not
   discovered at runtime. "Diagnosable" beats "appears to work".

### Task 3 — Spec/plan authoring skill

`.claude/skills/writing-specs/SKILL.md` embedding the two skeletons extracted from the
third-gen documents, with the signature practices called out as required sections: Design
invariants (numbered, MUST-hold), Diagnosis with `file:line`, Priority table + Wave
grouping, quantified acceptance gates with reproduction commands, plan-top invariant
restatement + TDD red-green steps per task, **and the existing execution conventions
preserved** (invariant 6): the plan header keeps the
`> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans` handoff line and the
checkbox (`- [ ]`) step convention used by the superpowers/GSD tooling. Includes the naming
convention (`docs/plans/YYYY-MM-DD-<slug>-spec.md` / `-plan.md`) and the docs-first commit
convention (`docs: add <feature> spec and plan` lands before implementation).

### Task 4 — AGENTS.md de-duplication

Replace `AGENTS.md`'s "文档更新要求（强制）" checklist body (`AGENTS.md:31-59`) with a short
**self-contained** enforcement paragraph: the obligation remains mandatory (keep the 强制
framing), state in one sentence what triggers it and the five target areas (module docs /
changelog / architecture diagrams / CLI-config docs / installer docs), and link to CLAUDE.md
"Documentation Requirements" for the authoritative per-item checklist. Rationale recorded in
the paragraph itself: AGENTS.md serves non-Claude agents, so it must carry the rule's
existence and force on its own — only the checklist detail is single-sourced (invariant 3).
Everything else in AGENTS.md stays.

### Task 5 — Docs sync

`docs/changelog.md` bullet under the current version block. Plus one clarifying line in
`docs/contributing.md` (skills section, `:71-75`): repository `skills/` = OpenClaw adapter
skills; `.claude/skills/` = Claude Code project skills (release runbook, spec authoring). No
`docs/modules/*.md` or architecture updates required — no module code or cross-module change.

## Expected impact

| Lever | Effect |
| --- | --- |
| Task 0+1 | "version sync" repair commits → 0; stale-uv.lock auto-update lockout class eliminated; stale README version headers caught; silent 4-tag pushes prevented by runbook |
| Task 2 | The seven families stop recurring silently — every session carries the rules; fix ratio should trend down from ~34% |
| Task 3 | New plans start at third-gen quality (including execution handoff) instead of re-deriving it |
| Task 4 | Meta-doc drift class closed with non-Claude enforcement intact |

## Documentation obligations (per CLAUDE.md)

- `docs/changelog.md` — bullet under current version block (Task 5)
- `docs/contributing.md` — skills-directory disambiguation line (Task 5)
- No module docs / architecture diagrams affected (no product-code or cross-module change)
