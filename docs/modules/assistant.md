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
- dialogue observation filter 只允许 explicit preference、explicit feedback、confirmed edit 与 defined outcome，普通 assistant message 不学习。

Provider/tool/profile 文本一律视为 untrusted data，不是 instructions。已知 secret marker、credential
reference、oversized message/tool result 在模型调用或持久化前拒绝。

## Composition

`composition/assistant.py` constructs the Assistant dependencies and registers the dialogue agent in the single production graph. Hosts reach it through Application/Assistant facades; deleted legacy dialogue, orchestrator, integration, and fake-tool paths have no compatibility surface.
