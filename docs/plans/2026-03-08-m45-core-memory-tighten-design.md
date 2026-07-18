# 4.5 核心记忆加载收口设计

## 问题

`4.5` 已经建立了主运行链路上的 core memory 注入：

- `MemoryManager.get_core_memory()`
- `MemoryManager.render_core_memory_prompt()`
- `LLMService.complete_with_core_memory()`
- `LLMService.complete_structured_task()`

但 `ProfileBuilder`、`PreferenceAnalyzer`、`AwarenessAnalyzer`、`InsightAnalyzer` 仍然保留了 fallback：

- 如果传入的是 `LLMService`，会走 `complete_structured_task()`
- 如果传入的是只有 `complete()` 的原始 registry，也能直接绕过 core memory 注入

这意味着 `4.5` 在默认路径上成立，但还不是一个被代码强制执行的约束。

## 目标

收口 `4.5`，让上述 4 个模块只接受带 core memory 注入能力的调用接口，不再允许回退到原始 `registry.complete(..., json_mode=True)`。

## 方案

采用最小收口方案：

- 为 Soul 相关结构化任务统一定义窄接口 `SupportsCoreMemoryTask`
- `ProfileBuilder`、`PreferenceAnalyzer`、`AwarenessAnalyzer`、`InsightAnalyzer` 全部只依赖这个接口
- 删除 `_complete()` 中直接调用 `registry.complete(..., json_mode=True)` 的分支
- `SoulEngine` 继续通过 `LLMService(registry=llm, memory=memory)` 注入该能力

## 非目标

- 不修改 `LLMRegistry`
- 不把 core memory 注入下沉到 provider / registry
- 不调整 `SearchStrategy`，它已经只依赖 `complete_structured_task()`

## 测试要求

- 现有基于 `FakeStructuredService` 的测试继续通过
- 新增失败测试，证明只提供 `complete()` 的 fake registry 不再被接受
- 全量验证 `ruff`、`mypy`、`pytest`
