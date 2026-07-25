# Keyword Inspiration: Axis Library + Single-Call Redesign Spec

> **Status:** Reviewed — 2026-07-05. Hardened through a 5-round Codex adversarial review
> (R1–R4 findings applied, R5 VERDICT: APPROVE). Targets phase 2 of
> `feature/discovery-inspiration-mvp`.
> **Predecessors:** builds on
> [`2026-07-02-like-secondary-interest-query-generation-spec.md`](./2026-07-02-like-secondary-interest-query-generation-spec.md)
> and [`2026-07-03-local-first-inspiration-grounding-spec.md`](./2026-07-03-local-first-inspiration-grounding-spec.md).

## Goal

把当前"**5 次 LLM 调用 + 确定性否决 → LLM 修复**"的关键词灵感链路,重构成
"**1 次 LLM(合并"抽轴"+"出词")+ 确定性装配 + 可累积可打分的轴库(灵感候选)**"。

三个可衡量目标:

1. **降 LLM 次数**:regular 一轮从 5 次(brainstorm / brainstorm.repair / curate /
   curate.repair / legacy planner)降到 **1 次**。
2. **保住灵感(横向丰富度)**:把"轴 / lens"从现在"生成了但被收敛层杀死"的隐性状态,
   提升为**一等维度**,覆盖按构造尽力满足、缺口显式上报(`coverage_shortfall`),
   不再靠事后 repair 补。
3. **让灵感可累积、可追溯、越跑越准**:轴写进库、带 evidence 溯源、随 yield/delight
   打分,好轴复用、坏轴沉底、时效轴到期退休。

### 一句话原则

**语义/发散交给 LLM(一次、带全局约束地过度生成),组合/收敛交给确定性层
(软打分 + 覆盖优先 + 缺口显式);grounding evidence 是"轴的来源",不是"关键词的来源"。**
取消"否决→修复"回路——改"过度生成 → 挑选"。

## Background — batch4 验收暴露的问题

以 `keyword_inspiration_e2e_batch4_after_selection_report_20260705.json` 为证据:

| # | 现象 | 根因(架构层) |
|---|---|---|
| B1 | 一轮串行最多 5 次 LLM | brainstorm/curate 各带 repair + 中间隔着确定性否决层 |
| B2 | 覆盖倾斜:游戏 7/7、NBA 3/7、科技新闻 1/7 | 每平台各自贪心 + reserve/repair 事后补,无全局配额 |
| B3 | youtube 25/25 候选被 `platform_style_mismatch` 全拒 → 强制 repair | marker 白名单是**硬否决门**,且英文 marker 集过窄 |
| B4 | 角度塌缩:7 平台几乎全是"独立游戏安利";`game_review_02`(设计师/机制拆解)几乎没活到输出 | 灵感被生成了,但收敛层(近邻漂移 + marker 专杀非套路措辞 + repair 求稳)把它压回最套路的轴 |
| B5 | `evidence_quality` 恒等于 `1.0`(`keyword_planner.py:2843`) | 硬编码常量,不反映主题相关性;离题证据(NBA↔美光财报)照样满分 |
| B6 | preview 与 production 各写一份 consume/filter/repair 循环 | 逻辑会漂移;preview 报告不能代表 prod,而验收正靠 preview |
| B7 | `keyword_planner.py` 3944 行,inspiration 编排全塞在 KeywordPlanner 内 | 编排层未拆分;纯函数被 5 次 LLM 包裹,无法表驱动单测 |

## Non-Goals

- 不改 grounding 的**检索 provider 实现**(local-first provider 保留);但 probe query 的
  **生成方式**从 LLM brainstorm 改为确定性构造,evidence 的**用途**从"转关键词"改为
  "④ 的上下文"(两处都是本方案的 in-scope 改动,列在这里只为澄清边界)。
- 不改 gate / cohort 统计口径(replace 门禁保持现状)。
- 不在本期接 yield → 轴打分的回填闭环(建表占位,phase 2 接)。见 Phasing。
- 不动 explore/related_chain 等其它 discovery strategy。

## Target Pipeline

