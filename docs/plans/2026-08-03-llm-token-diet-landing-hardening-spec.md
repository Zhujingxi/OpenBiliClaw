# LLM Token Diet Landing Hardening Spec

**Created:** 2026-08-03
**Status:** accepted for implementation
**Scope:** `perf/llm-token-diet` landing correctness, replay evidence, evaluation-cache
correctness, reason normalization, integration with current `main`, and release verification.

## 1. Context

`perf/llm-token-diet` 已完成 compact evaluation profile、per-item long-tail recall、
embedding prefilter、bounded eval cache、candidate coalescing、body-text caps、profile views、
chat core-memory split 与 eval reason diet。分支也包含大量单元测试和 golden fixtures。

当前实现还不能作为可合入证据：

1. 2026-07-18 记录的 replay `PASS` 已被后续规格明确作废，分支内没有新的有效 artifact。
2. replay 读取 raw `soul.json`，没有复现生产的 user overrides 与 active speculations。
3. embedding 空向量/部分失败会被生产 recall 逻辑静默降级，replay 不能据此证明
   compact + recall 确实被执行。
4. A/B 的实际 provider / instance / model 只被平铺记录，没有按 pair/run 归属，也没有
   阻止非模型实验在两臂之间发生 route drift。
5. body-text cap 已成为两臂共同的生产行为，脚本却直接拒绝 `--arm-b body-cap`，导致
   Task 7 的 model-visible input gate 无法执行。
6. batch eval cache key 在 recall 生成前命中；实际 recalled labels、embedding namespace、
   prompt-visible content/source context 没有形成完整的可复现输入闭包。
7. reason diet 只依赖 prompt 约束；parser/runtime 不会把 `<0.5` 的 reason 强制清空，
   也不会把 `>=0.5` 的 reason 截到 30 个 Unicode code points。
8. 分支落后当前 `main`，现有测试结果不能替代 rebase 后的集成验证。

本规格是上述三个历史规格的 landing 修正版；发生冲突时，以本规格的验收门为准：

- `2026-07-05-llm-token-diet-spec.md`
- `2026-07-18-profile-views-spec.md`
- `2026-07-18-eval-reason-diet-spec.md`

## 2. Goals

### G1. Replay 证明生产等价

Replay 使用与生产 evaluator 相同的 effective profile、negative exemplars、prompt caps、
embedding model namespace、route 和 output ceiling。基础设施降级必须显式进入 artifact；
会改变实验语义的降级必须让 gate 失败，不能变成零分或无 recall 的正常观测。

### G2. 三类 model-visible 变更都有可执行对照

同一个脚本必须支持：

- `compact`：legacy full profile/no recall 对比 production compact + recall；
- `body-cap`：production profile/recall 下，legacy uncapped body 对比 production 200+100 cap；
- `reason-diet`：production inputs 下，legacy unconditional reason 对比 production reason diet。

每一类实验使用冻结 snapshot、重复 A/A 与 A/B、同一 admission policy，并产出独立 JSON。

### G3. Eval cache 覆盖决定 prompt 的稳定输入

缓存命中必须只发生在“同一 evaluator 语义输入”上。至少覆盖：

- prompt-visible content fields 的确定性 digest（包括截断后的 body/description、metrics、
  tags、platform/type/strategy 与 effective source context）；
- compact profile + recall pool digest；
- negative exemplars digest；
- recall/embedding namespace；
- cache schema version。

当 recall 发生临时/部分失败时，本次 degraded score 不得写入可在恢复后命中的正常 cache。

### G4. Reason 契约由 runtime 兜底

- `score < 0.5`：最终 `relevance_reason == ""`；
- `score >= 0.5`：strip 后最多 30 个 Unicode code points；
- missing/`None` reason 仍归一化为 `""`；
- 非字符串仍按现有 malformed-member retry 处理；
- single 和 batch evaluator 共用同一纯函数；
- cache 与持久化只接收归一化后的值。

Prompt 约束继续负责减少模型实际生成的 output tokens；runtime 归一化负责保证持久化契约，
两者缺一不可。

### G5. 在当前 main 上给出可复核的 landing 结论

完成 rebase、冲突语义审查、静态检查、全量测试、真实 replay gates 和配置/CLI smoke；
所有结果记录到 landing 文档或 artifact，不能沿用 rebase 前的绿色结果。

## 3. Non-goals

