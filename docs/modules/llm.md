# LLM 多模型支持

> 运行时并发由单一 `LLMConcurrencyGate` 管理：所有 provider 请求受总 gate（默认 4）约束，后台还受 `max(1, total-1)`（默认 3）约束。后台 admission 依据 canonical durable inventory 把工作分为 `refill.expression > refill.evaluation > refill.supply > maintenance`；有 refill waiter 时保证下一批新准入至少两个 refill 槽并可借满三个，库存为零时 park 新 maintenance。对话与 `api.sentiment` 是交互流量；未知 caller 只告警一次并按 maintenance 处理。旧 `bypass_semaphore=True` 只绕过后台 gate，`PrioritySemaphore` 仍从 `llm.service` 兼容导出。

热重载不会替换 gate 对象，而是原地 `reconfigure()`：升容立即按优先级唤醒等待者；降容不撤销已进入 provider 的工作，并在 active 降到新容量以下前停止新准入。配置探测也使用 `api.config_probe` 后台分类经过同一 gate。

> 统一的多 LLM Provider 接口，支持 OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OpenAI-compatible，带显式备选 Provider、retry 和健康检查。

## 概述

`llm/` 包提供了一套抽象的 LLM 调用接口，上层模块（Soul Engine、Discovery Engine 等）通过 `LLMService` 或 `LLMRegistry` 发起调用，不需要关心底层用的是哪个模型。