```
① 选兴趣 (确定性)     按 interest weight 排序取多样性窗口(确定性, 非随机) + 鉴权(过采样降权/跳过)
                      鉴权信号 = discovery_interest_selection_ledger 近 K 轮频次 + 轴库饱和度
② 取轴   (确定性读库) 从 discovery_inspiration_axis 捞选中兴趣的现有轴
                      按 (freshness × max(yield_score, prior)) 排序, 丢弃 stale / time_sensitive 过期
③ 取素材 (确定性检索) probe query 由确定性模板拼出(interest_label × 现有轴 axis_label /
                      example_terms × 少量池内高产词), 经现有 local-first provider 检索 +
                      历史入池 —— **删除 brainstorm LLM 前置**, ④ 之前不再有任何 LLM
④ 生成   (LLM ×1)     输入[选中兴趣 + 现有轴 + 新素材 + 平台风格指南]
                      输出{ axes[] 增量新轴, keywords[] 沿轴的平台原生候选词 }
⑤ 装配   (确定性)     硬门(去重/url/超长/脚本) + 软分(风格贴合→排序, 不淘汰)
                      + 按 interest×axis×platform 配额挑选(覆盖优先, 缺口显式)
                      + core/decoration 拼词
⑥ 回写   (确定性)     新轴 upsert 进库; 复用轴 bump use_count/last_used_at
                      [phase 2] yield/delight 回填 axis.yield_score
```

只有 ④ 用 LLM。①②③⑤⑥ 全是确定性 / 检索。

**改写范围(关键)**:现状里 grounding 之前有一次 `_brainstorm_inspiration_branches` LLM 调用
(`keyword_planner.py:1702` → LLM 于 `:2982`),grounding 的检索 query 来自 LLM 分支——若"③
现状不变",5→1 的目标就不成立。因此 Phase 1 **删除该前置**,③ 的 probe query 改为确定性构造;
且 `_run_inspiration_stage`(regular)与 `_run_shared_inspiration_stage`(regular+explore 合并
路径,`keyword_planner.py:1456`,同样先 brainstorm)**都在改写范围内**。

### 灵感在链路里的位置(对齐前面讨论)

- **③ grounding 是灵感的素材源**;**④ 的 axes 输出是灵感的发散产物**;
  **轴库(`discovery_inspiration_axis`)是灵感的持久化沉淀**。
- evidence **不再被转成候选词**(消除 B5 的 raw-title 倒灌与离题满分):
  evidence 只作为 ④ 的上下文,让模型"沿素材揭示的轴发散",关键词一律由 ④ 产出。
- 收敛层(⑤)新增一条 KPI:**角度存活率**——不许把 ④ 的多样性又收敛没了(治 B4)。

## Data Model — `discovery_inspiration_axis`(灵感候选库)

新开一张表,**不复用** `discovery_inspiration_expansion_cache`:后者是"某一轮某条 expansion
顺带的 `detail_axes` 标签",没有跨轮聚合、去重、打分、新鲜度;轴库是蒸馏在其上的**可复用、
有成绩、会过期**的一等实体。

```sql
CREATE TABLE IF NOT EXISTS discovery_inspiration_axis (
    axis_id           TEXT PRIMARY KEY,   -- 稳定 hash(interest_label + normalize(axis_label))
                                          -- normalize = NFKC + casefold + 去空白/标点,
                                          -- 防止措辞微调裂出重复轴
    interest_label    TEXT NOT NULL,      -- 所属二级兴趣(与 selection ledger 对齐)
    interest_id       TEXT,               -- 若有稳定 interest_id 则填
    axis_label        TEXT NOT NULL,       -- "设计师视角/机制拆解" / "子类型:种田城建模拟" / "锚点:只狼"
    axis_kind         TEXT NOT NULL,       -- 软分类: subgenre|creator_lens|hands_on|anchor|community_vocab|event|method
    example_terms     TEXT,               -- JSON: 该轴携带的社区语汇 ["耐玩","劝退","拆解"]
    evidence_refs     TEXT,               -- JSON: 催生该轴的 grounding 记录/URL(可追溯)
    source            TEXT NOT NULL,       -- external_search | pooled_history
    time_sensitive    INTEGER NOT NULL DEFAULT 0,   -- 1=强时效(交易截止日/选秀), 常态期自动降权
    freshness_ttl_days INTEGER,           -- time_sensitive 轴的有效窗口
    yield_score       REAL NOT NULL DEFAULT 0.0,    -- [phase2] 该轴出的词的 admissions/delight 回填
    admissions        INTEGER NOT NULL DEFAULT 0,   -- [phase2]
    use_count         INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'active', -- active|stale|retired
    created_at        TEXT NOT NULL,
    last_used_at      TEXT,
    last_refreshed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_discovery_inspiration_axis_interest
    ON discovery_inspiration_axis (interest_label, status);
```

