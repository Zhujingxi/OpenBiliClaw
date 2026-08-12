---
name: writing-specs
description: Use when authoring a spec or implementation plan, or planning multi-task work.
---

# Write Specs and Plans

Produce an authoritative design spec and an executable implementation plan before changing
code. Resolve every placeholder against the live repository.

## Keep planning artifacts out of the maintained docs tree

Draft a spec and implementation plan in the issue/PR or an ignored local workspace. The repository `docs/` tree contains current-state product documentation only; do not recreate a historical plans archive. Once implementation lands, transfer durable decisions into `docs/architecture.md`, `docs/spec.md`, and the affected `docs/modules/` pages.

## SPEC skeleton

```markdown
# <Feature> Spec — <measurable outcome>

**Created:** YYYY-MM-DD
**Scope:** <affected behavior, modules, and integrations>
**Out of scope:** <explicit non-goals>

## Goal

State the current cost or failure, quantified target outcomes, and the exact commands that
verify them.

## Design invariants (MUST hold in every phase)

1. **<Invariant name>:** <falsifiable rule and verification surface>.
2. **<Invariant name>:** <falsifiable rule and verification surface>.

## Current diagnosis

### D1. <Observed problem>

Record evidence at `path/to/file.py:line`; distinguish confirmed facts from hypotheses.

### D2. <Observed problem>

Record the caller, data flow, failure mode, and existing test coverage with `file:line` evidence.

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | <gate or prerequisite> | **MUST** | <risk it controls> |
| 1 | <main change> | RECOMMENDED | <measured leverage> |

List dependencies, then group work into **Wave A**, **Wave B**, and later waves by risk and
dependency. State what can ship independently and where work may safely stop.

## Phase designs

### Phase 0 — <name>

Define interfaces, algorithms, error behavior, tests, rollout, and numeric acceptance gates.

## Expected impact

| Lever | Measured effect |
| --- | --- |
| <phase> | <quantified result> |

## Documentation obligations

List every module doc, changelog, architecture diagram, CLI/config reference, installer doc,
and README surface triggered by the design.
```

Every invariant must be testable, every diagnosis must cite live code, and every claimed
improvement must have a reproduction command.

## PLAN skeleton

```markdown
# <Feature> — Implementation Plan

> **Spec:** <issue/PR/local draft reference>
> **Status:** <revision and review state>
> **Execution order:** <dependency-ordered tasks and Wave grouping>
> **Tech:** <runtime, interpreter, focused tests, lint, format, and type-check commands>

**Invariants that MUST hold — re-read before each task:**

- <Restate the spec's invariant without weakening it.>
- <Restate the spec's invariant without weakening it.>

### Task N: <single deliverable>

**Files:** Add/modify/test exact paths.

**Interfaces:** Consumes: <inputs/dependencies>. Produces: <outputs/contracts>.

**Steps:**

- [ ] Write one focused failing test for <behavior>.
- [ ] Run `<focused test command>` and confirm FAIL for the intended missing behavior.
- [ ] Add the minimal implementation needed for that test.
- [ ] Rerun `<focused test command>` and confirm PASS with no warnings.
- [ ] Run the touched regression tests, lint/format checks, and MyPy command.

**Acceptance:**

- Numeric gate: <metric, comparator, threshold, sample size, and failure meaning>.
- Reproduce with `<exact command>`; record the result in the PR.

## Verification after merge

State the production/shadow/canary observation, commands, owner, duration, and rollback trigger.

## Explicitly out of scope

- <non-goal>
```

Keep the checkbox steps intact. Restate every spec invariant at the plan top and require the executor to re-read them before each task.

## Quantified-gate example

For a model-visible profile reduction, a concrete gate is: **admission flip rate ≤ 3% and
Spearman rank correlation ≥ 0.95 on at least 100 aligned candidates**, verified by
`scripts/run_profile_diet_ab.py`. The command, dataset provenance, baseline commit, and observed
values belong in the spec and the task acceptance block—not only in a PR comment.

Use the skeleton above for quantified invariants, D1..Dn diagnosis, risk waves, phase designs, impact, and documentation obligations.
