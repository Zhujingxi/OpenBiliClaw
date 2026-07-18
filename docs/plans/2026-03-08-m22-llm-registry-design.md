# M2.2 LLM Registry 设计

**目标**

完成 `docs/v0.1-todolist.md` 中 `2.2 Provider Registry`：根据配置自动注册 provider，支持默认 provider 降级、按顺序 fallback 调用，以及独立的健康检查命令。

**核心决策**

- 新增 registry 工厂函数，根据 `Config.llm` 实际可用配置构建 `LLMRegistry`
- 默认 provider 若不可注册，自动降级到第一个可用 provider
- `LLMRegistry` 增加统一的 `complete(...)` 与 `health_check_all()` 能力
- `health-check` 独立为 CLI 命令，不塞进 `config-show`

**范围**

- 修改 `src/openbiliclaw/llm/base.py`
- 新增 `src/openbiliclaw/llm/registry.py`
- 修改 `src/openbiliclaw/llm/__init__.py`
- 修改 `src/openbiliclaw/cli.py`
- 新增 registry / CLI 测试

**不在范围内**

- 不把 registry 接进所有业务 engine
- 不做并发 health check
- 不做复杂优先级策略配置

**注册规则**

- `openai` / `claude` / `deepseek`：存在非空 `api_key` 时注册
- `ollama`：始终可注册，只要有默认模型或可用 `base_url`
- 若 `default_provider` 已配置且可注册，则使用它
- 若 `default_provider` 不可注册，则降级到第一个可用 provider
- 若没有任何 provider 可注册，抛明确的 registry 构建错误

**fallback 规则**

- 默认先调用当前默认 provider
- 若抛出 `LLMProviderError` / `LLMRateLimitError` / `LLMTimeoutError`，按候补顺序继续尝试
- 若抛出 `LLMResponseError`，本轮不继续 fallback，直接失败
- 全部失败时，抛包含尝试 provider 列表的汇总错误

**CLI 行为**

- `config-show`：显示默认 provider、已注册 provider、最终选定默认 provider
- `health-check`：逐个检查已注册 provider，输出可用性和简短原因

**验收标准**

- 配置多个 provider 时自动注册并选择默认 provider
- 默认 provider 不可注册时自动降级
- 默认 provider 失败时 fallback 到备选 provider
- `openbiliclaw health-check` 能显示各 provider 状态
