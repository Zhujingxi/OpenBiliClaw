# Issue #91 Preference Feedback Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make recommendation feedback affect both topic axes, preserve the real source platform, and give users clear, consistent preference-correction actions across desktop, mobile, and extension surfaces.

**Architecture:** Keep card feedback as a soft, non-blocking signal. Expand the existing curator feedback snapshot from topic-key-only labels to normalized key/group aliases, preserve the recommendation's source when building feedback events, and reuse existing profile-edit/chat/probe APIs from clearer UI entry points. No new API, database schema, config, dependency, or request-path LLM work is introduced.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, pytest, vanilla JavaScript/TypeScript, Node test runner, existing desktop/mobile/extension UI assets.

## Global Constraints

- Work only in the isolated `codex/issue-91-preference-feedback` worktree.
- Follow RED → GREEN → REFACTOR for every behavior change; production code must not precede its failing test.
- Card feedback remains soft: do not write one dislike directly into permanent profile overrides.
- Do not change `feedback_batch_threshold`, scoring constants, similarity thresholds, admission policy, configuration, dependencies, or database schema.
- Preserve Issue #98's optimistic UI, 10-second undo barrier, failure rollback, and background feedback scheduling.
- Probe copy is exact: interest = `确认喜欢 / 暂时搁置 / 确认不喜欢 / 多聊聊`; avoidance = `确认避雷 / 搁置避雷 / 不是雷点 / 多聊聊`.
- A key+group double match applies one topic penalty or bonus, never two.
- CLI is excluded from probe-card presentation because it has no probe notification UI; existing `openbiliclaw feedback` behavior remains unchanged.
- Documentation updates required by `CLAUDE.md` are part of the implementation, not follow-up work.

---

## File Map

- `src/openbiliclaw/storage/database.py`: expose `topic_group` in recent feedback rows.
- `src/openbiliclaw/recommendation/curator.py`: normalize feedback/candidate topic aliases and apply one exact/semantic adjustment across both axes.
- `src/openbiliclaw/sources/event_format.py`: render `dismiss` as an explicit feedback action.
- `src/openbiliclaw/api/app.py`: build feedback events from the recommendation's real platform.
- `src/openbiliclaw/web/desktop/index.html`: desktop correction CTA markup.
- `src/openbiliclaw/web/desktop/assets/js/app.js`: desktop probe copy/actions and correction navigation.
- `src/openbiliclaw/web/desktop/assets/css/app.css`: desktop visible action/CTA layout.
- `src/openbiliclaw/web/js/view-models.js`: canonical mobile probe action descriptors.
- `src/openbiliclaw/web/js/views/profile.js`: mobile profile text actions and defer.
- `src/openbiliclaw/web/js/views/recommend.js`: mobile correction CTA navigation.
- `src/openbiliclaw/web/css/app.css`: mobile action/CTA responsive layout.
- `extension/popup/popup.html`: extension correction CTA markup/style.
- `extension/popup/popup.js`: extension action descriptors, defer handling, response copy, and correction navigation.
- Backend, desktop, mobile, and extension test files listed per task below.

---

### Task 1: Preserve both feedback topic axes in synchronous and embedding scoring

**Files:**
- Modify: `src/openbiliclaw/storage/database.py:4383`
- Modify: `src/openbiliclaw/recommendation/curator.py:46-70,106-115,145-220,372-505`
- Test: `tests/test_storage.py`
- Test: `tests/test_pool_curator.py`

**Interfaces:**
- Consumes: `content_cache.topic_key`, `content_cache.topic_group`, existing `FeedbackSignals` fields.
- Produces: `candidate_feedback_topics(item: DiscoveredContent) -> frozenset[str]`; feedback topic sets containing normalized key/group aliases.

- [ ] **Step 1: Write failing storage and context tests**

Add to `tests/test_storage.py`:

```python
def test_feedback_signals_return_topic_key_and_group() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV_TOPIC",
            title="动画叙事拆解",
            source="search",
            topic_key="动漫解说",
            topic_group="动漫",
        )
        recommendation_id = db.insert_recommendation("BV_TOPIC", confidence=0.9)
        db.update_recommendation_feedback(recommendation_id, feedback_type="dislike")

        rows = db.get_feedback_signals()

        assert rows[0]["topic_key"] == "动漫解说"
        assert rows[0]["topic_group"] == "动漫"
```

Extend `test_build_context_reads_feedback_signals()` in `tests/test_pool_curator.py` so the cached row has `topic_group="游戏"` and add:

```python
assert "game" in ctx.feedback.disliked_topic_keys
assert "游戏" in ctx.feedback.disliked_topic_keys
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_storage.py::test_feedback_signals_return_topic_key_and_group tests/test_pool_curator.py::test_build_context_reads_feedback_signals -q
```

Expected: FAIL because `get_feedback_signals()` does not select `topic_group`, and the context only stores `topic_key`.

- [ ] **Step 3: Return and normalize both feedback axes**

Change the SQL projection in `Database.get_feedback_signals()` to:

```sql
SELECT r.feedback_type, c.up_mid, c.up_name, c.topic_key,
       c.topic_group, c.source, c.title, c.franchise_key
```

