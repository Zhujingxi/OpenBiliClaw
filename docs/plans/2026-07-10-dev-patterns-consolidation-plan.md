# Dev-Patterns Consolidation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (fall back to plain
> task-by-task execution if the skill is unavailable in the executing environment).
> **Spec:** [`2026-07-10-dev-patterns-consolidation-spec.md`](./2026-07-10-dev-patterns-consolidation-spec.md)
> **Status:** r3 — 2026-07-10, revised after adversarial review rounds 1-2 (fail-closed
> bump incl. version-string validation, `--root` injection, `.gitignore` unignore for
> `.claude/skills/`, README version headers, superpowers handoff preserved, AGENTS.md keeps
> self-contained enforcement, reproducible gates).
> **Execution order (from Spec):** Task 0 → 1 → 2 → 4 → 3 → 5. Task 1 depends on Task 0's
> script existing; Task 4 depends on Task 2's canonical text existing. No task changes
> product runtime behavior; Tasks 1-4 change agent-visible docs and carry explicit
> compatibility checks instead of quality gates.
> **Tech:** Python 3.11+ stdlib-only for the script, pytest (`asyncio_mode=auto`), Ruff,
> MyPy strict, 100-char lines. Interpreter is `.venv/bin/python` (plain `python`/`python3`
> has no deps). Run per task: `.venv/bin/python -m pytest <touched tests> -q`, then
> `.venv/bin/python -m ruff check` / `ruff format --check` on touched files, then
> `.venv/bin/python -m mypy scripts/release.py` for Task 0 (scripts/ may be outside the
> mypy default scope — run it explicitly on the new file).

**Invariants that MUST hold (from Spec — re-read before each task):**
- CLAUDE.md pitfall section ≤ 70 lines; every rule cites ≥ 1 resolvable commit hash; no
  contradiction with the existing prompt-cache / documentation-requirements sections.
- The documentation-discipline **checklist** exists in full only in CLAUDE.md; AGENTS.md
  keeps a self-contained obligation statement + link, never a checklist copy.
- `scripts/release.py` default mode is read-only (proven by before/after file hashes in a
  CLI-level test); mutation only via explicit `--bump`/`--extension`; mutation is
  fail-closed (pre-validate all targets, zero writes on any ambiguity); the script never
  runs git commit/tag/push.
- The release skill instructs one tag per `git push` (4-tag silent-failure incident).
- "Never hand-edit" applies to mechanical version fields only; changelog heading and README
  callout content are deliberate hand edits that `--check` verifies afterwards.
- No file under `src/openbiliclaw/` or `extension/` changes; the full existing test suite
  passes untouched.
- The version-file list is defined exactly once (`VERSION_FILES` in the script); the skill
  and docs reference the script, never restate the list.
- The authoring skill preserves the superpowers/GSD execution conventions (REQUIRED
  SUB-SKILL handoff line, checkbox steps).

---

### Task 0: `scripts/release.py` — version consistency check + bump

**Files:** Add `scripts/release.py`;
Test `tests/test_release_script.py`

**Interfaces:** Consumes: repo files listed in `VERSION_FILES`; `uv` binary if present.
Produces: CLI exit codes (0 consistent / 1 inconsistent), per-group report, in-place version
rewrites under `--bump`/`--extension`.

**Steps:**
- [ ] 1. Failing tests first (`tests/test_release_script.py`, fixture trees under
  `tmp_path` — no git, no network; the `uv lock` runner is injected/monkeypatched):
   - Per-kind parsers extract the version from **real-format** fixtures: TOML
     (`version = "0.3.161"`), Python (`__version__`), JSON `version`, package-lock **root
     `version` + `packages[""].version` only** (fixture includes dependency entries with
     their own `version` fields that must be ignored — mutating a dependency version does
     NOT trip `--check`; mutating either project field DOES), `.iss` comment-example
     versions at both occurrences (warn-only kind: reported, bumped, never a failure),
     HTML `softwareVersion` JSON-LD field, `uv.lock` `name = "openbiliclaw"` block version,
     README CN/EN 📌 version headers (`📌 最新版本：**vX.Y.Z` / `📌 Latest: **vX.Y.Z`).
   - `check_versions(root)`: consistent fixture tree → exit-code-0 result; mutate one
     enforced file → exit-code-1 result naming exactly that file; backend vs extension
     groups are independent (differing across groups is not a failure; differing within a
     group is).
   - Missing changelog heading `## v<backend-version>` → warning present, exit code
     unaffected.
   - `bump_versions(root, backend="0.4.0")` rewrites all backend-group entries (targeted
     substitution; JSON formatting preserved — assert the byte neighborhood around the
     version value is unchanged), leaves the extension group alone unless
     `extension="X.Y.Z"` is given, calls the injected `uv lock` runner exactly once **with
     `cwd=root`**, and a subsequent `check_versions` passes.
   - **Fail-closed:** with one target file missing (or a file whose expected pattern
     matches zero or multiple-unexpected times), `bump_versions` raises before writing —
     sha256 of every fixture file identical before/after the failed call.
   - **Malformed version fail-closed:** `bump_versions(root, backend="nope")` (and CLI
     `main(["--bump", "nope", "--root", ...])`) rejects anything not matching
     `^\d+\.\d+\.\d+$` before touching any file — sha256 of every fixture file identical
     before/after; CLI exits non-zero with a clear message.
   - **CLI read-only proof:** run the CLI entry `main(["--check", "--root", str(tmp_path)])`
     against a fixture root — sha256 of every file identical before/after; exit code 0 on
     consistent, 1 on inconsistent fixtures.
   - Missing `uv` binary (runner raises `FileNotFoundError`) **or** failing `uv lock`
     (runner raises `CalledProcessError`) → bump still rewrites text files, report flags
     `uv.lock` as "manual re-lock required" including the failure reason, exit code 1
     (tree not yet consistent).
