# Like Secondary-Interest Query Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed-template inspiration seed generation with coverage-aware like-derived secondary-interest sampling, LLM probe brainstorming, Exa grounding, and platform-specific keyword generation.

**Architecture:** Add focused pure helpers in `discovery/inspiration.py` for secondary-interest selection, coverage snapshots, brainstorm parsing, and quota filtering. Wire `KeywordPlanner._run_inspiration_stage()` through a new brainstorm step before Exa search and pass selected interests, brainstorm branches, grounding, and coverage constraints to the existing curator/generator. Keep the existing keyword pool and `regular` / `explore` split intact.

**Tech Stack:** Python 3.12, SQLite, existing `KeywordPlanner`, existing `LLMService.complete_structured_task()`, pytest, Ruff, MyPy.

---

### Task 1: Pure Secondary-Interest And Brainstorm Helpers

**Files:**
- Modify: `src/openbiliclaw/discovery/inspiration.py`
- Test: `tests/test_discovery_inspiration.py`

- [ ] **Step 1: Write failing tests**

Add tests that:

```python
def test_like_secondary_interest_window_prefers_specific_positive_interests_and_downweights_covered() -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="Switch 独立游戏", category="游戏", weight=0.95, source="like"),
                InterestTag(name="王者荣耀匹配机制", category="游戏", weight=0.9, source="accepted"),
                InterestTag(name="AI 工具实测", category="科技", weight=0.82, source="profile"),
            ],
            disliked_topics=["AI 焦虑贩卖"],
        )
    )
    snapshot = {
        "Switch 独立游戏": {"generated_keyword_count": 30, "admitted_count": 20},
        "王者荣耀匹配机制": {"generated_keyword_count": 0, "admitted_count": 0},
    }

    interests = build_like_secondary_interest_window(profile, coverage_snapshot=snapshot, max_interests=2)

    assert [item.label for item in interests] == ["王者荣耀匹配机制", "AI 工具实测"]
    assert all("AI 焦虑贩卖" not in item.label for item in interests)
```

Add tests for brainstorm parsing:

```python
def test_parse_brainstorm_branches_accepts_schema_and_filters_unknown_interests() -> None:
    selected = [
        SecondaryInterest(interest_id="secondary:switch", label="Switch 独立游戏", parent="游戏", weight=0.9),
    ]
    content = json.dumps({"interest_branches": [{
        "secondary_interest": "Switch 独立游戏",
        "branch_id": "switch-hidden-gems",
        "branch_label": "隐藏佳作",
        "lens_family": "work_entity",
        "probe_queries": ["Switch 独立游戏 冷门佳作", "Nintendo Switch hidden gems indie"],
        "expected_platform_fit": ["bilibili", "reddit"],
        "avoid": ["云推荐"],
    }, {
        "secondary_interest": "未知兴趣",
        "branch_id": "unknown",
        "probe_queries": ["should drop"],
    }]}, ensure_ascii=False)

    branches = parse_brainstorm_branches(content, selected_interests=selected)

    assert len(branches) == 1
    assert branches[0].branch_id == "switch-hidden-gems"
    assert branches[0].probe_queries == ("Switch 独立游戏 冷门佳作", "Nintendo Switch hidden gems indie")
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run --extra dev pytest tests/test_discovery_inspiration.py::test_like_secondary_interest_window_prefers_specific_positive_interests_and_downweights_covered tests/test_discovery_inspiration.py::test_parse_brainstorm_branches_accepts_schema_and_filters_unknown_interests -q
```

Expected: fail because helpers/classes do not exist.

- [ ] **Step 3: Implement helpers**

Add dataclasses `SecondaryInterest`, `BrainstormBranch`, `GroundedProbe`, helper `build_like_secondary_interest_window()`, parser `parse_brainstorm_branches()`, and deterministic fallback branches for sparse LLM output.

- [ ] **Step 4: Verify green**

Run the targeted tests above.

### Task 2: Coverage Snapshot DAO

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_discovery_inspiration.py`

- [ ] **Step 1: Write failing test**

Add a test that inserts keyword rows with `source_interest`, marks some used/yielded, inserts content rows with matching `topic_group`, then calls `get_keyword_interest_coverage_snapshot()` and verifies generated/used/yield/admitted counts.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run --extra dev pytest tests/test_discovery_inspiration.py::test_keyword_interest_coverage_snapshot_counts_keywords_and_admitted_pool -q
```

