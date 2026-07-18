# OpenClaw Deployment Guide Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand the OpenClaw onboarding docs so OpenClaw has a clear Docker-first, local-fallback deployment, initialization, and usage guide.

**Architecture:** Keep the workspace skill as the shortest executable entrypoint, and move the fuller explanation into `docs/openclaw-quickstart.md`. Update the integrations/index/changelog docs so the guide is discoverable from existing module docs.

**Tech Stack:** Markdown documentation, workspace skill instructions, existing OpenBiliClaw CLI and Docker runtime

---

### Task 1: Expand the long-form OpenClaw guide

**Files:**
- Modify: `docs/openclaw-quickstart.md`

**Step 1: Add the failing expectation**

Expected gaps to close:
- Docker-first vs local-fallback is not explicit enough
- OpenClaw deployment/initialization flow is too terse
- Usage section does not walk through the full skill lifecycle

**Step 2: Verify current guide is insufficient**

Run: `rg -n "Docker 优先|本地部署|使用流程|首次初始化后|docker compose up" docs/openclaw-quickstart.md`
Expected: Missing or thin coverage for at least some of these sections

**Step 3: Write the minimal documentation update**

Add:
- deployment decision rule
- Docker deployment steps
- local deployment steps
- initialization flow
- post-init smoke checks
- routine usage flows for OpenClaw

**Step 4: Verify the guide now covers the required sections**

Run: `rg -n "Docker 优先|本地部署|初始化|自检|日常使用" docs/openclaw-quickstart.md`
Expected: Matches for all major sections

### Task 2: Tighten the workspace skill instructions

**Files:**
- Modify: `skills/openbiliclaw-adapter/SKILL.md`

**Step 1: Add the failing expectation**

Expected gaps to close:
- no explicit Docker-first choice
- no deployment decision rule
- no concise daily usage loop after init

**Step 2: Verify current skill is too thin**

Run: `rg -n "Docker|本地|doctor|get-profile|recommend" skills/openbiliclaw-adapter/SKILL.md`
Expected: Missing a deployment decision rule and concise deployment split

**Step 3: Write the minimal skill update**

Add:
- Docker-first/local-fallback decision note
- short bootstrap path for Docker
- short bootstrap path for local
- brief daily command loop

**Step 4: Verify the skill still stays concise but complete**

Run: `rg -n "Docker-first|Docker 优先|本地兜底|日常使用" skills/openbiliclaw-adapter/SKILL.md`
Expected: Matches for the new sections

### Task 3: Update discoverability docs

**Files:**
- Modify: `docs/modules/integrations.md`
- Modify: `docs/index.md`
- Modify: `docs/changelog.md`

**Step 1: Add the failing expectation**

Expected gaps to close:
- integrations doc does not mention Docker-first/local-fallback guide shape
- index should expose the richer guide
- changelog should note the guide expansion

**Step 2: Write the minimal doc updates**

Add short references only; do not duplicate the full guide.

**Step 3: Verify references are present**

Run: `rg -n "openclaw-quickstart|Docker 优先|本地兜底" docs/modules/integrations.md docs/index.md docs/changelog.md`
Expected: Matches in the intended files

### Task 4: Verify referenced commands still work

**Files:**
- Verify only

**Step 1: Run adapter self-check**

Run: `uv run python -m openbiliclaw.integrations.openclaw.cli doctor`
Expected: JSON with `"ok": true` and `skill_pack_exists: true`

**Step 2: Verify the docs point at real files**

Run: `rg -n "docs/openclaw-quickstart.md|skills/openbiliclaw-adapter/SKILL.md" docs/modules/integrations.md docs/index.md docs/changelog.md skills/openbiliclaw-adapter/SKILL.md`
Expected: Matches only to existing files and current guide references
