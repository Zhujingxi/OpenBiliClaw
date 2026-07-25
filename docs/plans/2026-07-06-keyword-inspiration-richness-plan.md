# Keyword Inspiration 多平台丰富度修复 — Implementation Plan（Phase 2.1）

> **Spec:** [`2026-07-06-keyword-inspiration-richness-spec.md`](./2026-07-06-keyword-inspiration-richness-spec.md)
> **Status:** Reviewed — 2026-07-06 (Codex 4-round adversarial review, R1-R3 findings applied, R4 APPROVE)。承接已提交的 Phase 1/2（`e1c4d5fe`）。
> **Executor:** Opus 4.8 subagent；Claude 逐 task 验收 + 真机验收；TDD。

**Goal:** 让高平台数（6 平台 = 48 槽/单次调用）的 core_concept 从"复述话题名"回到"锚定具体
素材事件"，不破坏一轮 ≤1 次成功 LLM 调用不变式。**F1（prompt 产出具体候选）+ F1.5（装配层选
具体候选）双管为主**（Codex R1 S1:光改 prompt,装配器没具体性信号照样挑泛化词）;F2 max_tokens
动态、F3 metadata 回填是辅助/观测。

**Tech Stack:** Python 3.11+, MyPy strict, Ruff 100-char, pytest（`asyncio_mode=auto`）。
Interpreter `.venv/bin/python`。测试内联手写假数据，无新 fixture。

**Invariants:** 一轮 ≤1 次**成功**生成调用（允许 max_tokens 错误的有界一次重试,非 salvage/repair）；
system prompt 100% 静态（进 cache）；不改最终 keyword **拼接**/admission（**候选排序择优是本次有意变更**,
见 Task 1.5）；Phase 1/2 现有测试零断言修改。

---

### Task 1: prompt 具体性规则（F1，主）

**Files:** `src/openbiliclaw/llm/prompts.py`；Test `tests/test_llm_prompts.py`

**Steps:**
1. 失败测试：`_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT` 含一条规则，语义为"core_concept 必须锚定
   fresh_evidence 里的具体实体/事件/作品/人物/机制，禁止直接复述 interest 或 axis_label；无具体
   锚点时才可退回话题级"，且带至少一组正反例（反：`新游推荐`；正：`士官长 登陆PS5`）。
2. 实现：把该规则作为**静态文本**加进现有 Rules 列表（编号顺延），不引入任何 per-call 变量、
   不 f-string。
3. 确认 `test_prompt_builder_system_messages_are_call_invariant` 仍通过（system prompt 跨两次
   不同输入 byte-identical）。
4. Gate：`.venv/bin/python -m pytest tests/test_llm_prompts.py -q` + ruff + mypy。

### Task 1.5: 装配器具体性排序（F1.5，主·择优侧 — Codex R1 S1）

**Files:** `src/openbiliclaw/discovery/inspiration.py`；Test `tests/test_discovery_inspiration.py`

**Steps:**
1. 失败表驱动测试（无 LLM）：同一 interest/platform/axis 槽位喂 {泛化候选
   `core_concept`=interest 或 axis_label 的复述, 具体候选 `core_concept`=某专名事件} →
   `materialize_platform_keywords` 选中**具体候选**;都具体或都泛化 → 退回 style_score 排序
   （加断言防过度改动）。
2. `is_specific(core_concept, interest, axis_label)` 纯函数（**剥离残留法,按 span/子串,非空格
   token — R2 S2 方向 + R3 S2 CJK 修正**）：把 core_concept 归一化字符串里的 interest span、
   axis_label span、泛化/风格 marker 词（复用装配层已有平台 marker 集）**按最长优先做子串移除**,
   去残留空白/标点后剩非空即 True,剩空即 False。**必测四类（含无空格 CJK）**:
   `新游推荐 盘点` 与 `新游推荐盘点`(axis=`新游推荐`)→ 空 → False;
   `游戏资讯 士官长登陆PS5` 与 `游戏资讯士官长登陆PS5`→ 剩"士官长登陆PS5" → True。
   （空格 token 相等法会把无空格的 `新游推荐盘点` 当整 token 漏判成 True——CJK 关键词常无空格,
   子串剥离才对。）
3. 改 `_choose_materialize_candidate` 排序键 `(需要新轴, style_score, -index)` →
   `(需要新轴, is_specific, style_score, -index)`。确定性补位候选 `is_specific=False`（话题级兜底,
   合理）。
4. 确定性 `restatement_rate(report)` 纯函数 + 固定 6 平台 fixture 测试（Spec AC5）：修复后
   `restatement_rate ≤ 0.3`,旧排序键下 > 0.3——证明 F1.5 真的改变了选择。
5. 薄证据不幻觉 fixture（Spec AC6）：evidence 无专名 → mock 返回话题级候选 → 断言装配不把不存在
   的专名当具体候选。
