# 2026-06-04 — Unified Discovery Candidate Pool Spec

## 0. Scope

This spec unifies the **discovery module** after source-specific candidate
fetching.

The intended contract is:

```text
Different sources differ only in how they find and normalize content.
After that, all candidates enter one pending-evaluation pool and are judged
by one evaluator against the user's Soul profile.
```

Affected sources:

| Source | Current fetch path | Target alignment |
|--------|--------------------|------------------|
| Bilibili | Backend API strategies: search / trending / related_chain / explore | Fetch-only producer, then shared pending pool |
| Xiaohongshu | Extension/API writes rich notes directly into `content_cache` | Extension/API writes pending candidates, then shared evaluator |
| Douyin | Runtime producer + direct/plugin search/hot/feed strategy | Fetch-only producer, then shared pending pool |
| YouTube | Runtime producer + yt_search / yt_trending / yt_channel strategy | Fetch-only producer, then shared pending pool |
| Generic Web | Adapter exists but is not in steady-state refresh | May join the same pool when scheduled later |

Out of scope:

- User behavior cognition / profile-event ingestion.
- Recommendation ranking UI changes.
- Changing source quotas or pool target semantics.
- Rewriting `RecommendationEngine.serve()`.
- Making every source fully functional in the same implementation slice; this
  spec defines the target architecture.

## 1. Problem

The project already converges all sources into `content_cache` before serving
recommendations, but the path into that cache is not uniform.

Bilibili, Douyin, and YouTube formal discovery paths usually evaluate candidates
inside each strategy before the engine caches them. Xiaohongshu is different:
the extension/API caches notes first, then `RecommendationEngine.classify_pool_backlog()`
later fills `relevance_score`, `topic_group`, and `style_key`.

That works operationally, but it creates three design problems:

1. Source code paths decide too much. Fetching content and judging content are
   mixed together.
2. Xiaohongshu bypasses discovery-engine post-processing before cache, including
   some source-independent dedupe and diversity controls.
3. Batch evaluation currently assumes one `source_platform` per batch. A truly
   mixed Bilibili / Xiaohongshu / Douyin / YouTube evaluator cannot use the
   current prompt shape safely because it passes batch-level platform context.

The desired discovery architecture should separate:

```text
source-specific fetching
source-agnostic candidate evaluation
source-agnostic cache admission
```

## 2. Goals

1. All source fetchers emit the same candidate DTO.
2. All candidates are first written into a pending-evaluation pool.
3. One discovery evaluator drains mixed-source batches from that pool.
4. Evaluation uses the same Soul profile, negative exemplars, score schema, and
   topic/style/franchise schema for every platform.
5. Only evaluated and accepted candidates are written into `content_cache`.
6. `RecommendationEngine.classify_pool_backlog()` becomes a legacy/recovery
   fallback, not the normal Xiaohongshu path.

## 3. Target Data Flow

```text
ContinuousRefreshController
  -> computes source deficits / raw headroom
  -> asks each active source producer to fetch candidates

Source producers
  -> BilibiliSearchProducer
  -> BilibiliTrendingProducer
  -> BilibiliRelatedProducer
  -> BilibiliExploreProducer
  -> XhsExtensionProducer
  -> DouyinSearchHotFeedProducer
  -> YoutubeSearchTrendingChannelProducer
  -> enqueue normalized candidates

PendingDiscoveryCandidateStore
  -> source-aware dedupe
  -> recently-viewed exclusion
  -> status / retry / stale management

DiscoveryEvaluator
  -> builds mixed-source batches
  -> calls shared LLM batch evaluation
  -> writes evaluated fields back to pending store

DiscoveryPoolWriter
  -> applies acceptance policy
  -> applies source-agnostic diversity/cache guards
  -> writes accepted candidates to content_cache

RecommendationEngine
  -> precompute_pool_copy()
  -> precompute_delight_scores()
  -> serve()
```

## 4. Candidate Data Model

Introduce a durable pending table, tentatively:

```text
discovery_candidates
```

Required columns:

| Column | Meaning |
|--------|---------|
| `id` | Internal candidate id used for LLM result matching |
| `candidate_key` | Source-aware dedupe key, e.g. `youtube:VIDEO_ID` |
| `source_platform` | `bilibili`, `xiaohongshu`, `douyin`, `youtube`, `web` |
| `source_strategy` | `search`, `trending`, `xhs-extension-search`, `yt_channel`, etc. |
| `content_type` | `video`, `note`, `article`, `page`, or empty when unknown |
| `content_id` | Platform-native id |
| `content_url` | Clickable canonical URL |
| `title` | Display title |
| `author_name` | Canonical author/creator name |
| `description` | Short description/text excerpt |
| `cover_url` | Optional cover URL |
| `duration` | Seconds, when meaningful |
| `view_count` | Platform metric, optional |
| `like_count` | Platform metric, optional |
| `tags_json` | Normalized tags |
| `source_context` | Fetch context, e.g. search query, hot/feed/channel |
| `candidate_tier` | `primary` or `backfill` |
| `raw_payload_json` | Source-specific raw metadata for debugging |
| `status` | Pending lifecycle status |
| `relevance_score` | Evaluator output |
| `relevance_reason` | Evaluator output |
| `topic_key` | Fine topic label |
| `topic_group` | Coarse topic group |
| `style_key` | Shared style taxonomy |
| `franchise_key` | Shared franchise/IP key |
| `failure_reason` | Last failure or rejection reason |
| `discovered_at` | First seen timestamp |
| `last_seen_at` | Last source refresh timestamp |
| `evaluated_at` | Evaluation timestamp |
| `cached_at` | Time accepted into `content_cache` |

Status values:

```text
pending_eval
evaluating
evaluated
cached
rejected_low_score
rejected_duplicate
rejected_cache_admission
rejected_recently_viewed
rejected_franchise_quota
failed_eval
stale
```

`content_cache` remains the formal recommendation pool. The pending table is
raw/evaluation staging only.

## 5. Source Producer Contract

Each source producer should have one responsibility: fetch and normalize
candidates.

Conceptual protocol:

```python
class DiscoveryCandidateProducer(Protocol):
    source_platform: str
    source_strategy: str

    async def produce(
        self,
        profile: SoulProfile,
        *,
        limit: int,
        pool_snapshot: PoolDistributionSnapshot | None = None,
    ) -> list[DiscoveredContent]: ...
```

Producer rules:

- Must populate `source_platform`, `source_strategy`, `content_id`, and
  `content_url` when available.
- Should populate title, author, cover, description, duration, and metrics.
- Must not call content evaluation.
- Must not write directly to `content_cache`.
- May attach `source_context` or source-specific raw payload.
- May return low-confidence candidates; the evaluator decides acceptance.

Current source mapping:

| Source | Target producer behavior |
|--------|--------------------------|
| Bilibili search/trending/related/explore | Keep API/search logic, remove final LLM filtering from producer |
| Xiaohongshu extension/API | Convert returned notes into pending candidates, not direct `content_cache` rows |
| Douyin search/hot/feed | Keep plugin/direct fetch bridge, return pending aweme candidates |
| YouTube search/trending/channel | Keep scraper logic, return pending YouTube candidates |
| Web adapter | Can enqueue extracted page items once scheduled |

## 6. Mixed Batch Evaluation

The evaluator is where the agent judges whether the user is likely to like a
candidate.

Current code has two similar evaluation paths:

- `ContentDiscoveryEngine.evaluate_content_batch()`
- `RecommendationEngine.classify_pool_backlog()`

Target state:

```text
DiscoveryEvaluator.evaluate_mixed_batch(profile, candidates)
```

Inputs:

- Soul profile summary.
- Recent negative exemplars.
- A mixed batch of pending candidates.
- Per-item `source_platform`, `source_strategy`, `content_type`, and
  `source_context`.

Outputs per candidate:

```json
{
  "candidate_id": "...",
  "content_id": "...",
  "score": 0.82,
  "reason": "...",
  "topic_key": "...",
  "topic_group": "...",
  "style_key": "deep_dive",
  "franchise_key": ""
}
```

### 6.1 Prompt Change

Current batch prompt passes a batch-level platform:

```text
<source_platform>bilibili</source_platform>
<content_batch>[...]</content_batch>
```

