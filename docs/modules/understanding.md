# User Understanding

`src/openbiliclaw/understanding/` 是 canonical user profile 的唯一 owner，并由 production composition 构造。

## 已落地

- frozen typed canonical profile：stable/emerging interests、avoidances、content/style/creator/language/provider preferences 与 insight；每个 claim 具有 deterministic ID、confidence、freshness、evidence links 与 lifecycle；
- 显式 user override，优先级高于 inference，remove/set 均产生稳定 audit identity；
- 四种稳定 analyzer identity（preference、avoidance、topic lifecycle、insight），使用 PydanticAI structured `ProposalBatch`、显式 capability requirement 与 hard `RunPolicy`；模型不可用时 profile read 和 deterministic edit 仍工作；
- deterministic proposal policy 校验 confidence、evidence、freshness、field ownership、override 和 contradiction；不调用模型解决冲突；
- SQLite proposals/evidence/ledger/profile/checkpoint 同事务提交，proposal 在 decision/profile 前写入；per-analyzer cursor 保证 retry/restart idempotency；
- versioned `DiscoveryProfile`、`RecommendationProfile`、`DialogueProfile`，按 purpose 裁剪且有字符预算，不暴露 evidence IDs；
- analyzer input 仅含最多 50 条 observation 的 500-char evidence summary，不含 provider payload、credential reference 或无界历史。

## 隐私边界

Canonical profile 只存 claim 和 observation evidence reference。原始 provider payload、Cookie、token、credential reference、网页 HTML 和自由 prompt 均不能进入 analyzer input 或 projection。其他 product modules 只能消费 `projections.py` 的 bounded view，不能导入 `profile.py`。

## Composition

Application workflows and Assistant consume the bounded projections above. Deleted `memory/`, `soul/`, legacy JSON state, and evaluation paths have no compatibility facade or double-write path.
