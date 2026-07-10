# Inspiration Sampling, Grounding Budget, And Enablement Gate Spec

## Goal

Harden the secondary-interest inspiration flow before it is enabled on real
traffic. The current implementation (MVP + like-secondary-interest flow +
platform grounding + hardening pass) is functionally complete and test-green,
but it has an unbounded grounding fanout, an interest sample size that
contradicts the like-secondary spec, a risk-control amplification path into
Bilibili search, a free-text lens validation that will drift, a coverage
control loop keyed on raw label strings that the 12h profile consolidation
will silently break, and — most importantly — **no defined criterion for
deciding whether the whole flow earns its keep** before
`inspiration_replace_merged_keywords` is ever enabled.

This spec makes the per-cycle cost **bounded, configurable, and observable**,
makes the control loop **survive profile label drift**, and defines a
**mechanical kill/keep gate** for enablement — without changing the pipeline
shape:

```text
coverage snapshot -> sampled interests -> brainstorm -> grounding -> curator -> keywords
```

## Current Implementation State (2026-07-03)

The implementation now matches this spec with the following concrete scope:

- Like-secondary-interest sampling is active; `inspiration_aspect_window_size`
  remains the candidate window and `inspiration_interest_sample_size` is the
  per-run selected sample.
- Regular and explore share one brainstorm / grounding stage when due
  together; branches carry `kind_fit`.
- Grounding is budgeted by `inspiration_max_probe_searches_per_stage`,
  `inspiration_platforms_per_probe`,
  `inspiration_riskcontrolled_probe_budget`, and
  `inspiration_search_pages_per_probe`.
- `platform_sources` currently supports Bilibili, YouTube, X/Twitter cookie
  replay, Reddit command backend, Douyin direct-client when supplied, and
  Xiaohongshu / Zhihu only when an explicit search bridge callable is supplied.
  It never enqueues browser/plugin tasks from keyword planning.
- X/Twitter inspiration grounding reuses the existing `XClient.search()` path
  (`twitter-cli` / x.com cookie replay) and is risk-controlled.
- Generic platform-native terms are allowed to reach the AI curator. Hard
  deterministic rejection remains reserved for noise, raw evidence-title/URL
  copies, overlong queries, and clear platform language/style mismatches.

## Non-Goals

- Do not change the provider chain semantics (`FallbackInspirationSearchProvider`
  first-non-empty ordering stays).
- Do not enqueue async/plugin browser tasks from keyword planning. Xiaohongshu
  and Zhihu are supported only through explicitly injected search bridge
  callables; if no bridge is supplied, they are absent from
  `platform_sources`. Douyin direct-client and X cookie replay are synchronous
  platform-source backends.
- Do not tune the `_secondary_interest_score` exponents in this iteration;
  tuning happens via dry-run observation after budgets land.
- Do not move grounding results into `discovery_candidates` — inspiration-only
  stays inspiration-only.
- Do not add embedding-based interest/topic matching or keyword-level
  embedding diversity checks. The lens quota stays a cheap self-reported
  heuristic; downstream pool topic diversity (m118) and explore cluster caps
  already enforce diversity on actual content. Label matching improvements in
  this spec are normalization + explicit migration only.
- Do not add any further pipeline stages. The design is complexity-frozen
  until the enablement gate (section 7) produces real-traffic evidence about
  which stages earn their cost.
- Do not add a deterministic "specificity critic" or hard generic-term ban.
  Broad/native community terms such as hashtags or "讨论" may be useful
  platform language and should be judged by the curator and downstream yield.

## Problems Being Fixed

### P1: Unbounded grounding fanout (correctness + cost)

`_run_inspiration_stage()` grounds `branch.probe_queries[:2]` for **every**
branch the brainstorm LLM returns, with no cap on branch count. The stage runs
up to twice per planner cycle (`regular`, then `explore` when due). With the
current interest window (see P2) a single cycle can legally issue 60+
`provider.search()` calls.