Mixed-source evaluation must move platform context into each item:

```json
[
  {
    "candidate_id": "c1",
    "source_platform": "bilibili",
    "source_strategy": "search",
    "content_type": "video",
    "title": "..."
  },
  {
    "candidate_id": "c2",
    "source_platform": "xiaohongshu",
    "source_strategy": "xhs-extension-search",
    "content_type": "note",
    "title": "..."
  }
]
```

Prompt rule:

```text
Use source_platform to understand content format and available metadata.
Do not lower or raise preference score merely because the content comes from
a different platform. Score against the user's Soul profile with one rubric.
```

The batch-level `<source_platform>` can be removed or set to `mixed`.

### 6.2 Batch Selection

The evaluator should drain pending candidates with a fair mixed policy:

1. Prefer source families currently below available quota.
2. Interleave sources so one platform does not fill the whole batch.
3. Preserve age priority so old pending items are not stranded.
4. Avoid recently viewed keys before LLM evaluation.
5. Apply source-aware dedupe before evaluation.
6. Keep batch size compatible with existing LLM cost controls, initially 30.

Example:

```text
Batch size 30:
  Bilibili: 12
  Xiaohongshu: 6
  Douyin: 6
  YouTube: 6
```

The exact split should be driven by source deficits and pending availability,
not a hard per-source ratio.

## 7. Capacity Contract

The pending pool must not weaken `pool_target_count`.

`pool_target_count` continues to mean:

```text
target number of frontend-available recommendation candidates
```

A candidate is frontend-available only after it has been evaluated, accepted,
written to `content_cache`, classified with non-empty `style_key` /
`topic_group`, has `pool_expression` / `pool_topic_label`, is linkable, is not
recently viewed, and is inside the same serve-window diversity gates used by
`get_pool_candidates()`.

### 7.1 Discovery Stop Rule

When:

```text
pool_available_count >= pool_target_count
```

the runtime must not start new source discovery:

- Do not call Bilibili / Xiaohongshu / Douyin / YouTube producers.
- Do not enqueue new pending candidates from scheduled refresh.
- Do not run normal LLM evaluation for pending candidates.

Non-discovery maintenance may still run:

- expire stale pending candidates;
- enforce raw/pending ceilings;
- prefetch covers;
- repair legacy rows when explicitly requested.

This preserves the user's expectation: once the recommendation pool is full,
background discovery stops spending source/API/LLM budget.

### 7.2 Pending Raw Material Ceiling

Adding a pending pool creates a second inventory surface. The raw material
ceiling must count both:

```text
fresh/evaluated material in content_cache
+ pending/evaluating/evaluated-but-not-cached rows in discovery_candidates
```

Otherwise, the system could stop at the formal recommendation-pool cap while
quietly accumulating thousands of unevaluated source candidates.

Default raw ceiling should mirror the existing pool slack contract:

```text
raw_material_ceiling = max(pool_target_count * 2, pool_target_count + 120)
```

Source-specific raw headroom should also include pending candidates for that
source family. Example:

```text
source_raw_count("xiaohongshu")
  = content_cache raw xhs rows
  + discovery_candidates pending/evaluating/evaluated xhs rows
```

### 7.3 Replenishment Request Formula

For each source family, requested discovery count should be bounded by all three
limits:

```text
requested_count = min(
  available_deficit_by_source,
  raw_headroom_by_source,
  global_available_deficit
)
```

Where:

```text
global_available_deficit = max(0, pool_target_count - pool_available_count)
```

If `global_available_deficit == 0`, `requested_count` is zero for every source.

### 7.4 Evaluation Drain Rule

The mixed evaluator should also be capacity-aware.

It may drain pending candidates only when:

```text
pool_available_count < pool_target_count
```

and it should stop admitting evaluated candidates once the current deficit is
filled. The admission writer should re-read current availability before writing
each batch because another refresh task may have filled the pool concurrently.

If the pool becomes full while a batch is already being evaluated:

- complete the in-flight LLM call;
- persist evaluation results in `discovery_candidates`;
- admit only up to the remaining capacity;
- leave the rest as `evaluated` for a later deficit, or mark stale if they age
  out before use.

