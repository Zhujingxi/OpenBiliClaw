# Profile-usage registry

> **Purpose.** One row per surface that consumes the user profile — so that
> per-stage tailoring is deliberate, the portrait boundary is auditable, and no
> new serializer fork appears by accident. Seeded from the profile-views spec
> (`docs/plans/2026-07-18-profile-views-spec.md` §D1–D8) and the 2026-07-16
> token-diet audit. Every `file:line` below was cross-checked against the working
> tree; re-verify on move (Task 5 relocates the serializers into
> `soul/profile_views.py`).

## The serialization paths

The profile reaches an LLM prompt through a small set of serializers, all
currently defined in `discovery/strategies/_utils.py` (Task 5 moves them to
`soul/profile_views.py` and leaves re-export stubs):

| Serializer | Defined at | Shape | Portrait? | Notes |
| --- | --- | --- | --- | --- |
| `build_profile_summary` | `discovery/strategies/_utils.py:595` | dict | **No** | Canonical structured profile; portrait deliberately excluded (`_utils.py:606-610`). `favorite_up_users` also excluded (`_utils.py:627`). |
| `compact_content_prompt_profile_summary` | `discovery/strategies/_utils.py:60` | dict | **No** | Caps a `build_profile_summary` dict for high-volume content prompts. Aliased as `compact_evaluation_profile_summary` (`discovery/engine.py:102`). Dislike floor preserved (`_utils.py:25-29`). |
| `build_query_generation_profile_summary` | `discovery/strategies/_utils.py:1053` | dict | **No** | Query-trimmed taste shape; drops awareness/insights/timestamps. Interests cap 64, domains ≤16. |
| `OnionProfile.to_llm_context(include_portrait=False)` | `soul/profile.py:720` | str | **No** (opted out) | Divergent string fork used only by the speculators. `include_portrait=True` default keeps the portrait for eval/persona rendering. Task 7 folds this into a `speculation` view. |
| `render_core_memory_prompt` | `memory/manager.py:677` | str | **Yes** | Chat-only core-memory block; reads raw soul layer today (`get_core_memory`, `manager.py:593`). Injected into the system prompt by `complete_with_core_memory` (`llm/service.py:360`). Task 6 splits stable/volatile + honors overrides. |
| `ProfileResponse` (openclaw) | `integrations/openclaw/operations.py:105` | dataclass | **Yes** (intentional) | External API surface; portrait re-exposed at `operations.py:113`, each list capped `[:5]` (`operations.py:114-120`). |

## Consumer surfaces