Add beside `candidate_amplification_keys()` in `curator.py`:

```python
def candidate_feedback_topics(item: DiscoveredContent) -> frozenset[str]:
    """Return normalized fine/coarse topic aliases used by feedback scoring."""
    values = (
        normalize_amplification_key(str(getattr(item, "topic_key", "") or "")),
        normalize_amplification_key(str(getattr(item, "topic_group", "") or "")),
    )
    return frozenset(value for value in values if value)


def _feedback_row_topics(row: dict[str, object]) -> frozenset[str]:
    values = (
        normalize_amplification_key(str(row.get("topic_key", "") or "")),
        normalize_amplification_key(str(row.get("topic_group", "") or "")),
    )
    return frozenset(value for value in values if value)
```

In `PoolCurator.build_context()`, replace the single `topic_key` extraction in both dislike and like branches with:

```python
topics = _feedback_row_topics(row)
if ftype == "dislike":
    disliked_topics.update(topics)
elif ftype in ("like", "save"):
    liked_topics.update(topics)
```

Keep the existing UP and franchise extraction inside the dislike branch.

- [ ] **Step 4: Verify the storage/context tests are GREEN**

Run the Step 2 command again.

Expected: PASS.

- [ ] **Step 5: Write failing exact-match adjustment tests**

Add to `tests/test_pool_curator.py`:

```python
def test_feedback_dislike_matches_key_when_group_is_present() -> None:
    feedback = FeedbackSignals(disliked_topic_keys=frozenset({"动漫解说"}))
    item = DiscoveredContent(
        bvid="BV_KEY",
        topic_key="动漫解说",
        topic_group="动漫",
    )
    assert PoolCurator._feedback_adjustment(item, feedback) == -0.10


def test_feedback_dislike_matches_group_when_key_differs() -> None:
    feedback = FeedbackSignals(disliked_topic_keys=frozenset({"动漫"}))
    item = DiscoveredContent(
        bvid="BV_GROUP",
        topic_key="动画资讯",
        topic_group="动漫",
    )
    assert PoolCurator._feedback_adjustment(item, feedback) == -0.10


def test_feedback_topic_double_match_applies_once() -> None:
    feedback = FeedbackSignals(disliked_topic_keys=frozenset({"动漫解说", "动漫"}))
    item = DiscoveredContent(
        bvid="BV_BOTH",
        topic_key="动漫解说",
        topic_group="动漫",
    )
    assert PoolCurator._feedback_adjustment(item, feedback) == -0.10


def test_feedback_like_matches_either_topic_axis_once() -> None:
    feedback = FeedbackSignals(liked_topic_keys=frozenset({"建筑", "建筑史"}))
    item = DiscoveredContent(
        bvid="BV_LIKE",
        topic_key="建筑史",
        topic_group="建筑",
    )
    assert PoolCurator._feedback_adjustment(item, feedback) == 0.05
```

- [ ] **Step 6: Run the exact-match tests and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_pool_curator.py -k 'matches_key_when_group or matches_group_when_key or double_match or like_matches_either' -q
```

Expected: at least the key-with-group case FAILS because current code selects `topic_group or topic_key`.

- [ ] **Step 7: Apply one exact adjustment across the alias set**

Replace the topic section of `_feedback_adjustment()` with:

```python
candidate_topics = candidate_feedback_topics(item)
if candidate_topics & feedback.disliked_topic_keys:
    adj -= _FEEDBACK_DISLIKE_TOPIC_PENALTY
if candidate_topics & feedback.liked_topic_keys:
    adj += _FEEDBACK_LIKE_TOPIC_BONUS
```

Keep UP and franchise adjustments unchanged.

- [ ] **Step 8: Write a failing embedding-path regression test**

Add to `tests/test_pool_curator.py`:

```python
async def test_async_feedback_embedding_checks_key_and_group_once() -> None:
    class FakeEmbeddingService:
        similarity_threshold = 0.99

        async def embed(self, text: str) -> list[float]:
            vectors = {
                "动漫解说": [1.0, 0.0, 0.0],
                "动漫": [0.0, 1.0, 0.0],
                "科技": [0.0, 0.0, 1.0],
            }
            return vectors.get(text, [0.0, 0.0, 0.0])

    db, _ = _make_db()
    curator = PoolCurator(db)
    now = _now()
    matching = DiscoveredContent(
        bvid="BV_MATCH",
        relevance_score=0.8,
        topic_key="动漫解说",
        topic_group="科技",
        discovered_at=now.isoformat(),
    )
    neutral = DiscoveredContent(
        bvid="BV_NEUTRAL",
        relevance_score=0.8,
        topic_key="科技",
        topic_group="科技",
        discovered_at=now.isoformat(),
    )
    context = ScoringContext(
        feedback=FeedbackSignals(disliked_topic_keys=frozenset({"动漫解说"})),
        now=now,
    )

    scores = await curator.score_candidates_async(
        [matching, neutral],
        context,
        embedding_service=FakeEmbeddingService(),
    )

    assert scores["BV_MATCH"] == scores["BV_NEUTRAL"] - 0.10
