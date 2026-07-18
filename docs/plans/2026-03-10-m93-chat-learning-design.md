# M93 聊天驱动画像学习设计

## 目标

让 `openbiliclaw chat` 和 popup `和阿B聊聊` 不再只是消费现有画像，而是把高质量聊天内容沉淀为受控学习信号，逐步参与偏好层和画像层更新。

## 设计原则

1. 不让单轮聊天直接重写 `soul.json`
2. 聊天先进入事件层和候选洞察层，再由阈值控制是否参与画像更新
3. CLI 与 popup 共用同一条后端学习链路
4. 第一版不新增用户确认 UI，先把后端链路跑通

## 数据流

### 1. 聊天事件持久化

每轮聊天完成后，写一条 `event_type="dialogue"` 到 `events` 表：

- `title`: 用户消息摘要
- `metadata.user_message`
- `metadata.assistant_reply`
- `metadata.source`: `cli` 或 `popup`

这样对话成为和 `view/search/feedback` 同级的原始证据。

### 2. 洞察候选提取

新增 `DialogueInsightAnalyzer`，输入：

- 用户消息
- 阿B 回复
- 当前 core memory

输出严格 JSON：

- `kind`: `interest` / `dislike` / `goal` / `value` / `state`
- `content`
- `confidence`
- `evidence`

候选结果持久化到 `data/memory/insight_candidates.json`，而不是单独建表。

### 3. 候选合并

新增或更新候选时：

- 文本相近则视为同一候选
- 合并 `occurrences`
- 刷新 `updated_at`
- `confidence` 取较高值

### 4. 生效门槛

只有满足以下条件之一的候选，才进入偏好重分析：

- `confidence >= 0.8` 且 `occurrences >= 2`
- `confidence >= 0.9` 且 `kind in {"dislike", "goal"}`

这些达标候选会被转换成高权重事件批次，送入现有 `PreferenceAnalyzer`。

### 5. 画像更新

沿用已有 `9.2` 逻辑：

- 偏好变化不明显：只更新 `preference.json`
- 偏好变化明显：重建 `soul.json`

## 模块改动

### 新增

- `src/openbiliclaw/soul/dialogue_insight_analyzer.py`

### 修改

- `src/openbiliclaw/soul/dialogue.py`
- `src/openbiliclaw/soul/engine.py`
- `src/openbiliclaw/memory/manager.py`
- `src/openbiliclaw/api/app.py`
- `tests/test_soul_dialogue.py` 或扩展现有 `tests/test_soul_engine.py`
- `tests/test_memory_manager.py`

## 运行时行为

- CLI `chat`：每轮对话后自动记忆，但不额外提示复杂状态
- popup `和阿B聊聊`：同样自动记忆
- 用户短期内看不到 UI 变化；画像更新仍通过 `profile` / popup 画像页体现

## 错误处理

- 提取失败：只记录日志，不中断聊天回复
- 候选 JSON 损坏：回退为空列表并重建
- 未达到阈值：不触发偏好/画像更新

## 测试策略

1. `DialogueInsightAnalyzer`：结构化提取、坏 JSON、空响应
2. `MemoryManager`：`insight_candidates.json` 读写与合并
3. `SocraticDialogue`：聊天后写 `dialogue` 事件并调用 analyzer
4. `SoulEngine`：阈值未达不更新、阈值达到后触发偏好更新

## 文档更新范围

- `docs/modules/soul.md`
- `docs/modules/memory.md`
- `docs/modules/cli.md`
- `docs/modules/extension.md`
- `docs/changelog.md`
- `docs/v0.1-todolist.md`
