# 2026-05-30 — Pool Target Means Frontend Available Count Spec

## 0. Scope

This spec changes the candidate-pool capacity contract:

```text
scheduler.pool_target_count = target number of candidates the frontend can actually show as "可换"
```

The target must apply to `pool_available_count`, not merely to raw `fresh`
rows in `content_cache`.

Affected areas:

| Area | Required outcome |
|------|------------------|
| Runtime replenishment | Continues discovery while the frontend-visible available count is below `pool_target_count` |
| Source quota accounting | Uses a servable/windowed source count for refill decisions, so raw source inventory cannot falsely stop replenishment |
| Pool cap enforcement | Allows raw material to exceed the frontend target when topic-window diversity removes rows from the available count |
| Runtime status | Keeps `pool_available_count` as the only "可换" number shown to users |
| Documentation | Describes `pool_target_count` as an available-count target, not a strict raw-row cap |

Out of scope:

- Removing the per-`topic_group` diversity window.
- Changing recommendation ranking or MMR selection.
- Making disabled sources contribute quota.
- Fixing unrelated XHS token backfill, Bilibili WBI rate limits, or LLM latency.

## 1. Problem

On the local database, the configured target is 300:

```toml
[scheduler]
pool_target_count = 300
```

Only Bilibili is enabled, so the effective source target is:

```text
bilibili = 300
```

Observed pool state:

| Metric | Count |
|--------|------:|
| raw fresh/not-disliked/not-recommended rows | 341 |
| pending rows | 17 |
| ready rows before per-topic window | 300 |
| frontend `pool_available_count` with per-topic window | 246 |

The mismatch comes from two different definitions of "full":

1. `count_pool_candidates()` counts frontend availability and applies the
   default `max_per_topic_group=3` window.
2. `count_pool_candidates_by_source()` counts linkable fresh source inventory
   and does not apply readiness or the per-topic window.
3. `_build_source_replenishment_plan()` sees `pool_available_count < 300`, but
   then sees `bilibili = 300/300` by the source-inventory count and returns no
   Bilibili discovery plan.

Result: the frontend can stay stuck below 300 forever even though the runtime
believes the Bilibili source quota is already full.

There is a second half to the same bug: `_enforce_pool_cap()` currently calls
`trim_pool_source_overflow(source_share_quotas=_source_target_counts())` every
tick. That trim also counts raw linkable rows, so it pins Bilibili raw inventory
back to 300. If replenishment only changes the read side, the runtime will
discover new material, precompute it, and then suppress the raw overflow back to
300 before the frontend available count can climb. The fix must therefore cover
both sides of the loop:

```text
refill decision reads available-by-source
cap enforcement does not trim raw rows back to the frontend target
```

## 2. Definitions

### 2.1 Available Candidate

An **available candidate** is a row that can be counted in
`pool_available_count` and returned by the pool serve path under the same
state.

It must satisfy the existing servable gates:

```text
COALESCE(pool_status, 'fresh') = 'fresh'
COALESCE(feedback_type, '') != 'dislike'
COALESCE(pool_expression, '') != ''
COALESCE(pool_topic_label, '') != ''
COALESCE(style_key, '') != ''
COALESCE(topic_group, '') != ''
NOT EXISTS (SELECT 1 FROM recommendations r WHERE r.bvid = content_cache.bvid)
not recently viewed by source-aware content key
source is linkable according to `_is_linkable_pool_source(...)`
```

It also participates in the same topic-window rule used by
`count_pool_candidates()` / `get_pool_candidates()`:

```text
default max_per_topic_group = 3
```

### 2.2 Raw Pool Material

Raw material is fresh, not disliked, not already recommended content that may
or may not be ready to serve. It includes pending and not-yet-linkable rows,
such as XHS rows waiting for `xsec_token` backfill.

Raw material may exceed `pool_target_count`. That is expected when diversity
windows, recent-view filters, XHS linkability, or precompute/classification
gates remove rows from the frontend available count.

Raw material is still bounded. The storage/cost bound is a separate raw ceiling,
not `pool_target_count`.

### 2.3 Source-Available Count

A **source-available count** is the available-candidate count grouped by
platform family after applying the same frontend availability gates and topic
window.

For the default local setup, if the frontend sees 246 available Bilibili rows,
source-available accounting must also report:

```text
bilibili = 246
```

not `bilibili = 300`.

The source-available count must partition the topic window globally, then group
the surviving rows by source family. In a mixed-source pool:

```text
sum(count_pool_available_candidates_by_source().values())
== count_pool_candidates()
```

This invariant prevents a subtle over-count where each source applies its own
per-topic window and the grouped sum exceeds the frontend-visible count.

### 2.4 Raw Material Ceiling

The **raw material ceiling** is the hard cap for raw fresh inventory, including
pending and currently unopenable rows. It is required because
`pool_target_count` no longer caps raw rows.

Default:

```text
raw_material_ceiling = max(pool_target_count * 2, pool_target_count + 120)
```

For `pool_target_count = 300`, raw fresh inventory may grow up to 600 before
raw-ceiling trim runs. This gives the topic window enough slack to surface 300
available rows without allowing unbounded database growth.

The 2x multiplier is intentionally conservative: topic-window haircuts,
recent-view exclusions, pending copy/classification, and optional-source
linkability can all reduce frontend availability below raw inventory. The
ceiling bounds worst-case discovery/precompute spend while leaving enough slack
for diverse long-tail topics to enter the available window. If later production
metrics show the typical haircut is much smaller, this multiplier can be tuned
down as a separate config change.

### 2.5 Raw Material Count Parity

Raw material counting and raw-ceiling trimming must enumerate the same row set.
They should share a selector/helper where practical.

The raw material row set is:

```text
COALESCE(pool_status, 'fresh') = 'fresh'
COALESCE(feedback_type, '') != 'dislike'
NOT EXISTS (SELECT 1 FROM recommendations r WHERE r.bvid = content_cache.bvid)
not recently viewed by source-aware content key
```

It intentionally does not require readiness fields or linkability. This means
XHS rows waiting for `xsec_token` count toward the raw ceiling and can be
suppressed when the source exceeds its raw-ceiling quota.

If the implementation exposes both total and grouped raw counts, they must obey:

```text
sum(count_pool_raw_material_by_source().values())
== count_pool_raw_material_candidates()
```

This is the raw-side mirror of the source-available parity invariant.

## 3. Required Behavior

### 3.1 Target Contract

`pool_target_count` is satisfied only when:

```text
pool_available_count >= pool_target_count
```

For the default config, the user-facing goal is:

```text
前端显示：还有 300 条可换
```

not:

```text
数据库里有 300 条 fresh/raw 候选
```

This target is a replenishment floor and steady-state availability goal, not a
raw-row setpoint. Temporary available-count overshoot is acceptable after a
refresh wave; the runtime should not burn LLM/discovery work trying to maintain
exact equality at every tick. The hard bound is the raw material ceiling.

### 3.2 Replenishment Contract

When `pool_available_count < pool_target_count`, runtime replenishment must
continue for any enabled source whose source-available count is below its
target and whose raw-ceiling quota still has headroom.

Example:

```text
pool_target_count = 300
effective source targets = {bilibili: 300}
pool_available_count = 246
source-available counts = {bilibili: 246}
source raw material counts = {bilibili: 300}
source raw ceiling quotas = {bilibili: 600}
raw headroom = {bilibili: 300}
```

Expected plan:

```python
[(["search", "related_chain", "trending", "explore"], 54)]
```

The exact requested limit can still be constrained by `discovery_limit` and the
existing oversampling logic, but the plan must not be empty.

The requested source deficit must be clamped by raw-ceiling headroom:

```python
requested = min(
    source_available_target[source] - source_available_count[source],
    source_raw_ceiling_quota[source] - source_raw_count[source],
)
```

If available is below target but raw headroom is zero, the plan must be empty.
That state means inventory diversity is the binding constraint at the configured
storage/cost ceiling. The runtime should wait for consumption, stale/dislike
maintenance, or profile/topic changes to reopen raw headroom rather than
discover and immediately suppress more rows.

### 3.3 Source Quota Contract

Source quota enforcement must protect the configured source mix without using
raw source inventory as a hard stop for frontend availability.

For enabled optional sources:

- If XHS/Douyin/YouTube are enabled and under their source-available target,
  their producer loops remain responsible for their deficits.
- Bilibili discovery must not steal quota that belongs to enabled optional
  sources.
- Disabled sources remain removed from the effective share map.

For Bilibili-only setups:

- Bilibili keeps the full available target.
- Bilibili discovery continues until the available count reaches the target.

### 3.4 Cap Enforcement Contract

The runtime must keep more raw rows than `pool_target_count` when doing so is
necessary to produce `pool_target_count` available rows after the topic window.