```

- [ ] **Step 9: Run the async test and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_pool_curator.py::test_async_feedback_embedding_checks_key_and_group_once -q
```

Expected: FAIL because the current async path embeds only `topic_group` when it is non-empty.

- [ ] **Step 10: Check all candidate topic vectors and adjust once**

Replace the async feedback-adjustment block with:

```python
candidate_topics = candidate_feedback_topics(item)
candidate_topic_vecs = []
if embedding_service is not None:
    for candidate_topic in candidate_topics:
        vector = await embedding_service.embed(candidate_topic)
        if vector:
            candidate_topic_vecs.append(vector)

if embedding_service is not None and candidate_topic_vecs:
    adj = 0.0
    if item.up_mid and item.up_mid in context.feedback.disliked_up_mids:
        adj -= _FEEDBACK_DISLIKE_UP_PENALTY
    if any(
        cosine_similarity(topic_vec, disliked_vec)
        >= embedding_service.similarity_threshold
        for topic_vec in candidate_topic_vecs
        for disliked_vec in _disliked_vecs.values()
    ):
        adj -= _FEEDBACK_DISLIKE_TOPIC_PENALTY
    if any(
        cosine_similarity(topic_vec, liked_vec)
        >= embedding_service.similarity_threshold
        for topic_vec in candidate_topic_vecs
        for liked_vec in _liked_vecs.values()
    ):
        adj += _FEEDBACK_LIKE_TOPIC_BONUS
    item_franchise = (getattr(item, "franchise_key", "") or "").strip()
    if item_franchise and item_franchise in context.feedback.disliked_franchises:
        adj -= _FEEDBACK_DISLIKE_FRANCHISE_PENALTY
    score += adj
else:
    score += self._feedback_adjustment(item, context.feedback)
```

Do not change the separate fatigue calculation in this task.

- [ ] **Step 11: Verify all curator/storage tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_storage.py tests/test_pool_curator.py -q
.venv/bin/ruff check src/openbiliclaw/storage/database.py src/openbiliclaw/recommendation/curator.py tests/test_storage.py tests/test_pool_curator.py
```

Expected: PASS with no Ruff errors.

- [ ] **Step 12: Commit Task 1**

```bash
git add src/openbiliclaw/storage/database.py src/openbiliclaw/recommendation/curator.py tests/test_storage.py tests/test_pool_curator.py
git commit -m "fix(recommendation): match feedback across topic axes"
```

---

### Task 2: Preserve feedback source platform and action context

**Files:**
- Modify: `src/openbiliclaw/sources/event_format.py:221-245`
- Modify: `src/openbiliclaw/api/app.py:6280-6362`
- Test: `tests/test_api_app.py:4812-4917`

**Interfaces:**
- Consumes: recommendation row `source_platform`, `format_event_context()`.
- Produces: feedback events with real `source_platform`, explicit action context, legacy Bilibili fallback.

- [ ] **Step 1: Write failing cross-platform API tests**

Add beside the existing feedback endpoint tests in `tests/test_api_app.py`:

```python
def test_feedback_endpoint_preserves_recommendation_source_platform(self) -> None:
    from fastapi.testclient import TestClient

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeDatabase:
        def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object]:
            return {
                "id": recommendation_id,
                "bvid": "zhihu:answer:42",
                "title": "如何理解城市更新",
                "source_platform": "zhihu",
            }

        def update_recommendation_feedback(
            self,
            recommendation_id: int,
            *,
            feedback_type: str,
            feedback_note: str = "",
        ) -> None:
            return None

    memory = FakeMemoryManager()
    client = TestClient(create_app(memory_manager=memory, database=FakeDatabase()))

    response = client.post(
        "/api/feedback",
        json={
            "recommendation_id": 7,
            "feedback_type": "dislike",
            "note": "这个方向不适合我",
        },
    )

    assert response.status_code == 200
    event = memory.events[0]
    assert event["source_platform"] == "zhihu"
    assert "在知乎" in str(event["context"])
    assert "标记不喜欢" in str(event["context"])
    assert "备注:这个方向不适合我" in str(event["context"])


def test_feedback_endpoint_falls_back_to_bilibili_for_legacy_rows(self) -> None:
    from fastapi.testclient import TestClient

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeDatabase:
        def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object]:
            return {"id": recommendation_id, "bvid": "BV1LEGACY", "title": "旧推荐"}

        def update_recommendation_feedback(
            self,
            recommendation_id: int,
            *,
            feedback_type: str,
            feedback_note: str = "",
        ) -> None:
            return None

    memory = FakeMemoryManager()
    client = TestClient(create_app(memory_manager=memory, database=FakeDatabase()))
    response = client.post(
        "/api/feedback",
        json={"recommendation_id": 8, "feedback_type": "dismiss", "note": ""},
    )

    assert response.status_code == 200
    assert memory.events[0]["source_platform"] == "bilibili"
    assert "在 B 站忽略了" in str(memory.events[0]["context"])
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py -k 'preserves_recommendation_source_platform or falls_back_to_bilibili_for_legacy_rows' -q
```

Expected: the Zhihu test FAILS because the event is hard-coded to Bilibili; dismiss context also lacks a shared event-format action.

- [ ] **Step 3: Add explicit dismiss formatting**

Add to `_EVENT_TYPE_LABELS` in `event_format.py`:

```python
"dismiss": "忽略了",
```

- [ ] **Step 4: Build API feedback context from the real platform**

Update the import inside the endpoint to include `format_event_context`:

```python
from openbiliclaw.sources.event_format import (
    SOURCE_BILIBILI,
    build_event,
    format_event_context,
)
```

Replace the hard-coded context/source block with:

```python
rec_title = str(recommendation.get("title", ""))
source_platform = (
    str(recommendation.get("source_platform") or SOURCE_BILIBILI).strip().lower()
    or SOURCE_BILIBILI
)
feedback_context = format_event_context(
    event_type=feedback_type,
    source_platform=source_platform,
    title=rec_title,
)
if note:
    feedback_context = f"{feedback_context},备注:{note}"