Expected: fail because the DAO does not exist.

- [ ] **Step 3: Implement DAO**

Add `Database.get_keyword_interest_coverage_snapshot(limit: int = 200) -> dict[str, dict[str, object]]`. Use `discovery_keywords.source_interest` for keyword/yield counts and `content_cache.topic_group` / `pool_topic_label` as best-effort admitted pool coverage. Keep it read-only and tolerant of missing metadata.

- [ ] **Step 4: Verify green**

Run the targeted test.

### Task 3: Planner Brainstorm + Exa Grounding Integration

**Files:**
- Modify: `src/openbiliclaw/runtime/keyword_planner.py`
- Test: `tests/test_keyword_planner.py`

- [ ] **Step 1: Write failing tests**

Add tests that:

1. In inspiration replacement mode, the first LLM call is `discovery.keyword_brainstorm`; Exa is called with brainstormed `probe_queries`, not the old fixed `{interest} 具体案例 机制 方法 争议 深度分析` query.
2. The curator input contains `selected_secondary_interests`, `brainstorm_branches`, `grounding_records`, and `coverage_constraints`.
3. Repeated/covered interests have lower chance by asserting a heavily covered interest is absent from the selected payload while undercovered positive interests remain.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run --extra dev pytest tests/test_keyword_planner.py::test_inspiration_stage_brainstorms_probe_queries_before_exa_search tests/test_keyword_planner.py::test_inspiration_stage_passes_coverage_constraints_to_curator -q
```

Expected: fail because the planner still uses fixed seed queries.

- [ ] **Step 3: Implement integration**

Add `_brainstorm_inspiration_branches()`, `_keyword_interest_coverage_snapshot()`, `_fallback_brainstorm_branches()`, and update `_run_inspiration_stage()` to:

```text
selected secondary interests
-> brainstorm branches
-> Exa search each probe query
-> derive seeds with branch/interest provenance
-> curate/generate platform keywords
```

Keep fixed aspect-window fallback only when brainstorm returns no valid branches.

- [ ] **Step 4: Verify green**

Run the targeted keyword planner tests.

### Task 4: Provenance And Quota Enforcement

**Files:**
- Modify: `src/openbiliclaw/discovery/inspiration.py`
- Modify: `src/openbiliclaw/runtime/keyword_planner.py`
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_discovery_inspiration.py`
- Test: `tests/test_keyword_planner.py`

- [ ] **Step 1: Write failing tests**

Add tests that overrepresented `secondary_interest` or `lens_family` outputs are trimmed before insertion and that inserted keywords persist `source_interest`, `angle_label` / `lens_family`, and `generation_reason`.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run --extra dev pytest tests/test_keyword_planner.py::test_inspiration_stage_enforces_secondary_interest_quota_per_platform -q
```

Expected: fail because all expansions are currently inserted.

- [ ] **Step 3: Implement minimal quota validation**

Add a deterministic filter that caps per-platform realized keywords to at most 2 per `source_interest` and at most 2 per `lens_family` unless there are no alternatives.

- [ ] **Step 4: Verify green**

Run the targeted tests.

### Task 5: Documentation And Verification

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/storage.md`
- Modify: `docs/changelog.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`

- [ ] **Step 1: Update docs from future spec to implemented behavior**

Document that inspiration replacement mode now uses like-derived secondary interests, brainstorm probes, Exa grounding, and coverage snapshot inputs.

- [ ] **Step 2: Run targeted validation**

Run:

```bash
uv run --extra dev pytest tests/test_discovery_inspiration.py tests/test_keyword_planner.py tests/test_llm_module_routing_e2e.py -q
uv run --extra dev ruff check src/openbiliclaw/discovery/inspiration.py src/openbiliclaw/runtime/keyword_planner.py src/openbiliclaw/storage/database.py tests/test_discovery_inspiration.py tests/test_keyword_planner.py
uv run --extra dev mypy src/openbiliclaw/discovery/inspiration.py src/openbiliclaw/runtime/keyword_planner.py src/openbiliclaw/storage/database.py
git diff --check
```

Expected: all pass.

## Self-Review

- Spec coverage: positive secondary interests, negative boundaries, coverage sampling, brainstorm probes, Exa grounding, platform curator inputs, regular/explore split, and feedback counters are covered.
- Placeholder scan: no TBD/TODO implementation gaps are intended in this plan.
- Type consistency: new public helper names are defined in Task 1 and reused by planner in Task 3.