三张表分工:

| 表 | 语义 | 生命周期 |
|---|---|---|
| `discovery_inspiration_probe_cache` | 原始 grounding 素材(世界上/历史里有啥) | 随 grounding 刷新 |
| `discovery_inspiration_axis` **(新)** | 蒸馏出的可复用灵感轴 + 成绩 + 新鲜度 | 累积,慢变 |
| `discovery_inspiration_expansion_cache` | 某一轮的临时 curated 产物 | 每轮临时 |

### 有界化与排序(Phase 1 即生效)

- **排序**:`(freshness × max(yield_score, exploration_prior))`,`exploration_prior` 默认
  `0.3`。Phase 1 里 `yield_score` 恒为 0,若直接乘积则全库并列 0、"好轴先用"退化为随机——
  prior 保证新鲜度仍能区分排序;tie-break 依次为 `last_refreshed_at` 降序 →
  `use_count` **升序**(把机会让给少用的轴,天然多样)→ `axis_kind` 轮转。
- **每 interest active 轴数上限**(默认 16):upsert 后超限时,把排序值最低的溢出轴置
  `status='stale'`。这是 Phase 1 就生效的有界化规则,不依赖 Phase 2 的衰减/退休调度;
  库增长因此有上界 `O(interests × 16)`。
- **轴标签复用**:④ 的 prompt 把 `existing_axes`(含 `axis_id`)喂给模型并要求
  "语义相同的轴必须原样引用现有 `axis_id`/`axis_label`,不许换措辞重造",配合 normalize
  兜底,双保险防重复轴。

### 新增 DAO(`storage/database.py`)

- `upsert_inspiration_axes(axes: list[AxisRow], *, bump_usage: bool = True) -> None` — ④ 后
  回写,冲突按 `axis_id` 合并(更新 `last_refreshed_at`、追加 `evidence_refs`;
  `bump_usage=True` 时 bump `use_count`/`last_used_at`,preview 路径传 `False`);随后执行
  上文的 per-interest active 上限规则。
- `list_inspiration_axes(interest_labels, *, limit, now) -> list[AxisRow]` — ② 读取,内部按
  上文排序规则过滤 `status!='active'` 与 time_sensitive 过期。
- `[phase2] backfill_axis_yield(axis_id, admissions, delight) -> None` — ⑥ 回填。

`AxisRow` 作为 `discovery/inspiration.py` 的新 frozen dataclass。

## The Single Merged LLM Call(④)

**一次调用同时"增量抽轴 + 沿轴出词"**,输出拆成两个数组,既不牺牲质量也对 prompt-cache 友好。

### Input payload(user message,按"最稳定→最易变"排序,遵守 prompt-cache 约定)

```jsonc
{
  "profile_digest": "...",                 // 最稳定
  "platform_guides": { "youtube": {...} }, // 稳定:各平台 query_style 标记/示例(降级为提示, 非门)
  "selected_interests": [                   // 本轮候选兴趣(确定性选出)
    { "label": "游戏评价", "parent": "游戏", "weight": 0.635 }
  ],
  "existing_axes": [                        // 来自轴库,较稳定 → 命中缓存
    { "axis_id": "...", "interest": "游戏评价",
      "axis_label": "设计师视角/机制拆解", "axis_kind": "creator_lens",
      "example_terms": ["拆解","设计理念"] }
  ],
  "fresh_evidence": [                        // 最易变:本轮 grounding 素材(标题/讨论/URL)
    { "interest": "游戏评价", "title": "浅析《只狼》忍义手的设计理念【游戏提灯】",
      "url": "https://...", "source": "pooled_history" }
  ],
  "allocation_targets": {                    // 确定性算出的配额(见 ⑤)
    "游戏评价": { "platforms": ["bilibili","zhihu","reddit"], "min_axes": 2 }
  }
}
```