```

Pass `source_platform=source_platform` to `build_event()`. Keep the stored event type as `feedback` and keep the existing metadata/scheduler behavior.

- [ ] **Step 5: Verify API behavior and non-blocking regression tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py -k 'feedback_endpoint or feedback_api' -q
.venv/bin/ruff check src/openbiliclaw/api/app.py src/openbiliclaw/sources/event_format.py tests/test_api_app.py
```

Expected: PASS, including the existing scheduler/non-blocking tests.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/sources/event_format.py tests/test_api_app.py
git commit -m "fix(api): preserve feedback source platform"
```

---

### Task 3: Make desktop probe actions explicit and add correction entry points

**Files:**
- Modify: `src/openbiliclaw/web/desktop/index.html:166-184`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js:1384-1401,2740-2750,3310-3675,6150-6170`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css`
- Test: `tests/test_desktop_web_probe_defer.py`
- Test: `tests/test_desktop_web_issue_98_e2e.py`
- Create: `tests/test_desktop_preference_correction.py`

**Interfaces:**
- Consumes: existing `openProfilePage()`, `enterProfileEdit()`, `openChatPage()`, probe response endpoints, Issue #98 pending action coordinator.
- Produces: `probeActionCopy(type)`, visible desktop action labels, `openProfileCorrection()`, `openChatCorrection()`.

- [ ] **Step 1: Write failing desktop copy tests**

Replace the icon-only assertions in `tests/test_desktop_web_probe_defer.py` with:

```python
def test_desktop_probe_actions_use_visible_semantic_copy() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    render_body = _function_body(js, "renderMessages")
    probe_branch = render_body[
        render_body.index("const isAvoidance") : render_body.index("if (resolvedResult)")
    ]

    for label in (
        "确认喜欢",
        "暂时搁置",
        "确认不喜欢",
        "多聊聊",
        "确认避雷",
        "搁置避雷",
        "不是雷点",
    ):
        assert label in js
    assert "data-probe=\"confirm\"" in render_body
    assert "data-probe=\"defer\"" in render_body
    assert "data-probe=\"reject\"" in render_body
    assert "feedback-icon-btn" not in probe_branch
```

Retain the existing tests that `interest.deferred` and `avoidance.deferred` do not trigger profile refresh.

- [ ] **Step 2: Write failing correction-entry tests**

Create `tests/test_desktop_preference_correction.py`:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_recommendation_header_exposes_correction_actions() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    assert "推荐不准？" in html
    assert 'id="editProfileFromRecommendations"' in html
    assert 'id="chatFromRecommendations"' in html


def test_desktop_correction_actions_reuse_profile_and_chat_flows() -> None:
    js = (
        ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js"
    ).read_text(encoding="utf-8")
    assert "async function openProfileCorrection()" in js
    assert "openProfilePage();" in js
    assert "await enterProfileEdit();" in js
    assert "function openChatCorrection()" in js
    assert "openChatPage();" in js
    assert 'safeBind("#editProfileFromRecommendations", "click", openProfileCorrection)' in js
    assert 'safeBind("#chatFromRecommendations", "click", openChatCorrection)' in js
```

- [ ] **Step 3: Run the desktop tests and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_desktop_web_probe_defer.py tests/test_desktop_preference_correction.py -q
```

Expected: FAIL because the message center is icon-only and correction controls do not exist.

- [ ] **Step 4: Add a desktop action-copy helper and visible buttons**

Add near the probe helpers in `app.js`:

```javascript
const PROBE_ACTION_COPY = Object.freeze({
  interest: Object.freeze({
    confirm: "确认喜欢",
    defer: "暂时搁置",
    reject: "确认不喜欢",
    chat: "多聊聊",
  }),
  avoidance: Object.freeze({
    confirm: "确认避雷",
    defer: "搁置避雷",
    reject: "不是雷点",
    chat: "多聊聊",
  }),
});