核心设计：
- **Provider 抽象** — `LLMProvider` ABC 定义统一接口
- **Registry 管理** — 根据 config 自动注册可用 provider，fallback 默认关闭、可在配置中显式打开
- **Service 门面** — `LLMService` 封装 prompt 组装 + 调用 + 校验
- **统一异常** — 所有 provider 错误归一化为标准异常类型

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 2.1 Provider 实现 | ✅ | OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OpenAI-compatible，带 retry + 超时 |
| 2.2 Provider Registry | ✅ | 自动注册 + 可配置 fallback + health check |
| 2.3 Prompt 管理与 Service | ✅ | Prompt 构建器 + LLMService 门面 |
| v0.3.164+ OpenAI-compatible JSON-object 合约 | ✅ | `LLMService.complete_structured_task()` 与 `complete_multimodal_structured_task()` 共享最小兼容层：已有大写 `JSON` 仅归一为小写 `json`；完全没有该 token 时只追加 `json`。这满足部分 OpenAI-compatible 端点对 `response_format=json_object` 的字面消息约束，不改变业务规则、画像、阈值、user 内容或 core-memory 排序；非结构化 `complete_with_core_memory()` 完全不改写 prompt。 |
| v0.3.162+ LLM 失败可操作说明 | ✅ | `llm.base.describe_llm_failure()` 沿异常 cause/context 链翻译上层错误；新增 authentication / unauthorized / invalid API key / 401 鉴权桶，并将 insufficient quota / quota / exhausted / 429 归入「额度用尽或被限流」桶，API 与 CLI 继续消费同一函数，不新增 init reason code |
| v0.3.164 LLM 失败安全边界 | ✅ | `describe_llm_failure()` 识别 moderation、鉴权、额度/限流、provider/service 超时与空响应；`safe_llm_failure_message()` 为 API / CLI / OpenClaw 的公共边界提供固定安全兜底，未知异常不回传上游文本 |
| v0.3.160+ Discovery 统一评估契约 | ✅ | 单条与 batch 内容评估 prompt 仅允许 `explore` 保留主题距离例外；`search` / `trending` / `hot` / `feed` / `related_chain` / `channel` / `creator` 及所有平台不得获得基础分、自动加分、较低门槛或事后画像关联，明显不匹配内容允许低于 admission 门槛 |
| 4.5 核心记忆加载 | ✅ | 统一 core memory 注入入口，覆盖 Soul 全链路 |
| v0.3.149+ 关键词合并 prompt 探索 block | ✅ | `build_merged_keywords_prompt()` 支持可选 `explore_domains_block`，只在 runtime 判断 B 站 explore refresh 到期 / 即将到期且有补货空间时追加；system prompt 明确这些 query 是探索性 B 站搜索方向，不应把常规兴趣关键词换皮成 explore。`parse_merged_keywords_with_presence_and_explore_domains()` 在保留平台关键词 decline / omission 语义的同时清洗 `explore_domains` |
| v0.3.147+ Prompt layer cache | ✅ | `profile_prompt_layers()` 把结构化画像拆为 `profile_core` / `profile_life_context` / `profile_interests` / `profile_style_context` / `profile_recent_context`，从稳定到易变排序；`PromptLayerRenderCache` 按层 digest 复用已渲染 JSON prompt block，供 discovery eval、推荐分类 / 文案 / delight 和统一关键词 planner 共享，画像核心不变时 provider 看到的前缀保持 byte-stable |
| v0.3.144+ 缓存前缀保护 | ✅ | `LLMService.complete_with_core_memory()` / `complete_structured_task()` / `complete_multimodal_structured_task()` 支持 `inject_core_memory=False`，供候选 eval、推荐分类 / delight、跨平台关键词生成、awareness / insight / speculation / profile build、初始化偏好分析这类已自带完整结构化上下文的路径跳过重复 memory 注入；`build_soul_profile_prompt()` 也保持静态 system，并把 tone / preference / awareness / insight 放在巨大 history 前，稳定 provider prompt-cache 前缀 |
| v0.3.150+ DeepSeek thinking 显式关闭 | ✅ | `DeepSeekProvider.complete(..., reasoning_effort="")` 会向 DeepSeek 请求体写入 `thinking={"type":"disabled"}`。DeepSeek v4 默认开启 thinking，单纯省略字段并不会关闭 reasoning；配置页 LLM 探测和短结构化任务因此能真正避免 thinking 先耗尽输出预算后返回空 `content` |
| v0.3.150+ reasoning-only 诊断 | ✅ | OpenAI-compatible / DeepSeek / OpenRouter / Ollama native 返回 HTTP 200 且含 `reasoning_content` / `reasoning` / `thinking`、但最终 `content` 为空时，仍判为不可用，但错误会明确提示 `returned reasoning but no final content` 并带 `finish_reason`，避免和完全空响应混淆 |
| v0.3.117+ reasoning-first 探活 | ✅ | `LLMProvider.health_check()` 与配置页 LLM 测试探针统一使用 `max_tokens=4096`，避免 SenseNova 等 OpenAI-compatible reasoning-first 模型先产出 `message.reasoning`、尚未到 `message.content` 就被截断，从而误报空响应 |
| v0.3.75 Per-module LLM 路由生效 | ✅ | `LLMService` 按 caller bucket 路由 `[llm.soul/discovery/recommendation/evaluation]`，通过 `LLMRegistry.complete_provider()` 精确调用 chat-capable provider；provider 错误不 spill 到 default，拼错 provider INFO 一次后降级 |
| v0.3.75 Provider per-call model | ✅ | OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OpenAI-compatible 的 `complete(..., model=...)` 支持单次模型覆盖，不修改 provider 实例默认 `_model` |
| 体验优化：B站动态语气 | ✅ | 推荐、画像总结和聊天 prompt 统一接入 `ToneProfile`，在“老B友”基础上按用户画像微调语气 |
| v0.3.0 Ollama embedding 兜底 | ✅ | `OllamaProvider.embed()` 走原生 `/api/embeddings`，配合 `bge-m3` 模型可在 Mac/Win/Linux CPU 跑相似度计算，不需要额外的 embedding API Key |
| v0.3.0 EmbeddingService 双层缓存 | ✅ | L1 内存 + L2 SQLite 持久化；`build_embedding_service` 按 provider 自动选默认 model（gemini→gemini-embedding-001 / openai→text-embedding-3-small / ollama→bge-m3） |
| 可选封面 image-only embedding | ✅ | `[llm.embedding].multimodal_enabled` + 多模态 embedding 模型（`gemini-embedding-2` 族，或 `dashscope` + `qwen3-vl-embedding` 等）时，`EmbeddingService.embed_image()` 把压缩封面打成向量，与文本同 `model`/维度空间；discovery 入池按封面 URL 派生键（`image_embedding_cache_key_for_url`）预热，Delight 线上 `precompute_delight_scores` 消费（见 [recommendation 模块](recommendation.md) 封面视觉加成）。默认关闭；provider/model 不支持图像或开关关闭时自动 no-op（纯文本模型零成本、打分与旧版逐字节一致） |
| DashScope 多模态 embedding | ✅ | `provider = "dashscope"` → `DashScopeEmbeddingProvider`：原生 multimodal-embedding API；`embed`/`embed_image` 独立向量（不 `enable_fusion`）；默认 `qwen3-vl-embedding`；`output_dimensionality` 对 qwen3-vl 透传 `dimension`；embedding-only（`complete` 拒绝） |
| v0.3.113 Embedding 目标维度 | ✅ | `[llm.embedding].output_dimensionality` 默认 1024，与 Ollama `bge-m3` 对齐；Gemini 传 `output_dimensionality`，`provider = "openai"` 且模型为 `text-embedding-3-*` 时传 `dimensions`，Ollama / OpenRouter / 泛 OpenAI-compatible 等未确认支持的后端不传。L2 cache 仅在 provider 确认支持目标维度时按 `model#dim=N` 签名隔离，同一文本的不同维度向量不会互相覆盖，也不会把未生效的兼容后端标成伪维度 |
| v0.3.155 Ollama embedding 诊断 + 自修 | ✅ | `llm/ollama_diagnostics.py`：`diagnose_ollama_embedding()` 把向量模型不可用分类为 `not_running` / `model_missing` / `model_broken` / `model_path_encoding` / `disk_full` / `network` / `model_oom` / `error`（先 `/api/tags` 判定服务与模型在位，再真打一次 embed——覆盖"模型在列表里但加载失败"的 500 场景）。`model_path_encoding` 专指 Windows 非 ASCII 用户名 / mojibake 路径导致 `llama-server` 无法从 `.ollama\models` 加载模型的失败，重新拉取不会修复，需迁移模型目录或手动设置 `OLLAMA_MODELS` 到纯英文路径；`model_oom` 从旧 `model_broken` 中拆出，明确内存不足时重拉无效；`disk_full` 既识别 pull / probe 错误文本，也会在拉取前检查 `OLLAMA_MODELS` / 托管模型目录所在卷是否至少有约 2.0GB 空间；`network` 区分无法访问 registry 的下载源问题与本地模型损坏。`pull_ollama_model()` 经原生 `/api/pull` 流式拉取 / 重拉模型并回调进度；两者均 `trust_env=False` 且可注入 `httpx.MockTransport` 测试。`OllamaProvider.embed()` 失败日志附带响应体错误片段（此前只有裸状态码）。供 `/api/init-status` 的 `embedding_check`/`embedding_detail` 与 `POST /api/embedding/repair` 一键修复使用（见 [init 模块](init.md)） |
| v0.3.97 EmbeddingService 实时探活 | ✅ | `EmbeddingService.probe()` 绕过 L1/L2 缓存直接打一次 provider，返回是否拿到非空向量；供 `/api/health.embedding_ready` 做**实时**就绪判定（缓存命中的旧成功不会掩盖 provider 已掉线 / 模型没拉）。`/api/health` 侧自带 TTL + single-flight，probe 不缓存结果、每次都真打 |
| v0.3.114 配置页服务探测 | ✅ | `POST /api/config/probe-service` 对用户当前表单草稿做无写入真实探测：LLM 走临时 `LLMRegistry.complete_provider()`，embedding 走临时 `EmbeddingService.probe()`，结果供 PCWeb / 插件设置页行内展示 |
| v0.3.20 Embedding fallback 能力识别 | ✅ | `LLMProvider.supports_embedding` 类属性显式声明 provider 是否真的有 embeddings endpoint。Claude / DeepSeek / OpenRouter 标 `False`（前者无 API、后两者继承自 OpenAIProvider 但实际后端不路由 embeddings）；OpenAI / Gemini / Ollama 标 `True`。当前只在 `[llm.embedding].fallback_provider` 非空时尝试一个显式备选 provider |
| v0.3.89.1 OpenRouter embedding 显式路径 | ✅ | `[llm.embedding].provider = "openrouter"` 现在会被 `_build_dedicated_embedding_provider` 构造成 `OpenRouterProvider` 实例（必须配 `model = "<vendor>/<model>"`，例如 `google/gemini-embedding-2-preview`；无显式 model 时拒绝构建，避免 404）。`OpenRouterProvider.supports_embedding` 仍保持 `False` —— 只有用户显式在 `[llm.embedding]` 选 openrouter 才走这条路径，不污染 chat-side 的自动回退链。`[llm.openrouter]` 的 `http_referer` / `x_title` 也会透传给 embedding 实例，让 OpenRouter 后台账单与 chat 流量归一 |
| v0.3.20 OpenAI Provider embed | ✅ | `OpenAIProvider.embed()` 走 `/v1/embeddings`，默认 `text-embedding-3-small`。OpenAI 用户没显式配 embedding 时不再静默返回 None。失败返回 `[]`（与 Ollama / Gemini 一致），调用方降级处理 |
| v0.3.31 DeepSeek 空内容兜底 | ✅ | DeepSeek 返回 HTTP 200 但 `content=""` 时，provider 会重试一次；`reasoning_effort` 开启时仍先关闭 thinking 重试，普通模式则原参数重试，避免 explore / structured task 因一次空内容直接降级为空结果 |
| v0.3.32 Embedding 与 LLM Provider 解耦 | ✅ | `EmbeddingConfig` 拥有独立的 `api_key` / `base_url` / `output_dimensionality`；`build_embedding_service` 直接构造一个独立 provider 实例（不走 chat-side `LLMRegistry`），切换 chat 模型不会改变 embedding provider / model / 维度，并把旧的 `embedding_wants_ollama` 自动注册 hack 删掉 |
| v0.3.x 显式 fallback provider | ✅ | 自动 fallback 链已移除。`LLMRegistry.complete()` 只在 `[llm].fallback_provider` 非空时按 `default_provider → fallback_provider` 尝试；embedding 只在 `[llm.embedding].fallback_provider` 非空时尝试一个备选 provider，空 provider 不再跟随 `[llm].default_provider` |
| v0.3.98 Ollama 作 chat fallback 识别 | ✅ | `_ollama_is_chat_capable()` 新增第四个入口：`[llm].fallback_provider = "ollama"`。此前只认 `[llm.ollama] model` / `default_provider` / 模块 override，导致用户把本地 Ollama 设为 chat 兜底、却没单独配 `[llm.ollama] model` 时，Ollama 被判为 embedding-only 并被 `_fallback_order()` 静默剔除，主 provider 失败直接 `LLMFallbackError`。现在尊重该意图（无 `model` 时用 `llama3` 默认，需本地已 `ollama pull` chat 模型；`bge-m3` 这类 embedding 模型仍无法兜底 chat）|
| v0.3.32 OpenAI 协议兼容 provider | ✅ | 新增 `openai_compatible` 一级 provider（独立 `[llm.openai_compatible]` block），用于 Groq / Together / Azure OpenAI / vLLM / 自建等任何走 OpenAI 协议的服务。底层复用 `OpenAIProvider`，但 `provider_name="openai_compatible"`，与 `[llm.openai]` 互不干扰。`base_url` 必填（缺失会被 `_collect_config_issues` 拦下、`_maybe_openai_compatible_provider` 拒绝注册）。embedding 段也接受 `openai_compatible` |
| v0.3.69 Gemini reasoning-first 模型适配 | ✅ | `GeminiProvider._is_reasoning_first_model` 用 prefix 识别 `gemini-3.x` / `gemini-2.5-pro*`，json_mode 下不再附加 `thinking_budget=0`（这些模型会以 `400 INVALID_ARGUMENT` 拒绝）；`gemini-2.5-flash` 等非 reasoning-first 模型继续走省钱通路。pricing 补全 `gemini-3.1-pro-preview` / `gemini-3-pro-preview` 别名，配套 CLI / config / 文档统一改用真实模型 ID |
| v0.3.71 Prompt-cache 与 400 诊断 | ✅ | `build_awareness_prompt` / `build_batch_content_evaluation_prompt` / `build_soul_profile_prompt` 的 user prompt 按稳定画像 / tone / preference 在前、本次批次或历史在后排序，并使用 `sort_keys=True` 的确定性 JSON；`OpenAIProvider._map_error()` 会把 OpenAI-compatible HTTP 400 响应体摘要写入 WARNING 和错误文本，便于定位 MiMo 等兼容服务的请求 schema 问题 |
| v0.3.71 Awareness 缓存形态回归锁 | ✅ | `build_awareness_prompt` 的 system 内容固定为模块级常量 `_AWARENESS_SYSTEM_PROMPT`，user 块顺序锁定为 `<soul_profile>` → `<preference_summary>` → `<recent_events>`，并通过 `tests/test_llm_prompts.py` 的 byte-equal / 末尾块 / 不同字典 key 序仍产相同字节三组回归测试保证未来改动不会再把变量数据放进 system、不把 recent_events 之后塞入稳定块、或丢掉 `sort_keys=True` |
| v0.3.74 结构化输出共享解析 | ✅ | 新增 `llm/json_utils.py`，统一提供 `extract_llm_json_list()` / `extract_llm_json_object()` / `parse_llm_json_tolerant()`。调用方可传 item/object predicate 和 wrapper aliases，兼容 root array/object、`results/items/data/output/scores/evaluations` 等 wrapper、singleton dict、Markdown fenced JSON、JSONL、多 root echo 后最终结果，以及 MiMo 形态的 malformed `{ [ ... ] }` 数组包裹 |
| v0.3.74 Ollama embedding 空凭据静默本地默认 | ✅ | `embedding.provider="ollama"` 且 embedding `api_key/base_url` 为空时直接构造本地 Ollama provider，默认 `http://localhost:11434/v1`；如果 chat-side `[llm.ollama].base_url` 非空，会复用并规范化到 `/v1`，不再触发 `_emit_embedding_compat_warning()`。远端 embedding provider 留空凭据时仍保留一次性向后兼容 WARNING |
| v0.3.77 LM Studio JSON mode 兼容 | ✅ | `OpenAIProvider` 的 `json_mode=True` 对普通 OpenAI-compatible 后端默认使用 `json_object`，遇到 `response_format.type` 只允许 `json_schema/text` 时用通用 `json_schema` 重试；对本地 LM Studio（默认 `localhost/127.0.0.1:1234` 或 URL 含 `lmstudio` / `lm-studio`）首次请求即不发送 `response_format`，依赖 prompt 约束 JSON，避免 compat 层在 `json_object` / `json_schema` 下丢失 `message.content` 后再浪费一整次 LLM 调用 |
| v0.3.78 Codex OAuth 实验认证 | ✅ | `[llm.openai].auth_mode="codex_oauth"` 时，OpenAI provider 复用 Codex CLI 的 ChatGPT OAuth 凭据；`codex_auth.py` 负责导入 `~/.codex/auth.json`、安全落盘、临期刷新，`OpenAIProvider` 在 401 时强制刷新并重试一次。该路径为非官方实验集成，只允许 OpenAI 官方 `base_url` |
| v0.3.x LLM 限流识别 | ✅ | `is_llm_rate_limit_error()` 会沿异常链识别 `LLMRateLimitError`、cooldown、429 / quota / resource exhausted 文本；discovery / recommendation 批量调用据此跳过逐条 fallback，避免一次 provider 限流放大成 N 个必失败调用和堆栈日志 |
| v0.3.x 余额 / 账单错误熔断 | ✅ | OpenAI-compatible provider 会把 HTTP 402、`Insufficient Balance`、`payment required`、`billing`、余额不足等 provider 余额 / 账单失败归一为 `LLMRateLimitError`，跳过 provider 内部 retry，并让 registry cooldown 与批量任务的“跳过逐条 fallback”保护生效 |
| v0.3.x Eval-batch 负样本锚定与跨平台公平 | ✅ | `build_batch_content_evaluation_prompt` 新增可选 `negative_examples` kwarg；非空时在 user prompt `<source_context>` 与 `<content_batch>` 之间插入 `<negative_examples>` 块（`sort_keys=True` 决定性 JSON）。`None` / `[]` 退回原 user 字节形态以保留 cold-start 缓存前缀。`_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT` 加入永久规则：按话术 / 商业意图 / 标题结构层面 pattern-match 候选与示例，不要看关键词重叠；混源 batch 中不得仅因 `source_platform` 不同而抬高或压低 preference score，只能把平台作为内容语境。规则改动一次后 system message 保持 call-invariant |
| v0.3.x dislike-aware prompts | ✅ | `build_preference_analysis_prompt` 明确把 negative / dislike / thumbs_down 事件限制为 `disliked_topics` 与风格避让证据，禁止提取为正向兴趣；`build_awareness_prompt` 可从近期 dislike 生成“最近开始避开 X”的保守观察；单条 / 批量推荐表达 prompt 会消费 `profile_summary.disliked_topics`，命中避雷项时不得热情背书 |
| v0.3.x 避雷探针多样性 prompt | ✅ | `build_avoidance_generation_prompt` 会携带 `existing_avoidance_details`，让 LLM 看到已有 active 的 `source_mode`、`source_signal`、体验轴和 specifics；system prompt 要求同一 `source_mode` + 同一粗主题 / 证据源只生成一个候选，已有 AI positive_boundary 时不再输出 AI 教程 / 测评 / 趋势换皮项 |
| v0.3.x 第三方 API 网关适配（issue #72） | ✅ | 两条路径：(1) `[llm.claude].base_url` 全链路穿透到 `AsyncAnthropic`，Claude 可走任何 Anthropic 协议（`/v1/messages`）中转网关，留空仍用官方地址；(2) `[llm.openai]` / `[llm.openai_compatible]` 新增 `api_flavor` —— `""`/`"chat_completions"` 走 `/v1/chat/completions`（默认），`"responses"` 走 `/v1/responses`（system→`instructions`、`max_tokens`→`max_output_tokens`、json_mode→`text.format`、`input_tokens_details.cached_tokens` 归一为 `cached_input_tokens`；每个 Responses 请求都会显式发送顶层 `store=false`，兼容官方 OpenAI 及由 ChatGPT/Codex Responses 端点驱动的第三方网关；gpt-5 家族拒收 `temperature` 时自动降参重试）。非法值被 `_collect_config_issues` blocking 拦下 |
| v0.3.162+ 托管 Ollama 生命周期自愈 | ✅ | `runtime/ollama_supervisor.py` 记录托管 daemon 的完整启动规格并新增 watchdog；`with-embedding` 私有 11435 daemon 纳入一键修复与崩溃自动拉起（详见下方[托管 Ollama 生命周期](#托管-ollama-生命周期v03162)） |
| v0.3.165 海外网络三模式 | ✅ | `OpenAIProvider` / `ClaudeProvider` / `GeminiProvider`（含 DeepSeek / OpenRouter 子类与 embedding 实例）同时接收 `proxy` 与 `trust_env`。registry 统一读取 `[network].mode`：`direct` 注入忽略环境代理的 SDK transport，`system` 保留 SDK 环境继承，`custom` 注入指定代理并强制 `trust_env=False`。**Ollama 工厂不读该策略**——本地 / CN 直连由 `tests/test_network_proxy_isolation.py` 守卫 |

## 公开 API

### Provider 类

```python
from openbiliclaw.llm import (
    ClaudeProvider,
    DeepSeekProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
)

# 创建 provider
provider = OpenAIProvider(api_key="sk-...", model="gpt-4o")
response = await provider.complete([{"role": "user", "content": "hello"}])
print(response.content)  # str
print(response.provider)  # "openai"
print(response.usage)     # {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}

# 单次调用覆盖模型；不会写回 provider._model
response = await provider.complete(
    [{"role": "user", "content": "hello"}],
    model="gpt-4.1-mini",
)

# JSON mode；普通 OpenAI-compatible 后端使用 response_format 约束并保留 json_schema fallback。
# 本地 LM Studio 首次请求即跳过 response_format，依赖 prompt 约束 JSON 输出。
response = await provider.complete(
    [{"role": "user", "content": "只返回 JSON 对象"}],
    json_mode=True,
)

# 健康检查
available = await provider.health_check()  # bool
# health_check 使用 max_tokens=4096，兼容先输出 reasoning 再输出 content 的服务。
# 设置页 / 插件的配置探针也使用同一个连通性探针预算。

provider = OpenRouterProvider(
    api_key="or-...",
    model="openai/gpt-4o-mini",
    http_referer="https://example.com",
    x_title="OpenBiliClaw",
)

provider = GeminiProvider(
    api_key="gemini-key",
    model="gemini-2.5-flash",
)
```

### Codex OAuth 凭据辅助

```python
from openbiliclaw.llm.codex_auth import (
    get_valid_codex_token,
    import_codex_credentials,
    load_codex_credentials,
)

# 导入官方 Codex CLI 登录态，默认读取 ~/.codex/auth.json，
# 写入 ~/.openbiliclaw/codex_auth.json。
credentials = import_codex_credentials()
print(credentials.account_id)

# Provider 运行时会调用它；临期时自动刷新。
token = await get_valid_codex_token()
```

Codex OAuth 是实验路径：OpenAI 官方 API 认证仍以 Platform API key 为准；该模块只复用本机 Codex CLI 凭据，不自建 OAuth PKCE 浏览器流程，也不会把 token 打印到 CLI 输出。

### Registry

```python
from openbiliclaw.llm import build_llm_registry
from openbiliclaw.config import load_config

registry = build_llm_registry(load_config())
print(registry.available_providers)  # ["openai", "claude", "gemini", "deepseek", "ollama", "openrouter", "openai_compatible"]
print(registry.default_provider)     # "deepseek"

# 默认不 fallback；如需备选，设置 [llm].fallback_provider 为第二个 provider
response = await registry.complete([{"role": "user", "content": "hi"}])

# 精确调用某个 chat-capable provider，不走 fallback；用于 per-module override
response = await registry.complete_provider(
    "deepseek",
    [{"role": "user", "content": "hi"}],
    model="deepseek-v4-flash",
)
assert registry.is_chat_capable("ollama") in (True, False)

# 全量健康检查
results = await registry.health_check_all()
# {"openai": HealthCheckResult(available=True, is_default=True), ...}
```

### 配置草稿探测 API

```http
POST /api/config/probe-service
```

该接口面向设置页，不写配置文件。后端会把请求中的 `config.llm` 合并到当前配置的内存副本，然后按 `kind` 真实打一次目标服务：

- `kind="llm"`：构建临时 `LLMRegistry`，校验 `default_provider` 是 chat-capable，再调用 `complete_provider(provider, ..., max_tokens=4096)` 发送最小 chat completion；如果 provider 只返回 reasoning / thinking 而没有最终 `content`，返回 `ok=false` 并显示明确诊断。
- `kind="llm_fallback"`（v0.3.155+）：同 `llm`，但探测对象是 `[llm].fallback_provider` 这一个精确 provider（走 `complete_provider`，不走 fallback 链）。备选未配置或与 `default_provider` 同名时直接返回 `ok=false` + 明确说明（不是 500）。
- `kind="embedding"`：构建临时 `EmbeddingService`，调用 `probe()` 绕过 L1/L2 cache 获取一次真实向量。

失败以 `ok=false` 的正常响应返回，前端可直接显示 provider / model / latency / error；详见 [配置参考](config.md)。

### LLMService

```python
from openbiliclaw.llm import LLMService
from openbiliclaw.llm.service import module_overrides_from_config

service = LLMService(
    registry=registry,
    memory=memory_manager,
    module_overrides=module_overrides_from_config(config),
)
response = await service.complete_socratic_dialogue(
    user_message="我最近喜欢看纪录片",
    history=[...],
)
# prompt 自动包含用户画像（core memory）和动态 tone profile，空响应自动拦截

response = await service.complete_structured_task(
    system_instruction="你要从用户行为中提取结构化偏好。",
    user_input='{"events": [...]}',
)
# 自动注入 core memory，并以 json_mode 调用 provider

response = await service.complete_structured_task(
    system_instruction="你要批量评估候选内容。",
    user_input="<profile_core>...<profile_recent_context>...<content_batch>...",
    caller="discovery.evaluate_batch",
    inject_core_memory=False,
)
# 已在 user_input 携带完整结构化上下文的高频结构化任务
# (如候选 eval / 推荐分类与 delight / 关键词生成 / 画像分析) 可关闭额外 core memory 注入，
# 让 provider-side prompt cache 前缀更稳定。

from openbiliclaw.llm import is_llm_rate_limit_error

try:
    await service.complete_structured_task(system_instruction="...", user_input="...")
except Exception as exc:
    if is_llm_rate_limit_error(exc):
        # 批量调用方可跳过逐条 fallback，等待下一轮调度重试。
        ...
```

### 结构化 JSON 解析 helper

```python
from openbiliclaw.llm.json_utils import extract_llm_json_list, extract_llm_json_object

scores = extract_llm_json_list(
    response.content,
    wrapper_aliases=("scores", "evaluations"),
    item_predicate=lambda item: isinstance(item, dict) and "score" in item,
)

profile_delta = extract_llm_json_object(
    response.content,
    wrapper_aliases=("result", "data"),
    object_predicate=lambda obj: isinstance(obj, dict) and "summary" in obj,
)
```

这些 helper 是 MiMo / OpenAI-compatible / reasoning 模型结构化输出的统一容错边界。调用方仍应用 predicate 限定自己真正接受的 shape，避免 schema echo 或 prompt 示例被误当作结果。

### Merged keyword prompt

```python
from openbiliclaw.llm.prompts import (
    build_merged_keywords_prompt,
    parse_merged_keywords_with_presence_and_explore_domains,
)

messages = build_merged_keywords_prompt(
    profile_summary=profile_summary,
    profile_blocks=profile_blocks,
    platform_blocks=[{"platform": "bilibili", "need": 8, "recent_keywords": []}],
    explore_domains_block={
        "need_domains": 5,
        "queries_per_domain": 3,
        "covered_topic_groups": ["人工智能", "认知科学"],
    },
)
keywords, present, explore_domains = parse_merged_keywords_with_presence_and_explore_domains(
    response.content,
    ["bilibili"],
    per_platform_cap=8,
)
```

`explore_domains_block` 是可选项；未传时 prompt 与解析仍按普通多平台关键词生成运行。传入时，模型可在平台 key 之外额外返回 `explore_domains`，每个 domain 包含 `domain / novelty_level / queries`。这些 queries 会被 runtime 写入 B 站 `discovery_keywords` query cache，因此 prompt 规则要求它们保持探索性、跨域和 B 站可直接搜索，而不是普通兴趣关键词的换皮。

### Inspiration axis-keyword prompt

`build_inspiration_axis_keyword_prompt()` 是 regular / shared inspiration stage 唯一的 LLM 调用（caller `discovery.keyword_inspiration`），一次返回 `{axes[], keywords[]}`。system prompt 是模块级静态常量 `_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT`，所有 per-call 数据（platform guides、已选兴趣、既有轴、fresh evidence、allocation targets）都在 user message 里按稳定→易变排序、`ensure_ascii=False, indent=2, sort_keys=True` 序列化。

Phase 2.1（多平台丰富度修复 F1）在该静态 Rules 里新增一条**产出具体性规则**：`core_concept` 必须锚定 `fresh_evidence` 里的具体实体 / 事件 / 作品 / 人物 / 机制（专名、作品名、具名争议、具体机制），**不得直接复述 interest 或 axis_label**；prompt 内置正反例（反：`新游推荐` 只是话题名 → 不合格；正：`士官长 登陆PS5` / `腾讯网易 新游发布`），并保留出口——某槽位 evidence 确实没有具体锚点时**允许**退回话题级、不硬造专名。该规则是纯静态文本（无 f-string、无 per-call 变量），因此仍满足 byte-identical prompt-cache 契约，`test_prompt_builder_system_messages_are_call_invariant` 覆盖 `build_inspiration_axis_keyword_prompt` 并逐字校验跨两次不同输入的 system message 相同。装配端还有确定性 `is_specific` 排序把"产出具体候选"真正落到"选中具体候选"（见 [discovery.md](./discovery.md) 的 `materialize_platform_keywords`）。

### Prompt layer render cache

```python
from openbiliclaw.llm.prompt_cache import PromptLayerRenderCache, profile_prompt_layers

cache = PromptLayerRenderCache()
blocks = cache.render_json_layers(profile_prompt_layers(profile_summary))
stats = cache.stats()
```

`profile_prompt_layers()` 只负责确定层次和顺序：core / life / interests / style / recent，未知扩展字段进入末尾 `profile_extra`。`PromptLayerRenderCache` 不缓存业务画像本身，只缓存当前层 digest 对应的 JSON prompt block。调用方仍每次从最新 profile 构造 layer payload；digest 不变时复用完全相同的字符串，digest 变化时只替换该层。

#### Runtime 全局补货优先 admission

`LLMConcurrencyGate.update_inventory(available=..., target=...)` 只消费 canonical durable snapshot，产生 `healthy / refill / empty` 三态。后台先取得 cancellation-safe `RefillAdmissionSemaphore`，再取得 total priority permit；退出时逆序释放，因此后台 holder 不会在等待 total 时占住交互保留槽。

| 流量类 | total priority | 说明 |
|---|---|---|
| interactive | 0 | `soul.dialogue*`、`api.sentiment`，仅经过 total gate |
| refill.expression | 1 | 推荐文案回填，补货最高优先 |
| refill.evaluation | 2 | 候选 batch / single 评估 |
| refill.supply | 3 | 仅在 durable inventory 低于目标时动态升级的关键词/原料生成 |
| maintenance | 4 | Soul、评测、purge 与健康库存下的 discovery；未知 caller 也落此类 |

当 refill waiter 存在时，新 maintenance 最多一个；没有 runnable refill 时 maintenance 可借用所有空闲后台槽，保持 work-conserving。`empty` 只 park 新 maintenance，绝不会取消或抢占已经进入 provider 的 maintenance。状态输出同时包含 refill/maintenance active、waiting、priority-active 与 inventory state。

#### 分模块路由(v0.3.75+)

`LLMService` 的 `module_overrides` 来自 `module_overrides_from_config(config)`。
路由不使用 caller 第一段朴素判断，而是内置 bucket：

| caller 前缀 | module bucket |
|---|---|
| `soul.*` | `soul` |
| `discovery.search/explore/trending/related.*`、`yt_search.*`、`sources.xhs.*` | `discovery` |
| `recommendation.evaluate_batch`、`discovery.evaluate*`、`eval.*` | `evaluation` |
| 其他 `recommendation.*` | `recommendation` |

命中 override 后走 `registry.complete_provider(provider, ..., model=model)`：

- override provider 错误 / rate-limit：直接报错，不自动 spill 到 default。
- override provider 未注册或不是 chat-capable：按 `(bucket, provider)` INFO 一次，然后走默认 provider 路径；是否跨 provider fallback 取决于 `[llm].fallback_provider` 是否非空。
- 只填 `model` 不填 `provider`：使用 `registry.default_provider` + per-call model。

### 异常体系

```
LLMProviderError          # 基类
├── LLMRateLimitError     # 429 / rate limit
├── LLMTimeoutError       # 请求超时
└── LLMResponseError      # 响应无效（空内容）

LLMFallbackError          # 所有 provider 都失败
RegistryBuildError        # 无法构建 registry（无可用 provider）

LLMServiceError           # Service 层基类
├── LLMResponseContentError  # Service 层空响应
└── LLMProviderExecutionError  # Provider 调用失败
```

`openbiliclaw.llm.base.describe_llm_failure(exc)` 返回面向用户的中文错因，未识别时返回 `None`。特异性顺序为 moderation → auth → quota/rate-limit → timeout / provider / empty response，避免 401 或配额耗尽被降级成泛化不可用。

- `describe_llm_failure(exc) -> str | None`：识别 moderation、鉴权、额度/限流、超时、provider 全部不可用、provider/service 空响应。
- `safe_llm_failure_message(exc) -> str`：公共边界使用；未知异常退化为固定安全提示，不回传上游异常文本。

## 配置项

```toml
[llm]
default_provider = "deepseek"  # "deepseek" | "openai" | "claude" | "gemini" | "ollama" | "openrouter" | "openai_compatible"

[llm.openai]
api_key = ""
model = "gpt-4o"
base_url = ""  # 留空使用默认，或设置兼容 API 地址
auth_mode = "" # "" / "api_key" / "codex_oauth"
api_flavor = "" # "" / "chat_completions" = /v1/chat/completions；"responses" = /v1/responses（第三方网关）

[llm.claude]
api_key = ""
model = "claude-sonnet-4-20250514"
base_url = ""  # 留空 = Anthropic 官方；第三方 Anthropic 协议网关填其地址

[llm.gemini]
api_key = ""  # 也支持通过 GOOGLE_API_KEY / GEMINI_API_KEY 注入
model = "gemini-2.5-flash"

[llm.deepseek]
api_key = ""
# 默认 deepseek-v4-flash;可选 deepseek-v4-pro;旧 deepseek-chat / deepseek-reasoner 将于 2026/07/24 弃用
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
# "" = 显式关闭 thinking; "high" / "max" = 开启 DeepSeek v4 thinking
reasoning_effort = "max"

[llm.ollama]
model = "llama3"
base_url = "http://localhost:11434/v1"

[llm.openrouter]
api_key = ""
model = "openai/gpt-4o-mini"
base_url = "https://openrouter.ai/api/v1"
http_referer = ""
x_title = "OpenBiliClaw"
```

## 托管 Ollama 生命周期（v0.3.162+）

`runtime/ollama_supervisor.py` 负责本进程"拥有"的 Ollama daemon 的完整生命周期。

**记录的 daemon 规格**：模块级 `_ManagedDaemon(proc, base_url, models_dir)` 取代了旧的裸
`_managed_proc` 句柄。`proc` 为我们 spawn 的 `Popen`（可发信号），或 `None` 表示"收养"——
仅限专用私有端口（`with-embedding` 的 `127.0.0.1:11435`）在启动时已有 daemon 应答的
force-quit 残留场景；收养只做记录、绝不发信号，但让 watchdog 能在它死后按记录的
`(host, models_dir)` 拉起新 daemon。任何 restart 都复用记录规格：私有 daemon 永远不会
回到 11434、也不会丢私有模型目录。`stop_managed_ollama` 清整条记录。

**端点判定**：`is_managed_endpoint(endpoint)` 做 host:port 归一化比较（`localhost` ≡
`127.0.0.1` ≡ `::1`，scheme / `/v1` path 不敏感）；`may_manage_ollama_endpoint(endpoint)` =
默认 loopback 11434 **或** 已记录的托管 daemon，是 `api/app.py` 两个修复 gate
（not_running / provider_error）唯一使用的谓词。`ensure_managed_ollama(endpoint)` 按记录
路由 not_running 修复的启动动作（私有 → `start_managed_ollama_at(记录目录, 记录端口)`，
否则默认路径）。

**Watchdog**：`start_ollama_watchdog(interval_seconds=30)` 幂等地启动单个 daemon 线程
（`obc-ollama-watchdog`），两条成功启动路径（默认 + 私有，含收养分支）都会自动布防。
每周期探测记录端点：健康即清零失败计数；探测失败且（自有进程已退出，或收养记录不再应答）
才经 spec-aware `restart_managed_ollama()` 重启——探测失败但自有进程仍存活时不动它
（绝不因单次探测失败杀活 daemon）。连续重启失败按 5s 起步、翻倍、300s 封顶退避，
连续 5 次失败后放弃（上报 phase `down` + ERROR 日志），直到 `reset_watchdog_backoff()`
（任何一次成功启动 / 手动修复成功都会调用）或进程重启。重启用 restart-in-progress 标志
与手动修复互斥。

**修复覆盖**（`POST /api/embedding/repair` 的 `may_manage` 判定，其余条件：
`manage_ollama=true` + `ollama_required` + loopback）：

| Endpoint | 记录状态 | not_running 动作 | provider_error 动作 |
| --- | --- | --- | --- |
| `localhost:11434`（默认） | 有/无记录 | 默认路径启动（同旧行为） | spec-aware restart |
| `127.0.0.1:11435`（with-embedding 私有） | 有记录（spawn 或收养） | `start_managed_ollama_at(记录目录, 记录端口)` | spec-aware restart（私有路径） |
| 自定义端口 / 远端 | 无记录 | 409（不越权，同旧行为） | 409 |
| 任意 | `manage_ollama=false` | 409 | 409 |

拒绝原因：`external_ollama`（记录外的 daemon 在应答）、`adopted_alive`（收养 daemon 仍
活着——不能停我们不拥有的进程）、`private_daemon`（`restart_managed_ollama_with_models_dir`
是默认 daemon 的路径迁移工具，对私有记录拒绝）、`restart_in_progress`。

**`OLLAMA_KEEP_ALIVE` 归属**：私有 daemon 完全由我们拥有，`OLLAMA_KEEP_ALIVE=24h` 与
`OLLAMA_HOST` / `OLLAMA_MODELS` 一律**硬设**（用户环境里的 `OLLAMA_KEEP_ALIVE=0` 不会
渗入导致 5 分钟卸载 + 冷启动 502 被误诊为 `model_broken`）；默认 daemon 路径保持
`setdefault`，尊重用户的全局设置。

## 设计决策

1. **retry 策略**：传输 / provider 临时错误走 3 次重试 + 线性退避（0.25s × attempt）；通用 OpenAI-compatible 的 `LLMResponseError` 默认不重试。DeepSeek 例外：线上观测到它会偶发 HTTP 200 但 `content=""`，因此 `DeepSeekProvider` 对空内容额外重试一次。`reasoning_effort=""` 会显式发送 `thinking={"type":"disabled"}`，避免 DeepSeek v4 省略字段时默认开启 thinking。HTTP 400 会记录 provider response body 摘要，避免只看到 `Error code: 400`
2. **fallback 顺序**：默认关闭。chat 只在 `[llm].fallback_provider` 非空时按默认 provider 优先、随后这个显式备选 provider 尝试；embedding 只在 `[llm.embedding].fallback_provider` 非空时按显式 provider 优先、随后这个备选 provider 尝试。Embedding provider 留空表示禁用，不再跟随默认 LLM。
   - **备选何时触发**：`LLMRegistry.complete()` 链上遇到 provider 级失败时——`LLMProviderError` / `LLMTimeoutError` / `LLMRateLimitError`（限流同时触发 60s cooldown），以及 v0.3.156+ 的 `LLMResponseError`（空 / 坏 content——劣质网关最常见的死法是 HTTP 200 但内容为空，provider 内部自重试一次后换备选再试；此前该类错误直接上抛、备选永远不接管）。单 provider 链耗尽后统一抛 `LLMFallbackError`（原始错误在 `__cause__`）。
   - **备选何时刻意不触发**：`complete_provider()` 精确路由（per-module override 与配置探测按用户指定 provider 调用，跨 provider 兜底会违背意图；注意 `[llm.<module>]` **只填 `model` 不填 `provider` 同样命中精确路由**——模型钉死意味着换 provider 也违背意图，该模块的调用同样不走备选）；备选与默认 provider 同名、未注册（缺凭据）或非 chat-capable 时被 `_fallback_order()` 静默丢弃——运行时静默丢弃是正确行为（不能每次补全都刷日志），死状态的可见性由两处兜底：`_collect_config_issues` 在保存 / 加载时以 blocking issue 拦截（见 [配置参考](config.md)），`build_llm_registry` 在构建时对「同名 / 未注册 / 非 chat-capable」按具体原因打一次 WARNING（v0.3.155+，覆盖 env 覆盖与手改 config.toml 绕过保存校验的场景）。
   - **开关语义（v0.3.156+）**：`[llm].fallback_provider` 非空即启用，留空即关闭——旧的 `[llm].fallback_enabled` 布尔字段从未被回退链读取，已彻底移除（config 加载忽略存量 key，PUT /api/config 忽略旧客户端仍发送的该字段，GET 不再回显）。embedding 侧的 `[llm.embedding].fallback_enabled` 仍然有效（借用 chat-side 凭据的旧兼容开关）。
   - **init 前置探测认备选（v0.3.156+）**：`InitPrereqs.chat_ready()` 先探默认 provider，失败且存在可用备选（已注册、chat-capable、非同名）时再探备选，任一通过即 ready——运行时所有 chat 调用都走回退链，主 provider 挂、备选健康时不应拦初始化（经备选通过时 INFO 一条说明）。
3. **Protocol DI**：`SupportsComplete` Protocol 解耦了调用方和具体实现，测试时可注入 Fake
4. **Prompt 集中管理**：所有 prompt 在 `prompts.py` 中定义，不散落在各模块
5. **统一上下文注入**：`complete_with_core_memory()` / `complete_structured_task()` 默认负责把核心记忆注入到 Soul 相关任务里；已在 `user_input` 自带完整结构化上下文的高频任务可传 `inject_core_memory=False`，或通过 `llm.task_options.without_core_memory_kwargs()` 在兼容旧 stub 的前提下关闭注入，避免动态 core memory 破坏 provider prompt-cache 前缀
6. **OpenAI-compatible 复用**：DeepSeek、OpenRouter 这类兼容 OpenAI 协议的 provider 复用同一套重试、超时和错误归一化逻辑，只在子类中注入默认地址或额外请求头
7. **Gemini 独立适配**：Gemini 走官方 `google-genai` SDK，不强行复用 OpenAI-compatible 抽象；provider 内部负责把统一 `messages` 渲染成 quickstart 风格的单文本 prompt
8. **Gemini 可选依赖降级**：环境里缺少 `google-genai` 时，`llm` 包和 registry 仍可正常导入；只有真正实例化 Gemini provider 时才会给出明确缺依赖错误。守卫捕获的是 `ImportError` 而非仅 `ModuleNotFoundError`（issue #80）——SDK 装上了但其原生传递依赖加载失败（如 Termux/Android 下 `cryptography` 的 manylinux 轮子 dlopen 失败）同样降级而不是让 CLI 启动即崩，实例化报错会附带底层 import 失败详情
9. **Prompt 风格集中收口**：推荐、画像和聊天的“老B友”语气由共享 `ToneProfile` 驱动，不允许各模块各自发散成不同人格
10. **Prompt-cache 约定**：高频结构化 builder 的 system prompt 必须保持静态；user prompt 按“tone / 画像 / 长期偏好 / 来源上下文 / 本批内容或历史”从稳定到易变排序，并使用确定性 JSON。使用完整 `profile_summary` 的高频链路优先经 `profile_prompt_layers()` 分层渲染，稳定层放前、recent 层放后；调用方不得再把同一份动态画像通过 core memory 追加进 system prompt，便于 DeepSeek / Claude / OpenAI / Gemini 的 provider-side prompt cache 复用前缀
11. **结构化输出只在 helper 处放宽**：业务模块不再各自手写 JSON 截取逻辑；容错集中在 `json_utils.py`，模块侧用 predicate 收紧语义，避免一个 provider 的异常 shape 修复污染其他任务。
12. **分模块 override 不隐式改意图**：`[llm.<module>]` 命中时必须精确调用用户指定的 chat provider；只有 provider 拼错或不是 chat-capable 时才降级到默认链并 INFO 一次。模型覆盖通过 per-call `model=` 完成，避免污染 provider 实例状态或影响其他模块。
13. **Codex OAuth 只做认证层**：`auth_mode="codex_oauth"` 不注册新 provider，而是给现有 `OpenAIProvider` 注入动态 token provider。该模式只允许 OpenAI 官方 `base_url`，防止 ChatGPT OAuth token 泄露给 OpenAI-compatible 代理。
14. **失败分类先于批响应解析**：共享 classifier 保持 rate-limit / no-provider / auth / invalid-response 的特定语义优先级，并额外识别连接失败与 HTTP 500/502/503/504；调用方只把 provider transient 交给协调器退避，不把 JSON shape 错误误判成网络失败。
