# Local-First Inspiration Grounding Spec

## Goal

Reduce external search/API consumption and account-risk exposure in the
search-backed keyword inspiration flow by reusing existing discovery assets as
the first grounding source. External providers (`platform_sources`, Exa,
You.com) become gap-fill providers, not the default first move.

Current pipeline:

```text
like secondary interests -> coverage-aware sampling -> AI brainstorm
-> external/search grounding -> AI curator -> platform keyword lists
```

Target pipeline:

```text
like secondary interests -> coverage-aware sampling -> AI brainstorm
-> local grounding from existing discovery assets
-> external/search grounding only for local gaps
-> AI curator -> platform keyword lists
```

## Phasing

This spec is delivered in two explicit phases so "implemented" always has a
precise meaning:

**Phase 1 (the current implementation plan,
`docs/superpowers/plans/2026-07-03-local-first-inspiration-grounding.md`):**

- `LocalInspirationProvider` + provider chain + config default;
- evidence source: `content_cache` **only**;
- `discovery_interest_selection_ledger`, so sampled secondary interests are
  cooled down immediately instead of waiting for keyword rows or candidate
  yield to appear;
- relevance-scored DAO with CJK handling (section 2);
- provider attribution + budget accounting + `grounding_source` keyword
  metadata (section 4) — the metadata must land in Phase 1 because
  retroactive attribution is impossible and the 14-day gate needs the data
  to accumulate;
- ledger fields with the budget-aware `external_searches_saved` definition
  (section 5);
- report fields as empty-safe stubs (section 7).

**Phase 2 (before the 14-day gate decision):**

- remaining evidence sources: `discovery_candidates`,
  `discovery_keywords` × `discovery_keyword_yield`,
  `discovery_inspiration_probe_cache`;
- echo-chamber caps (per-`content_id` / per-`topic_label` / old-tail mixing,
  section 6);
- report substance: claim timing, source-interest distribution,
  interest-weight buckets, and the local-grounding duplicate-rate metric
  (sections 6–7).

## Non-Goals

- Do not write local grounding evidence into `discovery_candidates`.
- Do not add new browser/plugin tasks for Xiaohongshu or Zhihu.
- Do not add embedding retrieval as the first version. Use cheap local text
  evidence first; cached embeddings can be a later optimization.
- Do not hard-ban generic terms. Local evidence can still produce broad,
  platform-native terms; the AI curator and downstream yield decide whether
  they are useful.
- Do not replace Exa / You.com / platform source search entirely. The new
  behavior is local-first plus external gap-fill.
- Do not merge partial local evidence with external results. When local
  evidence is below the sufficiency floor it is discarded for that probe and
  the first-non-empty chain semantics are preserved. **Known limitation,
  accepted**: one good local preview can be thrown away; revisit only if the
  dry-run ledger shows a high near-miss rate.

## Problems

### P1: External search is spent even when the local database already has useful evidence

The system has rich local assets:

- admitted `content_cache` rows;
- evaluated / duplicate / recently-viewed `discovery_candidates`;
- `discovery_keywords` rows plus `discovery_keyword_yield`;
- `discovery_inspiration_probe_cache` and expansion history.

The current provider chain does not try these before calling networked
providers.

### P2: Risk-controlled external sources are used for inspiration even when they add little novelty

X/Twitter cookie replay and Bilibili/Douyin direct search are useful but risky.
If a probe can be grounded locally, spending risk budget on those sources is
unnecessary.

### P3: Existing content distribution is passed as aggregate hints, not concrete inspiration evidence

Coverage and pool distribution tell the planner what is overrepresented, but
they do not give the brainstorm/curator concrete local titles, tags, topics,
or successful query examples to riff from.

### P4: Gate/report cannot quantify saved searches

`keyword-inspiration-report` can compare cohorts, but it does not show how
many external searches were avoided by local evidence. Without that number,
we cannot tell whether local-first grounding is paying for itself.

## Design

### 1. Add a Local Inspiration Provider

Introduce `LocalInspirationProvider` in
`src/openbiliclaw/discovery/inspiration_provider.py` implementing the existing
`InspirationSearchProvider` protocol:

```python
class LocalInspirationProvider:
    def __init__(
        self,
        database: object,
        *,
        lookback_days: int = 30,
        min_results: int = 2,
        min_distinct_sources: int = 1,
    ) -> None:
        raise NotImplementedError

    def begin_stage(self) -> None:
        raise NotImplementedError

    def grounding_ledger(self) -> dict[str, object]:
        raise NotImplementedError

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        raise NotImplementedError
```

It reads local rows via a database DAO and maps them to `ExaPreviewItem`
without writing anything back. When no database is supplied at construction
time, the backend is skipped entirely (defensive default for callers that
cannot provide one).

Provider chain default becomes:

```python
("local_cache", "platform_sources", "exa", "you")
```

Aliases:

- `local_cache`
- `local`
- `cache`

### 2. Local Evidence DAO

Add `Database.search_local_inspiration_evidence(query, *, limit, lookback_days)`.

