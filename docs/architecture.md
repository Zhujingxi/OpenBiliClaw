# 架构设计

## 系统概览

OpenBiliClaw 采用分层架构设计，从上到下依次为：

1. **用户交互层** — Chrome 浏览器插件（B 站 + 小红书页面行为采集 · 推荐展示 · 对话交互 · xhs 任务调度 / 初始化画像导入）
2. **外部集成层** — OpenClaw adapter / skill wrappers / 本地 API 等对外接入边界
3. **Agent 核心层** — 自研编排器 + Soul Engine + Discovery Engine + Recommendation Engine + Skill System
4. **多源适配层（v0.3.0+）** — `SourceAdapter` 协议下的 B 站 / 小红书 / 通用 Web 三类源
5. **多层网状记忆存储** — Core / Episodic / Semantic / Working Memory（SQLite + 向量索引 + JSON）

详见 [项目 Spec](spec.md) 中的架构图。

## 模块职责

### Agent Orchestrator (`agent/`)
- 任务调度和策略决策
- 多步推理和自省优化
- Skill 注册、发现和调度

### Integrations (`integrations/`)
- 对外系统接入边界
- adapter bootstrap、DTO 裁剪和异常翻译
- 将现有 runtime / engine 能力暴露为 OpenClaw 可调用 skill
- 提供 JSON CLI bridge，供仓库内真实 OpenClaw skill pack 调用

### User Soul Engine (`soul/`)
- 行为数据分析和画像构建
- 五层灵魂模型（事件→偏好→觉察→洞察→灵魂）
- `InterestSpeculator` — 兴趣推测与投机性发现
- 苏格拉底式用户对话

### Memory System (`memory/`)
- 五层网状记忆管理
- 跨层关联和双向修正
- 自我编辑和遗忘机制

### Content Discovery (`discovery/`)
- 多策略内容发现（search · trending · related_chain · explore），按 source 配额并行调度
- 内容评估（基于用户 Soul，LLM 批量打分）
- 候选分层、去重和缓存写入；写入时 `pool_status='suppressed'` 的旧候选在重新发现时自动复活成 `'fresh'`
- v0.3.0+ 多样性栈：trending 按 rid 交错 / explore 按 domain 交错 / `_compress_topic_repeats` 单次压缩 / `trim_topic_group_overflow` 跨源跨轮配额（任意 topic_group ≤ 池子 10%）/ deficit-source 合并 + 并行 fan-out

### Sources (`sources/`) — 多源适配层 (v0.3.0+)
- `SourceAdapter` Protocol：每个内容源实现统一接口
- `bilibili_adapter` — B 站 API 直连（WBI 签名、v_voucher 自动恢复）
- `xiaohongshu_adapter` — 小红书扩展代理（被动收集 + 关键词搜索 + 创作者订阅 + `bootstrap_profile` 初始化画像任务，零后端爬取）
- `web_adapter` — 通用 Web（Playwright CDP + LLM 内容抽取）
- `SourceRecipe` — 源任务持久化与分发

### Recommendation Engine (`recommendation/`)
- 推荐排序与朋友式推荐表达生成；统一从候选池读取
- `PoolCurator` 五维评分（relevance · freshness · topic_fatigue · source_monotony · serendipity）
- v0.3.1 双轴 fatigue：`recent_topic_keys` (细) + `recent_topic_groups` (粗) 取 max；曲线 `count^1.5/len*5`，count=2 即触发 0.47 强抑制
- `_merge_topic_supergroups` — serve 时基于 embedding 把 `动漫杂谈/补番/解说` 等近义 topic 合并为同一聚类
- `prewarm_supergroup_embeddings` — refresh tick 后台预热所有池中 topic_group embedding，让 reshuffle 跑全 cache hit
- `batch_insert_recommendations` — 单 transaction 批量插入，避免 popup 给 10 条结果时 10 次 fsync
- 个性化专题生成

