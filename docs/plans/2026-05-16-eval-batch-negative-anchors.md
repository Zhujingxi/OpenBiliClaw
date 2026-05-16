# Eval Batch Negative Anchors Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Prerequisite:** `2026-05-16-event-satisfaction-signal.md` must be merged first. Without classified negative events the helper has no material to work with and the prompt block stays empty.

**Goal:** Feed the batch content evaluator a short, deterministic list of recent quick-exit / explicit-negative titles so it can pattern-match candidates against the user's actual rejection history and downscore look-alikes — even before the preference layer has been re-cleaned.

**Architecture:** New `recent_negative_exemplars` helper reads the satisfaction-classified event store through `Database.query_events`; `build_batch_content_evaluation_prompt` grows one optional kwarg and two permanent system-rule lines; discovery engine wires the helper into its eval-batch call site via `self._database`. The no-examples user block is preserved on cold start (block omitted entirely), and the system prompt remains call-invariant after the constant changes once.

**Tech Stack:** Python (soul, llm, discovery), pytest, ruff, mypy strict.

---

### Task 1: Add the Recent Negative Exemplars Helper

**Files:**
- Create: `src/openbiliclaw/soul/negative_exemplars.py`
- Test: `tests/test_negative_exemplars.py`

**Step 1: Write failing tests**

Add tests proving:
- Empty event store → `recent_negative_exemplars(event_store)` returns `[]`.
- A store with 3 negative-classified events returns 3 records, each shaped `{"title": str, "reason": str, "age_days": int}`.
- A store with 20 negative events returns at most `limit` (default 8); the kept items are the 8 highest-scoring by recency weight (newer wins).
- Recency weighting follows `exp(-age_days / half_life_days)` with `half_life_days=14` default.
- Two events whose normalized title prefix matches (lowercased, stripped of leading hash/emoji, first 20 chars) collapse to one slot, keeping the newer.
- Titles longer than 80 characters are truncated with a trailing `…`.
- A storage exception is swallowed: helper returns `[]` and emits a `logger.debug`.

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_negative_exemplars.py -q
```

Expected: fail because the module does not exist.

**Step 3: Implement the helper**

Create `negative_exemplars.py` with:
- `MAX_LIMIT = 8`, `DEFAULT_HALF_LIFE_DAYS = 14`, `TITLE_MAX_CHARS = 80`.
- `recent_negative_exemplars(event_store, *, limit=8, half_life_days=14, now=None) -> list[dict]`.
- Internally calls `event_store.query_events(satisfaction_modes=frozenset({"negative"}), limit=200)` (the cap is bigger than the output cap so the recency scorer has material to work with).
- Computes `age_days` from each event's `created_at` and `now or datetime.utcnow()`. If a test double only supplies `timestamp`, tolerate that as a fallback.
- Sorts by recency weight descending, dedupes by normalized prefix, truncates titles, slices to `min(limit, MAX_LIMIT)`.
- Each returned dict carries `{"title": ..., "reason": event["satisfaction_reason"], "age_days": ...}`.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/negative_exemplars.py tests/test_negative_exemplars.py
git commit -m "feat(soul): recent_negative_exemplars helper for downstream evaluators"
```

---

### Task 2: Extend the Batch Content Evaluation Prompt

**Files:**
- Modify: `src/openbiliclaw/llm/prompts.py`
- Test: `tests/test_llm_prompts.py`

**Step 1: Write failing tests**