| Surface | Trigger cadence | View / serializer | Fields (caps) | Portrait? | LLM? |
| --- | --- | --- | --- | --- | --- |
| Recommendation evaluation / expression | Per candidate (discovery + serve) | `compact_content_prompt_profile_summary(build_profile_summary(...))` — `recommendation/engine.py:126-127` | compact (20 core / 64 interests / 32 domains / 12 recent; dislikes uncapped) | No | Yes |
| Discovery evaluation | Per candidate batch | `_evaluation_profile_summary` = `compact_evaluation_profile_summary(build_profile_summary(...))` — `discovery/engine.py:1874`; applied `discovery/engine.py:823` | compact | No | Yes |
| Discovery evaluation digest (cache key) | Per candidate batch | `_evaluation_profile_digest` — `discovery/engine.py:1862`; used `discovery/engine.py:1542`, `:2119` | digest over the compact prompt-visible slice | No | No (cache key) |
| Search keyword generation | Per discovery cycle | `build_query_generation_profile_summary` — `discovery/strategies/search.py:547`, `:550` | query-trimmed | No | Yes |
| Explore domain generation | Per discovery cycle | `build_query_generation_profile_summary` — `discovery/strategies/explore.py:428` | query-trimmed | No | Yes |
| Keyword planner | Per planning batch | `build_query_generation_profile_summary` — `runtime/keyword_planner.py:1221` | query-trimmed | No | Yes |
| Bilibili extension search fallback | Per producer run | `build_query_generation_profile_summary` — `runtime/bilibili_producer.py:41` | query-trimmed | No | Yes |
| Douyin direct keyword gen | Per discovery cycle | `build_query_generation_profile_summary` — `discovery/strategies/douyin_direct.py:71` | query-trimmed | No | Yes |
| YouTube keyword gen | Per discovery cycle | `build_query_generation_profile_summary` — `discovery/strategies/youtube.py:155` | query-trimmed | No | Yes |
| X keyword gen | Per discovery cycle | `build_query_generation_profile_summary` — `discovery/strategies/x.py:247` | query-trimmed | No | Yes |
| Xiaohongshu keyword gen | Per discovery cycle | `build_query_generation_profile_summary` — `sources/xhs_keyword_gen.py:49` | query-trimmed | No | Yes |
| Pool snapshot / diagnostics | On snapshot | `build_profile_summary` — `discovery/pool_snapshot.py:114` | full dict | No | No |
| Speculative interest generation | 12h speculation | `OnionProfile.to_llm_context(include_portrait=False)` — `soul/speculator.py:1386` | string, portrait excluded | No | Yes |
| Avoidance speculation | 12h speculation | `OnionProfile.to_llm_context(include_portrait=False)` — `soul/avoidance_speculator.py:1271` (via getattr) | string, portrait excluded | No | Yes |
| Page extractor | Per fetched page | none — profile not read; `inject_core_memory=False` — `sources/llm_extractor.py:85` | (none) | No | Yes |
| Chat (Socratic dialogue) | Per chat turn | `render_core_memory_prompt` via `complete_with_core_memory` — `llm/service.py:360` | core memory (portrait + traits + values + awareness + insights) | Yes | Yes |
| Consolidator judge | 12h consolidation | inherits `inject_core_memory=True` — `soul/consolidator.py:809` | core memory (unaudited; Task 8) | Yes | Yes |
| Layer updaters (×3) | On profile update | inherits `inject_core_memory=True` — `soul/layer_updaters.py:320`, `:415`, `:526` | core memory (unaudited; Task 8) | Yes | Yes |
| Category migration | On migration | inherits `inject_core_memory=True` — `soul/category_migration.py:141` | core memory (unaudited; Task 8) | Yes | Yes |
| Pool purge (dislike match) | On new dislike | inherits `inject_core_memory=True` — `soul/pool_purge.py:196` | core memory (unaudited; Task 8) | Yes | Yes |
| Dialogue-insight analyzer | Post-chat | inherits `inject_core_memory=True` — `soul/dialogue_insight_analyzer.py:64` | core memory (plausibly wanted; Task 8) | Yes | Yes |
| Probe sentiment judge | Per probe reply | inherits `inject_core_memory=True` — `api/app.py:6241` | core memory (plausibly wanted; Task 8) | Yes | Yes |
| Related-chain seed | Per discovery cycle | direct read `favorite_up_users[:1]` — `discovery/strategies/related_chain.py:392` | favorite UPs only | No | No |
| `/api/profile-summary` (UI) | On request | direct read — `api/app.py:3990` | full profile incl. portrait | Yes | No |
| OpenClaw `get_profile` | On request | `ProfileResponse` — `integrations/openclaw/operations.py:105` | portrait + 5 traits / 5 needs / 5 interests | Yes (external) | No |
| Delight scoring | Per candidate | embeddings only — `recommendation/delight.py` (no LLM profile prompt; spec D8) | (embedding vectors) | No | No |

## Portrait boundary (invariant)

`personality_portrait` is allowed into exactly two prompt/response surfaces:

- **Chat core memory** (`render_core_memory_prompt` → `complete_with_core_memory`).
- **OpenClaw external `ProfileResponse`** (`operations.py:113`) — plus UI
  (`/api/profile-summary`) and eval personas, which are out of the profile-views
  scope but keep the portrait by design.

Every content-pipeline serializer (`build_profile_summary`,
`compact_content_prompt_profile_summary`,
`build_query_generation_profile_summary`, speculator
`to_llm_context(include_portrait=False)`) MUST exclude it. Enforced by
`tests/test_profile_views_guards.py`.
