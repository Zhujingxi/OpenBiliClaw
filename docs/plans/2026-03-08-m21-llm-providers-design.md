# M2.1 LLM Provider 实现设计

**目标**

完成 `docs/v0.1-todolist.md` 中 `2.1 Provider 实现`：补齐 OpenAI、Claude、DeepSeek、Ollama 四个 provider 的超时、有限重试、错误归一化与基础兼容性。

**核心决策**

- 保持 `LLMProvider.complete()` 作为统一调用接口，不修改上层 engine 的使用方式
- 在 `llm/base.py` 中增加统一异常类型，供各 provider 对外暴露一致的错误语义
- OpenAI-compatible 路径复用：OpenAI、DeepSeek、Ollama 尽量共用一套请求与错误处理逻辑
- Claude 继续使用 Anthropic SDK，但同样归一到统一异常类型

**范围**

- 修改 `src/openbiliclaw/llm/base.py`
- 修改 `src/openbiliclaw/llm/openai_provider.py`
- 修改 `src/openbiliclaw/llm/claude_provider.py`
- 新增 `src/openbiliclaw/llm/ollama_provider.py`
- 修改 `src/openbiliclaw/llm/__init__.py`
- 增加 provider 单元测试

**不在范围内**

- 不实现 provider registry 自动初始化
- 不做跨 provider fallback
- 不做流式响应
- 不做 schema 级 JSON 校验

**错误处理策略**

- `LLMRateLimitError`：429 / 限流类错误
- `LLMTimeoutError`：网络超时或请求超时
- `LLMResponseError`：响应结构非法、空内容、缺少关键字段
- `LLMProviderError`：其他 provider 请求失败

**Provider 约束**

- OpenAI：处理 rate limit、retry、超时
- DeepSeek：继承 OpenAI-compatible provider，默认 `base_url=https://api.deepseek.com`
- Ollama：本地默认 `base_url=http://localhost:11434/v1`，不强制 `api_key`
- Claude：处理 SDK 异常映射、重试、超时

**验收标准**

- 四个 provider 都能构造统一的 `LLMResponse`
- OpenAI / Claude 在超时、限流、空响应等情况下给出明确统一错误
- DeepSeek 与 Ollama 能复用 OpenAI-compatible 通路
- 单元测试覆盖成功、重试、超时与异常映射