The cap enforcement path must not immediately suppress all raw rows above
`pool_target_count` if frontend availability remains below target.

Cap enforcement must use two separate concepts:

- `pool_target_count`: frontend available-count target.
- `raw_material_ceiling`: raw fresh inventory cap, including pending and
  currently unopenable rows.

The cap path must not pass `_source_target_counts()` directly into raw source
trimming. Source targets are available-count targets; using them as raw quotas
recreates the deadlock.

Required behavior:

- If `pool_available_count < pool_target_count`, source-overflow trim must not
  suppress raw rows just because raw source inventory exceeds the available
  source target.
- Raw trim may run only against `raw_material_ceiling` and raw ceiling quotas,
  not against `pool_target_count`.
- Raw ceiling quotas should be derived from the same source shares but scaled to
  the raw ceiling. Example for Bilibili-only target 300:

```text
source available target = {bilibili: 300}
source raw ceiling quota = {bilibili: 600}
```

- In a multi-source target 300 with `bilibili:xiaohongshu:douyin = 8:1:1`, raw
  ceiling 600 maps to approximate raw quotas `{bilibili: 480, xiaohongshu: 60,
  douyin: 60}`.
- `reactivate_under_quota_pool_sources()` must use source-available deficits for
  choosing what to revive, but must not reactivate beyond remaining raw-ceiling
  capacity.
- `trim_pool_to_target_count()` must not be called with
  `target=pool_target_count` for this path. Either call it with
  `target=raw_material_ceiling` or replace it with a clearer
  `trim_pool_to_raw_ceiling(...)` helper.
- `trim_pool_source_overflow()` may remain raw-count based only if it uses the
  same all-raw-material source count and receives raw ceiling quotas. It must
  not receive frontend available source targets or a linkable-only source count
  for raw-ceiling enforcement.
- Raw-ceiling trims are allowed to suppress pending or currently unopenable XHS
  rows. This is the intended design: producer-stop prevents more over-fetch,
  and trim enforces the hard storage/cost ceiling. A later rediscovery or
  source-balance reactivation may move the row back to `fresh` once it is
  linkable and capacity is available.
- Raw-ceiling trims must shed least-servable rows first: non-linkable rows
  before linkable rows, then non-ready rows before ready rows, then the existing
  relevance/recency ranking inside each tier. Enforcing the ceiling must not
  suppress an openable row while an unopenable row from the same trim candidate
  set remains.

Cap enforcement should still:

- stop discovery when `pool_available_count >= pool_target_count`;
- apply stale, dislike, and topic overflow maintenance;
- prevent unbounded growth with the required raw material ceiling.

## 4. Backend Design

### 4.1 Add Source-Available Counting

Add a database method that mirrors the public availability count but groups by
source family.

Recommended API:

```python
def count_pool_available_candidates_by_source(
    self,
    *,
    max_per_topic_group: int = 3,
    xhs_self_nickname: str = "",
) -> dict[str, int]:
    ...
```

Requirements:

- Call `_ensure_fresh_read()`.
- Use the same readiness gates as `count_pool_candidates()`.
- Use the same recent-view filtering.
- Use `_is_linkable_pool_source(...)`.
- Use `_pool_source_family(...)`.
- Apply the same `ROW_NUMBER() OVER (PARTITION BY topic_group)` window when
  `max_per_topic_group > 0`.
- Keep the query projection narrow; it only needs fields required for counting,
  recent-view checks, linkability, and source-family grouping.

The old `count_pool_candidates_by_source()` can remain for raw/linkable source
diagnostics and for pool distribution snapshots. It must not be used with
frontend source targets for replenishment or cap enforcement.

Raw-ceiling headroom and raw-ceiling trim must not use
`count_pool_candidates_by_source()` because that method excludes unopenable XHS
rows. Add separate raw-material count helpers, for example:

```python
def count_pool_raw_material_candidates(self) -> int:
    ...

def count_pool_raw_material_by_source(self) -> dict[str, int]:
    ...
```

Requirements:

- Count fresh, not-disliked, not-recommended rows.
- Exclude recently viewed rows.
- Group by `_pool_source_family(...)`.
- Do not require `pool_expression`, `pool_topic_label`, `style_key`,
  `topic_group`, or `_is_linkable_pool_source(...)`.

Raw ceiling trim and raw headroom decisions must use this all-material row set.
Linkable-only source counts may remain for diagnostics and pool snapshots.