### 7.5 Runtime Status

Runtime status should distinguish:

| Metric | Meaning |
|--------|---------|
| `pool_available_count` | Candidates the frontend can immediately serve |
| `pool_raw_count` | Total raw material across `content_cache` and pending candidates |
| `pool_pending_eval_count` | Pending candidates waiting for LLM evaluation |
| `pool_evaluated_pending_count` | Evaluated candidates not yet admitted |

The UI should keep using `pool_available_count` for "可换" count. Pending
candidates are diagnostic/background inventory, not user-visible capacity.

## 8. Acceptance Policy

Evaluation produces neutral scores. Admission to `content_cache` applies a
separate policy:

```text
score threshold
recently viewed exclusion
pool-wide franchise quota
style/topic caps
source raw headroom
linkability requirements
```

Default thresholds should preserve current behavior:

| Strategy family | Initial threshold |
|-----------------|------------------|
| search-like | 0.65 |
| trending-like | 0.60 |
| related-chain | 0.65 plus shared related-chain bonuses if retained |
| explore | lower threshold or explicit novelty adjustment |
| plugin/feed backfill | 0.60-0.65 depending on source context |

Important: source-specific discovery intent may still matter. For example,
`explore` intentionally accepts cross-domain candidates, while `related_chain`
may carry seed/depth context. That intent should be represented as structured
candidate metadata and consumed by the shared acceptance policy, not hidden
inside a source strategy.

## 9. Content Cache Admission

Accepted candidates are converted to `DiscoveredContent.to_cache_kwargs()` and
written through the same cache writer.

Target shared post-evaluation steps:

1. Merge duplicate candidates by source-aware identity.
2. Normalize topic groups/keys where embedding service is available.
3. Apply pool snapshot rerank.
4. Compress repeated topics within the admitted window.
5. Enforce per-batch franchise/style caps.
6. Enforce pool-wide franchise/source/topic guards.
7. Write accepted rows to `content_cache`.
8. Warm MMR embeddings best-effort.

This makes Xiaohongshu follow the same post-evaluation path as Bilibili instead
of relying on recommendation backlog classification.

## 10. Source-Specific Notes

### 10.1 Bilibili

Current Bilibili strategies already do useful candidate ordering and context
construction. Keep that logic, but split it before the final LLM evaluation.

Target:

```text
BilibiliStrategy.fetch_candidates()
  -> pending pool
  -> mixed evaluator
  -> shared cache admission
```

### 10.2 Xiaohongshu

Current `_cache_xhs_notes()` should no longer write normal rich notes directly
to `content_cache` in the primary flow.

Target:

```text
extension observed-urls / task-result
  -> validate note metadata
  -> filter self-authored notes
  -> backfill/preserve xsec_token
  -> enqueue pending XHS candidates
  -> mixed evaluator
  -> shared cache admission
```

XHS linkability still matters. Candidates without usable `xsec_token` may remain
pending or be evaluated but not admitted until the URL is upgraded. The first
implementation should prefer keeping them pending to avoid spending LLM tokens
on currently unservable rows.

Bootstrap profile task results may still propagate behavioral events for the
soul/profile pipeline. That is separate from discovery candidate admission.

### 10.3 Douyin

The plugin/direct bridge can stay source-specific. The change is that
`DouyinDirectStrategy` should stop running its own evaluation and should enqueue
normalized aweme candidates for the shared evaluator.

Diagnostic CLI flags such as `--no-cache` or `--no-evaluate` may continue to
exist, but they should be clearly marked as preview/smoke paths outside the
production recommendation pipeline.

### 10.4 YouTube

YouTube search/trending/channel strategies should keep scraper and budget logic
but stop evaluating inside each strategy. The runtime ledger remains a fetch
budget, not an evaluation/admission budget.

### 10.5 Generic Web

The web adapter already extracts `DiscoveredContent`. Once steady-state source
recipes are scheduled, extracted web items should enter the same pending pool.

## 11. Relationship To Recommendation Backlog Classification

`RecommendationEngine.classify_pool_backlog()` should remain as a compatibility
and repair path for:

- Legacy `content_cache` rows missing evaluation fields.
- Manually imported rows.
- Rows created before this migration.
- Emergency recovery if pending-pool admission fails.

