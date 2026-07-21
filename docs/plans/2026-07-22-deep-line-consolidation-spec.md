# 深层线归一 Spec — pipeline 深层直写退役,深层画像以认知路径为唯一事件入口

**Created:** 2026-07-22(用户决策:「合并流程 2/3,深层以流程 3 为准」)
**Scope:** `soul/pipeline.py`、`soul/layer_updaters.py`、`soul/engine.py`、`soul/cognition_cycle.py`、`soul/posture_gate.py` 接入点、相关测试与文档。基于分支 `feat/cognitive-profile-pipeline`(未合 main,shadow 期未开始——正是做此收敛的最低成本窗口)。
**Out of scope:** 对话深层自述路径(保留:用户亲口说的经门控直达,架构讨论已定「自述权威最高但仍过门控」);soul 整份重建的门控(保留);INTEREST/SURFACE/ROLE 快线(不动);insight hypothesis 存储迁移。

## Goal

现状(认知流水线四 Wave 落地后)深层画像有**两个事件驱动入口**:
- 统计路径(原流程 2):事件信号在 pipeline 的 VALUES/CORE 层攒够阈值 → layer updater 直接产出层变更 → 门控接入点② → 写入(`layer_updaters.py:74` `_GATED_LAYERS`、`:95` 接入点②)。
- 认知路径(流程 3):事件 → 觉察提炼 → 假设(验证:行为/探针/对话投票,confirm 置 validated + 置信 ≥0.75,`engine.py:850-851`)/ 疑惑(澄清)→ 结算出口经门控。

统计路径绕过了「假设-验证」环节,与架构原则「深层结论要挣来」冲突;双入口也造成同一批深层证据可能被两条路径重复消费。**目标:退役统计路径的直写,深层画像的事件驱动入口收敛为认知路径唯一;并补齐「confirmed 假设 → 门控 → 深层落地」的显式提交通道。**

验证:`pytest tests/test_pipeline_advanced.py tests/test_posture_gate.py tests/test_confusion_lifecycle.py tests/test_soul_engine.py -q` 全绿;回归断言 VALUES/CORE 信号不再触发 layer updater 直写。

## Design invariants (MUST hold)

1. **深层事件入口唯一**:事件驱动的深层画像变更只能来自——(a) confirmed 假设的提交通道;(b) 疑惑 resolved 的直接结算出口;(c) 对话深层自述(candidates goal/value/state)。三者全部过态势门控。pipeline 不再对 VALUES/CORE 做阈值消费直写。
2. **深层证据不丢**:VALUES/CORE 类信号仍照常落 events 表并进觉察提炼的 cursor 范围(cognition cycle 读全量事件,天然覆盖)——退役的是"直写",不是"证据"。pipeline 对这两层的缓冲/阈值机制整体移除或短路(实现取最小改动),不得静默丢弃已缓冲信号(迁移时一次性转为觉察原料标记或直接由下轮提炼覆盖,写台账说明)。
3. **confirmed 假设的提交通道**(本 spec 新增,原架构「深层慢线只接受 confirmed 洞察」的补全):insight hypothesis 达 confirmed(validated=True 且 confidence ≥0.75,含对话 settles 确认与 update_from_feedback 确认)时,生成深层候选更新 → 态势门控(独立接入点,替代原接入点②)→ accept 写入深层层 + 台账 / downgrade 保持假设观察(不降置信,记台账)/ reject 记理由。提交幂等:同一假设已提交过(台账有 success 回执)不重复提交。
4. **门控接入点重排**:接入点① dialogue 深层 candidates(保留)、接入点②' confirmed-假设提交(新,替代原 pipeline VALUES/CORE)、接入点③ soul rebuild(保留)。shadow/enforce/off 语义与 save-time 三条件不变;台账 verdict 行的接入点来源字段区分 ②'。
5. **回放与回归**:这是有意行为变更——INTEREST/SURFACE/ROLE 路径逐字节不变(既有测试全绿证明);VALUES/CORE 直写路径的既有测试改写为「不再直写」的反向断言;`posture_gate_mode=off` 下 confirmed-假设提交通道直写(与其它接入点 off 语义一致)。
6. 台账/prompt-cache/阈值出处/单用户注入等既有不变量全部沿用。

## 设计要点

- **退役实现**:`_GATED_LAYERS` 相关的接入点②消费逻辑移除;`signals_from_events` 对 VALUES/CORE 层的路由改为不入 pipeline 缓冲(或入缓冲但消费为 no-op + 计数台账,取改动更小者;禁止留下永远增长的死缓冲)。downgrade-转-假设的既有函数(`layer_updaters.py:201`)迁移复用为 confirmed-提交通道的 downgrade 出口。
- **提交通道落点**:confirmed 判定发生在 `update_from_feedback` 与对话 settles 两处——统一在 confirm 动作后调用 `submit_confirmed_hypothesis_to_gate(hypothesis)`(新函数,soul/engine 或 posture_gate 侧);候选内容=假设文本+置信+依据 refs;写入目标层由 LLM 门控裁决输出附带(accept 时给出目标层/字段建议,白名单校验,解析失败按 downgrade)。
- **12h 兜底**:cognition cycle 扫描 confirmed 且无提交回执的假设(遗漏补交,幂等)。
- **文档**:soul.md 三线图与门控接入点更新;spec.md/architecture 图中"pipeline 深层层"节点改标"(证据经觉察提炼)";changelog 条目;原 2026-07-17 spec 加一行指针注记(不改写历史文档,新决策以本 spec 为准)。

## 验收门

- 反向断言:VALUES/CORE 信号批灌入 pipeline → 层数据不变、无门控接入点②调用、事件仍可被觉察提炼消费。
- 提交通道:confirm(两条路径各一)→ 门控调用(shadow 记录/enforce 拦截/off 直写)→ accept 写入+台账回执;重复 confirm 不重复提交;12h 补交幂等。
- 全量回归:`tests/` 相关文件全绿;mypy/ruff 干净。