### 4.2 Update Replenishment Planning

`ContinuousRefreshController._build_source_replenishment_plan()` should compute
source deficits from source-available counts, then clamp those deficits by raw
ceiling headroom.

Required behavior:

```python
source_available_counts = database.count_pool_available_candidates_by_source(
    xhs_self_nickname=self._xhs_self_nickname()
)
source_available_targets = self._source_target_counts()
source_raw_counts = database.count_pool_raw_material_by_source()
source_raw_ceiling_quotas = self._source_raw_ceiling_quotas()

available_deficit = (
    source_available_targets[source] - source_available_counts.get(source, 0)
)
raw_headroom = (
    source_raw_ceiling_quotas[source] - source_raw_counts.get(source, 0)
)
requested = max(0, min(available_deficit, raw_headroom))
```

`_build_source_replenishment_plan()` and `_source_deficit()` should call a
single shared helper, such as `_source_requested_count(source)`, so Bilibili
planning and optional-source producers cannot drift into different clamp logic.

If the database method is missing on older test doubles, the controller may
fall back to `count_pool_candidates_by_source()` for compatibility. The fallback
must log a warning so production regressions do not silently restore the old
deadlock or reopen the XHS pending-inventory hole. Production `Database` must
expose the new raw-material method.

### 4.3 Update Source Deficit Helpers

`_source_deficit()` should use the same source-available counts and the same
raw-ceiling headroom clamp, because XHS, Douyin, and YouTube producers also
need to know whether the frontend-visible quota is under-filled and whether
they are allowed to add more raw material.

This avoids a future version of the same bug where optional producers stop
because raw rows exist but frontend availability is still below target.

This has an intentional side effect: optional sources with many pending or
unopenable rows may keep showing available deficits until their raw headroom is
exhausted. Once raw headroom reaches zero, producer loops must stop requesting
more for that source until consumption or maintenance reopens capacity.

### 4.4 Update Cap Enforcement

`ContinuousRefreshController._enforce_pool_cap()` must be revised so source
target counts are not reused as raw quotas.

Required structure:

1. Compute `pool_available` through `count_pool_candidates()`.
2. Compute `source_available_targets = _source_target_counts()`.
3. Compute `raw_material_ceiling = _raw_material_ceiling()`.
4. Compute `source_raw_ceiling_quotas` by distributing
   `raw_material_ceiling` across effective source shares.
5. Reactivate suppressed items only for source-available deficits, and only
   while raw ceiling capacity remains.
6. Run source overflow trim only with `source_raw_ceiling_quotas`.
7. Run total raw trim against `raw_material_ceiling`, keyed by raw count. Calling
   the raw trim unconditionally with `target=raw_material_ceiling` is acceptable
   because the database helper should early-return when raw rows are already
   within the ceiling; preserving the old `pool_available > pool_target_count`
   gate is not acceptable.
8. Return `pool_available >= pool_target_count`.

The controller may still skip discovery when the available target is met. It
must not trim raw rows down to the available target.

### 4.5 Pool Snapshot Semantics

`discovery/pool_snapshot.py` may continue using
`count_pool_candidates_by_source()` for raw distribution diagnostics if that is
deliberate. The spec requires the implementation to document this choice in a
code comment or test name so future readers do not confuse the snapshot's raw
source counts with replenishment's source-available counts.

### 4.6 Keep Runtime Status Stable

`pool_available_count` already uses `count_pool_readiness()["available"]`, which
delegates to `count_pool_candidates()`. That public field should keep meaning:

```text
frontend-visible available candidates
```

No frontend API rename is needed.

### 4.7 Improve Diagnostics

When the controller sees `pool_available_count < pool_target_count` but produces
an empty replenishment plan, log the reason with both counts:

```text
pool refill below target but no source plan:
available=246 target=300 source_available={bilibili:246} source_raw={bilibili:300}
targets={bilibili:300}
```

This log should make source-quota deadlocks visible without requiring ad hoc
SQLite inspection.

Also log cap-path raw ceiling decisions when suppression occurs:

```text
enforce_pool_cap: raw_ceiling=600 raw_count=642 suppressed=42
source_raw_ceiling={bilibili:600}
```

## 5. Frontend Contract

No UI semantics change is needed beyond documentation:

- `pool_available_count` remains the number shown as "还有 N 条可换".
- `pool_pending_count` remains "正在整理成可换内容".
- Frontend must not display raw material as "可换".

