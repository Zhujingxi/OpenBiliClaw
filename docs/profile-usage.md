# Profile-usage registry

> **Purpose.** One row per surface that consumes the user profile — so that
> per-stage tailoring is deliberate, the portrait boundary is auditable, and no
> new serializer fork appears by accident. Seeded from the profile-views spec
> (`docs/plans/2026-07-18-profile-views-spec.md` §D1–D8) and the 2026-07-16
> token-diet audit. Every `file:line` below was cross-checked against the working
> tree. Task 5 (Wave B) relocated the three content-pipeline serializers into
> `soul/profile_views.py`; `discovery/strategies/_utils.py` now re-exports them.

## The serialization paths

The profile reaches an LLM prompt through a small set of serializers. The three
content-pipeline serializers now live in `soul/profile_views.py` (Task 5 moved
them verbatim from `discovery/strategies/_utils.py`, which keeps re-export stubs
so every legacy import path stays valid):

| Serializer | Defined at | Shape | Portrait? | Notes |
| --- | --- | --- | --- | --- |
| `build_profile_summary` | `soul/profile_views.py:360` (re-export `discovery/strategies/_utils.py`) | dict | **No** | Canonical structured profile; portrait deliberately excluded (`profile_views.py:371-375`). `favorite_up_users` also excluded (`profile_views.py:392`). |
| `compact_content_prompt_profile_summary` | `soul/profile_views.py:512` (re-export `discovery/strategies/_utils.py`) | dict | **No** | Caps a `build_profile_summary` dict for high-volume content prompts. Aliased as `compact_evaluation_profile_summary` (`discovery/engine.py:102`). Dislike floor preserved (`profile_views.py:46-50`). |
| `build_query_generation_profile_summary` | `soul/profile_views.py:914` (re-export `discovery/strategies/_utils.py`) | dict | **No** | Query-trimmed taste shape; drops awareness/insights/timestamps. Interests cap 64, domains ≤16. |
| `speculation` (→ `to_llm_context(include_portrait=False)`) | `soul/profile_views.py` (`speculation`); renderer `soul/profile.py:720` (onion) / `:115` (flat) | str | **No** (opted out) | String view for the two speculator prompts. Task 7 collected the former in-line `to_llm_context(include_portrait=False)` fork into a façade view that delegates to the profile's own renderer (zero behaviour change). `include_portrait=True` default still keeps the portrait for eval/persona rendering (not this path). |
| `chat_core_memory` / `render_core_memory_blocks` | `soul/profile_views.py` (`chat_core_memory`), `memory/manager.py` (`render_core_memory_blocks` / `render_core_memory_prompt`) | `(stable, volatile)` str pair | **Yes** (stable block) | Chat core-memory view. Reads the **effective** profile (AI ⊕ overrides via `_effective_soul_data`, `manager.py`), so manual edits show. `complete_with_core_memory` injects `stable_block` (portrait/identity/preference) into system and `volatile_block` (awareness/insights) ahead of the user turn — awareness churn no longer breaks the cached system prefix (Task 6). `render_core_memory_prompt` kept as the concatenated compat wrapper for non-chat readers. |
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
| Speculative interest generation | 12h speculation | `profile_views.speculation(profile)` — `soul/speculator.py:1386` | string, portrait excluded | No | Yes |
| Avoidance speculation | 12h speculation | `profile_views.speculation(profile)` — `soul/avoidance_speculator.py:1271` (getattr guard keeps `{}` fallback for non-object profiles) | string, portrait excluded | No | Yes |
| Page extractor | Per fetched page | none — profile not read; `inject_core_memory=False` — `sources/llm_extractor.py:85` | (none) | No | Yes |
| Chat (Socratic dialogue) | Per chat turn | `chat_core_memory` via `render_core_memory_blocks` → `complete_with_core_memory` — `llm/service.py` | effective core memory, split: system = portrait + 核心特质/价值观/深层需求/MBTI + 偏好摘要 (stable); user = 近期观察 + 当前洞察 (volatile) | Yes | Yes |
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
`build_query_generation_profile_summary`, the `speculation` view →
`to_llm_context(include_portrait=False)`) MUST exclude it. Enforced by
`tests/test_profile_views_guards.py`.
