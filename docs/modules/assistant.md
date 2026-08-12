# Assistant（目标模块，尚未接入生产组合根）

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

## 尚未落地

Plan 12 Phase 6 的 legacy 删除（`soul/dialogue.py`、dialogue schedulers、`integrations/agent.py`、
旧 orchestrator/skill/fake-tool plumbing）按批准 deviation 延后至 Plan 15：必须在 callers 切换到唯一
production composition graph 后一次删除。目前 target Assistant 没有 production caller、双写或
compatibility facade。API/CLI host routes 在 Plan 13 接入 Application + Assistant。
