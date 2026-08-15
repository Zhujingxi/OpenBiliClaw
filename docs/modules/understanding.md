# User Understanding

`src/openbiliclaw/understanding/` 是 canonical user profile 的唯一 owner，并由 production composition 构造。

## 已落地

- frozen typed canonical profile：stable/emerging interests、avoidances、content/style/creator/language/provider preferences 与 insight；每个 claim 具有 deterministic ID、confidence、freshness、evidence links 与 lifecycle；
- 显式 user override，优先级高于 inference，remove/set 均产生稳定 audit identity；
- 四种稳定 analyzer identity（preference、avoidance、topic lifecycle、insight）与显式 capability requirement / hard `RunPolicy`；production 路由的 preference analyzer 使用 PydanticAI `PromptedOutput(PreferenceDraftBatch)`，让模型只生成无 ID/时间戳的受限 draft，application 再生成 deterministic claim/proposal identity、解析 evidence reference 并产出 canonical `ProposalBatch`；模型不可用时 profile read 和 deterministic edit 仍工作；
- deterministic proposal policy 校验 confidence、evidence、freshness、field ownership、override 和 contradiction；不调用模型解决冲突；
- SQLite proposals/evidence/ledger/profile/checkpoint 同事务提交，proposal 在 decision/profile 前写入；per-analyzer cursor 保证 retry/restart idempotency；
- versioned `DiscoveryProfile`、`RecommendationProfile`、`DialogueProfile`，按 purpose 裁剪且有字符预算，不暴露 evidence IDs；
- analyzer input 仅含最多 50 条 observation 的 500-char evidence summary，不含 provider payload、credential reference 或无界历史。

## 隐私边界

Canonical profile 只存 claim 和 observation evidence reference。原始 provider payload、Cookie、token、credential reference、网页 HTML 和自由 prompt 均不能进入 analyzer input 或 projection。其他 product modules 只能消费 `projections.py` 的 bounded view，不能导入 `profile.py`。

## 方向决策 (decided)

- **No fixed trait taxonomies.** Understanding never adopts MBTI-style trait schemas or soul-style fixed layers (tone/posture/topic state machines). The profile stays an open, evidence-grounded claim set; the LLM chooses which dimensions are salient for this user.
- **Trust tiers by provenance.** Claims distinguish `user_statement` (explicit, highest trust) from inferred claims; re-synthesis and conflict resolution never let inference override an explicit statement. The deterministic proposal policy enforces this without calling a model.
- **Chat correction channel.** Users adjust understanding via Assistant dialogue; corrections become proposed profile revisions (pending action) and persist as `user_statement` evidence — the same write path as every other profile change, so dialogue needs no separate learning machinery.
- **Semantic matching substrate.** The embedding service becomes a first-class matching input: evidence items, profile fragments and candidates are embedded; vector recall feeds LLM rerank. The durable embedding index lands together with the semantic-retrieval consumer in Recommendation, as already anticipated below.
- **Re-synthesis job.** A bounded periodic job re-synthesizes profile claims from evidence (the honest successor to legacy consolidation); cadence and budget are set when the job lands.
- **Exploration hypotheses reuse the proposal path (landed).** Likes/saves on exploration-served items enter as confidence-`0.2` emerging-interest proposals with arm/hypothesis evidence. `UnderstandingService.consider` runs them through the existing `ProposalPolicy`; low-confidence decisions persist with `pending` ledger status and cannot change profile claims until later corroboration supplies a policy-acceptable proposal. Exploration dismissals never create global avoidances. The explicit `exploration.disabled=true` override is the sole zeroing statement and removal restores learned allocation.
- **Two planes.** The user evidence ledger (facts about the user) and the agent policy decision journal (briefs, hypotheses, lessons, outcomes) are separate append-only stores, cross-referenced by ID; policy artifacts never enter the user evidence ledger, and user evidence never carries policy authority.

## Composition

Application workflows and Assistant consume the bounded projections above. Content-dimension `PreferenceClaim` values appear in discovery interests and recommendation positive topics; this bridges the routed preference analyzer until the stable-interest analyzer is routed. Composition also exposes the independently configured embedding service, but Understanding does not own a semantic index or provider projection bodies. Recommendation discovery remains text-query based, so no durable embedding index exists until a concrete semantic retrieval consumer lands. Deleted `memory/`, `soul/`, legacy JSON state, and evaluation paths have no compatibility facade or double-write path.