- 不把 embedding prefilter 从 `shadow` 自动切到 `enforce`；enforce 仍需独立线上 shadow 数据。
- 不重新设计 discovery keyword/inspiration 算法。
- 不改变 admission 评分 rubric、阈值语义或 recommendation 排序。
- 不在本任务中持久化 eval score cache；仍为进程内 LRU。
- 不承诺消除 provider 本身的 nondeterminism；通过同日 repeated A/A envelope 测量它。

## 4. Design invariants

### I1. Effective profile parity

Replay profile 等于生产 `SoulEngine.get_profile()` 的可观察结果：

1. load current onion profile；
2. apply `profile_overrides.json`；
3. attach active interest speculations；
4. freeze serialized profile and digest before any arm runs。

Artifact 记录 raw/effective profile digest、override presence 和 active-speculation count，不写入
任何 secret 或完整私人画像正文。

### I2. Embedding completeness is auditable

Replay 的 embedding wrapper 记录每个 request 的 model namespace、非空向量、维度与异常。

- provider exception、空向量、NaN/Inf、同一 namespace 维度漂移：实验失败；
- tail-interest pool 为空：合法的 zero-recall case，记录 `eligible_tail_count=0`；
- 向量完整但没有兴趣超过 similarity threshold：合法，记录 injected label count 为 0；
- compact/body-cap/reason-diet acceptance 默认要求可用的生产 embedding service；若生产配置
  明确禁用 embedding，则必须通过显式 `--allow-no-embedding` 运行，artifact 标为 degraded，
  不能作为 compact + recall 的 landing 证据。

### I3. Route equivalence is enforced

每个 LLM call 带以下 replay attribution：

- pair kind (`control` / `treatment`)
- repeat index
- logical run (`A1`, `A2`, `A`, `B`)
- actual provider / instance / model

非 `model=...` 实验要求每个 logical run 内 route 唯一，且 A/A/A/B 使用相同 route。
显式 model 实验仅允许 treatment B 使用目标 route；control A/A 和 treatment A 仍必须一致。
route 为空、混用或意外 failover 都让 gate 失败。

### I4. Replay failures are not quality observations

Timeout、LLM/embedding exception、缺失 parsed member、score vector 长度不符、snapshot drift、
route drift、artifact write failure 均以非零退出。不得转换为 score 0 后继续统计。

明确的瞬时 provider rate limit 可以在 registry cooldown 后对同一 chunk 做有界重试；重试前
必须恢复该 chunk 的评估输出字段，失败调用必须留在 route audit，且只有后续成功调用使用同一
实际 route 时才能视为 recovered。HTTP 402、余额/计费错误和其它异常不重试。

### I5. Body-cap contrast is faithful

`body-cap` 的 arm A 在 prompt construction 层临时关闭 cap，但仍使用原始 candidate body，
以保留 description/body dedup 关系；不得通过提前修改 `DiscoveredContent.body_text` 制造对照。
arm B 使用生产 200 head + `…` + 100 tail。Artifact 记录实际受 cap 影响的 candidate 数；为 0
时 gate 失败。

### I6. Cache determinism and degradation

Recall selection 被视为以下稳定函数：

`f(content_prompt_digest, recall_pool_digest, embedding_namespace)`。

模型 namespace 变化必须产生不同 key。正常 cache entry 只能由完整 recall 计算或明确的
no-recall production mode 写入。临时 embedding failure 的计算结果可以返回给本轮调用，但不写
normal cache；恢复后必须重新评估。

### I7. Cache lookup does not mutate prompt semantics

为形成 content digest 可以构造轻量、纯 deterministic 的 prompt payload；不得发起 LLM 调用。
缓存命中不应为了“验证命中”重复做所有 embedding provider 请求。允许使用稳定 namespace +
完整输入 digest 复用此前由完整 recall 产生的 entry。

### I8. Prompt-cache convention remains intact

所有 system prompt 仍为 module-level byte-stable constants；新增 replay attribution、digest、
runtime reason normalization 均不能进入生产 system prompt。Per-call data 仍只在 user message。

### I9. No hidden default regression during rebase

冲突不能用机械 ours/theirs 解决。特别检查：

- `inspiration_search_enabled` 保留当前 main 的默认；
- 当前 main 新增的 LLM timeout、source pacing、visual/danmaku/TLS 配置不能丢失；
- token-diet 新增的 `eval_prefilter_mode`、eval coalescing、route 文档不能丢失；
- tests 与文档同时保留两侧语义。

