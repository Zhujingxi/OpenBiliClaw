# AI Runtime

`src/openbiliclaw/ai/` 是当前类型化 PydanticAI 执行边界、provider plugins 与离线评测原语。Runtime Composition 构造配置的 model route；未配置模型时相关能力明确 unavailable，不存在第二套调用栈。

## 执行边界

`AIRuntime.run(AgentRunRequest[DepsT, OutputT]) -> AgentRunResult[OutputT]` 是新边界唯一的
production entrypoint。请求携带稳定 `AgentId`、domain-owned typed dependencies、PydanticAI
`Agent`、有界 history/context、可选 PydanticAI `UserContent` attachments、`ModelRequirements`、
`RunPolicy` 与 usage attribution；不存在 `complete(prompt) -> str` 兼容门面。attachments 当前仅由
`recommendation.inspect` 使用，且来自 allowlisted image fetch 后的 `BinaryContent`；不包含 native video。

- `ModelRoute` 在构造时检查 primary 和每个 fallback 的 tools、structured output、vision、
  context、streaming、reasoning 能力；任何不兼容 fallback 令启动失败。
- `RunPolicy` 将 request/input/output/total token 和 tool-call 上限直接映射到 PydanticAI
  `UsageLimits`，并限制总 elapsed timeout、retry 次数和四种显式 priority。`RunPriority` 当前仅是
  contract metadata；Core `ResourceBudget` 尚无 priority-aware queue，因此它暂不影响 admission 顺序。
- `PolicyBook` 在 `AIRuntime.run` 唯一 choke point 应用 config `[runtime.agents."<agent-id>"]`
  的 per-agent RunPolicy override；override 在构造时校验，非法 budget 在启动时失败而非运行中。
  composition 的 atomic reload 会重建 runtime，因此 budget 调整无需重启即可生效。
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

`RouteTable`、`ConfiguredModel` 和 capability matrix 由 Composition 显式构造。凭据不是 route 或
request 字段；`ai.providers.ModelFactory` 通过单一配置入口构造 PydanticAI native provider，凭据仅在
`CredentialVault.resolve()` callback 内交给 selected trusted client。模型托管与本地推理均在应用外部。
完整 model/embedding contract、capability probe 与 safe diagnostics 见
[AI Providers](ai-providers.md)。

## 离线测试与评测

`ai.runtime.testing` 导出 PydanticAI `TestModel` / `FunctionModel`；测试全局默认
`ALLOW_MODEL_REQUESTS=False`，真实 provider 测试必须显式 opt-in 并标记 `integration`。
`ai.evaluation` 只提供 immutable recorded `Dataset`、typed runner、metric/report/comparison；不读取
production repository，也不包含 optimizer 或 self-modification。

## Domain ownership

Understanding, Recommendation, and Assistant own their stable agent identities and prompts; all execute through this runtime. Deleted `llm/`, legacy evaluation, orchestrator, and skill implementations have no compatibility wrappers. Recommendation defines the batched `recommendation.evaluate` contract separately from the per-candidate, vision-required `recommendation.inspect` route; configured `[runtime.agents."recommendation.inspect"]` limits resolve through the same `PolicyBook`. Additional offline domain evaluation datasets remain deferred.
