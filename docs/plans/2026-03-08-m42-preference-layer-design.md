# M4.2 偏好层设计

**目标**

基于最近一批事件，通过 LLM 生成结构化偏好画像，并将结果增量合并到 `data/memory/preference.json`，为后续 Soul 层和推荐层提供稳定输入。

## 范围

- 从 SQLite 事件层读取最近 N 条事件
- 用 LLM 提取兴趣标签、风格偏好、使用情境、讨厌主题、常看 UP
- 将结果与已有偏好做衰减和合并
- 持久化到 `preference.json`
- `SoulEngine.analyze_events()` 接入偏好分析器

本阶段不生成 `SoulProfile`，不更新 awareness / insight / soul。

## Prompt 设计原则

本阶段采用“结构化提取 prompt”，并参考官方文档中的稳定做法：

- OpenAI 官方建议优先使用结构化输出与明确 schema，而不是仅靠自然语言要求“返回 JSON”
- Anthropic 官方建议将任务、上下文、规则、输出格式、示例用清晰区块分隔，尤其适合抽取类任务

落地规则如下：

1. Prompt 拆分为明确区块：
   - `<task>`
   - `<context>`
   - `<rules>`
   - `<output_schema>`
   - `<examples>`
   - `<event_batch>`
2. 明确禁止模型猜测：
   - 证据不足时返回空列表、低权重或默认值
3. 输出必须是严格 JSON
4. 给少量 few-shot 样例，展示“事件 -> 标签/风格”的映射方式
5. 本地代码做二次校验与归一化，不能直接信任模型输出

## 数据结构

偏好持久化结构对齐 `PreferenceLayer`：

- `interests`
- `style`
- `context`
- `exploration_openness`
- `disliked_topics`
- `favorite_up_users`

每个兴趣标签包含：
- `name`
- `category`
- `weight`
- `first_seen`
- `last_seen`
- `source`

## 增量更新策略

- 首次运行：直接保存分析结果
- 后续运行：
  - 对旧标签按时间做衰减
  - 按 `name + category` 合并新旧标签
  - 新结果更新 `weight`、`last_seen`
  - 旧结果保留 `first_seen`
  - 新标签补齐 `first_seen`
- `weight` 始终 clamp 到 `0.0 ~ 1.0`
- 低于最小阈值的旧标签可被丢弃，避免长期噪声累积

## 架构

- 新增 `PreferenceAnalyzer`
  - 读取/格式化事件
  - 构建偏好分析 prompt
  - 调用 LLM
  - 解析和校验 JSON
  - 合并旧偏好
- `SoulEngine.analyze_events()` 调用 `PreferenceAnalyzer`
- `MemoryManager` 继续只负责事件/存储层，不承担偏好推理

## 测试策略

- `PreferenceAnalyzer` 单测：
  - prompt 组装
  - JSON 解析
  - 权重规范化
  - merge/decay
  - 坏响应错误处理
- `SoulEngine` 单测：
  - 分析事件后 preference 层被更新并保存

## 参考资料

- OpenAI Structured Outputs best practices:
  https://platform.openai.com/docs/guides/structured-outputs/best-practices
- Anthropic XML tags:
  https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags
- Anthropic multishot prompting:
  https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/multishot-prompting