function probeActionCopy(type) {
  return PROBE_ACTION_COPY[isAvoidanceProbeType(type) ? "avoidance" : "interest"];
}
```

In desktop profile rows and `renderMessages()`, build real text buttons from the copy object:

```javascript
const actionCopy = probeActionCopy(messageType(msg));
const actionButtons = `
  <button class="probe-btn is-confirm" data-probe="confirm" type="button">${actionCopy.confirm}</button>
  <button class="probe-btn is-neutral" data-probe="defer" type="button">${actionCopy.defer}</button>
  <button class="probe-btn is-reject" data-probe="reject" type="button">${actionCopy.reject}</button>`;
```

Use `${actionCopy.chat}` for the existing chat button. Remove probe-only `feedback-icon-btn` SVG markup; do not change notification-candidate or recommendation-card icons.

- [ ] **Step 5: Add desktop correction markup/navigation**

Under the recommendation heading in `index.html`, add:

```html
<p class="preference-correction-callout">
  推荐不准？
  <button id="editProfileFromRecommendations" type="button">编辑画像</button>
  <span aria-hidden="true">，或</span>
  <button id="chatFromRecommendations" type="button">直接告诉阿B</button>
</p>
```

Add in `app.js`:

```javascript
async function openProfileCorrection() {
  openProfilePage();
  await enterProfileEdit();
}

function openChatCorrection() {
  openChatPage();
}
```

Bind them with the exact `safeBind()` calls asserted by the test.

Add CSS using existing tokens:

```css
.preference-correction-callout {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 6px 0 0;
  color: var(--fg-muted);
  font-size: 13px;
}

.preference-correction-callout button {
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--accent);
  font: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}

.preference-correction-callout button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}
```

- [ ] **Step 6: Extend the existing Playwright behavior test**

In `tests/test_desktop_web_issue_98_e2e.py`, add assertions to the existing probe scenario:

```python
expect(interest.locator('[data-probe="confirm"]')).to_have_text("确认喜欢")
expect(interest.locator('[data-probe="defer"]')).to_have_text("暂时搁置")
expect(interest.locator('[data-probe="reject"]')).to_have_text("确认不喜欢")

expect(avoidance.locator('[data-probe="confirm"]')).to_have_text("确认避雷")
expect(avoidance.locator('[data-probe="defer"]')).to_have_text("搁置避雷")
expect(avoidance.locator('[data-probe="reject"]')).to_have_text("不是雷点")
```

Keep the existing undo, request-count, and failure-rollback assertions intact.

- [ ] **Step 7: Verify desktop tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_desktop_web_probe_defer.py tests/test_desktop_preference_correction.py tests/test_desktop_web_issue_98_e2e.py -q
.venv/bin/ruff check tests/test_desktop_web_probe_defer.py tests/test_desktop_preference_correction.py tests/test_desktop_web_issue_98_e2e.py
```

Expected: PASS. If Playwright is unavailable, the test must skip for its existing dependency reason; static tests still pass.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/openbiliclaw/web/desktop/index.html src/openbiliclaw/web/desktop/assets/js/app.js src/openbiliclaw/web/desktop/assets/css/app.css tests/test_desktop_web_probe_defer.py tests/test_desktop_preference_correction.py tests/test_desktop_web_issue_98_e2e.py
git commit -m "feat(web): clarify preference correction actions"
```

---

### Task 4: Align mobile message/profile actions and add correction navigation

**Files:**
- Modify: `src/openbiliclaw/web/js/view-models.js:515-545`
- Modify: `src/openbiliclaw/web/js/views/profile.js:15-28,327-465`
- Modify: `src/openbiliclaw/web/js/views/recommend.js:1-55,170-245`
- Modify: `src/openbiliclaw/web/css/app.css`
- Test: `tests/test_mobile_web_view_models.py`
- Create: `tests/test_mobile_preference_correction.py`

**Interfaces:**
- Consumes: existing `getProbeMessageActions()`, `getAvoidanceProbeMessageActions()`, `navigateToTab()`, profile response APIs.
- Produces: canonical mobile descriptors reused by message/profile views; `enterProfileEditMode()`; recommendation correction controls.

- [ ] **Step 1: Write failing mobile action-contract tests**

Update the existing mobile action tests in `tests/test_mobile_web_view_models.py` to assert:

```python
assert 'label: "确认喜欢", action: "confirm"' in view_models
assert 'label: "暂时搁置", action: "defer"' in view_models
assert 'label: "确认不喜欢", action: "reject"' in view_models
assert 'label: "确认避雷", action: "confirm"' in view_models
assert 'label: "搁置避雷", action: "defer"' in view_models
assert 'label: "不是雷点", action: "reject"' in view_models
```

Add:

```python
def test_mobile_profile_uses_probe_action_descriptors() -> None:
    profile_js = (
        ROOT / "src/openbiliclaw/web/js/views/profile.js"
    ).read_text(encoding="utf-8")
    assert "getProbeMessageActions" in profile_js
    assert "getAvoidanceProbeMessageActions" in profile_js
    assert 'data-action="defer"' in profile_js
    assert "\\u2713" not in profile_js
    assert "\\u2717" not in profile_js