- [ ] 2. Implement `scripts/release.py`: stdlib-only, full type annotations,
  `VERSION_FILES` as a module constant of `(relative_path, kind, group, policy)` tuples;
  pure functions `check_versions` / `bump_versions` taking `root: Path` (+ injectable
  `uv_lock_runner`); `main()` with argparse (`--check` default, `--bump X.Y.Z`,
  `--extension X.Y.Z` — either mutation flag alone or both — and `--root PATH` defaulting
  to `Path(__file__).resolve().parent.parent`, which is both the production default and the
  test injection point). Regexes anchored tightly enough to touch only version fields
  (test-proven in step 1); version-string validation and target pre-validation pass before
  any write.
- [ ] 3. `.venv/bin/python -m pytest tests/test_release_script.py -q`; ruff check + format
  on both files; `.venv/bin/python -m mypy scripts/release.py`.
- [ ] 4. **Acceptance:** `.venv/bin/python scripts/release.py --check` against the real
  repo — record the output in the PR. Expected exit 0 on a settled tree. (Concurrent
  release sessions can transiently hold a mid-bump tree; record what is observed, never
  "fix" another session's in-flight state.)

### Task 1: Release skill

**Files:** Add `.claude/skills/release/SKILL.md`; Modify `.gitignore`

**Interfaces:** Consumes: `scripts/release.py` (Task 0), CLAUDE.md README-callout rule.
Produces: on-demand runbook for release sessions.

**Steps:**
- [ ] 1. Write the skill: YAML frontmatter fenced by `---` lines containing `name: release`
  and a `description:` that states triggers (releasing a version, bumping versions, pushing
  release tags). Body = the six-step runbook from the spec: pre-flight (clean tree, main,
  pull, full tests) → version bump **only** via `scripts/release.py --bump`
  (+ `--extension` when the extension changed; never hand-edit mechanical version fields)
  → changelog top entry (hand edit, verified by `--check` warning) → README 📌 callout
  (hand edit; restate only the three hard limits: ≤ 4 bullets / replace-not-append / CN-EN
  lockstep; link CLAUDE.md rule 10 for the rest) → `chore: release X.Y.Z` commit → tags
  pushed **one per `git push`**, 4-tag incident stated as reason → post-push verification:
  `gh run list --limit 10` and confirm one run per pushed tag's workflow, GHCR
  visibility note.
- [ ] 2. `.gitignore`: replace the blanket `.claude/` line (currently `:72`) with
  `.claude/*` + `!.claude/skills/` so project skills are trackable while session-local
  files stay ignored. Verify:
  `git check-ignore .claude/skills/release/SKILL.md; echo $?` → 1 (not ignored);
  `git check-ignore .claude/settings.local.json; echo $?` → 0 (still ignored).
- [ ] 3. Verify (exact commands):
  `head -1 .claude/skills/release/SKILL.md` is `---`;
  `grep -c '^name: release' .claude/skills/release/SKILL.md` → 1;
  `grep -c '^description:' .claude/skills/release/SKILL.md` → 1;
  `grep -c 'scripts/release.py' .claude/skills/release/SKILL.md` ≥ 2;
  `grep -ci 'one tag per' .claude/skills/release/SKILL.md` ≥ 1;
  `grep -c 'pyproject' .claude/skills/release/SKILL.md` → 0 (no restated file list —
  invariant: the script is the single source).

### Task 2: CLAUDE.md pitfall rules

**Files:** Modify `CLAUDE.md`

**Steps:**
- [ ] 1. Insert section `## Hard-Won Pitfall Rules` after "Code Conventions": intro line,
  the seven rules from the spec (bold imperative + one rationale line with commit/issue
  citation each), the three-question meta-rule as a closing blockquote.
- [ ] 2. Verify every cited hash resolves (concrete, non-vacuous):
  `hashes=$(awk '/^## Hard-Won Pitfall Rules/{f=1;next} f&&/^## /{exit} f' CLAUDE.md | grep -oE '\b[0-9a-f]{8}\b')` —
  assert `echo "$hashes" | wc -w` ≥ 7 (at least one citation per rule), then
  `for h in $hashes; do git cat-file -t $h; done` — every line outputs `commit`.
- [ ] 3. Verify size:
  `awk '/^## Hard-Won Pitfall Rules/{f=1;next} f&&/^## /{exit} f' CLAUDE.md | wc -l` ≤ 70.
- [ ] 4. Compatibility check (agent-visible change, spec invariant 6): the new section
  cross-references but does not restate or contradict "LLM Prompt-Cache Convention" and
  "Documentation Requirements" — manual read of both sections side by side, noted in the
  PR description.

### Task 3: Spec/plan authoring skill

**Files:** Add `.claude/skills/writing-specs/SKILL.md`

**Steps:**
- [ ] 1. Write the skill (frontmatter: `name: writing-specs`, `description` with triggers:
  authoring a spec/plan, planning multi-task work). Body:
   - Naming + docs-first convention: `docs/plans/YYYY-MM-DD-<slug>-spec.md` / `-plan.md`,
     committed as `docs: add <feature> spec and plan` **before** implementation.
   - SPEC skeleton: header (Created/Scope/Out of scope) → Goal with quantified targets +
     verification commands → Design invariants (numbered, MUST hold) → Current diagnosis
     (D1..Dn with `file:line`) → Priority classification table + Wave grouping → Phase
     designs → Expected impact table → Documentation obligations.
   - PLAN skeleton: blockquote header **starting with the
     `> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans` handoff line**,
     then spec link / status / execution order / tech commands → invariants restated with
     "re-read before each task" → `### Task N` blocks with Files / Interfaces / **checkbox
     TDD steps** (failing test → confirm FAIL → minimal implementation → confirm PASS →
     lint/mypy) / Acceptance with numeric gates + reproduction commands → Verification
     after merge → Explicitly out of scope.
   - One worked micro-example of a quantified gate ("flip rate ≤ 3%, Spearman ≥ 0.95,
     verified by `scripts/run_profile_diet_ab.py`") and a pointer to the llm-token-diet
     spec as reference.
- [ ] 2. Verify (exact commands): `head -1` is `---`; `grep -c '^name: writing-specs'` → 1;
  `grep -c 'REQUIRED SUB-SKILL: superpowers:executing-plans'` ≥ 1;
  `grep -c -- '- \[ \]'` ≥ 1 (checkbox convention present);
  `grep -c '^\`\`\`' .claude/skills/writing-specs/SKILL.md` is even (fenced blocks closed).

### Task 4: AGENTS.md de-duplication

**Files:** Modify `AGENTS.md`

**Steps:**
- [ ] 1. Replace the "文档更新要求（强制）" checklist body (`AGENTS.md:31-59`) with a short
  self-contained enforcement paragraph: keep the 强制 framing and the section heading;
  state in one sentence what triggers the obligation and the five target areas (module
  docs / changelog / architecture diagrams / CLI+config docs / installer docs); link to
  CLAUDE.md "Documentation Requirements" for the authoritative checklist; note that
  AGENTS.md readers must follow it even if they never load CLAUDE.md automatically. All
  other AGENTS.md sections untouched.
- [ ] 2. Verify (exact commands): `grep -c '强制' AGENTS.md` ≥ 1;
  `grep -c 'Documentation Requirements' AGENTS.md` ≥ 1; and three distinctive checklist
  literals currently present in the removed span (verified against AGENTS.md as of r3)
  return 0 after the edit:
  `grep -cF '架构图不是装饰' AGENTS.md` → 0,
  `grep -cF 'gh repo edit --description' AGENTS.md` → 0,
  `grep -cF '已实现功能' AGENTS.md` → 0 (both current occurrences, `:35` and `:49`, sit
  inside the replaced span).

### Task 5: Docs sync

**Files:** Modify `docs/changelog.md`, `docs/contributing.md`

**Steps:**
- [ ] 1. `docs/changelog.md`: one bullet under the current version block:
  `chore(dev): scripts/release.py 版本一致性检查/升版工具 + release/writing-specs 项目技能 +
  CLAUDE.md 防坑规则（自提交史提炼）`.
- [ ] 2. `docs/contributing.md` skills section (`:71-75`): add one line distinguishing
  repository `skills/` (OpenClaw adapter skills) from `.claude/skills/` (Claude Code
  project skills: release runbook, spec authoring).
- [ ] 3. Full gate once at the end: `.venv/bin/python -m pytest -q` (entire suite — proves
  the no-runtime-change invariant), `ruff check` on touched Python files,
  `.venv/bin/python -m mypy scripts/release.py`.

---

## Verification after merge

1. Next release is executed through the release skill; confirm zero hand-edited mechanical
   version fields (`git show --stat` of the release commit matches `scripts/release.py
   --bump` output plus the two content edits) and tags pushed individually with all
   workflows triggering.
2. `scripts/release.py --check` added to the contributor habit loop (candidate for CI in a
   follow-up — explicitly out of scope here).
3. Watch the fix-commit ratio over the following month
   (`git log --pretty=%s --since=<date> | grep -cE '^fix'` over total) as a soft signal for
   the pitfall rules' effect.

## Explicitly out of scope

- CI enforcement of `release.py --check` (follow-up candidate).
- Any change under `src/openbiliclaw/` or `extension/`.
- Rewriting historical `docs/plans/` documents to the new template.
- Modifying gsd-*/superpowers skill definitions.
