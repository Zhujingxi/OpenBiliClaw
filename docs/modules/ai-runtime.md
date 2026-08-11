# AI Runtime（target kernel，尚未接入生产组合根）

`src/openbiliclaw/ai/` 已落地类型化 PydanticAI 执行边界与离线评测原语。当前生产调用仍走
`src/openbiliclaw/llm/`；新旧实现没有互相调用，也没有双路执行。生产切换和 legacy 删除必须等
Plans 09–13 的 domain agents/hosts 落地后，再由 Plan 15 Composition 一次完成。

## 执行边界

`AIRuntime.run(AgentRunRequest[DepsT, OutputT]) -> AgentRunResult[OutputT]` 是新边界唯一的
production entrypoint。请求携带稳定 `AgentId`、domain-owned typed dependencies、PydanticAI
`Agent`、有界 history/context、`ModelRequirements`、`RunPolicy` 与 usage attribution；不存在
`complete(prompt) -> str` 兼容门面。

- `ModelRoute` 在构造时检查 primary 和每个 fallback 的 tools、structured output、vision、
  context、streaming、reasoning 能力；任何不兼容 fallback 令启动失败。
- `RunPolicy` 将 request/input/output/total token 和 tool-call 上限直接映射到 PydanticAI
  `UsageLimits`，并限制总 elapsed timeout、retry 次数和四种显式 priority。`RunPriority` 当前仅是
  contract metadata；Core `ResourceBudget` 尚无 priority-aware queue，因此它暂不影响 admission 顺序。
- 所有执行取得 Core `ResourceBudget`；timeout 转为安全 typed failure，`CancelledError` 原样传播。
- provider 失败只暴露 unavailable/rate-limited/unauthorized/timeout/invalid-output/
  budget-exhausted 分类与非秘密 model instance ID，不回显上游 body。
- `UsageRecord` 归因到 agent、workflow、model instance、provider 和可选 recommendation batch；
  `UsageSink` 只是 persistence port，本阶段没有提前实现 repository。

## 上下文和消息安全

`ContextProjection` 在构造时执行 UTF-8 byte bound；`trim_history()` 只保留能装入预算的最新完整
turn，不做总结。单个 tool return 超限会在进入 history 前拒绝。运行前审计 input/history/context，
运行后审计完整 PydanticAI messages；`vault:`、Authorization、API key、password 和 Cookie canary
不得进入模型消息。稳定 system instructions 继续由 domain-owned `Agent` 定义，volatile projection
只进入当次 user input。

## 路由与配置现状

本阶段没有新增 TOML 字段。`RouteTable`、`ConfiguredModel` 和 capability matrix 由未来 provider
plugins/Composition 显式构造；现有 `[llm]` 配置和 `LLMRegistry` 行为未变化。凭据不是 route 或
request 字段，后续 provider adapter 只能在模型边界内通过 CredentialVault 解析。

## 离线测试与评测

`ai.runtime.testing` 导出 PydanticAI `TestModel` / `FunctionModel`；测试全局默认
`ALLOW_MODEL_REQUESTS=False`，真实 provider 测试必须显式 opt-in 并标记 `integration`。
`ai.evaluation` 只提供 immutable recorded `Dataset`、typed runner、metric/report/comparison；不读取
production repository，也不包含 optimizer 或 self-modification。

## 明确推迟

- Plan 03 Phase 5：domain agent registry、prompts 与 agent conversion，等待 Plans 09–12 owner。
- Phase 6 内容迁移：旧 `eval/` scenarios/rubrics 等待对应 domain owner；当前仅有通用 mechanics。
- Phase 7：`llm/`、`eval/`、`agent/orchestrator.py`、`agent/skill.py` 的删除等待所有生产 caller
  改线；本阶段禁止 compatibility wrapper 或假 tool protocol。