### P2: Interest sample size contradicts the spec

`keyword_planner.py` passes `inspiration_aspect_window_size` (default **32**,
a leftover from the aspect-window MVP) as `max_interests` into
`build_like_secondary_interest_window()`. The like-secondary spec requires
sampling **4-8** interests per cycle, with system-side coverage control. 32
interests also bloats the brainstorm user payload for no gain.

### P3: Bilibili risk-control amplification

The default backend chain is `("platform_sources", "exa", "you")`, and
`PlatformSourceInspirationProvider` rotates 2-of-N enabled backends per probe,
so Bilibili takes roughly 2/3 of all grounding probes when three platforms are
enabled. `BilibiliAPIClient.search()` already skips when the process-wide
cooldown is active (good), but inspiration probes:

- still **contribute** to v_voucher exhaustion streaks and can trigger or
  escalate the process-wide cooldown that the whole discovery search round and
  the explore strategy share (the exact failure mode of the 2026-05 storm);
- waste one of the two fanout slots when the cooldown is active (the client
  returns `[]` but the backend was still "selected");
- can each burn ~21s of retry backoff inside the client, stalling the planner
  cycle.

### P4: Free-text lens validation

Explore-kind validation matches `lens_family` against
`_EXPLORE_LATERAL_LENS_KEYWORDS` substrings, while the brainstorm schema
already declares an enum
(`work_entity|hands_on|community_language|creator|method|event|adjacent`).
Substring matching on LLM-authored labels will silently misclassify when
labels drift (e.g. `"community_language"` matches `"community"`, but a future
`"regional_culture"` label matches nothing and gets rejected for the wrong
reason).

### P5: No per-cycle grounding observability

There is no ledger of how many grounding searches actually went to which
platform. When a v_voucher storm happens, the inspiration stage's contribution
is not attributable.

### P6: No kill/keep criterion for enablement

Every acceptance criterion in the plan stack so far is *distributional*
(coverage spread, quota compliance). None measures end-to-end quality: do
inspiration-generated keywords produce candidates that get admitted, and is
their delight/diversity better than the merged flow's? Without a defined
metric, `inspiration_replace_merged_keywords` becomes a judgment call and the
feature becomes unfalsifiable. The measurement is cheap: inspiration keywords
already carry `inspiration_id`/`expansion_id` provenance and the
`discovery_keyword_yield(keyword_id, content_id)` ledger attributes admitted
content to keywords, so additive mode is a natural A/B.

### P7: Coverage keys break under profile label drift

The coverage control loop joins **three label namespaces by raw string
equality**: profile interest specifics (sampler input),
`discovery_keywords.source_interest` (generation history, grouped by exact
string in `get_keyword_interest_coverage_snapshot()`), and
`content_cache.pool_topic_label` / `topic_group` (admitted-pool distribution).
The only normalization today is `.strip()`. The 12h LLM profile consolidation
rewrites and merges interest labels as its core job — after a rename, the
interest's history orphans, the sampler sees a "never covered" interest, and
the undercovered boost repeatedly over-selects it. This is a recurring
12-hourly event, not an edge case.

### P8: Regular and explore run the full pipeline twice per cycle

The like-secondary spec says the two kinds "share the same pipeline but
differ in branch policy", but the implementation runs brainstorm + grounding +
curator once per kind. A cycle where explore is due pays 2× LLM calls and 2×
grounding budget for inputs that are ~identical (same profile, same coverage
snapshot, same selected interests).

## Design

### 1. Split interest sample size from the candidate window

New config field:

```toml
[discovery]
inspiration_interest_sample_size = 6   # clamp 1..16
```

- `build_like_secondary_interest_window()` keeps receiving
  `inspiration_aspect_window_size` (default 32) as the **candidate pool
  bound** for scoring, but the planner truncates the diverse selection to
  `inspiration_interest_sample_size` before brainstorm.