6. Gate：`.venv/bin/python -m pytest tests/test_discovery_inspiration.py -q` + ruff + mypy。

### Task 2: max_tokens 随槽位动态放大（F2，辅）

**Files:** `src/openbiliclaw/runtime/keyword_planner.py`（常量）、
`src/openbiliclaw/runtime/inspiration_pipeline.py`（调用点）；Test `tests/test_inspiration_pipeline.py`

**Steps:**
1. 核实 provider `max_tokens` 上限（读 `llm/openai_provider.py` 透传逻辑 + config 的
   default_provider=openai_compatible/deepseek-v4-flash 经 sensenova 网关）——确知 <16384 则把
   CEIL 调到该值并在报告注明;否则 CEIL=16384（重试逻辑作未知 provider 兜底）。
2. 常量:floor 复用 `_INSPIRATION_AXIS_KEYWORD_MAX_TOKENS=8192`;新增
   `_INSPIRATION_AXIS_KEYWORD_MAX_TOKENS_CEIL=16384`、`_PER_SLOT_TOKEN_BUDGET=256`、阈值常量 24。
3. 调用点算 `slots = len(selected_interests) * len(target_platforms)`,
   `max_tokens = min(CEIL, 8192 + max(0, slots - 24) * 256)`,传入单次调用;写
   `llm_telemetry.max_tokens_requested`。
4. **provider 保护（R1 S2）**：捕获 max_tokens 相关 provider 错误 → 降 floor 8192 **重试一次**
   （有界,非无限;错误恢复,非 Phase-1 禁的 salvage-repair）;重试仍失败才走确定性 fallback。
5. 失败测试:slots∈{8→8192, 24→8192, 48→14336};一轮恰好 1 次成功调用;mock provider 抛 max_tokens
   错 → 断言降 8192 重试且只重试一次。
6. Gate:targeted pytest + ruff + mypy。

### Task 3: core_concept / decoration 进 metadata（F3，观测）

**Files:** `src/openbiliclaw/discovery/inspiration.py`；Test `tests/test_discovery_inspiration.py`

**Steps:**
1. 失败测试：`materialize_platform_keywords` 产出的每个 `RealizedKeyword.metadata` 含
   `core_concept` 与 `decoration`（值来自对应 `MaterializeCandidate`；确定性补位路径也带上其模板
   core + 空 decoration）。
2. 实现：`_realized_from_materialize` 写入这两键（不改最终 `keyword` 文本、不改其它 metadata）。
3. **显式写 preview 报告（R1 S2:现码 preview 根本不写 `metadata_by_platform`,它只为插入构建且在
   插入前 return）**：在 preview 返回前 `report["metadata_by_platform"] = metadata_by_platform`;
   加 preview 级测试断言 `report["metadata_by_platform"][platform][keyword]` 含 core_concept/
   decoration。
4. Gate：targeted pytest（含 `tests/test_inspiration_pipeline.py` 的 preview 报告断言）+ ruff + mypy。

### Task 4: 文档 + 完整门

**Files:** `docs/modules/discovery.md`、`docs/modules/llm.md`、`docs/changelog.md`

**Steps:**
1. discovery.md：F1.5 装配层具体性排序 + max_tokens 动态 + metadata 增补 core/decoration;
   说明"平台越多单次调用越摊薄,prompt 产出 + 装配择优 + 动态预算三管齐下"。
2. llm.md（R1 S3,prompts.py 契约变更）：`_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT` 新增的
   core_concept 具体性规则 + 仍满足 byte-identical cache 契约。
3. changelog.md：当前版本块下一条 bullet。
4. 完整门：`.venv/bin/python -m pytest tests/test_llm_prompts.py tests/test_inspiration_pipeline.py
   tests/test_discovery_inspiration.py tests/test_keyword_planner.py tests/test_config.py -q` ;
   `ruff check src/ tests/` ; `mypy src/`。
5. **真机验收留给 Claude**（Spec AC7）：不要跑。

---

## Sequencing & risk

- Task 1（prompt 产出）+ Task 1.5（装配择优）是主力,缺一不可（R1 S1）;Task 1.5 是纯确定性、
  最能直接验证的一环。Task 2/3 独立、低风险。
- 风险点：Task 1 的规则若写得太硬，可能在 evidence 稀薄时逼模型硬造专名 → 规则里必须保留"无具体
  锚点可退话题级"的出口（Spec 已写）。真机验收专门看这个。
- 回滚：feature 分支，未 push；不合并直到真机验收显示丰富度回升且无回归。

## Out of scope

- 拆分单次调用为多次（破坏 1-call 不变式）——不做。
- 兴趣数 cap 调整——不做。
- 关键词级 embedding 去重——Phase 3。
