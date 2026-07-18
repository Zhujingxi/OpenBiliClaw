# 统一 Discovery 评估设计

## 目标

除 `explore` 外，任何平台或发现策略都不得通过提示词获得基础分、额外加分或更低的匹配标准。候选统一按内容与 Soul 画像的真实匹配度评分，明显不匹配的内容必须允许低于 admission 门槛。

## 设计

- 单条与批量内容评估 prompt 使用同一契约。
- `search`、`trending`、`hot`、`feed`、`related_chain`、`channel`、`creator` 等路径只作为解释候选来源的上下文，不影响分数标尺。
- 热度、搜索命中、相关推荐、订阅关系和平台算法背书都不能设置最低分、自动加分、降低门槛或替候选事后寻找画像关联。
- `explore` 是唯一例外：允许主题陌生，但仍需有具体、可信的吸引点，不能仅凭抽象心理需求给高分。
- 所有准入判断复用一个纯函数：非 `explore` 阈值不得低于全局 admission 门槛；`explore` 是唯一例外，最终展示下限固定为现有主策略阈值 `0.58`。
- `ContentDiscoveryEngine` 写入 `content_cache` 前再次执行同一准入判断。未评估、缺失分数或低于有效阈值的内容 fail closed，不依赖调用方自律。
- `Database.cache_content()` 不再把缺失的 `relevance_score` 默认为 admission 门槛，避免未来直接写入者凭空获得合格分。
- 推荐池、缓存回填、平台补位和 delight 展示使用同一来源感知的 SQL 准入条件，使 `explore` 的 `0.58` 例外在最终出口真实生效，同时不放宽任何其他来源。
- OpenClaw 兼容启动路径构造并注入 `DiscoveryCandidatePipeline`，抖音、YouTube 与 B 站刷新都经过统一候选评估/准入编排。
- 不新增用户配置或平台特判；唯一策略特判是精确匹配 `source_strategy == "explore"`。

## 数据流

常规来源统一走：抓取原始候选 → `discovery_candidates` → 批量 Soul 评估 → 有效阈值判断 → 缓存写入防线 → 来源感知的展示出口。

兼容或手动调用即使绕过候选队列，也必须经过策略内部评估和缓存写入防线；低分数据可以保留在候选审计表，但不能进入可展示池。

## 错误处理

- LLM 不可用、返回数量不一致或分数缺失时不缓存，候选保持可重试或被标为失败。
- 缓存层收到未知来源时按普通来源处理，使用全局 admission 门槛。
- 只有精确的 `explore` 标记可使用 `0.58`；`explore-*`、平台名或其他近似字符串均不能获得例外。

## 验证

- prompt 契约测试同时覆盖单条和批量 builder。
- 测试明确禁止旧的 `trending >= 0.6`、`search` 特判和 `related_chain` 放宽文案。
- discovery 模块文档与 changelog 同步记录统一评估语义。
- 红绿测试覆盖统一阈值纯函数、缓存 fail-closed、缺失分数默认值、`explore` 最终可展示、非 explore 低分在所有出口不可展示，以及 OpenClaw 管线注入。
- 完成后运行相关模块测试、Ruff、MyPy 和全量 pytest。
