# M2.3 Prompt 管理与调用适配设计

**目标**

完成 `docs/v0.1-todolist.md` 中 `2.3 Prompt 管理与调用适配` 的 P0 部分：建立统一的 Prompt 构建层和 LLM 调用适配层，在发起 LLM 请求前自动注入 core memory，并把当前已有的 `soul/dialogue.py` 占位逻辑接入真实调用链。

**核心决策**

- 新增 `llm/prompts.py`，集中管理 prompt 构建逻辑，不把业务 prompt 塞进 provider 或 registry
- 新增 `llm/service.py`，负责组装 registry、注入 core memory、执行统一的 LLM 调用
- `MemoryManager` 提供稳定的 core memory prompt 文本渲染入口，避免各模块直接拼接 dict
- `SocraticDialogue.respond()` 改为通过 prompt builder + service 进行真实调用，并保留用户可接受的降级回复

**范围**

- 新增 `src/openbiliclaw/llm/prompts.py`
- 新增 `src/openbiliclaw/llm/service.py`
- 修改 `src/openbiliclaw/llm/__init__.py`
- 修改 `src/openbiliclaw/memory/manager.py`
- 修改 `src/openbiliclaw/soul/dialogue.py`
- 必要时对 `src/openbiliclaw/soul/engine.py` 做最小适配
- 新增 prompt / service / dialogue 相关测试

**不在范围内**

- 不引入模板引擎、热加载或 prompt 版本管理
- 不实现发现、推荐等具体业务 prompt
- 不修改 provider fallback 策略
- 不做流式输出或结构化 schema 解析

**调用架构**

- `llm/prompts.py`
  - 提供统一的消息构建函数
  - 支持 system 角色说明、core memory 注入、历史对话拼接、当前任务附加说明
  - 先实现 `build_socratic_dialogue_prompt(...)`
- `llm/service.py`
  - 提供统一 LLM 调用入口
  - 自动把 core memory 转成 system prompt 的一部分
  - 对空响应和 prompt 输入错误做显式错误处理
- `soul/dialogue.py`
  - `respond()` 不再返回固定占位文本
  - 成功时记录用户和 agent 的 turn
  - 失败时写日志并返回自然的降级回复

**core memory 注入规则**

- 若 `MemoryManager` 有 soul / preference 数据，渲染为稳定文本区块并插入 system prompt
- 若暂时没有画像，仍生成可用 prompt，并明确标记“尚未建立完整画像”
- 当前任务说明追加在 core memory 之后，避免覆盖用户画像上下文

**错误处理**

- provider 层失败继续由现有 registry fallback 处理
- service 层只负责：
  - prompt 输入不合法时抛明确错误
  - LLM 返回空内容时抛业务错误
- `SocraticDialogue.respond()` 捕获上述错误并返回降级文案，不把原始异常暴露给用户

**验收标准**

- Prompt 构建时能稳定注入 core memory
- 统一调用层能通过 registry 发起请求
- `SocraticDialogue.respond()` 能生成真实 LLM 响应
- LLM 失败时对话模块返回降级回复，且历史记录仍正确更新