```

- [ ] **Step 2: Write failing mobile correction-entry tests**

Create `tests/test_mobile_preference_correction.py`:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mobile_recommendation_header_exposes_correction_actions() -> None:
    js = (
        ROOT / "src/openbiliclaw/web/js/views/recommend.js"
    ).read_text(encoding="utf-8")
    assert "推荐不准？" in js
    assert 'data-correction-target="profile"' in js
    assert 'data-correction-target="chat"' in js
    assert 'navigateToTab("profile")' in js
    assert "enterProfileEditMode" in js
    assert 'navigateToTab("chat")' in js
    assert 'document.getElementById("chat-input")?.focus()' in js
```

- [ ] **Step 3: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_mobile_web_view_models.py tests/test_mobile_preference_correction.py -q
```

Expected: FAIL on old copy, icon-only profile actions, missing defer, and missing correction entry points.

- [ ] **Step 4: Make mobile descriptors canonical**

Change `view-models.js` functions to:

```javascript
export function getProbeMessageActions() {
  return [
    { label: "确认喜欢", action: "confirm", primary: true },
    { label: "暂时搁置", action: "defer", primary: false },
    { label: "确认不喜欢", action: "reject", primary: false },
    { label: "多聊聊", action: "chat", primary: false },
  ];
}

export function getAvoidanceProbeMessageActions() {
  return [
    { label: "确认避雷", action: "confirm", primary: true },
    { label: "搁置避雷", action: "defer", primary: false },
    { label: "不是雷点", action: "reject", primary: false },
    { label: "多聊聊", action: "chat", primary: false },
  ];
}
```

Import these functions in `profile.js`. Render the first three descriptors for profile rows:

```javascript
const actions = getProbeMessageActions().filter((action) => action.action !== "chat");
const actionMarkup = actions.map((action) => `
  <button class="spec-btn ${action.primary ? "confirm" : action.action}"
          data-action="${action.action}">${esc(action.label)}</button>`).join("");
```

Use `getAvoidanceProbeMessageActions()` for avoidance rows. Existing generic binders already pass `data-action`; ensure they disable all row buttons before awaiting and re-enable all on failure:

```javascript
const buttons = [...row.querySelectorAll(".spec-btn")];
for (const actionButton of buttons) actionButton.disabled = true;
try {
  await respondToProbe(domain, action, { surface: "profile" });
} catch {
  forgetHandledProbe(domain, "interest.probe");
  for (const actionButton of buttons) actionButton.disabled = false;
}
```

Apply the same pattern to `respondToAvoidanceProbe()`.

Export a focused wrapper beside the private `enterEdit()` function:

```javascript
export async function enterProfileEditMode() {
  await enterEdit();
}
```

- [ ] **Step 5: Add mobile correction controls**

Import the existing navigation function and the new profile wrapper in `recommend.js`:

```javascript
import { navigateToTab } from "../app.js";
import { enterProfileEditMode } from "./profile.js";
```

Add to `renderRecommendationHeader()` after the top row:

```javascript
const correction = document.createElement("p");
correction.className = "preference-correction-callout";
correction.innerHTML = `推荐不准？
  <button type="button" data-correction-target="profile">编辑画像</button>
  <span aria-hidden="true">，或</span>
  <button type="button" data-correction-target="chat">直接告诉阿B</button>`;
correction.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-correction-target]");
  if (!button) return;
  const target = button.dataset.correctionTarget;
  if (target === "profile") {
    navigateToTab("profile");
    await enterProfileEditMode();
  }
  if (target === "chat") {
    navigateToTab("chat");
    requestAnimationFrame(() => document.getElementById("chat-input")?.focus());
  }
});
header.appendChild(correction);
```

Add responsive CSS equivalent to Task 3 using mobile theme variables already defined in `web/css/app.css`.

- [ ] **Step 6: Verify mobile tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_mobile_web_view_models.py tests/test_mobile_preference_correction.py -q
.venv/bin/ruff check tests/test_mobile_web_view_models.py tests/test_mobile_preference_correction.py
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/openbiliclaw/web/js/view-models.js src/openbiliclaw/web/js/views/profile.js src/openbiliclaw/web/js/views/recommend.js src/openbiliclaw/web/css/app.css tests/test_mobile_web_view_models.py tests/test_mobile_preference_correction.py
git commit -m "feat(mobile): expose preference correction actions"
```

---

### Task 5: Align extension actions, defer behavior, and correction navigation

**Files:**
- Modify: `extension/popup/popup.html:4580-4610`
- Modify: `extension/popup/popup.js:190-250,500-545,1743-1920,2320-2465,2840-2935,5735-5790`
- Test: `extension/tests/popup-message-actions.test.ts`
- Test: `extension/tests/popup-profile-edit.test.ts`
- Create: `extension/tests/popup-preference-correction.test.ts`

**Interfaces:**
- Consumes: `setActiveTab()`, `enterProfileEditMode()`, `chatInput`, existing probe response API functions.
- Produces: `probeActionDescriptors(type)`, `probeResponseMessage(type, action, domain)`, extension correction controls.

- [ ] **Step 1: Write failing extension action tests**

Extend `extension/tests/popup-message-actions.test.ts`:

```typescript
test("message cards expose all semantic probe actions", () => {
  for (const action of ["confirm", "defer", "reject", "chat"]) {
    assert.match(
      buildMessageCard,
      new RegExp(`dataset\\.msgAction = "${action}"`),
    );
  }
  for (const label of [
    "确认喜欢",
    "暂时搁置",
    "确认不喜欢",
    "确认避雷",
    "搁置避雷",
    "不是雷点",
    "多聊聊",
  ]) {
    assert.match(popupJs, new RegExp(label));
  }
});

test("delegated message handler submits defer like confirm and reject", () => {
  const handler = popupJs.slice(
    popupJs.indexOf("function onMessageActionClick"),
    popupJs.indexOf("function renderMessagesList"),
  );
  assert.match(handler, /action === "confirm" \|\| action === "defer" \|\| action === "reject"/);
});
```

Extend `popup-profile-edit.test.ts`:

```typescript
test("profile probe rows expose defer with semantic copy", () => {
  assert.match(js, /probeActionDescriptors/);
  assert.match(js, /responseType, row/);
  assert.match(js, /"defer"/);
  assert.match(js, /暂时搁置/);
  assert.match(js, /搁置避雷/);
});
```

- [ ] **Step 2: Write failing extension correction tests**

Create `extension/tests/popup-preference-correction.test.ts`:

```typescript
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const html = readFileSync(resolve("popup", "popup.html"), "utf8");
const js = readFileSync(resolve("popup", "popup.js"), "utf8");

test("recommendation header exposes preference correction controls", () => {
  assert.match(html, /推荐不准？/);
  assert.match(html, /id="editProfileFromRecommendations"/);
  assert.match(html, /id="chatFromRecommendations"/);
});

test("correction controls reuse profile edit and chat tabs", () => {
  assert.match(js, /setActiveTab\("profile"\)/);
  assert.match(js, /void enterProfileEditMode\(\)/);
  assert.match(js, /setActiveTab\("chat"\)/);
  assert.match(js, /elements\.chatInput\.focus\(\)/);
});
```

- [ ] **Step 3: Run extension tests and verify RED**

Run:

```bash
cd extension
node --test --experimental-strip-types \
  tests/popup-message-actions.test.ts \
  tests/popup-profile-edit.test.ts \
  tests/popup-preference-correction.test.ts
```

Expected: FAIL because defer/correction controls are absent and copy differs.

- [ ] **Step 4: Add extension action descriptors and response copy**

Add near the probe helpers in `popup.js`:

```javascript
function probeActionDescriptors(type) {
  return isAvoidanceProbeType(type)
    ? [
        { action: "confirm", label: "确认避雷", className: "is-confirm" },
        { action: "defer", label: "搁置避雷", className: "is-neutral" },
        { action: "reject", label: "不是雷点", className: "is-reject" },
        { action: "chat", label: "多聊聊", className: "is-chat" },
      ]
    : [
        { action: "confirm", label: "确认喜欢", className: "is-confirm" },
        { action: "defer", label: "暂时搁置", className: "is-neutral" },
        { action: "reject", label: "确认不喜欢", className: "is-reject" },
        { action: "chat", label: "多聊聊", className: "is-chat" },
      ];
}

function probeResponseMessage(type, responseType, domain) {
  const isAvoidance = isAvoidanceProbeType(type);
  if (responseType === "defer") {
    return isAvoidance
      ? `好，「${domain}」先搁置，过阵子再确认是不是雷点。`
      : `好，「${domain}」先搁置，过阵子再问。`;
  }
  if (responseType === "confirm") {
    return isAvoidance
      ? `好，「${domain}」会作为避雷方向处理。`
      : `好，「${domain}」记住了。`;
  }
  return isAvoidance
    ? `好，「${domain}」不记成避雷。`
    : `好，「${domain}」会作为不喜欢处理。`;
}
```

Use descriptors to construct buttons in `renderSpeculativeInterests()` and `buildMessageCard()`. The profile rows use confirm/defer/reject; message cards use all four. Set `dataset.msgAction`/response type directly from each descriptor.

Update the delegated condition to:

```javascript
} else if (action === "confirm" || action === "defer" || action === "reject") {
```

Replace binary confirm/else success text in `handleSpecResponse()`, `handleProbeResponse()`, and `handleMessageResponse()` with `probeResponseMessage(type, responseType, domain)`.

- [ ] **Step 5: Add extension correction controls and binding**

Under the recommendation header title in `popup.html`, add:

```html
<p class="preference-correction-callout">
  推荐不准？
  <button id="editProfileFromRecommendations" type="button">编辑画像</button>
  <span aria-hidden="true">，或</span>
  <button id="chatFromRecommendations" type="button">直接告诉阿B</button>
</p>
```

Add both elements to the `elements` object. Add:

```javascript
function bindPreferenceCorrectionActions() {
  elements.editProfileFromRecommendations?.addEventListener("click", () => {
    setActiveTab("profile");
    void enterProfileEditMode();
  });
  elements.chatFromRecommendations?.addEventListener("click", () => {
    setActiveTab("chat");
    requestAnimationFrame(() => elements.chatInput?.focus());
  });
}
```

Call `bindPreferenceCorrectionActions()` once during popup bootstrap, next to `bindTabs()`.