The DAO should return dictionaries with this shape:

```python
{
    "title": "独立游戏 机制拆解：地图叙事如何成立",
    "url": "https://www.bilibili.com/video/BVlocal1",
    "highlights": ["围绕独立游戏、关卡设计、叙事节奏的分析。"],
    "source_table": "content_cache",
    "source_platform": "bilibili",
    "content_id": "BVlocal1",
    "topic_label": "独立游戏机制",
    "created_at": "2026-07-03 10:00:00",
}
```

Evidence sources, in priority order:

1. `content_cache`: admitted content with `pool_status` not hard-deleted.
   **(Phase 1)**
2. `discovery_candidates`: rows in `cached`, `evaluated`,
   `rejected_duplicate`, or `rejected_recently_viewed`. **(Phase 2)**
3. `discovery_keywords` joined with `discovery_keyword_yield`: historical
   keywords that produced admitted content. **(Phase 2)**
4. `discovery_inspiration_probe_cache`: recent probe evidence, when present.
   **(Phase 2)**

**Relevance rules (required, not optional).** Naive any-token `LIKE` matching
ordered by recency returns recent-but-barely-related rows, and section 3's
sufficiency rule would then suppress a useful external search with junk
evidence. The DAO must therefore:

- **Tokenize with CJK support.** Split the query on
  whitespace/punctuation; for any spaceless CJK run of length ≥ 4, add its
  character 2-grams as additional tokens (LLM-brainstormed Chinese probes
  frequently contain no delimiters — without this the provider systematically
  misses).