Add tests proving:
- `build_batch_content_evaluation_prompt(..., negative_examples=None)` returns the same user-message string as the current no-examples path (capture today's user message as a fixture before the change).
- `negative_examples=[]` returns the same user-message string as `None`.
- With `negative_examples=[{"title": "被微电子男朋友的学识震惊到", "reason": "quick_exit", "age_days": 2}]`, the user message contains a `<negative_examples>` block placed strictly between `<source_context>...</source_context>` and `<content_batch>...</content_batch>` (matches the cache-stable suffix slot in the builder).
- The system message is byte-identical to `_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT` regardless of whether `negative_examples` is provided (i.e. the new rules are unconditional additions to the constant, not call-conditional text). Do not assert equality against the pre-change system prompt, because adding permanent rules necessarily changes it once.
- `test_prompt_builder_system_messages_are_call_invariant` keeps passing for this builder when called with `negative_examples` of different lengths.
- JSON dump of `negative_examples` uses `ensure_ascii=False, indent=2, sort_keys=True` (per the project's prompt-cache convention).

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_llm_prompts.py -q
```

Expected: fail because the new kwarg and block are not implemented.

**Step 3: Implement prompt changes**

In `prompts.py`:
- Append two new rules to the body of `_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT` (the constant body, so the system message stays call-invariant). The rules should read along these lines, in Chinese to match the rest of that prompt:
  - "当 user 消息携带 `<negative_examples>` 时，把这些标题视为用户最近**明确不喜欢**的样本——理由可能是快速划走 (`quick_exit`) 或显式负反馈 (`explicit_negative`)。"
  - "对每个候选项，先与 `<negative_examples>` 中的标题做**结构 / 话术 / 商业意图**层面的比较；若高度相似（同款震惊体、同款保姆级全攻略、同款月入过万钓贴），`integration_fit` 与 `interest_overlap` 必须显著降低，不要被表面话题词吸引而错给高分。比较的是**话术模式**，不是关键词重叠。"
- Update `build_batch_content_evaluation_prompt`'s signature to accept `negative_examples: list[dict] | None = None` as the last kwarg.
- In the user-message composition:
  - If `negative_examples` is truthy, emit a `<negative_examples>` block right after the `<source_context>` block and before `<content_batch>`, serialized with `json.dumps(negative_examples, ensure_ascii=False, indent=2, sort_keys=True)`.
  - If falsy, omit the block entirely (no empty tag).

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/llm/prompts.py tests/test_llm_prompts.py
git commit -m "feat(llm): batch_content_evaluation accepts negative_examples block"
```

---

### Task 3: Wire the Helper into the Discovery Eval Batch Call Site

**Files:**
- Modify: `src/openbiliclaw/discovery/engine.py`
- Test: `tests/test_discovery_engine.py`

**Step 1: Write failing tests**

Add tests proving:
- The eval-batch call site calls `recent_negative_exemplars(self._database)` once per batch (not per candidate) and forwards the result to the prompt builder.
- A failure inside `recent_negative_exemplars` (mock raising) does not abort the batch; the batch runs with `negative_examples=None`.
- The helper result is cached for at most 5 minutes per database / latest-event-id fingerprint so back-to-back batches do not refetch.

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_discovery_engine.py -q
```

Expected: fail because the wiring does not exist.

**Step 3: Implement wiring**

In `discovery/engine.py`:
- Add a small helper method `_get_negative_exemplars()` on the engine class that caches the result in a `(timestamp, latest_event_id, exemplars)` tuple field and refreshes when either the latest event id changes or 5 minutes have passed.
- Use `self._database.get_latest_event_id()` as the fingerprint. If `self._database is None`, return `None` and omit the block.
- Wrap the call in `try/except Exception` and fall back to `None` on failure, logging at `DEBUG`.
- At the eval-batch call site (around `eval_batch start: ...` log), call `_get_negative_exemplars()` and pass the result as `negative_examples=` to `build_batch_content_evaluation_prompt`.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/discovery/engine.py tests/test_discovery_engine.py
git commit -m "feat(discovery): eval_batch consumes recent negative exemplars to suppress clickbait look-alikes"
```

---

### Task 4: Docs + Changelog

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/llm.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/architecture.md` (if the discovery/eval-batch data-flow diagram is present)
- Modify: `docs/spec.md` (if §3 diagrams include eval-batch inputs)
- Modify: `docs/changelog.md`

**Step 1: Update module docs**

- `discovery.md`: document that eval-batch now reads from `recent_negative_exemplars` and that the cache TTL is 5 minutes per engine instance.
- `llm.md`: update `build_batch_content_evaluation_prompt` signature; note the two new system-prompt rules are permanent additions to the constant.
- `soul.md`: cross-reference — `negative_exemplars.py` is the downstream consumer of the `inferred_satisfaction` work; mention the dependency direction.
- `architecture.md` / `spec.md`: if their diagrams show discovery eval-batch inputs, add the recent negative exemplar input from the event store.

**Step 2: Update changelog**

Add a bullet under the current version block in `docs/changelog.md`:
- `feat(discovery): eval_batch evaluator anchored on recent quick-exit / negative exemplars to suppress clickbait and course-pitch look-alikes`.

**Step 3: Commit**

```bash
git add docs/
git commit -m "docs: eval_batch negative exemplars"
```

---

## Verification

Run the full Python suite:

```bash
uv run --extra dev python -m pytest -q
```

Then manually verify on a live install where the satisfaction filter has been flipped on for a release cycle and the user has at least 3 negative-classified events:

1. Trigger a discovery cycle.
2. Capture the next eval-batch prompt body via the existing LLM debug log.
3. Confirm `<negative_examples>` is present and contains the expected recent quick-exit titles.
4. Run a discovery cycle on a profile with no negative events; confirm `<negative_examples>` is **absent** and the user-message body matches the no-examples prompt shape. The system prompt will differ from pre-change builds because the new rules are permanent, but it should remain byte-identical across calls after deployment.