Add `.preference-correction-callout` styles matching Task 3, using popup theme variables and a visible `:focus-visible` outline. Ensure the buttons wrap at narrow panel widths.

- [ ] **Step 6: Verify extension tests, typecheck, and build**

Run:

```bash
cd extension
npm test
npm run typecheck
npm run build
```

Expected: 676 or more tests PASS, typecheck PASS, Chrome bundle build PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add extension/popup/popup.html extension/popup/popup.js extension/tests/popup-message-actions.test.ts extension/tests/popup-profile-edit.test.ts extension/tests/popup-preference-correction.test.ts
git commit -m "feat(extension): clarify preference probe actions"
```

---

### Task 6: Synchronize mandatory documentation and run final verification

**Files:**
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: completed Tasks 1-5 behavior.
- Produces: user-facing and module documentation matching the final code.

- [ ] **Step 1: Update module documentation with exact behavior**

Add these facts to the relevant implemented-features/public-API sections:

`docs/modules/recommendation.md`:

```markdown
- 卡片 like/dislike 会在 Pool Curator 中同时匹配候选的细粒度 `topic_key` 与粗粒度
  `topic_group`；任一轴命中即施加一次软调整，两轴同时命中不会重复加权。
- recommendation feedback event 保留候选真实 `source_platform`，旧记录缺来源时兼容
  回退 `bilibili`。
```

`docs/modules/soul.md`:

```markdown
- 卡片反馈是可撤销的软信号并由后台批处理学习；需要确定性修正时，用户可从推荐区
  直接进入画像编辑（持久 override）或自由文本对话。单次 dislike 不会直接永久屏蔽主题。
```

`docs/modules/runtime.md`:

```markdown
- 桌面、移动和插件推荐区提供“编辑画像 / 直接告诉阿B”纠偏入口；兴趣/避雷 probe
  统一使用 confirm/defer/reject/chat 语义，所有操作均有可见文字。
```

- [ ] **Step 2: Update the current changelog block**

Add one bullet under the current version in `docs/changelog.md`:

```markdown
- 修复 Issue #91：推荐反馈同时作用于细/粗 topic 且保留真实平台来源；三端兴趣/避雷
  操作改为明确文字，并在推荐区提供画像编辑和自由对话纠偏入口。
```

Do not create a release version, alter README highlights, or edit architecture diagrams.

- [ ] **Step 3: Run documentation checks**

Run:

```bash
git diff --check
rg -n "Issue #91|topic_group|编辑画像|直接告诉阿B" docs/modules docs/changelog.md
```

Expected: no whitespace errors; all four required docs contain the new behavior.

- [ ] **Step 4: Run focused backend/UI tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest \
  tests/test_storage.py \
  tests/test_pool_curator.py \
  tests/test_api_app.py \
  tests/test_desktop_web_probe_defer.py \
  tests/test_desktop_preference_correction.py \
  tests/test_desktop_web_issue_98_e2e.py \
  tests/test_mobile_web_view_models.py \
  tests/test_mobile_preference_correction.py \
  -q --tb=short
```

Expected: PASS, with only existing environment-dependent skips.

- [ ] **Step 5: Run full repository verification**

Run:

```bash
.venv/bin/ruff format --check src tests
.venv/bin/ruff check src tests
.venv/bin/mypy src
PYTHONPATH=src .venv/bin/pytest -q --tb=short
cd extension
npm test
npm run typecheck
npm run build
```

Expected baseline or better:

```text
Python: 3900+ passed, 16 skipped, 0 failed
Extension: 676+ passed, 0 failed
Ruff: 0 errors
MyPy: 0 errors
TypeScript: 0 errors
Build: success
```

If `ruff format --check` reports an unrelated historical file, compare it with `main` and report it; do not mass-format unrelated files.

- [ ] **Step 6: Perform manual responsive/accessibility verification**

Verify desktop/mobile/plugin at 375px, 768px, 1024px, and 1440px:

```text
1. Interest actions read: 确认喜欢 / 暂时搁置 / 确认不喜欢 / 多聊聊.
2. Avoidance actions read: 确认避雷 / 搁置避雷 / 不是雷点 / 多聊聊.
3. Buttons wrap without horizontal scrolling or clipped labels.
4. Tab and Shift+Tab show a visible focus ring on every action.
5. Defer submits `defer`; reject submits `reject`; the two are never swapped.
6. “编辑画像” opens edit mode; “直接告诉阿B” focuses the chat textarea.
7. Existing 10-second undo and failed-request rollback still work on desktop.
```

Record the exact manual steps/results in the final handoff; do not commit temporary screenshots unless referenced from documentation.

- [ ] **Step 7: Commit Task 6**

```bash
git add docs/modules/recommendation.md docs/modules/soul.md docs/modules/runtime.md docs/changelog.md
git commit -m "docs: document preference feedback repair"
```

- [ ] **Step 8: Review final branch scope**

Run:

```bash
git status --short
git log --oneline main..HEAD
git diff --stat main...HEAD
git diff --check main...HEAD
```

Expected: clean worktree; only Issue #91 code, tests, spec, plan, and mandatory docs are present. Zhihu dispatcher and LLM Token diet files are not changed.