- `system_prompt` 100% 静态(module 级常量),遵守
  [CLAUDE.md 的 LLM Prompt-Cache 约定](../../CLAUDE.md);所有 per-call 变量在 user message。
- `existing_axes` 段较稳定 → 模型只需**增量发现新轴**(比冷推轻),且该段命中缓存。
- `json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)`。

### Output schema

```jsonc
{
  "axes": [                                 // 增量:本轮从 fresh_evidence 新发现/精炼的轴
    { "interest": "游戏评价", "axis_label": "子类型:种田城建模拟",
      "axis_kind": "subgenre", "example_terms": ["耐玩","沙盒"],
      "evidence_refs": ["https://..."], "time_sensitive": false }
  ],
  "keywords": [                             // 沿轴(现有 + 新)的平台原生候选词
    { "interest": "游戏评价", "axis_id_or_label": "设计师视角/机制拆解",
      "platform": "bilibili",
      "core_concept": "只狼 忍义手 设计理念",   // 进搜索文本
      "decoration": "拆解",                     // 可选;marker/风格,装配层决定要不要拼
      "recency_sensitivity": "low" }            // low→不加年份; high→走 sort 参数而非文本
  ]
}
```

关键约束写进 prompt:
- `keywords` 必须**跨轴分布**——同一 interest 至少覆盖 `allocation_targets.min_axes` 根不同轴
  (治 B4 角度塌缩)。
- **过度生成**:每个 allocation 槽位 ≥2 个候选(挑选权在 ⑤,不在模型)。
- 语义相同的轴必须原样引用现有 `axis_id`/`axis_label`,不许换措辞重造。
- `core_concept` 与 `decoration` **分开输出**——装配层据此控 token 数保召回。
- 时效性走 `recency_sensitivity` 字段,**不许把 "2025" 焊进 `core_concept`**。

### 预算与失败处理(取代 repair 回路)

单次合并调用必须**自带预算与降级设计**,否则 interests × axes × platforms 的组合会把输出
撑爆(现 curator 上限 4096 tokens,`keyword_planner.py:310`;现实现靠平台分批回避,`:938`)。

**输入硬上限**(超出即确定性截断,telemetry 记录):

| 维度 | 上限 | 说明 |
|---|---|---|
| `selected_interests` | 4 | ① 的抽样数收敛到 4(现默认 6) |
| `existing_axes` / interest | 6 | ② 排序后取 top-6 |
| `fresh_evidence` 总数 | 24(每 interest ≤ 8) | ③ 排序后截断 |
| `platform_guides` | 仅本轮目标平台 | 不发全量 7 平台指南 |
| 输出 `max_tokens` | 8192 | 期望输出 ≤ ~60 keywords + ~12 axes,留裕量 |

**解析与降级(0 次额外 LLM,一轮 LLM 计数恒 ≤ 1)**:

1. **容错解析**:JSON 截断时按**完整对象前缀 salvage**(逐项校验 `axes[]`/`keywords[]`,
   丢弃残缺尾项),telemetry 记 `parse_salvaged=true` 与丢弃计数。
2. **失败降级**:④ 返回空 / 完全不可解析 → **确定性 fallback,分两级**:
   - 有现有轴:以 `[interest_label × 现有轴 example_terms]` 模板 realize 候选;
   - 轴库也为空(冷启动 + ④ 失败双重不幸):直接以 `interest_label` 本身构造
     interest-only 检索词(质量降级但可用;selected_interests 非空是 stage 运行的前提,
     所以该级**总能为脚本兼容平台产出候选**)。interest-only 候选同样过全部硬门——
     脚本不兼容的平台(纯中文兴趣 × youtube/reddit)记
     `coverage_shortfall(reason=script_mismatch)`,不硬塞。轴覆盖此时自然记
     `coverage_shortfall(missing_axes)`。
   两级都进 ⑤ 装配;telemetry 记 `llm_call_failed=true`。该轮 LLM 计数为 1(失败的那次),
   **没有 repair 调用、没有重试**。
3. **部分缺失**:某 interest 在 `keywords[]` 里无候选或轴数不足 → 不回炉,由 ⑤ 的
   **确定性补位**处理(见 Deterministic Layers ⑤)。

## Deterministic Layers

### ① 选兴趣 + 鉴权

