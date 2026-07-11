# Like Secondary-Interest Query Generation Spec

## Goal

Replace the current fixed-template inspiration seed flow with a coverage-aware,
LLM-brainstormed query generation flow:

```text
like secondary interests
-> coverage-weighted interest sampling
-> LLM brainstorms search probes
-> Search provider chain grounds probes with real content evidence
-> curator/generator emits platform-specific keyword lists
-> keyword/candidate yield feeds the next sampling round
```

The objective is not just prettier keywords. The objective is to make the
keyword pool cover the user's positive preference space more completely while
reducing repeated searches around already-saturated interests and content types.

## Non-Goals

- Do not use disliked topics as positive search seeds.
- Do not let one fixed template shape every interest into the same lens.
- Do not rely on LLM "be diverse" instructions without system-side quota and
  coverage enforcement.
- Do not remove the existing keyword pool / `keyword_kind=regular|explore`
  separation.

## Inputs

### Positive Secondary Interests

The selector should prefer second-level interests derived from positive behavior.
For the current onion profile, `interest.likes[].specifics` are the primary
candidate set. A first-level `domain` is only used when that liked domain has no
valid specifics, and should be marked as low-specificity.

- explicit like / favorite / accepted recommendation;
- high-quality watch or dwell signals when available;
- profile-edited positive secondary interests;
- stable `SoulProfile.preferences.interests` specifics when backed by positive
  provenance.

These are positive search anchors. Examples:

```text
王者荣耀匹配机制
Switch 独立游戏
AI 工具实测
美食探店避坑
萌宠行为训练
```

If only first-level interests are available, the planner may use them as a
temporary fallback, but should mark the aspect as low-specificity so the
brainstorm agent must derive concrete second-level branches first.

### Negative Boundaries

Dislike / rejected / low-quality feedback should become boundaries, not seeds.
The planner must classify negative feedback before using it:

```text
topic boundary       -> avoid_topics
style boundary       -> avoid_styles
franchise boundary   -> avoid_franchises
quality boundary     -> avoid_low_information / avoid_clickbait / avoid_repost
```

Example:

```text
dislike: AI 焦虑贩卖
keep: AI 工具实测 / AI 论文解读 / AI 产品对比
avoid: 焦虑标题 / 夸张预测 / 低信息密度观点
```

## Coverage Snapshot

Before selecting interests, build a coverage snapshot from keyword and candidate
history.

Per secondary interest, track:

- `generated_keyword_count`: how many keywords were generated recently;
- `selected_keyword_count`: how many keywords were claimed/used recently;
- `candidate_count`: how many raw candidates reached `discovery_candidates`;
- `admitted_count`: how many final candidates entered `content_cache`;
- `yield_count`: how many admitted candidates were attributed to keywords from
  this interest;
- `last_selected_at` / `last_yielded_at`;
- dominant `content_type`, `source_platform`, `style_key`, `topic_group`, and
  `source_interest` shares when available.

This snapshot is the system-side control loop. Recent keywords alone are not
enough: an interest should also cool down if it already dominates the final
candidate pool.

## Interest Sampling

Each planning cycle samples a small set of positive secondary interests. Sampling
probability should combine affinity and coverage pressure:

```text
score =
  like_affinity
  * recency_weight
  * undercovered_boost
  * platform_fit
  / (1 + generated_keyword_count)^alpha
  / (1 + admitted_share / target_share)^beta
  / (1 + dominant_content_type_share / target_content_type_share)^gamma
```

Recommended first implementation:

- sample 4-8 secondary interests per generation cycle;
- reserve at least one slot for a never-covered or undercovered positive
  secondary interest when one exists;
- penalize a parent domain after one of its specifics is already selected, so a
  small window spreads across interests before taking a second branch from the
  same parent;
- cap one secondary interest to at most 2 expansion slots per cycle;
- cool down interests that recently generated many keywords even if those
  keywords have not yielded yet;
- cool down interests that already produced a large raw candidate share or a
  highly concentrated raw candidate content type, even before final admission;
- cool down interests whose admitted content already exceeds the intended pool
  share.

This is where diversity should be enforced. The LLM can suggest branches, but it
should not decide whether one interest is allowed to consume the whole batch.

## Brainstorm Agent

The brainstorm agent receives:

- compact user profile;
- selected positive secondary interests;
- negative boundaries;
- coverage snapshot summary;
- enabled platforms and their broad supply advantages.

It outputs search probes, not final platform keywords.

Expected JSON shape:

```json
{
  "interest_branches": [
    {
      "secondary_interest": "Switch 独立游戏",
      "branch_id": "switch-hidden-gems",
      "branch_label": "隐藏佳作与类型扩展",
      "why_it_might_work": "用户喜欢游戏与具体体验，Switch 独立游戏适合从作品实体扩展。",
      "probe_queries": [
        "Switch 独立游戏 冷门佳作",
        "Nintendo Switch hidden gems indie"
      ],
      "expected_platform_fit": ["bilibili", "youtube", "reddit"],
      "avoid": ["云推荐", "低信息密度盘点"]
    }
  ]
}
```

