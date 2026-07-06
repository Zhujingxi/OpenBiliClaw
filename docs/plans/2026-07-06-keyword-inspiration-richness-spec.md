# Keyword Inspiration: 多平台丰富度修复 Spec（Phase 2.1）

> **Status:** Reviewed — 2026-07-06 (Codex 4-round adversarial review, R1-R3 findings applied, R4 APPROVE)。承接 Phase 1/2（同 worktree），修一个真机暴露的具体退化。
> **Branch:** `feature/discovery-inspiration-mvp`。

## 问题（真机诊断，数据锁定）

单次合并 LLM 调用要覆盖 `interests(≤4) × platforms` 个槽位。平台越多，一次要吐的关键词越多
（2 平台=16 词 / 3 平台=25 / 6 平台=48），**丰富度随之单调下降**：

- 2 平台：`刺客信条剧情 解析`、`小学馆谢罪 如何看待`（锚定具体事件/作品）
- 6 平台：`新游推荐 盘点`、`游戏资讯 真实体验`（core_concept 塌成轴标签 + 平台后缀）

**根因（三个事实）**：
1. 喂进去的 grounding 素材三批相同且丰富（都含"士官长首次登陆PS5"），`fresh_evidence_truncated=0`
   —— 不是素材饿死。
2. `parse_salvaged=False` —— 8192 输出 token **没用完**。模型不是没预算，是**主动偷懒**：
   槽位多时把 `core_concept ≈ 轴标签` + 平台 marker 交差，不去雕琢素材里的具体锚点。
3. 兴趣数恒为 4（planner `min(4, sample_size)` cap），high 档没加宽兴趣，只加宽平台词数。

结论：丰富度瓶颈是**模型行为**（prompt 未强制具体性），不是 token 预算，也不是素材。

## Non-Goals

- 不破坏 Phase 1 生成调用不变式:**一轮 ≤1 次成功生成调用**;允许 max_tokens 错误的有界一次重试
  （错误恢复,非 salvage/repair 循环）;不引入 repair 回路。
- 不动兴趣数 cap（4）、不动 grounding、不动 config 档位语义。
- 不改 prompt-cache 约定：system prompt 必须保持 100% 静态（新增规则是静态文本，对每次调用一致）。

## Fix（四处：prompt 产出 + 装配层择优 双管，才是完整闭环）

> **关键（Codex R1 S1）**：F1 只让模型**产出**更多具体候选,但确定性装配器
> `_choose_materialize_candidate`（`discovery/inspiration.py`）按 `(需要新轴, style_score,
> -index)` 排序,**没有具体性信号**——就算池子里同时有"士官长 登陆PS5"和"新游推荐 盘点",
> 装配器照样可能挑中后者。所以必须 F1（产出）+ F1.5（择优）**双管**,缺一不可。

### F1（主·产出侧）：prompt 强制 core_concept 具体化

在 `_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT` 的 Rules 里新增（静态文本，不含 per-call 数据）：

- **core_concept 必须锚定 `fresh_evidence` 里的具体实体/事件/作品/争议**（专有名词、作品名、
  人物、具体机制），**不得直接复述 interest 或 axis_label**。反例明确写进 prompt：
  interest="游戏资讯与推荐" 时，`新游推荐`/`游戏资讯` 这类等于话题名的 core_concept 是**不合格**的；
  合格的是 `士官长 登陆PS5`、`腾讯网易 新游发布`。
- 当某槽位的 evidence 确实没有具体锚点时，**允许**退回话题级 core_concept（不硬造），但这应是少数。

这条对所有平台数都生效，尤其救高平台数。system prompt 静态 → 仍进 cache、仍过
`test_prompt_builder_system_messages_are_call_invariant`。

### F1.5（主·择优侧，Codex R1 S1）：装配器加具体性排序信号

`_choose_materialize_candidate` 的排序键从 `(需要新轴, style_score, -index)` 改为
`(需要新轴, is_specific, style_score, -index)`：

