# AI Model 与 Embedding Providers（target plugins，尚未接入生产组合根）

`src/openbiliclaw/ai/providers/` 为 target AI Runtime 构造 PydanticAI model，并独立提供 typed
embedding 边界。当前生产仍只使用 `src/openbiliclaw/llm/`；本模块不被生产 composition 导入，
不会双路调用 provider。切换、route startup probe 强制和 legacy 删除留待 Plans 09–15。

## Chat model 构造

`ModelInstanceConfig` 是 frozen、unknown-field-failing 的非秘密配置：provider、model、可选 endpoint、
opaque `cred_*` reference、受审查的 temperature/max_tokens/top_p、声明能力、owner 和 provider version。
原始 key 不属于配置。`ModelFactory` 只在 trusted constructor callback 内经 `CredentialVault.resolve()`
取得 key，并显式构造 OpenAI、Anthropic、Google、Ollama 或 OpenRouter 的 PydanticAI native model；
DashScope chat 因 PydanticAI 1.56 没有 native support 而明确失败，不使用 prompt fake tools。

`BuiltModel` 携带由非秘密完整配置计算的稳定 instance ID、owner、declared capabilities 与
`VerifiedCapabilities`。声明只是 claim；structured output、native tools、vision、streaming 四项在真实
probe 前均为 `unverified`。probe 只接受调用方提供的真实 operation，失败不会回显 provider body。
本阶段 probe integration entrypoint 默认 skip；Composition 尚不以 probe 结果阻断启动。

## Embedding

Embedding 不经过 PydanticAI chat model。`EmbeddingModelInfo` 记录 provider/model/dimensions/
normalized/version，并以完整 identity + content SHA-256 形成 cache key。`EmbeddingService`：

- 按输入顺序确定性分 batch，以 Core `ResourceBudget` 限制 transport 并发；
- 保留 `gather` 输入顺序，验证返回数量及每个 vector 的维度；
- 汇总 request/input-token usage，空输入、空文本和 mixed dimensions 直接拒绝；
- 对 transport 明确标为 retryable 的错误做有限 retry，每批有 timeout，取消原样传播。
- Google `batchEmbedContents` 不返回 usage metadata，Google embedding 的 token attribution 恒为 0（request 计数仍准确）。

当前配置样例需要的 OpenAI、Google 与 Ollama 均有 typed HTTP transport。网络响应立即通过 typed
Pydantic schema 解析；HTTP error 仅转换为安全 retry classification，不保留 body。remote embedding
key 也只在 trusted construction callback 中写入 scoped HTTP client header。

## 安全 diagnostics

`ProviderDiagnostic` 的 detail 是封闭枚举，construction health 不执行 domain agent，也不复制 exception、
response body、prompt、authorization header 或 credential。正常单测全局 `ALLOW_MODEL_REQUESTS=False`；
真实 capability probe 必须显式选择 integration test。

## 依赖

默认安装固定 `pydantic-ai-slim[anthropic,google,openai,openrouter]==1.56.0`。不再分别声明 OpenAI 与
Google SDK；它们由对应 PydanticAI extras 管理。Anthropic 暂固定 `>=0.78,<0.80`，因为
PydanticAI 1.56 引用的 beta type 在 Anthropic 0.80 被移除。`.[local-llm]` 的 `ollama` SDK 仍仅供
legacy 本地流程；target Ollama chat/embedding adapter 走已安装的 PydanticAI/httpx，无新增 SDK。

## 明确推迟

- production routes 与当前 `[llm]` TOML shape 的替换；
- verified capability store persistence 及 startup enforcement；
- legacy `llm/` provider/embedding、直接 caller 和重复 SDK 路径删除；
- provider diagnostics 的 CLI/API host surface。

这些工作需要 domain agents、host contract 和唯一 composition graph，按 Plans 09–15 处理。