The prompt may include a lens taxonomy as inspiration, but not as a fixed query
template. Useful lenses include:

- concrete entity / work / product;
- hands-on experience / review / comparison;
- community language / meme / controversy;
- creator / expert / interview;
- practical tutorial / method;
- event / debate / regulation;
- adjacent hobby / one-hop exploration.

No single lens should be the default. "Event / debate / regulation" is useful,
but must be quota-limited because it can collapse many interests into the same
legal/regulatory style.

## Search Grounding

Run searches using the brainstormed `probe_queries` and the configured
`[discovery].inspiration_search_backends` chain. The default chain is Exa first,
then You.com Free MCP as a fallback when Exa is rate-limited, fails, or yields no
usable preview items. The grounding stage's job is to ground branches in real,
current, searchable evidence.

Extract structured grounding records:

```json
{
  "secondary_interest": "Switch 独立游戏",
  "branch_id": "switch-hidden-gems",
  "probe_query": "Nintendo Switch hidden gems indie",
  "entities": ["Balatro", "Hades", "Dredge"],
  "community_terms": ["hidden gems", "cozy games", "roguelite"],
  "evidence_titles": ["..."],
  "evidence_urls": ["..."],
  "evidence_quality": 0.0
}
```

Grounding should filter snippet noise aggressively. Search highlights that are
sentence fragments, table separators, boilerplate, or generic words should not
be promoted into `source_terms`.

## Curator And Platform Keyword Generator

The final generator receives:

- selected secondary interests;
- full compact profile;
- negative boundaries;
- coverage snapshot;
- brainstorm branches;
- search grounding records;
- per-platform guides:
  - static supply advantage;
  - recent keywords;
  - avoid / prefer hints;
  - cold start state;
  - data-driven `supply_hint`;
  - platform deficit/need.

It emits platform-specific keyword lists:

```json
{
  "platform_keywords": {
    "bilibili": [
      {
        "keyword": "Switch 独立游戏 冷门佳作 盘点",
        "secondary_interest": "Switch 独立游戏",
        "branch_id": "switch-hidden-gems",
        "lens_family": "work_entity"
      }
    ],
    "reddit": [
      {
        "keyword": "Nintendo Switch hidden gems indie",
        "secondary_interest": "Switch 独立游戏",
        "branch_id": "switch-hidden-gems",
        "lens_family": "community_language"
      }
    ]
  }
}
```

System-side validation should enforce:

- every keyword has a known `secondary_interest`;
- each platform list respects requested `need`;
- repeated recent keywords are removed;
- `max_per_secondary_interest` is respected;
- `max_per_lens_family` is respected;
- must-cover undercovered interests are not silently dropped;
- platform keyword style follows the target platform guide.

If the LLM output violates quotas, deterministic post-processing should trim
overrepresented interests/lenses and either request repair or fill from the next
valid branch.

## Regular vs Explore

`regular` and `explore` should share the same pipeline but differ in branch
policy:

- `regular`: stay close to liked secondary interests and optimize for searchable,
  high-fit content.
- `explore`: anchor on liked secondary interests but require one-hop lateral
  expansion. Example: `Switch 独立游戏 -> 本地多人派对游戏 -> couch co-op design`.

Explore should still avoid pure cold-start guesses that have no connection to
positive behavior.

## Feedback Loop

Every inserted keyword should persist provenance:

```text
secondary_interest
branch_id
lens_family
probe_query
inspiration_id
expansion_id
platform
keyword_kind
```

When a keyword is claimed, yields candidates, or admits content into the final
pool, update the coverage snapshot counters. Future sampling uses those counters
to reduce the probability of already-covered interests and content types.

## Acceptance Criteria

- A generation cycle with 8+ positive secondary interests does not fill all
  platform lists from only the top 1-2 interests unless every other interest is
  explicitly invalid.
- Re-running generation after one secondary interest produced many keywords
  lowers that interest's sampling probability.
- Re-running generation after one secondary interest dominates admitted
  candidates lowers that interest's sampling probability even if its recent
  keyword count is low.
- Disliked feedback is used as boundary/avoid context, not as a positive search
  seed.
- Platform outputs differ by native search style, not only by language.
- Search grounding improves specificity but cannot override coverage quotas by
  itself.

## Migration From Current MVP

Current MVP:

```text
profile aspect window
-> fixed seed query per aspect
-> search preview
-> seed extraction
-> curator/generator
-> platform keywords
```

Target flow:

```text
like secondary-interest coverage snapshot
-> sampled secondary interests
-> brainstorm probe branches
-> search grounding
-> coverage-aware curator/generator
-> platform keywords
```

The existing inspiration tables and keyword provenance fields remain useful.
The main changes are:

- replace fixed `_seed_query_for_aspect()` with brainstormed probe branches;
- add secondary-interest coverage snapshot and sampler;
- persist `secondary_interest`, `branch_id`, and `lens_family`;
- add quota validation after curator output;
- feed admitted candidate distribution back into the sampler.