- The must-cover reservation survives truncation: when a never-covered /
  undercovered positive interest exists, at least one sample slot belongs to
  it (this is the existing `must_cover` behavior; it now operates on the
  smaller sample).
- `--interest-limit` in `keyword-inspiration-dry-run` overrides the sample
  size, not the window size.

Deterministic top-k (with the existing parent-domain spreading) remains
acceptable in place of true probabilistic sampling: the
`generated_keyword_count` feedback self-penalizes selected interests on the
next cycle, which provides rotation without RNG.

### 2. Deterministic branch and probe caps

Applied in `parse_brainstorm_branches()` (and the fallback generator), before
grounding:

- at most **2 branches per secondary interest** (matches the like-secondary
  spec's expansion-slot cap);
- at most `2 * inspiration_interest_sample_size` branches total, trimmed
  must-cover-first, then by branch order;
- probe queries normalized (`_normalize_match_text`) and deduplicated
  **across branches within a stage run** — the same probe is never searched
  twice in one run.

### 3. Grounding search budget

New config fields:

```toml
[discovery]
inspiration_max_probe_searches_per_stage = 12  # clamp 1..64
inspiration_platforms_per_probe = 2            # clamp 1..4 (was hard-coded)
inspiration_search_pages_per_probe = 1         # clamp 1..5
```

- `_run_inspiration_stage()` stops issuing `provider.search()` calls once the
  per-stage budget is spent; remaining branches keep their brainstorm-only
  metadata and may still reach the curator as ungrounded branches (grounding
  improves specificity; it is not an admission requirement).
- The budget is per stage run, so a cycle that runs `regular` + `explore`
  spends at most `2 * inspiration_max_probe_searches_per_stage` searches.
- Each backend search is wrapped in a hard deadline (8s,
  `asyncio.wait_for`); a timeout counts as a backend failure (non-fatal,
  logged at debug) so one slow platform cannot stall the planner cycle.
- `inspiration_search_pages_per_probe` fans out paginated backends such as
  Bilibili across page 1..N and increases one-shot providers' result budget,
  while preserving default cost at `1`.

Worst case with defaults: 6 interests → ≤12 branches → ≤24 candidate probes
→ **≤12 grounding searches per stage, ≤24 per cycle**, each fanning out to at
most 2 platforms. Once section 9 (shared stage run) lands, the per-cycle
worst case drops to a single stage budget (≤12).

### 4. Risk-aware platform backend selection

Extend `PlatformSearchBackend` with two optional members (default-safe via
`getattr`):

```python
risk_controlled: bool = False          # class attribute
def cooldown_remaining(self) -> float  # optional; 0.0 when absent
```

- `BilibiliPlatformSearchBackend` sets `risk_controlled = True` and delegates
  `cooldown_remaining()` to `BilibiliAPIClient.search_cooldown_remaining()`.
- `DouyinPlatformSearchBackend` and `XPlatformSearchBackend` are also
  risk-controlled. Douyin uses an injected direct client; X uses the existing
  `XClient.search()` / x.com cookie replay path.
- `PlatformSourceInspirationProvider._select_backends()` changes:
  1. skip any backend whose `cooldown_remaining() > 0` — the fanout slot goes
     to the next backend in rotation instead of being wasted;
  2. enforce a per-cycle budget for risk-controlled backends:

```toml
[discovery]
inspiration_riskcontrolled_probe_budget = 4  # clamp 0..32, per stage run
```

- The planner calls a new `provider.begin_stage()` hook (no-op on providers
  that lack it) at stage start to reset the risk-budget counter, so the
  budget is per stage run rather than per process lifetime.
- Once the risk-controlled budget is spent, rotation continues over the
  remaining non-risk backends (YouTube / Reddit and any supplied safe bridge),
  and `platform_sources` may legitimately return fewer or empty previews — the
  existing `fallback_on_empty` chain then
  forwards the probe to Exa / You.com. **The default chain order does not
  change**; risk exposure is bounded instead.

Rationale for keeping Bilibili in the chain at all (bounded, not removed):
Bilibili-native evidence is the least valuable grounding signal — the curator
already receives recent Bilibili keywords and platform guides, and the main
discovery flow already exercises Bilibili search supply — but community
phrasing from B站 titles is still useful for CN keyword style, so a small
budget (4) is retained rather than 0.

### 5. Canonical lens families

- Define `LensFamily` as a frozen enum in `discovery/inspiration.py`:
  `work_entity | hands_on | community_language | creator | method | event |
  adjacent | other`.
- `parse_brainstorm_branches()` / expansion parsing normalize the LLM's
  free-text `lens_family` to a canonical value at parse time: exact enum match
  first, then the existing keyword table as a **normalization aid**, else
  `other`. The canonical value is what gets persisted and validated.
- Explore-kind validation becomes set membership:
  `EXPLORE_ALLOWED_LENSES = {adjacent, community_language, creator, method,
  hands_on}`. `other` is allowed for `regular`, rejected for `explore`.
- Rejections keep the existing `non_lateral_explore_lens` reason; a new
  `unknown_lens_family` debug log records the raw label whenever
  normalization falls through to `other`, so drift is visible instead of
  silent.
- `_EXPLORE_LATERAL_LENS_KEYWORDS` substring matching is deleted as a
  validation path (it survives only inside the normalizer).

### 6. Per-stage grounding ledger (observability)

- `_run_inspiration_stage()` accumulates
  `{platform: search_count, skipped_cooldown, skipped_budget, timeouts}` and
  logs one INFO line per stage run:

```text
inspiration grounding ledger kind=regular searches=12 bilibili=4 youtube=5 reddit=3 skipped_cooldown=2 skipped_budget=1 timeouts=0
```

- `preview_inspiration_keywords()` (and therefore
  `keyword-inspiration-dry-run`) includes the same ledger plus
  `bilibili_search_cooldown_remaining` in its JSON report, so a real dry-run
  shows exactly what a live cycle would have cost.
- LLM cost remains attributable via the existing callers
  (`discovery.keyword_brainstorm`, `.repair`, curator callers) in
  `openbiliclaw cost --by caller`.

### 7. Cohort report and enablement gate

Additive mode (inspiration on, replace off) produces two keyword cohorts in
`discovery_keywords`, distinguishable by provenance: **inspiration**
(`inspiration_id` set) and **merged** (legacy flow, provenance NULL).

New DAO `get_keyword_cohort_stats(window_days: int)` aggregates per cohort:

- generated / claimed counts and claimed rate;
- candidates yielded per claimed keyword (via `discovery_keyword_yield`);
- mean delight score of yield-attributed admitted content (join the yield
  ledger's `content_id` to `content_cache`);
- distinct `pool_topic_label` count per 100 yield-attributed items (topic
  diversity proxy).

New CLI `openbiliclaw keyword-inspiration-report [--window-days 14]` prints
both cohorts side by side plus the gate verdict below. No new config fields;
the window is CLI-only.

**Kill/keep gate** (defaults; revisit only with data, and record the values
used in the report output so the decision is mechanical):

After ≥14 days of additive mode **and** ≥200 claimed inspiration keywords,
`inspiration_replace_merged_keywords=true` is permitted only if, vs the merged
cohort over the same window:

1. yield-attributed admissions per claimed keyword ≥ 0.8×;
2. mean delight of yield-attributed admissions ≥ 0.95× (relative);
3. topic diversity per 100 admissions strictly higher.

If the gate fails, inspiration stays additive (or is disabled) and the next
iteration must change something measurable — no "enable and see".

### 8. Coverage keys survive profile label drift

Two independent fixes, both required:

**Normalization (baseline robustness).** All coverage joins compare labels
through one shared normalizer (the existing `_normalize_match_text`:
casefold + whitespace collapse). `get_keyword_interest_coverage_snapshot()`
merges buckets whose normalized keys collide (raw label kept for display);
the sampler's `coverage.get(label)` lookups and the pool-label ↔ interest
matching go through the same normalized form. This kills pure
case/whitespace/punctuation drift for free.

**Consolidation migration (rename/merge survival).** New DAO
`migrate_keyword_interest_labels(mapping: dict[str, str])` rewrites
`discovery_keywords.source_interest` old → new (merging is additive: counters
naturally combine because the snapshot is computed from rows). The 12h
profile consolidation apply step calls it with its rename/merge mapping —
the consolidation pipeline already has an ops apply/revert path to hook into.
When consolidation cannot produce a mapping (free-form rewrite), the miss is
tolerated: the orphaned counters become dead buckets under the old label
(harmless — they never match a sampler lookup again), and the over-boost on
the "new" interest is bounded by the per-interest slot cap (≤2 expansion
slots) from section 2 plus its own freshly accumulating counters.

Revert safety: the migration is also recorded (old → new pairs, timestamp) in
the consolidation ops log so a profile revert can apply the inverse mapping.

### 9. Shared stage run across query kinds

Collapse the per-kind pipeline runs into one shared run per cycle
(independent of sections 1–8; may land after the budgets, but before real
Bilibili traffic since it halves grounding pressure):

- **One brainstorm call** per cycle: the prompt asks for branches tagged
  `kind_fit: regular | explore | both`; explore-fit branches must be one-hop
  lateral (the schema note moves into the user payload's output_schema, the
  system prompt stays static per the prompt-cache convention).
- **One grounding pass** over the deduplicated union of probes, spending a
  single `inspiration_max_probe_searches_per_stage` budget (the "worst case
  ×2" in section 3 drops back to ×1).
- **Curator runs per kind** (unchanged prompts, unchanged validation): the
  regular curator sees regular/both branches, the explore curator sees
  explore/both branches. Explore lens validation from section 5 applies
  exactly as before.
- If explore is not due this cycle, explore-fit branches are dropped after
  brainstorm at zero extra cost.

## Config Summary

| Field | Default | Notes |
| --- | --- | --- |
| `inspiration_interest_sample_size` | 6 | new; interests per brainstorm |
| `inspiration_max_probe_searches_per_stage` | 12 | new; grounding budget |
| `inspiration_platforms_per_probe` | 2 | new; was hard-coded ctor default |
| `inspiration_riskcontrolled_probe_budget` | 4 | new; Bilibili / Douyin direct / X per stage |
| `inspiration_search_pages_per_probe` | 1 | new; page fan-out / one-shot result multiplier |
| `inspiration_aspect_window_size` | 32 | unchanged; candidate pool only |

All new fields follow the existing pattern: `DiscoveryConfig` field +
`[discovery]` loader + rendered in generated config output + docs in
`docs/modules/config.md`. Sections 7–9 add **no** config fields (the report
window is a CLI flag; label normalization and the shared stage run are
unconditional behavior).

## Acceptance Criteria

- A stage run whose brainstorm returns 20 branches across 6 interests issues
  at most `inspiration_max_probe_searches_per_stage` provider searches and at
  most 2 branches per interest reach grounding.
- With Bilibili, Douyin direct, X, YouTube, and Reddit enabled and defaults, a
  stage run sends at most 4 searches total to risk-controlled backends; further
  probes rotate across non-risk backends only.
- When `BilibiliAPIClient.search_cooldown_remaining() > 0`, the Bilibili
  backend is never selected, and each probe still fans out to
  `inspiration_platforms_per_probe` non-cooldown backends when available.
- Duplicate probe queries (after normalization) within one stage run trigger
  exactly one provider search.
- `XPlatformSearchBackend` maps `XClient.search()` tweet dictionaries to
  `ExaPreviewItem` evidence with title, x.com URL, author/source terms,
  long-form article text when present, and metrics highlights.
- Xiaohongshu and Zhihu backends are constructed only when explicit async
  search bridge callables are supplied; keyword planning does not create
  plugin/browser tasks for them.
- A brainstorm branch labeled `lens_family="exploration ideas"` normalizes to
  a canonical lens; `explore` validation decisions depend only on the
  canonical enum, and `unknown_lens_family` is logged for unmapped labels.
- The dry-run JSON report contains the grounding ledger and the current
  Bilibili cooldown state.
- A backend that hangs longer than the deadline is treated as a non-fatal
  failure and does not extend the stage by more than the deadline.
- `keyword-inspiration-report` splits cohorts by provenance and reports
  claimed rate, admissions per claimed keyword, mean delight, and topic
  diversity per cohort, plus a gate verdict with the thresholds it applied.
- Two keyword rows whose `source_interest` differs only by case/whitespace
  land in one coverage bucket; after
  `migrate_keyword_interest_labels({"旧标签": "新标签"})`, the snapshot
  reports the combined counters under the new label and the sampler no longer
  treats the renamed interest as never-covered.
- (Section 9) A cycle where both kinds are due issues exactly one brainstorm
  call, spends one grounding budget, and explore keywords still pass
  explore-only lens validation; a cycle where explore is not due produces no
  explore keywords from the shared run.
- All existing inspiration/keyword-planner/config tests stay green.

## Verified Runs

- Focused verification: `ruff check` on changed Python files, `mypy` on
  changed source files, and
  `pytest tests/test_discovery_inspiration_provider.py tests/test_config.py tests/test_keyword_planner.py -q`
  passed.
- Full verification: `pytest -q` passed with `3309 passed, 32 skipped`.
- Real dry-run with X temporarily enabled via environment (config not written)
  produced `grounding_ledger.searches=12`,
  `platforms={"twitter": 4, "reddit": 12}`, `timeouts=0`.
- A shifted-interest dry-run, with the prior top-8 interests cooled only in
  memory, selected `游戏资讯与推荐 / 漫画 / 科技新闻 / 气候变化` and produced
  platform-specific keywords for all seven platforms.

## Rollout

1. Land sections 1–6 and 8; keep `inspiration_search_enabled=false` default.
   (Section 9 may land in this step or the next; it must land before step 4.)
2. Run `keyword-inspiration-dry-run` against real config for both kinds;
   verify ledger numbers and keyword quality; tune sample size / budgets if
   needed.
3. Enable `inspiration_search_enabled=true` (additive mode) on the dev
   instance; watch `openbiliclaw cost --by caller` and the grounding ledger
   across several cycles, and confirm no search-cooldown escalation is
   attributable to inspiration probes.
4. Let additive mode run until the gate's sample floor is met (≥14 days,
   ≥200 claimed inspiration keywords), then run
   `keyword-inspiration-report`.
5. Enable `inspiration_replace_merged_keywords=true` **only on a passing gate
   verdict**. On a failing verdict, keep additive (or disable), change one
   measurable thing, and re-run the gate — never enable replace to "see if it
   helps".

## Engineering Hygiene (non-blocking, tracked here so it is not lost)

- The feature branch currently has ~5.6k added lines and **zero commits**.
  Before starting this spec's tasks, checkpoint the existing work as one or
  more commits (storage / discovery helpers / planner / provider / config+CLI
  / docs+tests is a reasonable split).
- `keyword_planner.py` gained ~2.1k lines; extract the inspiration
  orchestration (brainstorm / grounding / curation / validation) into a
  dedicated module (e.g. `runtime/inspiration_stage.py`) in a follow-up
  refactor once this spec's behavior is locked by tests. Not part of this
  spec's tasks.