After this fix, the frontend should be able to naturally reach:

```text
还有 300 条可换
```

when discovery can find enough diverse, ready, linkable candidates.

## 6. Implementation Touchpoints

| File | Expected change |
|------|-----------------|
| `src/openbiliclaw/storage/database.py` | Add `count_pool_available_candidates_by_source()` with count/load parity and topic-window support; add `count_pool_raw_material_candidates()` / `count_pool_raw_material_by_source()` or equivalent all-raw counts; make raw-ceiling trim helpers use the same all-raw selector, including pending/non-linkable rows, and trim least-servable rows first; update `reactivate_under_quota_pool_sources()` or its caller so reactivation uses source-available deficits and respects raw-ceiling capacity |
| `src/openbiliclaw/runtime/refresh.py` | Use a shared source-request helper that combines source-available counts plus all-raw-material headroom for `_build_source_replenishment_plan()` and `_source_deficit()`; add `_raw_material_ceiling()` and raw-ceiling source quotas; update `_enforce_pool_cap()` so it no longer passes available targets into raw trim functions |
| `src/openbiliclaw/discovery/pool_snapshot.py` | Keep raw source-count semantics deliberately, or switch to available source counts with matching tests; do not leave the choice incidental |
| `tests/test_storage.py` | Cover source-available counting with repeated topics, missing copy/classification, recent views, source family grouping, source-count parity, and raw-ceiling/source-overflow trimming |
| `tests/test_refresh_runtime.py` | Cover the 246/300-style deadlock; update `_FakeDatabase` with separate `source_available_counts` and raw `source_counts`; cover raw-ceiling quotas passed by `_enforce_pool_cap()` |
| real-DB regression test | Seed the local failure shape and run plan/discover/enforce or enforce/plan cycle against real `Database`, proving raw cap enforcement does not suppress the new slack back to 300 |
| `docs/modules/config.md` | Define `pool_target_count` as frontend available target, not raw hard cap |
| `docs/modules/runtime.md` | Clarify available/raw/pending and source-available refill semantics |
| `docs/modules/discovery.md` | Document replenishment by frontend-visible source deficit |
| `docs/modules/recommendation.md` | Document raw pool may exceed target to produce target available count |
| `docs/changelog.md` | Add a short PR entry |

Architecture diagrams are not required unless the implementation introduces a
new module or external dependency. This is a counting/replenishment contract
change within existing modules.

## 7. Tests

### 7.1 Database Tests

Add focused tests for `count_pool_available_candidates_by_source()`:

1. Counts only rows that pass the same readiness gates as
   `count_pool_candidates()`.
2. Collapses Bilibili strategies (`search`, `related_chain`, `trending`,
   `explore`) into `bilibili`.
3. Collapses XHS/Douyin/YouTube source families the same way
   `count_pool_candidates_by_source()` does.
4. Excludes XHS rows without `xsec_token`.
5. Excludes recently viewed rows.
6. Applies `max_per_topic_group=3`, so 10 ready rows in one topic count as 3.
7. With `max_per_topic_group=0`, counts the same ready rows without the topic
   window.
8. In a mixed-source pool, asserts:

```python
sum(db.count_pool_available_candidates_by_source().values())
== db.count_pool_candidates()
```

9. Raw source overflow trim uses raw ceiling quotas, not available source
   targets. A Bilibili-only pool with 354 raw rows, 246 available rows, target
   300, and raw ceiling 600 must not suppress anything.
10. Total raw trim uses `raw_material_ceiling`; with raw ceiling 600 and 642 raw
    rows, it suppresses 42 rows while preserving available candidates as much as
    the ranking allows.
11. XHS rows without `xsec_token` count toward raw-material source headroom even
    though they do not count as available or linkable source inventory. Example:
    source-available XHS = 12, linkable raw XHS = 12, unopenable pending XHS =
    60, raw ceiling quota XHS = 60. Expected source request is 0, not 18.
12. Raw-ceiling source trim suppresses pending/non-linkable XHS rows when
    all-material raw inventory exceeds the source raw-ceiling quota. Example:
    source raw ceiling XHS = 60; source raw material has 50 linkable XHS rows
    and 60 non-linkable pending XHS rows. Expected trim suppresses 50
    non-linkable pending rows and preserves all 50 linkable rows, rather than
    ignoring the pending rows and leaving raw material at 110 or suppressing
    linkable rows while pending rows remain.
13. Raw-side parity holds:

```python
sum(db.count_pool_raw_material_by_source().values())
== db.count_pool_raw_material_candidates()
```

The parity test must include at least one non-linkable XHS row and one recently
viewed row, proving linkability is ignored but the viewed-window exclusion is
shared.

### 7.2 Refresh Runtime Tests

Add or update tests for:

1. Bilibili-only config:

```text
pool_target_count = 300
count_pool_candidates() = 246
count_pool_available_candidates_by_source() = {bilibili: 246}
count_pool_candidates_by_source() = {bilibili: 300}
```

Expected:

```python
_build_source_replenishment_plan()
== [(["search", "related_chain", "trending", "explore"], 54)]
```

2. Enabled optional source under available target:

```text
targets = {bilibili: 240, xiaohongshu: 30, douyin: 30}
source_available = {bilibili: 240, xiaohongshu: 12, douyin: 30}
```

Expected:

- Bilibili plan remains empty.
- XHS producer deficit is 18.

3. Source raw count at target but source-available below target must not return
`below_threshold`.

4. `_FakeDatabase` must distinguish:

```python
source_available_counts = {"bilibili": 246}
source_raw_counts = {"bilibili": 300}
```

Existing tests that currently feed only `source_counts` into the raw-by-source
path need migration to separate available-by-source and all-raw-material source
counts.

5. `_enforce_pool_cap()` passes source raw ceiling quotas to
`trim_pool_source_overflow()`, not source available targets. For target 300,
Bilibili-only config expects `{bilibili: 600}`, not `{bilibili: 300}`.

6. Raw ceiling headroom clamps replenishment:

```text
pool_target_count = 300
source_available = {bilibili: 270}
source_raw = {bilibili: 600}
source_raw_ceiling = {bilibili: 600}
```

Expected:

```python
_build_source_replenishment_plan() == []
_source_deficit("bilibili") == 0
```

7. When raw headroom reopens, replenishment resumes and clamps by the smaller
available/raw side:

```text
source_available = {bilibili: 250}
source_raw = {bilibili: 570}
source_raw_ceiling = {bilibili: 600}
```

Expected Bilibili requested deficit is 30, not 300 or 0.

### 7.3 Regression Verification Against Local Shape

Seed the local failure mode with a real `Database` when possible:

```text
300 ready Bilibili rows
176 distinct topic groups
12 groups above 3 rows
available with topic window = 246
```

Expected:

- runtime sees the pool as below target;
- Bilibili source deficit is positive;
- a refresh plan is generated;
- after a simulated discovery insert and `_enforce_pool_cap()`, raw rows are
  not suppressed back to 300 while available remains below target.

The fake-only suite is not sufficient for this regression, because the current
fake trim methods are no-ops and cannot catch churn caused by raw cap
enforcement.

## 8. Acceptance Criteria

- With only Bilibili enabled and enough diverse source results available, the
  frontend can reach `pool_available_count = 300`.
- A pool with 300 raw/ready Bilibili rows but only 246 windowed available rows
  does not stop replenishment.
- `pool_target_count` is not treated as a raw `fresh` row hard cap.
- Raw material may exceed 300 when needed to produce 300 frontend-visible rows.
- Raw material is bounded by the required raw ceiling, defaulting to
  `max(pool_target_count * 2, pool_target_count + 120)`.
- `_enforce_pool_cap()` does not call raw source trimming with
  `_source_target_counts()` quotas.
- Replenishment does not request rows for a source whose raw inventory is
  already at its raw-ceiling quota, even if that source is still below its
  available-count target.
- Disabled source shares still do not reserve quota.
- Enabled optional sources still receive their configured available-count share.
- Existing frontend copy remains truthful: "可换" always means
  `pool_available_count`.

## 9. Rollout Notes

This change is backend-first and should be safe for existing frontends:

1. Existing frontends already display `pool_available_count`.
2. Backend replenishment will keep filling until that public number reaches at
   least the target.
3. Users may see raw pool inventory exceed 300 in diagnostics; this is expected
   and bounded by the raw ceiling.
4. If discovery cannot find enough diverse topics before the raw ceiling is
   reached, the available count may remain below 300. In that state the runtime
   should stop requesting more rows for that full raw source and resume only
   after consumption, stale/dislike cleanup, or profile/topic changes reopen
   raw headroom.

The implementation should be one PR because the behavior is tightly coupled:
database count parity, runtime replenishment, and docs need to land together.
