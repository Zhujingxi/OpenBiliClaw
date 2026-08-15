# Assistant

`src/openbiliclaw/assistant/` 是 Application Workflows 之上的 bounded PydanticAI 对话 facade。
稳定 agent identity 为 `assistant.dialogue`；它只接收 safe application facade、版本化且有界的
`DialogueProfile`、locale 与 local-user/device-scoped conversation metadata。canonical profile、
evidence ledger、credential vault、provider secrets 和 repositories 都不进入 agent dependencies。

## 已落地

- 四种 discriminated output：message、recommendation presentation、clarification、pending action；
- interactive `RunPolicy`：6 tool calls、12k input / 2k output token、45 秒 timeout、一次 retry；
- 八个 native workflow tool contract，并按 intent、connected provider、skill/capability 选择，禁止全局暴露；
- provider-native read tools 复用 Content Integration 的 bounded tool contract；所有结果在 history 前 sanitise/clamp；
- `AssistantSkill` 只有 stable ID、tool factory、model requirements、静态 instructions；无 lifecycle/hook/credential；
- conversation/message/tool summary/pending-action/usage models，SQLite restart、retention、scope 与 deletion；
- recent-window + typed summary compaction，只在超限时运行，保留 unresolved actions、corrections、references，不能新增 confirmed facts；
- pending action exact-effect/expiry presentation 与 replay-safe deterministic confirmation；
- dialogue observation filter 只允许 explicit preference、explicit feedback、confirmed edit 与 defined outcome，普通 assistant message 不学习；
- profile correction channel（landed）：`propose_profile_revision(field, operation, value, rationale)` 只接受现有 claim ID 或 `exploration.disabled`，从 effect tuple 生成 deterministic idempotency key，先持久化 scoped/expiring pending action 且不改变 profile；统一确认端点批准后才调用 canonical `EditProfile`，`POST /v1/content/actions/reject` 显式拒绝。SET 用用户给出的新值生成同 kind、trust-1.0 的 statement claim，REMOVE 只移除；statement evidence 与 accepted claim 由 shared C2 hook best-effort 写入 embedding index；Assistant 不暴露任何 direct-mutation tool。

Provider/tool/profile 文本一律视为 untrusted data，不是 instructions。已知 secret marker、credential
reference、oversized message/tool result 在模型调用或持久化前拒绝。

## Model compatibility

Assistant 的 discriminated output 由 PydanticAI output tool 强制生成，因此 provider request 会使用 `tool_choice = "required"`。Kimi coding endpoint 支持普通 tool call，但默认 thinking 与 forced required tool choice 冲突；为该模型配置 `[model.options] disable_thinking = true` 后，OpenAI-native constructor 会发送 `thinking: {type: "disabled"}`，恢复真实 Assistant output tool 调用。Output tool 的 `kind` schema 同时枚举 message/recommendations/clarification/pending_action 四个合法 discriminator，避免 provider 生成 validator 必然拒绝的任意字符串。此开关不暴露 generic request body，也不影响其他 provider constructor。

## Composition

`composition/assistant.py` constructs the Assistant dependencies and registers the dialogue agent in the single production graph. Hosts reach it through Application/Assistant facades; deleted legacy dialogue, orchestrator, integration, and fake-tool paths have no compatibility surface.