- 按 `weight` 排序取多样性窗口(现有 `_selected_inspiration_interests` 保留骨架——注意
  现实现是**确定性排序窗口切片**,不是随机抽样,保持确定性,别引入随机)。
- **鉴权(确定性)** = 读 `discovery_interest_selection_ledger` 近 K 轮频次(**只统计
  `selection_scope='production'` 的行**——preview 是检视工具,不消耗生产端探索预算,
  也避免"验收连跑两轮 preview、第二轮换兴趣"的自我干扰)+ 轴库饱和度
  (某兴趣的 active 轴最近全被用过 = 选太多了)→ 对过采样兴趣降权/跳过,给低权兴趣让位。

### ② 取轴

- `list_inspiration_axes(selected_interests)`,按 `(freshness × max(yield_score, prior))`
  排序(见 Data Model §有界化与排序);time_sensitive 且超 `freshness_ttl_days` 的置 stale
  不返回。

### ③ 取素材(确定性 probe)

- probe query 确定性构造:`interest_label`、`interest_label + axis_label`、
  `interest_label + example_term` 的组合,按 ② 的轴排序取前 M 条;补少量池内高产词。
- 经现有 local-first provider 检索(实现不变),沿用现有 TTL cache 与历史入池。
- **不再有 `_brainstorm_inspiration_branches`**;`_fallback_brainstorm_branches` 一并删除
  (其"兜底出分支"职责由确定性 probe 构造天然覆盖)。

### ⑤ 装配(取代现有"否决层 + repair + backfill")

抽成**纯函数** `materialize_platform_keywords(candidates, allocation, config) -> (keywords, telemetry)`:

1. **硬门(仅 4 个,廉价)**:去重(归一化 + 可选 embedding 近邻)、url、超长、语言脚本。
   **删除** `platform_style_mismatch` 硬否决。
2. **软分**:平台 marker 命中 = 排序加分,不淘汰(治 B3;marker 集不再需要穷举)。
3. **配额分配**:按 `interest × axis × platform` 贪心/小 ILP 选满每平台槽位,
   同 interest 两槽必来自不同轴(治 B4)。**覆盖优先、缺口显式,由三层叠加实现,不靠 repair**:
   - 第一层:④ 的 prompt 要求过度生成(每槽位 ≥2 候选、跨 ≥ min_axes 轴);
   - 第二层:装配的挑选目标函数**先覆盖后分数**(先满足每 interest 的轴/平台覆盖,
     再按软分择优);
   - 第三层:候选池仍填不满的槽位走**确定性补位**——以
     `[interest_label + axis example_terms]` 模板直接拼词(`origin=deterministic_fill`
     入 telemetry)。补位是**平台感知**的:模板产物必须过**同一套硬门**(含语言脚本门),
     优先选用与目标平台脚本匹配的 example_terms;凑不出合规文本(如纯中文兴趣 ×
     youtube/reddit 的英文脚本要求)就**不硬塞垃圾词**,记
     `coverage_shortfall(reason=script_mismatch)`。连可用轴都没有(轴库空 + ④ 没产轴)
     的槽位记 `coverage_shortfall(interest, missing_axes, missing_platforms)`,**不静默**。
4. **拼词**:`core_concept` 为主,`decoration` 按平台风格与 token 预算可选拼接;
   `recency` 走 sort/filter 参数,不进文本(保召回)。
5. 输出 `telemetry`:每 interest 的**轴覆盖数**、软分分布、被硬门拒的少量项、
   `deterministic_fill` 计数、`coverage_shortfall` 明细、`parse_salvaged` /
   `llm_call_failed` 标志。

preview 与 production **共用此纯函数**(治 B6);函数可用假 candidates 表驱动单测(治 B7)。

### ⑥ 回写

- `upsert_inspiration_axes(output.axes + 被复用的现有轴)`;bump `use_count` / `last_used_at`。
- **[phase 2]** cohort 的 admissions/delight 按 `axis_id` 回填 `yield_score`。

## Code Structure Changes

1. 抽 `InspirationKeywordPipeline`(新模块或 `discovery/inspiration_pipeline.py`)承接
   ①–⑥ 编排,`KeywordPlanner` 只做调度(治 B7,拆 god-file)。
