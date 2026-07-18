# 4.5 核心记忆加载设计

## 目标

实现统一的核心记忆加载与注入机制：
- `MemoryManager.get_core_memory()` 返回稳定的结构化摘要，而不是原始层数据
- `render_core_memory_prompt()` 基于统一摘要生成稳定文本
- `LLMService` 提供统一的 core memory 注入入口
- 所有现有 LLM 调用链统一走该入口
- 为后续 5.x / 6.x 的发现与推荐任务预留统一任务接口

## 设计边界

本阶段不实现内容发现或推荐业务本身，只把这些未来任务需要的“统一调用方式”准备好。

本阶段也不重写所有 prompt builder。各模块仍可保留自己的任务 prompt，只是核心上下文注入统一交给 `LLMService` 处理。

## 架构方案

推荐方案是：
- `MemoryManager` 负责产生结构化 core memory 摘要与文本渲染
- `LLMService` 成为唯一负责“把 core memory 注入到任务 prompt”的门面
- 现有 analyzer / builder / dialogue 不直接依赖 `MemoryManager` 做字符串拼接，而是通过 service 获取统一注入

这样可以保证：
- 当前已有调用链行为一致
- 新增 discovery/recommend/evaluate 任务时不再复制 prompt 注入逻辑

## Core Memory 结构

`get_core_memory()` 返回稳定字典：

- `soul_summary`
  - `personality_portrait`
  - `core_traits`
  - `values`
  - `life_stage`
  - `deep_needs`
- `preference_summary`
  - Top-N 兴趣标签
  - 风格偏好
  - 探索倾向
  - 讨厌主题
  - 常看 UP 主
- `recent_awareness`
  - 最近 3~5 条观察
- `active_insights`
  - 最近或最高置信度的 3~5 条洞察假设

所有字段都做裁剪与默认值兜底，避免 prompt 被整层 JSON 直接淹没。

## Prompt 渲染

`render_core_memory_prompt()` 基于上述结构输出稳定文本分区：
- 用户画像
- 偏好摘要
- 近期观察
- 当前洞察

要求：
- 顺序固定
- 无数据时跳过或输出明确占位
- 对列表长度做上限控制

## LLMService 统一入口

新增通用入口，例如：
- `complete_with_core_memory(...)`
- `complete_structured_task(...)`

职责：
- 接收任务级 system prompt
- 自动注入 `render_core_memory_prompt()` 结果
- 再拼接任务输入
- 统一处理 provider 异常和空响应

`complete_socratic_dialogue()` 改为该通用入口的特化包装。

## 现有调用适配

本阶段统一适配：
- `SocraticDialogue`
- `ProfileBuilder`
- `PreferenceAnalyzer`
- `AwarenessAnalyzer`
- `InsightAnalyzer`

其中：
- Dialogue 继续通过 service 走字符串 prompt
- 各 analyzer / builder 改为通过 service 发起 JSON 模式任务
- 模块本身不再显式管理 core memory 文本注入

## 面向 5.x / 6.x 的预留

为后续 discovery / recommend / evaluation 准备统一调用方式：
- 任务说明
- core memory
- 任务输入 payload
- 可选 `json_mode`

这样后续新增：
- 搜索词生成
- 内容评估
- 推荐表达生成

都可以直接调用统一 service，而不是在每个模块重新手拼用户画像上下文。

## 兼容策略

- `render_core_memory_prompt()` 的中文区块结构保持兼容
- `SocraticDialogue.respond()` 对外行为不变
- `MemoryManager.initialize()` 和 `save_all()` 保持现有行为，只强化被测试覆盖
- 对已有测试中的 prompt 断言做最小必要更新

## 错误处理

继续沿用：
- `LLMProviderExecutionError`
- `LLMResponseContentError`

结构化任务如果 provider 返回空内容或非法 JSON：
- 由 analyzer / builder 抛本层错误
- service 只负责 provider 执行和 core memory 注入，不做业务 JSON 解析

## 测试策略

单元测试覆盖：
- `MemoryManager.get_core_memory()` 返回裁剪后的结构化摘要
- `render_core_memory_prompt()` 文本区块顺序稳定
- `LLMService.complete_with_core_memory()` 自动注入 core memory
- `complete_structured_task()` 在 `json_mode=True` 下正确透传
- `SocraticDialogue` 仍经统一 service 调用
- `ProfileBuilder` / `PreferenceAnalyzer` / `AwarenessAnalyzer` / `InsightAnalyzer` 不丢失 core memory 注入

## 影响文件

- 修改 `src/openbiliclaw/memory/manager.py`
- 修改 `src/openbiliclaw/llm/service.py`
- 轻量修改 `src/openbiliclaw/llm/prompts.py`
- 修改：
  - `src/openbiliclaw/soul/dialogue.py`
  - `src/openbiliclaw/soul/profile_builder.py`
  - `src/openbiliclaw/soul/preference_analyzer.py`
  - `src/openbiliclaw/soul/awareness_analyzer.py`
  - `src/openbiliclaw/soul/insight_analyzer.py`
- 新增或扩展测试：
  - `tests/test_memory_manager.py`
  - `tests/test_llm_service.py`
  - 对应 analyzer 的回归测试