It should not be the normal path for Xiaohongshu or any new source.

## 12. Error Handling

- Candidate enqueue failures should not block source fetch loops; log and
  continue.
- Batch-level LLM/provider evaluation failure, malformed batch length, or parser
  failure should release claimed rows back to `pending_eval` without consuming
  per-row retry budget; only item-attributable failures may use retry count and
  eventually mark `failed_eval`.
- Rate-limit failures should leave candidates `pending_eval` or move them to a
  cooldown state, not reject them as low score.
- If the LLM omits a candidate id, mark only that candidate failed; do not
  discard the whole batch when other ids are usable.
- If mixed batch parsing fails, retry once with smaller batches. A final
  fallback may group by source platform to preserve progress.
- Candidates older than a configured TTL should become `stale` unless refreshed
  by a source producer.
- If the recommendation pool is already full, pending candidates should not be
  evaluated merely to drain the queue. They should wait for available deficit or
  expire by TTL.

## 13. Migration Plan

1. Add pending candidate table and storage helpers.
2. Add mixed-source evaluator prompt support while keeping old evaluator API.
3. Route Xiaohongshu rich notes into pending candidates instead of direct cache
   for new rows.
4. Split Bilibili strategy fetch from LLM evaluation, preserving old behavior
   behind a compatibility wrapper during transition.
5. Split Douyin and YouTube strategy fetch from LLM evaluation.
6. Add capacity-aware replenishment and evaluation drain gates that count both
   `content_cache` and pending candidates as raw material.
7. Add shared admission writer from evaluated candidates to `content_cache`.
8. Keep `classify_pool_backlog()` running for legacy rows until the backlog is
   drained reliably.
9. Remove or de-emphasize per-source evaluation paths once tests cover the
   unified evaluator.

## 14. Testing

Required tests:

- Candidate store dedupes by `source_platform + content_id`.
- XHS observed notes enqueue pending candidates and do not directly create
  available `content_cache` rows in the primary path.
- Mixed batch prompt includes per-item `source_platform` and no longer relies on
  a single batch-level platform.
- Mixed evaluator matches LLM results by `candidate_id`, not by positional order
  alone.
- Evaluated Bilibili and XHS candidates with equivalent score schema admit
  through the same writer.
- Recently viewed source-aware keys are skipped before evaluation.
- XHS candidates without linkable `xsec_token` do not become available.
- Douyin and YouTube producer budget tests still count fetch units correctly.
- Legacy `classify_pool_backlog()` still classifies old unevaluated cache rows.
- End-to-end: a batch containing Bilibili, XHS, Douyin, and YouTube candidates
  produces evaluated rows and writes only accepted candidates to `content_cache`.
- When `pool_available_count >= pool_target_count`, runtime does not call source
  producers and the evaluator does not drain pending candidates.
- Raw material counts include both `content_cache` rows and pending candidates.
- Admission writer stops after filling the current available deficit, even if a
  mixed evaluation batch returned more accepted items.

## 15. Acceptance Criteria

The implementation is complete when:

1. A new source candidate can enter recommendation discovery without adding a
   custom post-fetch evaluation path.
2. Bilibili, Xiaohongshu, Douyin, and YouTube candidates can coexist in the same
   pending-evaluation batch.
3. Evaluation output schema is identical for all platforms.
4. `content_cache` receives only evaluated/accepted candidates in the primary
   flow.
5. Recommendation serving does not need to know which discovery source path
   produced the content.
6. Existing source quotas and runtime status continue to report available and
   pending counts correctly.
7. Source discovery and normal LLM evaluation stop when the frontend-available
   pool has reached `pool_target_count`.

## 16. Non-Goals

- Do not make platform-specific content shapes identical. A video, note, and
  article may have different metadata; they only share the evaluation schema.
- Do not force every batch to include every enabled platform.
- Do not use platform source as a preference score bonus or penalty.
- Do not remove source-specific discovery logic such as Douyin signing,
  YouTube scraper budgets, or XHS token handling.
- Do not treat discovery candidates as behavioral cognition events. User
  behavior remains a separate event pipeline.