- **Escape `LIKE` wildcards** (`%`, `_`, `\`) in tokens.
- **Score in Python, not SQL.** SQL selects a bounded recent candidate window
  (any-token match, recency-ordered, capped at ~200 rows); Python computes a
  per-row matched-token count over title+description.
- **Synthesize missing local URLs.** Phase 1 must not drop useful Bilibili
  rows just because older `content_cache` records have blank `content_url`.
  When `content_url` is blank and `bvid` is present, the DAO returns
  `https://www.bilibili.com/video/<bvid>` as the evidence URL.
- **Row quality floor:** when the query yields ≥ 2 tokens, a row must match
  at least 2 tokens (or contain the full query phrase) to count as evidence.
  One weak token match is not evidence.
- **Rank by relevance first:** matched-token count descending, then
  `created_at` descending. Recency is the tiebreaker, never the primary key.

FTS/embedding retrieval is intentionally deferred.

### 3. Local Sufficiency and Gap-Fill

`LocalInspirationProvider.search()` returns local previews only when evidence
is sufficient:

- at least `min_results` valid previews **that passed the row quality floor**;
- at least `min_distinct_sources` among `source_table/source_platform/topic`;
- after dedupe by URL/title.

If local evidence is insufficient, return `[]`. The existing fallback chain
will then try `platform_sources`, Exa, then You.com. Partial local evidence
below the floor is discarded for that probe (see Non-Goals).

This keeps the control rule simple and observable:

```text
local enough -> no external search
local not enough -> external gap-fill
```

### 4. Provider Attribution and Budget Accounting

Two accounting rules make local-first honest, and both need to know **which
provider actually served each probe**:

- `FallbackInspirationSearchProvider` exposes the backend alias of the
  provider that served the most recent `search()` call (e.g. an attribute
  `last_search_provider: str | None`, `None` when every provider returned
  empty). The planner's grounding loop issues searches sequentially, so
  reading it after each `await` is safe; if the loop ever becomes concurrent
  this must move into the return value.
- **Local hits do not consume the external grounding budget.** The stage
  budget (`inspiration_max_probe_searches_per_stage`) exists to bound network
  and risk exposure; a probe served by `local_cache` costs neither, so the
  planner does not count it against the budget.
- **Keyword provenance.** Branches record the serving alias for their probes,
  and generated keyword rows carry a `grounding_source` metadata value
  (`local_cache` / `platform_sources` / `exa` / `you` / `mixed` / `none`).
  This is persisted as a real `discovery_keywords.grounding_source` column,
  not as best-effort transient metadata; Phase 2's duplicate-rate metric and
  the gate report read this field. Landing it in Phase 1 lets the data
  accumulate before the gate.
- **Local hits are terminal.** Existing fallback augmentation is useful for
  risk-controlled platform source searches that return too few items, but it
  must not run after a sufficient `local_cache` hit. Otherwise a local hit can
  still spend external budget merely because it returned fewer rows than the
  requested limit.

### 5. Ledger Additions

Extend grounding ledger output with local-first accounting:

```json
{
  "local_hits": 18,
  "local_misses": 5,
  "external_searches_saved": 7,
  "local_sources": {
    "content_cache": 11,
    "discovery_candidates": 4,
    "keyword_yield": 3
  }
}
```

Definitions:

- `local_hits`: local provider calls that returned sufficient evidence.
- `local_misses`: local provider calls that returned `[]`.
- `local_sources`: evidence row counts by source table.
- `external_searches_saved`: **computed at the planner stage level, not by
  the provider** — a local hit only "saves" an external search that would
  actually have been issued under the remaining stage budget:

```text
external_searches_saved =
    min(local_hits, max(0, inspiration_max_probe_searches_per_stage
                            - external_searches_issued))
```

(The example above: 18 hits with budget 12 and 5 external searches issued
saves 7 — not 18.) Defining it as "local hits that prevented fallback" would
make it identically equal to `local_hits` and overstate savings.

### 6. Echo-Chamber Controls

The local provider must not just recycle the current pool forever. There is a
structural asymmetry to keep in mind: undercovered interests have little local
content, so they naturally miss and fall through to external search (good —
budget goes where novelty is needed); local hits therefore concentrate on
well-covered interests, exactly where evidence from the system's own pool adds
the least novelty and most easily produces keywords that re-find already-seen
content.

Controls:

- **(Phase 1)** tag keyword provenance with `grounding_source` (section 4) so
  the loop is measurable from day one;
- **(Phase 2)** report the **duplicate-rejection rate of local-grounded
  keywords vs external-grounded keywords** (`rejected_duplicate` /
  `rejected_recently_viewed` share per `grounding_source`). This is the
  direct evidence of whether the echo chamber is real, and more diagnostic
  than any per-row cap;
- **(Phase 2)** cap evidence per `content_id` and URL;
- **(Phase 2)** cap evidence per `topic_label`;
- **(Phase 2)** prefer recent rows but include a small tail from older
  yielded keywords;
- **(Phase 2)** avoid rows whose only signal is `rejected_low_score`;
- **(Phase 1)** expose local evidence sources in the dry-run report so drift
  is visible.

The AI curator still decides final keyword specificity and platform wording.

### 7. Report/Gate Additions

`keyword-inspiration-report` should add local-first fields:

- external searches saved per cohort;
- local/external grounding mix (from `grounding_source` provenance);
- duplicate-rejection rate by `grounding_source` (Phase 2, see section 6);
- claim counts by day and platform;
- claim counts by `source_interest`;
- optional interest-weight bucket when available in keyword metadata.

Phase 1 ships these as **empty-safe stub fields** (present, empty when the
underlying provenance has not accumulated); Phase 2 fills them with real
aggregation. **The substance must land before the 14-day gate decision** —
a gate verdict computed while these are still stubs is not valid.

These fields address two evaluation confounders:

- inspiration may target lower-weight long-tail interests, so raw delight may
  be lower for a valid reason;
- additive mode is not a clean A/B because cohorts compete for the same claim
  schedule and pool admission.

## Acceptance Criteria

- With `local_cache` first in the provider chain and sufficient local evidence,
  `preview_inspiration_keywords()` produces grounding records without calling
  Exa / You.com / platform sources for those probes.
- With insufficient local evidence, the provider chain falls through to the
  next configured provider (covered by a behavioral test, not just chain
  construction).
- A spaceless CJK query (e.g. `独立游戏机制`) matches local evidence via the
  2-gram token path.
- A row matching only one weak token of a multi-token query is excluded by
  the row quality floor.
- A probe served by `local_cache` does not decrement
  `inspiration_max_probe_searches_per_stage`.
- A sufficient `local_cache` hit does not trigger fallback augmentation to
  platform sources, Exa, or You.com.
- The dry-run JSON includes `local_hits`, `local_misses`,
  `external_searches_saved`, and `local_sources`, with
  `external_searches_saved` computed by the budget-aware formula in
  section 5.
- Generated keyword rows persist `grounding_source` in
  `discovery_keywords.grounding_source`.
- Local evidence never writes to `discovery_candidates` or `content_cache`.
- Generic terms are not hard-rejected by the local provider.
- The default config renders `inspiration_search_backends =
  ["local_cache", "platform_sources", "exa", "you"]`.
- Existing inspiration provider tests, keyword planner tests, config tests, and
  full pytest stay green.

## Rollout

0. **Checkpoint the existing working tree first.** The feature branch carries
   the full inspiration implementation (~7.5k lines across ~36 dirty files,
   zero commits ahead of main) — commit it in functional chunks **before**
   starting any task of this spec, or this spec's per-task commits will
   silently sweep prior work into mislabeled commits.
1. Land Phase 1 (local provider behind the default provider chain) while
   `inspiration_search_enabled=false` remains the global default.
2. Run `keyword-inspiration-dry-run` with the current real profile and
   inspect: selected interests, local evidence source mix and **evidence
   quality by eyeball** (the sufficiency rule is only trustworthy once the
   relevance scoring has been sanity-checked on real data), saved searches,
   and final platform keywords.
3. Enable additive mode only after risk-ledger fixes are in place
   (per-backend risk allocation, actual request-count ledger, and X/Douyin
   cooldown or daily cap).
4. Land Phase 2 (remaining evidence sources, echo caps, report substance)
   before the 14-day gate decision, so cohort bias and the local-grounding
   duplicate rate are visible in the verdict.
