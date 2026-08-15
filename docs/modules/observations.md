# Observation Ingress

`openbiliclaw.observations` 是用户行为证据的唯一 ingress 边界，并由 production composition 接入 Application 与 Understanding。它拥有 immutable typed observation vocabulary、shared provenance/trust validation、idempotent SQLite persistence、cursor replay 和只携带 committed IDs 的 post-commit notification；它不更新用户画像，也不拥有 analyzer checkpoint。

## 已落地

- 14 个 Pydantic discriminated observation variants：recommendation shown/opened/liked/disliked/saved/dismissed、host content opened/saved、assistant feedback/preference、deterministic profile edit、provider-history import，以及 credential/Takeout 共用的 `external_history_view` / `external_save`；schema version 固定为 1。
- 每条记录包含稳定 observation ID、producer idempotency key、occurred/received timestamp、可选 account/content ref、typed provenance/trust 和 variant-specific payload；不存在 `event_type + dict` fallback。
- 验证 producer allowlist、event allowlist、source/event pairing、clock skew、required content、account identity 和 trust。未认证 host producer 只能 low-trust 且不得声明 account identity。
- 单批最多 100 条、单条序列化最多 64KB；逐条 validation rejection 与 duplicate acceptance 分开返回。所有 accepted rows 在一个 SQLite transaction 内提交；rollback 不发布通知。
- Credentialed `History`/`Saved` pages and verified YouTube Takeout watch-history exports normalize to the same bounded external-content payload (title, optional creator, provider event ID). They are authenticated high-tier behavioral observations, so Understanding projects them at `0.6`, never statement-level `1.0`; identity is deterministic by provider content ID + event type and retries deduplicate.
- SQLite uniqueness `(producer, idempotency_key)`，按 insertion cursor deterministic replay。带 `ContentRef` 的 observation 会以 `(provider, external_id)` 幂等写入 `content_references`；observation 不携带 projection body，因此不写 `content_cache`。Understanding 将自行持有 processing checkpoint。Committed-ID publication 仅是 advisory latency hint；cursor reads 才是权威恢复路径，commit 后的 publish failure 不在 ingress 内重试。
- Future `ObservationProvider` 使用 Core `ObservationProviderRegistration`。未来浏览器插件必须通过独立的 signed/device-authenticated producer 提交相同 shared observation schema；browser-specific payload、cookie、cross-site tracker、browser session 与 managed browser 均不属于本模块。

## 安全边界

自由文本有严格长度限制，并拒绝 HTML、authorization/cookie canary 与 prompt-instruction 文本。通知只发布 observation IDs；完整 payload 只从 repository 读取。模块不得导入 Understanding、Recommendation、Assistant 或 Hosts。

## Composition

Application workflows own built-in producer submission; Understanding owns consumer checkpoints. Deleted event-ingress and scattered write paths have no compatibility or double-write path.