- `is_specific(candidate)` = **确定性判定（剥离残留法,方向 R2 S2 + CJK R3 S2 修正）**：
  **按 span/子串剥离,不按空格 token**（中文关键词常无空格,空格切分会漏剥）——把
  `core_concept` 归一化字符串里的 interest span、axis_label span、以及泛化/风格 marker 词
  （复用装配层已有平台 marker 词表：盘点/推荐/资讯/速看/合集/攻略/测评/解析/科普/避坑/亲测/
  清单/如何/评价/原理/discussion/review/explained/recommendation/tips… + `platform_style`
  标记集），**按最长优先做子串移除**,再去掉残留空白/标点,**剩非空 → `True`（有真实锚点）；
  剩空 → `False`（话题名 + 泛化后缀的复述）**。
  - **反例必须判 False（含无空格）**:`新游推荐 盘点` 与 `新游推荐盘点`（axis=`新游推荐`）
    都剥掉"新游推荐"+"盘点"→ 空 → False。空格 token 相等法会把无空格的 `新游推荐盘点` 当整
    token 漏判成 True——这是 R3 S2 抓的 CJK 漏洞,子串剥离才对。
  - **正例必须判 True（含无空格）**:`游戏资讯 士官长登陆PS5` 与 `游戏资讯士官长登陆PS5`
    （interest=`游戏资讯`）剥掉"游戏资讯"→ 剩"士官长登陆PS5"(真实体) → True。
- 具体候选**同槽位内压过**泛化候选；两者都具体或都泛化时退回原有 style_score 排序。
- 纯确定性、可表驱动单测,不依赖 LLM。这是把 F1 的"产出具体候选"真正落到"输出选具体候选"。
- 确定性补位（`_deterministic_fill_candidate`,轴模板拼词）本就是话题级,`is_specific=False`
  合理——它只在没有真候选时兜底,不参与与真候选的竞争。

### F2（次·保险，Codex R1 S2 修公式+provider 保护）：max_tokens 随槽位动态放大

虽然当前没触发截断，但 F1 会让每个 core_concept 变长（具体锚点比话题名长），48 槽 × 更长
输出可能逼近 8192。因此：

- **阈值式公式（修 R1 S2 矛盾）**：低槽位保持 floor 8192,只在超阈值后才加码：
  `max_tokens = min(CEIL, 8192 + max(0, slots - 24) * 256)`，`slots = len(selected_interests)
  * len(target_platforms)`。→ 8 槽=8192、24 槽=8192、48 槽=8192+24*256=14336（≤16384）。
  阈值 24 = 已知舒适区（3 平台×4 兴趣,诊断里 3 平台丰富度尚可）。
- 常量:floor 复用 `_INSPIRATION_AXIS_KEYWORD_MAX_TOKENS=8192`；新增
  `_INSPIRATION_AXIS_KEYWORD_MAX_TOKENS_CEIL=16384`、`_PER_SLOT_TOKEN_BUDGET=256`、阈值 24。
- **provider 保护（修 R1 S2）**：`openai_compatible` 把 max_tokens 原样透传,provider 上限
  <16384 会让请求直接失败 → 触发确定性 fallback(整轮 LLM 白丢,坏)。因此:
  - 实际请求的 max_tokens 写进 telemetry(`llm_telemetry.max_tokens_requested`)。
  - 单次调用捕获 **max_tokens 相关的 provider 错误**,**降到 floor 8192 重试一次**(有界一次,
    非无限;这是错误恢复,不是 Phase-1 禁的"salvage 后 repair");重试仍失败才走确定性 fallback。
  - Plan Task 2 先核实 deepseek-v4-flash 经 sensenova 网关的实际 max_tokens 上限,若确知
    <16384 则把 CEIL 调到该值,重试逻辑作为未知 provider 的兜底。
- 定位:纯防截断保险(诊断已证 token 非当前瓶颈),但 F1+F1.5 会让 core_concept 变长,48 槽下
  可能逼近 8192,故保留。

### F3（观测缺口）：preview 报告回填 core_concept / decoration

现 `RealizedKeyword.metadata` 只有 `axis_label`/`origin`/…，**没有 core_concept/decoration
拆分**（装配时已拼进最终 keyword，拆分丢失），导致 preview 报告无法核查"core 是否具体"。

- `_realized_from_materialize`（`discovery/inspiration.py`）把 `candidate.core_concept` 与
  `candidate.decoration` 原样写进 metadata（两个新键）。
- **preview 报告实际不含 `metadata_by_platform`（修 R1 S2）**：pipeline 只为**插入**构建
  `metadata_by_platform`,且 preview 在插入前就 return,`report["metadata_by_platform"]` 从未被
  写入。因此必须**显式**在 preview 返回前 `report["metadata_by_platform"] = metadata_by_platform`,
  并加 preview 级测试断言这两键可见。
- 不影响最终 keyword 文本、不影响装配、不影响 admission。