### I10. Quality failure changes the diet, not the gate

Final-commit 的首次真实 compact 100×3 replay 对 64 interests / 12 specifics 边界给出明确
失败：treatment Spearman 中位数 `0.494686 < 0.570454` control floor，admission delta 中位数
`-0.09 < -0.07` floor。该 artifact 只用于诊断，不能作为 landing PASS。

修正边界为 80 interests / 32 domains × 16 specifics；per-item tail recall 相应只覆盖 ranks
81..256。选择这一边界的约束是：

- 当前生产画像的全部实际 interests / domain specifics 都保留，主要只移除 volatile metadata；
- mature fixture 仍减少约 58% 字符，maxed fixture 减少约 63%，继续满足高成熟度画像的降本目标；
- 当前画像不再为了约 11% 的有限缩短承受语义截断；
- Spearman、flip-rate、admission floors 完全不变，三臂在修正后的 clean commit 重新执行。

## 5. Replay artifact contract

每个 artifact 至少包含：

- git commit、dirty flag、config path digest、DB path digest；
- candidate IDs、status/platform/strategy mix、snapshot digest；
- raw/effective profile digest、negative exemplar digest；
- experiment arm、body-cap affected count、tail-interest count；
- per pair raw scores、admission decisions和 metrics；
- attributed LLM calls、actual routes、usage；
- embedding namespace、call count、vector completeness/dimensions、recall injection counts；
- gate constants、derived envelope、pass/fail 与所有 blocking reasons。

Artifact 不包含 API key、Cookie、完整 config、完整 profile 或完整候选正文。

## 6. Acceptance gates

### A. Automated correctness

- targeted replay/cache/reason tests pass；
- profile-view golden/guard tests pass；
- config/API round-trip tests pass；
- Ruff、MyPy、`git diff --check` pass；
- full `pytest` pass（允许显式 documented environment skips，不允许 failure）。

### B. Real replay

在同一 rebase 后 commit、同一 DB snapshot、同一生产 config 上分别运行：

```bash
.venv/bin/python scripts/run_profile_diet_ab.py \
  --arm-b compact --sample 100 --repeats 3 \
  --output data/eval/profile-diet-compact.json

.venv/bin/python scripts/run_profile_diet_ab.py \
  --arm-b body-cap --platform reddit --sample 100 --repeats 3 \
  --output data/eval/profile-diet-body-cap.json

.venv/bin/python scripts/run_profile_diet_ab.py \
  --arm-b reason-diet --sample 100 --repeats 3 \
  --output data/eval/reason-diet.json
```

若指定 text platform 不足 100 条 eligible rows，必须选择真实有足量长正文的 text platform；
不得改用全空 body 的 Bilibili cohort 冒充 body-cap gate。

每个命令必须 exit 0，artifact 自身 `gate.passed=true`、无 blocking reasons、route 与 embedding
完整性 gate 通过。

### C. Runtime/E2E smoke

- `openbiliclaw config-show` 能显示/加载新增配置；
- 使用 deterministic fake provider 完成 candidate enqueue → coalesced claim → batch eval →
  cache → admission 的端到端路径；
- 模拟 embedding 首次失败、随后恢复，确认 degraded score 不会阻止恢复后的 recall re-eval；
- 模拟相同 content ID 但 prompt-visible body/source context 改变，确认 cache miss；
- 模拟低分长 reason 与高分超长 reason，确认归一化后再缓存/持久化；
- chat core-memory stable/volatile、profile overrides 与 extractor opt-out 回归测试继续通过。

## 7. Rollback and observability

- 代码回滚以独立 commit 为单位：replay-only、cache correctness、reason normalization、docs。
- `eval_prefilter_mode` 保持 `shadow`，不把此次 landing 与 enforce rollout 绑定。
- 合入后观察至少 48 小时：
  - `openbiliclaw cost --by caller` 的 evaluation/recommendation tokens per call；
  - evaluation cache hit rate；
  - embedding failure/empty-vector 日志；
  - score/admission distribution 与 `evaluation_response_missing`；
  - recommendation 文案缺失率与用户质量反馈。

任何真实质量回归优先回滚对应 model-visible diet commit，不通过放宽 replay gate 掩盖。