2. `materialize_platform_keywords` 纯函数 + preview/prod 统一(治 B6)。
3. `_platform_style_rejection_reason` 从"门"改"分":新增 `platform_style_score(keyword, platform) -> float`,
   旧的硬否决删除(治 B3)。
4. **配置收敛**:14 个 `inspiration_*` 旋钮压到 ~4(`enabled` / `replace` / 一个"广度-预算"档位 / `backends`),
   其余从档位派生成内部常量。

## Phasing

- **Phase 1(本期,可独立验证)**:建 `discovery_inspiration_axis` 表(含 `yield_score`/`admissions`
  占位字段,先不回填)+ 单次合并调用 ④ + 确定性装配 ⑤(含 style→软分、core/decoration 拼词)
  + preview/prod 统一 + 鉴权用 selection ledger。**验收即可看到 LLM 5→1、覆盖不塌、轴库跨轮复用。**
- **Phase 2**:yield → `axis.yield_score` 回填闭环;time_sensitive 衰减/退休;config 收敛;
  `InspirationKeywordPipeline` 抽取落定。

## Rollout / 回滚 / Preview 写入策略

- 一切仍在 `inspiration_search_enabled`(默认 off,`config.py:324`)之后;**默认用户零行为
  变化**。
- 对已开启 inspiration 的用户(pre-alpha 单用户,当前即开发者本人):旧
  brainstorm/curate/repair 路径**同版本删除**,同一 flag 直接切到新链路。**运行时没有旧路径
  可回滚**,回滚手段 = 版本降级(release channel 支持)。刻意不加临时兼容子开关——与
  "14 个 `inspiration_*` 旋钮收敛到 4"的目标相抵,且单用户场景下双路径共存只会拖延删除。
- 分支在验收跑(见 Acceptance Criteria)通过前不合 main。
- **Preview 写入策略**:preview 对关键词行保持 dry-run(现状);对**轴库默认同样不写**,
  新增 CLI flag `keyword-inspiration-preview --persist-axes` 显式开启轴库 upsert——验收的
  "两轮复用"用该 flag 跑,日常 preview 不污染库。preview 的 selection ledger 记录保留
  `selection_scope='preview'`,但**鉴权只统计 production 行**(见 ①)。此外
  `--persist-axes` 下的轴 upsert **不 bump `use_count` / `last_used_at`**——"使用"是生产
  语义,preview 只沉淀轴行本身(新轴插入、evidence 合并);否则第一轮 preview 会通过
  **轴库饱和度**信号让第二轮换兴趣。两条规则合起来,连跑两轮 preview 的兴趣选择才真正
  稳定,不会自我冷却换兴趣。

## Acceptance Criteria

1. 一次 regular inspiration stage 的 LLM `caller=` 计数 **≤ 1**(成功轮 == 1;fallback 轮
   计入失败的那 1 次,无 repair/重试),`_run_inspiration_stage` 与
   `_run_shared_inspiration_stage` 两条路径都满足;grounding 阶段 LLM 计数 == **0**。
2. 每个 selected interest 在最终产出中被 **≥ N 个平台**覆盖(N 由 `allocation_targets` 定),
   不再出现"科技新闻仅 1/7";候选池不足时由确定性补位达成,补不上的记
   `coverage_shortfall`,**不允许静默缺口**。
3. 每个 interest 的最终关键词**跨 ≥ 2 根不同轴,或 telemetry 记明确的
   `coverage_shortfall(missing_axes)`**(单轴/空池退化用例走 shortfall,不算违约;
   `deterministic_fill` 产出的槽位计入轴覆盖),无 niche≈underrated 式近义复述。
4. **无关键词仅因 style 被拒**(rejected_reasons 中不含 `platform_style_mismatch`)。
5. preview 与 production 走**同一个** `materialize_platform_keywords`(引用同一符号)。
6. `discovery_inspiration_axis` 在连续两轮间被 upsert 并复用(`--persist-axes` 下第二轮
   `existing_axes` 非空;鉴权只读 production-scope ledger + preview 轴写入不 bump
   use_count/last_used_at + 选择是确定性排序,三者共同保证两轮 preview 选中相同兴趣,
   复用可被稳定观测)。prompt-cache 合规由单测强制(builder 进
   `test_prompt_builder_system_messages_are_call_invariant`);观测性上,provider 上报缓存
   指标时 `openbiliclaw cost --by caller` 第二轮应出现非零 cached tokens(cpa 等不上报
   缓存的 provider 豁免此条)。