## Acceptance Criteria

1. **prompt 契约单测**：system prompt 含"core_concept 必须锚定具体 evidence、禁止复述
   axis_label/interest,无锚点才可退话题级"规则；system prompt 仍 byte-identical 跨两次不同输入
   （`test_prompt_builder_system_messages_are_call_invariant` 通过）。
2. **F1.5 装配择优（核心行为断言,表驱动无 LLM）**：同一 interest/platform/axis 槽位内,喂
   {泛化候选 `core_concept=interest/axis_label` 复述, 具体候选 `core_concept=某专名事件`},
   `materialize_platform_keywords` 选中**具体候选**;两者都具体或都泛化时退回 style_score 排序
   (加断言防过度改动)。`is_specific` 判定单测（剥离残留法）:`话题名+泛化后缀`→False,
   `话题名+真实体`→True,纯专名→True。
3. **max_tokens 动态**：单测 slots ∈ {8, 24, 48} 断言 `min(16384, 8192+max(0,slots-24)*256)`
   （8→8192、24→8192、48→14336）;telemetry 记 `max_tokens_requested`;一轮仍恰好 1 次成功 LLM
   调用（max_tokens 拒绝的降 floor 重试是错误恢复,mock provider 抛 max_tokens 错→断言重试到
   8192 且只重试一次）。
4. **metadata 回填 + preview 可见**：`RealizedKeyword.metadata` 含 `core_concept`/`decoration`
   （表驱动,含确定性补位路径取值）;**preview 报告 `metadata_by_platform[platform][keyword]`
   显式含这两键**（preview 级测试,因现码根本不写该键——见 F3）。
5. **确定性丰富度指标（替换定性 AC,可离线核）**：纯函数 `restatement_rate(report)` = metadata 里
   **`is_specific=False`(剥离残留法,与 F1.5 同一判定)** 的关键词占比。**判定表必须含 R2 S2 的两类**:
   `话题词+泛化后缀`（如 `新游推荐 盘点`）判 restatement(=非具体),`话题词+真实体`（如
   `游戏资讯 士官长登陆PS5`）判非 restatement。**固定 6 平台 fixture**（含专名假 evidence,mock LLM
   返回泛化+具体混合候选）跑装配,断言修复后 `restatement_rate ≤ 0.3`、修复前(旧排序键) > 0.3。
6. **薄证据不幻觉（Codex R1 S2）**：thin-evidence fixture（evidence 无任何专名,只有话题词）→
   断言输出**退回话题级 core_concept,不出现 evidence 里没有的专有名词**（防 F1 逼模型编造）。
   用 mock LLM 模拟"遵守规则的退化输出",断言装配不把不存在的专名当具体候选。
7. **真机验收（Claude 亲跑,定性佐证 5）**：6 平台跑一轮,对比修复前后同兴趣同平台的词,
   `restatement_rate` 实测下降、出现专名锚点;仍单次 LLM、零缺口回归。
8. **不回归**：Phase 1/2 全测试套件（LLM 计数、覆盖、fallback、pipeline 抽取零漂移）全绿。

## Open Decisions

- **F1（产出）+ F1.5（择优）双管为主,F2 为辅** —— locked（2026-07-06 Codex R1 S1:装配层无具体性
  信号,只改 prompt 不够）。
- **具体性规则写成静态 prompt 文本 + 明确反例**,不引入 per-call 变量 — locked。
- **F1.5 `is_specific` 用剥离残留法**：core_concept 去掉 interest/axis_label token + 泛化/风格
  marker 后**还剩非空 token 即具体**（不是旧的"被 axis_label 包含"——那对更长的
  `话题名+泛化后缀` 会误判成具体）,进排序键 `(需要新轴, is_specific, style_score, -index)` —
  locked（R1 S1 + R2 S2 方向修正）。
- **max_tokens 阈值式 `8192 + max(0, slots-24)*256`,CEIL 16384**,请求值进 telemetry,max_tokens
  拒绝降 floor 重试一次 — locked（R1 S2 修公式矛盾 + provider 保护）。
- **preview 必须显式写 `report["metadata_by_platform"]`** —— locked（R1 S2:现码 preview 根本不写）。
- **验收用确定性 `restatement_rate` 指标 + 薄证据不幻觉 fixture** 替换定性描述 — locked（R1 S2）。
- **core_concept/decoration 进 metadata 仅为观测**,不改最终词 — locked。