### Runtime (`runtime/`)
- 系统生命周期管理和服务编排
- `ContinuousRefreshController` — 后台定时刷新候选池；按 source 配额评估 deficit，多源缺货合并到一次 discover() 并行 fan-out
- `_enforce_pool_cap` 每 tick 跑 `trim_topic_group_overflow` + 必要时按 share quotas 修剪过额源
- `AccountSyncService` — 历史记录、收藏夹、关注列表同步

### Init 多源画像导入

`openbiliclaw init` 的首轮信号现在由两条路径合流：

1. B 站 API 直连拉取观看历史、收藏夹和关注列表。
2. 后端在 `xhs_tasks` 表入队 `bootstrap_profile`，由浏览器插件在用户已登录的小红书页面中先定位当前用户 profile，再解析 profile state / DOM 中的 `saved / liked` notes 和页面显式暴露的 `xhs_history` notes，回写 `/api/sources/xhs/task-result`。当任务显式传入 `max_scroll_rounds` 时，插件会在 profile tab 内优先探测 feed / waterfall / masonry 滚动容器做有限滚动，并先用 `status="partial"` 分批回传新增 notes，最终再用 `status="ok"` 完成任务；`scroll_wait_ms` 和 `max_stagnant_scroll_rounds` 也由任务 payload 控制，并由插件端裁剪到安全范围。

回写后的 notes 会转成普通事件层 payload：`saved -> favorite`、`liked -> like`、`xhs_history -> view`，并带 `metadata.source_platform="xiaohongshu"`。CLI 只短暂等待该任务结果；插件未连接、未登录或小红书页面不暴露对应数据时，初始化继续使用 B 站数据完成。

### LLM Providers (`llm/`)
- 统一的多模型接口（OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter）
- Provider 注册和切换
- v0.3.0+ embedding 兜底：`OllamaProvider.embed()` 走原生 `/api/embeddings`，配 `bge-m3` 模型可在 Mac/Win/Linux CPU 跑相似度计算，不需额外 API Key
- `EmbeddingService` L1 内存 + L2 SQLite 双层缓存

### Storage (`storage/`)
- SQLite 数据库管理
- 冷备份、完整性检查与显式修复
- 候选质量信号持久化与数据迁移
- v0.3.1 `get_pool_candidates` 用 `ROW_NUMBER() OVER (PARTITION BY topic_group)` 把每个 topic_group 在候选窗口里限到 ≤3 条，保证长尾 group 真正进得到候选窗口

## 运行时数据库约束

本地 API 与 CLI 的高频运行路径现在遵循两条约束：

1. **同进程共享单个 SQLite 实例**
   `MemoryManager`、`RecommendationEngine`、`ContentDiscoveryEngine` 会优先复用同一个 `Database`，避免一轮运行里多次 `Database(...).initialize()` 争锁。
2. **启动前先检查、运行中按周期冷备**
   `openbiliclaw start` 会在启动前检查数据库完整性；若健康且超过默认 24 小时未备份，会先生成一份冷备到 `data/backups/`。

数据库修复不在启动路径里自动执行，高风险恢复统一通过 `openbiliclaw db-repair` 触发。

## 对外集成约束

当前 OpenClaw 接入遵循两条边界：

1. **外部集成只通过 adapter 调用内核**
   OpenClaw 不直接访问 SQLite、memory JSON 或内部 engine 组合细节。
2. **skill 只是协议包装，不是业务主链**
   学习、推荐、反馈回流仍由 `runtime/`、`soul/`、`recommendation/` 等模块负责，`integrations/openclaw/skill.py` 只负责对外暴露稳定 handler。
3. **真实 OpenClaw 技能发现走仓库根目录 `skills/`**
   当前仓库通过 `skills/openbiliclaw-adapter/SKILL.md` 提供真实 workspace skill，再由 skill 内部调用 adapter CLI bridge。