7. `materialize_platform_keywords` 有**表驱动单测**(喂假 candidates,断言配额/去重/软分/
   拼词/确定性补位/shortfall telemetry),不依赖真 LLM;覆盖薄候选池、单轴、空候选三个
   退化用例。
8. **截断/垃圾输出可降级**:mock 截断 JSON → 前缀 salvage 生效;mock 空输出 → 确定性
   fallback 对**脚本兼容槽位**产出候选(有轴走模板级,轴库空走 interest-only 级,并记
   `coverage_shortfall(missing_axes)`);脚本不兼容槽位(如纯中文兴趣 × youtube/reddit)
   记 `coverage_shortfall(reason=script_mismatch)`,不要求产出候选;两种情况总 LLM 计数
   仍 ≤ 1。
9. 同一轮 upsert 超过 per-interest active 上限时,溢出轴被置 `stale`(库有界)。

## Open Decisions(已拍板)

- **新表 `discovery_inspiration_axis`,不扩 expansion cache** — locked。
- **不建 bootstrap 种子文档**,库空由 ④ 从 grounding 冷启动 — locked。
- **yield 回填 phase 2**,phase 1 只建表 + 占位字段 — locked。
- **轴自由发挥**(LLM 每轮从素材现抽),非固定模板 — locked。
- **③ probe 确定性化 + 删除 brainstorm 前置**,shared stage 一并改写 — locked
  (2026-07-05 Codex 对抗审查 R1)。
- **失败降级 = 确定性 fallback,无 repair/重试**,一轮 LLM 恒 ≤1 — locked(同上)。
- **不加兼容子开关**,回滚 = 版本降级 — locked(同上)。
- **preview 轴库写入走 `--persist-axes` 显式开启** — locked(同上)。
- **失败 fallback 两级阶梯**(轴模板 → interest-only),冷启动双失败也能确定性产出 —
  locked(2026-07-05 Codex 对抗审查 R2)。
- **确定性补位平台感知**,过同一套硬门,凑不出合规文本记 shortfall 不硬塞 — locked(同上)。
- **鉴权只统计 production-scope selection ledger**,preview 不消耗探索预算 — locked(同上)。
- **preview 轴 upsert 不 bump usage 字段**(`bump_usage=False`),interest-only fallback 同样
  过脚本门,"覆盖保证"措辞统一为"按构造尽力 + 缺口显式" — locked(2026-07-05 Codex
  对抗审查 R3)。

## Worked Example — 游戏评价(端到端)

```
① 选中: 游戏评价(w=0.635)                    // 鉴权: 近 3 轮未过采样, 放行
② 取轴: [设计师视角/机制拆解(creator_lens, yield 0.4),
         子类型:种田城建模拟(subgenre, yield 0.1)]   // 来自库
③ 取素材(确定性 probe: "游戏评价"/"游戏评价 设计理念"/"游戏评价 耐玩" …):
   《只狼》忍义手设计理念 / GMTK 拆解 / 环世界 耐玩度 / 文明7 玩家想法
④ 一次 LLM:
   axes(增量): [锚点:只狼(anchor), 锚点:文明7(anchor)]
   keywords(跨轴):
     bilibili  core="只狼 忍义手 设计理念"  axis=设计师视角  deco="拆解"    recency=low
     zhihu     core="如何评价 文明7 的设计取舍" axis=锚点:文明7            recency=low
     reddit    core="rimworld colony sim replayability" axis=子类型       recency=low
⑤ 装配: 去重 → 软分排序 → 保证"游戏评价"跨≥2 轴 → 拼词(不加"2025")
   最终: bili=只狼设计拆解 / zhihu=文明7设计评价 / reddit=殖民模拟耐玩
⑥ 回写: upsert 锚点:只狼、锚点:文明7; bump 设计师视角/子类型 use_count
```

对比 batch4 现状(bili/reddit/youtube 全是"独立游戏推荐盘点"):
**同样的 grounding 素材,新链路把"设计师视角/锚点"这些横向轴保住并落到了输出,灵感没死。**
