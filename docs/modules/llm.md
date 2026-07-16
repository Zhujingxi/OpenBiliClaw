# LLM 多模型支持

> 运行时并发由单一 `LLMConcurrencyGate` 管理：所有 provider 请求受总 gate（默认 4）约束，后台还受 `max(1, total-1)`（默认 3）约束。后台 admission 依据 canonical durable inventory 把工作分为 `refill.expression > refill.evaluation > refill.supply > maintenance`；有 refill waiter 时保证下一批新准入至少两个 refill 槽并可借满三个，库存为零时 park 新 maintenance。对话与 `api.sentiment` 是交互流量；未知 caller 只告警一次并按 maintenance 处理。旧 `bypass_semaphore=True` 只绕过后台 gate，`PrioritySemaphore` 仍从 `llm.service` 兼容导出。

热重载不会替换 gate 对象，而是原地 `reconfigure()`：升容立即按优先级唤醒等待者；降容不撤销已进入 provider 的工作，并在 active 降到新容量以下前停止新准入。配置探测也使用 `api.config_probe` 后台分类经过同一 gate。

> 统一的多 LLM Provider 接口，支持 OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OpenAI-compatible，并提供按稳定 connection ID 执行的全局有序 Chat / Embedding route、Provider retry、revision-aware circuit 与安全探测。

## 概述

`llm/` 包提供了一套抽象的 LLM 调用接口。`LLMService` 的普通、结构化、多模态和工具调用共享同一条全局路由；caller tag 只用于并发准入与 usage 归属，不再选择模块专属 Provider / model。

核心设计：
- **Provider 抽象** — `LLMProvider` ABC 定义统一接口
- **有序路由** — `OrderedLLMRoute` / `OrderedEmbeddingRoute` 按配置数组顺序尝试 connection，并以稳定 ID 隔离同类型连接
- **Service 门面** — `LLMService` 封装 prompt 组装 + 调用 + 校验
- **统一异常与熔断** — Provider 错误归一化后进入安全 aggregate attempt 与 revision-aware circuit

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 2.1 协议 Adapter | ✅ | OpenAI-compatible（含 OpenAI / DeepSeek / OpenRouter presets）、Anthropic-compatible、Gemini、Ollama 与 DashScope，带 retry + 超时 |
| 2.2 原生 route factory | ✅ | descriptor/capability 驱动的 adapter 构造 + 最多 10 项有序 fallback + exact health probe |
| 2.3 Prompt 管理与 Service | ✅ | Prompt 构建器 + LLMService 门面 |
| 模型连接 protocol factory（阶段 4） | ✅ | `build_chat_adapter()` 从单条 `ChatConnection` 构造按稳定 ID 命名的 Chat adapter；`build_embedding_adapter()` 从 `EmbeddingProviderConfig` + 同一个不可变共享 `EmbeddingModelSettings` 构造 embedding adapter。Embedding adapter 还暴露 connection type/preset，并按具体 model 判定图像 embedding 能力；本地能力 checker 异常原样传播，不伪装成不支持 |
| 全局有序 Chat route（阶段 5） | ✅ | `OrderedLLMRoute` 精确保持 1–10 条配置数组顺序，允许多个同类型 connection；Provider 内 transport retry 完成后才尝试下一项，整条 route 共用一个总 deadline。rate-limit、永久配置错误与 transient 使用不同 circuit；普通成功只清 timed/transient，exact probe 可绕过并在同 ID+revision 成功时关闭永久态；aggregate attempt 和响应 metadata 均为 secret-safe |
| 共享设置有序 Embedding route（阶段 6） | ✅ | `OrderedEmbeddingRoute` 精确保持 Provider 数组顺序，并要求所有 adapter 持有同一个不可变 `EmbeddingModelSettings` 对象；空值、非数值和非有限向量按本次调用 fallback，固定维度不匹配及原生维度多模态探测长度不一致打开 provider+revision 永久 circuit。普通成功不清永久态，精确探测只调用目标 ID，并在多模态开启时使用仓库固定 PNG；只有类型化 Provider/明确 transport 失败参与 fallback，能力 property/checker 异常传播，共享设置生成与 Provider ID/顺序无关的 cache namespace |
| v0.3.164+ OpenAI-compatible JSON-object 合约 | ✅ | `LLMService.complete_structured_task()` 与 `complete_multimodal_structured_task()` 共享最小兼容层：已有大写 `JSON` 仅归一为小写 `json`；完全没有该 token 时只追加 `json`。这满足部分 OpenAI-compatible 端点对 `response_format=json_object` 的字面消息约束，不改变业务规则、画像、阈值、user 内容或 core-memory 排序；非结构化 `complete_with_core_memory()` 完全不改写 prompt。 |
| v0.3.162+ LLM 失败可操作说明 | ✅ | `llm.base.describe_llm_failure()` 沿异常 cause/context 链翻译上层错误；新增 authentication / unauthorized / invalid API key / 401 鉴权桶，并将 insufficient quota / quota / exhausted / 429 归入「额度用尽或被限流」桶，API 与 CLI 继续消费同一函数，不新增 init reason code |
| v0.3.164 LLM 失败安全边界 | ✅ | `describe_llm_failure()` 识别 moderation、鉴权、额度/限流、provider/service 超时与空响应；`safe_llm_failure_message()` 为 API / CLI / OpenClaw 的公共边界提供固定安全兜底，未知异常不回传上游文本 |
| v0.3.160+ Discovery 统一评估契约 | ✅ | 单条与 batch 内容评估 prompt 仅允许 `explore` 保留主题距离例外；`search` / `trending` / `hot` / `feed` / `related_chain` / `channel` / `creator` 及所有平台不得获得基础分、自动加分、较低门槛或事后画像关联，明显不匹配内容允许低于 admission 门槛 |
| 4.5 核心记忆加载 | ✅ | 统一 core memory 注入入口，覆盖 Soul 全链路 |
| v0.3.149+ 关键词合并 prompt 探索 block | ✅ | `build_merged_keywords_prompt()` 支持可选 `explore_domains_block`，只在 runtime 判断 B 站 explore refresh 到期 / 即将到期且有补货空间时追加；system prompt 明确这些 query 是探索性 B 站搜索方向，不应把常规兴趣关键词换皮成 explore。`parse_merged_keywords_with_presence_and_explore_domains()` 在保留平台关键词 decline / omission 语义的同时清洗 `explore_domains` |
| v0.3.147+ Prompt layer cache | ✅ | `profile_prompt_layers()` 把结构化画像拆为 `profile_core` / `profile_life_context` / `profile_interests` / `profile_style_context` / `profile_recent_context`，从稳定到易变排序；`PromptLayerRenderCache` 按层 digest 复用已渲染 JSON prompt block，供 discovery eval、推荐分类 / 文案 / delight 和统一关键词 planner 共享，画像核心不变时 provider 看到的前缀保持 byte-stable |
| v0.3.144+ 缓存前缀保护 | ✅ | `LLMService.complete_with_core_memory()` / `complete_structured_task()` / `complete_multimodal_structured_task()` 支持 `inject_core_memory=False`，供候选 eval、推荐分类 / delight、跨平台关键词生成、awareness / insight / speculation / profile build、初始化偏好分析这类已自带完整结构化上下文的路径跳过重复 memory 注入；`build_soul_profile_prompt()` 也保持静态 system，并把 tone / preference / awareness / insight 放在巨大 history 前，稳定 provider prompt-cache 前缀 |
| v0.3.150+ DeepSeek thinking 显式关闭 | ✅ | `OpenAIProtocolProvider` 的 DeepSeek preset 在 `reasoning_effort=""` 时写入 `thinking={"type":"disabled"}`。DeepSeek v4 默认开启 thinking，单纯省略字段并不会关闭 reasoning；配置页探测和短结构化任务因此能避免 thinking 先耗尽输出预算后返回空 `content` |
| v0.3.150+ reasoning-only 诊断 | ✅ | OpenAI-compatible / DeepSeek / OpenRouter / Ollama native 返回 HTTP 200 且含 `reasoning_content` / `reasoning` / `thinking`、但最终 `content` 为空时，仍判为不可用，但错误会明确提示 `returned reasoning but no final content` 并带 `finish_reason`，避免和完全空响应混淆 |
| v0.3.117+ reasoning-first 探活 | ✅ | `LLMProvider.health_check()` 与配置页 LLM 测试探针统一使用 `max_tokens=4096`，避免 SenseNova 等 OpenAI-compatible reasoning-first 模型先产出 `message.reasoning`、尚未到 `message.content` 就被截断，从而误报空响应 |
| 全局 route 取代模块选择 | ✅ | `LLMService` 所有调用路径只委托同一个 `complete()`；`ModuleOverride`、`module_overrides_from_config()` 与相关构造参数已删除。caller 只参与并发准入和 usage 归属，不会选择 connection 或 model |
| v0.3.75 Provider per-call model | ✅ | OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OpenAI-compatible 的 `complete(..., model=...)` 支持单次模型覆盖，不修改 provider 实例默认 `_model` |
| 体验优化：B站动态语气 | ✅ | 推荐、画像总结和聊天 prompt 统一接入 `ToneProfile`，在“老B友”基础上按用户画像微调语气 |
| v0.3.0 Ollama embedding 兜底 | ✅ | `OllamaProvider.embed()` 走原生 `/api/embeddings`，配合 `bge-m3` 模型可在 Mac/Win/Linux CPU 跑相似度计算，不需要额外的 embedding API Key |
| v0.3.0 EmbeddingService 双层缓存 | ✅ | L1 内存 + L2 SQLite 持久化；生产 bundle 从原生 route 的共享 settings 派生 model、维度、阈值、多模态开关与 cache namespace，拒绝缓存空、非数值、非有限或维度不符的向量；只对已识别 Provider/transport 失败降级，取消、调用方、能力 checker 和未知编程错误传播。 |
| 可选封面 image-only embedding | ✅ | `[models.embedding.settings].multimodal_enabled` + 支持图像的共享 embedding 模型启用封面向量；每个 ordered Provider 都受同一共享模型空间约束。 |
| DashScope 多模态 embedding | ✅ | DashScope Embedding 使用 `[[models.embedding.providers]]` 记录并设 `type = "dashscope_api"`；全 route 的 `model` 与 `output_dimensionality` 统一放在共享 `[models.embedding.settings]`，Provider 记录不携带 model/settings 覆盖。adapter 调用原生 multimodal-embedding API，`embed` / `embed_image` 生成独立向量（不启用 `enable_fusion`）；共享 model 为 `qwen3-vl-embedding` 时按共享维度传递 `dimension`；该类型只支持 Embedding。 |
| v0.3.113 Embedding 目标维度 | ✅ | `[models.embedding.settings].output_dimensionality` 属于全 route 共享空间；adapter 仅在协议/模型明确支持时传维度参数，返回维度不符会打开该 Provider + revision 的 `config_error` circuit。 |
| v0.3.155 Ollama embedding 诊断 + 自修 | ✅ | `llm/ollama_diagnostics.py`：`diagnose_ollama_embedding()` 把向量模型不可用分类为 `not_running` / `model_missing` / `model_broken` / `model_path_encoding` / `disk_full` / `network` / `model_oom` / `error`（先 `/api/tags` 判定服务与模型在位，再真打一次 embed——覆盖"模型在列表里但加载失败"的 500 场景）。`model_path_encoding` 专指 Windows 非 ASCII 用户名 / mojibake 路径导致 `llama-server` 无法从 `.ollama\models` 加载模型的失败，重新拉取不会修复，需迁移模型目录或手动设置 `OLLAMA_MODELS` 到纯英文路径；`model_oom` 从旧 `model_broken` 中拆出，明确内存不足时重拉无效；`disk_full` 既识别 pull / probe 错误文本，也会在拉取前检查 `OLLAMA_MODELS` / 托管模型目录所在卷是否至少有约 2.0GB 空间；`network` 区分无法访问 registry 的下载源问题与本地模型损坏。`pull_ollama_model()` 经原生 `/api/pull` 流式拉取 / 重拉模型并回调进度；两者均 `trust_env=False` 且可注入 `httpx.MockTransport` 测试。`OllamaProvider.embed()` 失败日志附带响应体错误片段（此前只有裸状态码）。供 `/api/init-status` 的 `embedding_check`/`embedding_detail` 与 `POST /api/embedding/repair` 一键修复使用（见 [init 模块](init.md)） |
| v0.3.97 EmbeddingService 实时探活 | ✅ | `EmbeddingService.probe()` 绕过 L1/L2 缓存直接打一次 provider，返回是否拿到非空向量；供 `/api/health.embedding_ready` 做**实时**就绪判定（缓存命中的旧成功不会掩盖 provider 已掉线 / 模型没拉）。`/api/health` 侧自带 TTL + single-flight，probe 不缓存结果、每次都真打 |
| 阶段 9 权威模型草稿探测 | ✅ | `POST /api/model-config/probe` 对一条 revision-bound Chat draft，或一条 Embedding provider + 完整共享 settings 做无写入真实探测；只调用目标稳定 ID，不走 fallback/cache。gate admission 后检查 init，取得短 model path lock 后再次检查 init，再从请求 revision 捕获 `keep` 凭据；因此等待慢保存期间启动的 init 会在 credential/network 前安全返回。网络调用不持配置锁；完成后再次校验 revision/record/settings，过期结果返回最新 snapshot 且不进入 history、不关闭 live circuit。旧 `/api/config/probe-service` 只保留 `kind="network_proxy"` |
| Embedding capability descriptor | ✅ | connection type registry 按 preset/model 声明 embedding 与多模态能力；factory 构造时再次校验，Provider 不再借用 Chat bucket。 |
| OpenAI-protocol presets | ✅ | OpenAI、DeepSeek、OpenRouter 与 custom endpoint 都是 `openai_compatible` 记录，由统一 `OpenAIProtocolProvider` 承载，稳定 ID 保持实例、usage 与 circuit 隔离。 |
| 原生有序 fallback | ✅ | Chat 与 Embedding 分别按 `models.chat.connections` / `models.embedding.providers` 原序尝试；无需默认/备选槽或布尔开关。 |
| v0.3.69 Gemini reasoning-first 模型适配 | ✅ | `GeminiProvider._is_reasoning_first_model` 用 prefix 识别 `gemini-3.x` / `gemini-2.5-pro*`，json_mode 下不再附加 `thinking_budget=0`（这些模型会以 `400 INVALID_ARGUMENT` 拒绝）；`gemini-2.5-flash` 等非 reasoning-first 模型继续走省钱通路。pricing 补全 `gemini-3.1-pro-preview` / `gemini-3-pro-preview` 别名，配套 CLI / config / 文档统一改用真实模型 ID |
| v0.3.71 Prompt-cache 与 400 诊断 | ✅ | `build_awareness_prompt` / `build_batch_content_evaluation_prompt` / `build_soul_profile_prompt` 的 user prompt 按稳定画像 / tone / preference 在前、本次批次或历史在后排序，并使用 `sort_keys=True` 的确定性 JSON；`OpenAIProvider._map_error()` 会把 OpenAI-compatible HTTP 400 响应体摘要写入 WARNING 和错误文本，便于定位 MiMo 等兼容服务的请求 schema 问题 |
| v0.3.71 Awareness 缓存形态回归锁 | ✅ | `build_awareness_prompt` 的 system 内容固定为模块级常量 `_AWARENESS_SYSTEM_PROMPT`，user 块顺序锁定为 `<soul_profile>` → `<preference_summary>` → `<recent_events>`，并通过 `tests/test_llm_prompts.py` 的 byte-equal / 末尾块 / 不同字典 key 序仍产相同字节三组回归测试保证未来改动不会再把变量数据放进 system、不把 recent_events 之后塞入稳定块、或丢掉 `sort_keys=True` |
| v0.3.74 结构化输出共享解析 | ✅ | 新增 `llm/json_utils.py`，统一提供 `extract_llm_json_list()` / `extract_llm_json_object()` / `parse_llm_json_tolerant()`。调用方可传 item/object predicate 和 wrapper aliases，兼容 root array/object、`results/items/data/output/scores/evaluations` 等 wrapper、singleton dict、Markdown fenced JSON、JSONL、多 root echo 后最终结果，以及 MiMo 形态的 malformed `{ [ ... ] }` 数组包裹 |
| v0.3.74 Ollama 本地凭据语义 | ✅ | 原生 `type="ollama"` descriptor 不声明 credential；factory 使用记录中的 endpoint 或本地默认地址，并固定绕过海外代理策略。 |
| v0.3.77 LM Studio JSON mode 兼容 | ✅ | `OpenAIProvider` 的 `json_mode=True` 对普通 OpenAI-compatible 后端默认使用 `json_object`，遇到 `response_format.type` 只允许 `json_schema/text` 时用通用 `json_schema` 重试；对本地 LM Studio（默认 `localhost/127.0.0.1:1234` 或 URL 含 `lmstudio` / `lm-studio`）首次请求即不发送 `response_format`，依赖 prompt 约束 JSON，避免 compat 层在 `json_object` / `json_schema` 下丢失 `message.content` 后再浪费一整次 LLM 调用 |
| v0.3.78 Codex OAuth 实验认证 | ✅ | `type="codex_oauth"` 是独立 Chat connection type；`codex_auth.py` 导入并刷新本机 Codex CLI 凭据，factory 在 token lookup 前限制为 OpenAI 官方 endpoint。 |
| v0.3.x LLM 限流识别 | ✅ | `is_llm_rate_limit_error()` 会沿异常链识别 `LLMRateLimitError`、cooldown、429 / quota / resource exhausted 文本；discovery / recommendation 批量调用据此跳过逐条 fallback，避免一次 provider 限流放大成 N 个必失败调用和堆栈日志 |
| v0.3.x 余额 / 账单错误熔断 | ✅ | OpenAI-compatible provider 会把 HTTP 402、`Insufficient Balance`、`payment required`、`billing`、余额不足等 provider 余额 / 账单失败归一为 `LLMRateLimitError`，跳过 provider 内部 retry，并让 registry cooldown 与批量任务的“跳过逐条 fallback”保护生效 |
| v0.3.x Eval-batch 负样本锚定与跨平台公平 | ✅ | `build_batch_content_evaluation_prompt` 新增可选 `negative_examples` kwarg；非空时在 user prompt `<source_context>` 与 `<content_batch>` 之间插入 `<negative_examples>` 块（`sort_keys=True` 决定性 JSON）。`None` / `[]` 退回原 user 字节形态以保留 cold-start 缓存前缀。`_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT` 加入永久规则：按话术 / 商业意图 / 标题结构层面 pattern-match 候选与示例，不要看关键词重叠；混源 batch 中不得仅因 `source_platform` 不同而抬高或压低 preference score，只能把平台作为内容语境。规则改动一次后 system message 保持 call-invariant |
| v0.3.x dislike-aware prompts | ✅ | `build_preference_analysis_prompt` 明确把 negative / dislike / thumbs_down 事件限制为 `disliked_topics` 与风格避让证据，禁止提取为正向兴趣；`build_awareness_prompt` 可从近期 dislike 生成“最近开始避开 X”的保守观察；单条 / 批量推荐表达 prompt 会消费 `profile_summary.disliked_topics`，命中避雷项时不得热情背书 |
| v0.3.x 避雷探针多样性 prompt | ✅ | `build_avoidance_generation_prompt` 会携带 `existing_avoidance_details`，让 LLM 看到已有 active 的 `source_mode`、`source_signal`、体验轴和 specifics；system prompt 要求同一 `source_mode` + 同一粗主题 / 证据源只生成一个候选，已有 AI positive_boundary 时不再输出 AI 教程 / 测评 / 趋势换皮项 |
| v0.3.x 第三方 API 网关适配（issue #72） | ✅ | `anthropic_compatible` 接受 Messages API endpoint；`openai_compatible` 的 `api_mode` 选择 Chat Completions 或 Responses。descriptor、validator 与 factory 共用同一字段语义。 |
| v0.3.162+ 托管 Ollama 生命周期自愈 | ✅ | `runtime/ollama_supervisor.py` 记录托管 daemon 的完整启动规格并新增 watchdog；`with-embedding` 私有 11435 daemon 纳入一键修复与崩溃自动拉起（详见下方[托管 Ollama 生命周期](#托管-ollama-生命周期v03162)） |
| v0.3.165 海外网络三模式 | ✅ | connection factory 按最终 endpoint 读取 `[network].mode` 并向 OpenAI/Anthropic/Gemini adapter 注入 transport；Ollama 与国内 endpoint 固定直连。 |
| v0.3.166 国内网关代理豁免 | ✅ | registry 的 `_outbound_proxy(base_url)` / `_outbound_trust_env(base_url)` 改为按 endpoint 粒度裁决，委托 `network.is_domestic_endpoint()`。国内大模型网关（DeepSeek `api.deepseek.com`、商汤 `.cn`、通义 `aliyuncs.com`、智谱 / 文心 / 混元 / 火山 / Kimi / MiniMax / 阶跃 / 百川 / 硅基流动 / 无问芯穹 / PPIO 等）与 localhost / 内网自建端点，即使 `[network].mode` 为 `system` / `custom` 也强制直连（`proxy=""`、`trust_env=False`），避免把国内请求绕道境外梯子导致超时；识别覆盖 `.cn` 顶级域 + 非 `.cn` 厂商域名白名单 + loopback / 私有 / link-local IP。豁免按 endpoint 生效，墙外网关仍走全局代理策略。DeepSeek 子类以固定 `https://api.deepseek.com` 参与裁决 |
| Issue #113 CA 环境防护 | ✅ | `network.set_outbound_proxy(..., mode="system")` 在任何继承环境的 SDK 客户端构造前检查 `SSL_CERT_FILE` / `SSL_CERT_DIR` / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`。只移除指向不存在目标的失效覆盖，让 httpx / OpenSSL 回退到默认可信 CA store；有效私有 CA、`HTTPS_PROXY` 等代理变量和 TLS 验证均保持不变，避免 Windows 遗留 CA 路径导致所有客户端在发请求前直接 `FileNotFoundError`。 |

## 公开 API

### Connection-record adapter factory

```python
from openbiliclaw.llm.connection_factory import (
    AdapterRuntimeOptions,
    build_chat_adapter,
    build_embedding_adapter,
)
from openbiliclaw.model_config import (
    ChatConnection,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
)

runtime_options = AdapterRuntimeOptions(timeout_seconds=300.0)
chat = build_chat_adapter(
    ChatConnection(
        id="chat-primary",
        name="My OpenAI connection",
        type="openai_compatible",
        preset="openai",
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        credential=CredentialConfig(source="env", value="OPENAI_API_KEY"),
    ),
    runtime_options,
)

settings = EmbeddingModelSettings(
    model="text-embedding-3-small",
    output_dimensionality=1024,
)
embedding = build_embedding_adapter(
    EmbeddingProviderConfig(
        id="embedding-primary",
        name="OpenAI embedding",
        type="openai_compatible",
        preset="openai",
        base_url="https://api.openai.com/v1",
        credential=CredentialConfig(source="env", value="OPENAI_API_KEY"),
    ),
    settings,
    runtime_options,
)
assert chat.name == "chat-primary"
assert embedding.name == "embedding-primary"
assert embedding.settings is settings
```

`AdapterRuntimeOptions` 是 frozen、secret-safe 的构造参数，仅包含 timeout、可选的精确环境映射和可选 Codex token loader；传入的 environment 会在构造时复制成只读快照，后续修改调用方 dict 不会改变 credential resolution，映射和值也不进入 repr。proxy / `trust_env` 不对调用方开放，Factory 始终按最终 endpoint 调用 `openbiliclaw.network.proxy_for_endpoint()` / `trust_env_for_endpoint()`，Ollama 则固定直连。Credential 在构造期一次解析：`inline` 使用存储值，`env` 只读取记录指定的变量名，`oauth` 只接受 `credential_ref=codex`，`none` 只接受 descriptor 没有 credential 字段的本地类型。Codex OAuth 在调用 token loader 前要求 endpoint 精确等于 `https://api.openai.com/v1`（空值会规范成该地址）；显式端口、额外 path、query、fragment 或 userinfo 均失败关闭。OpenAI / Anthropic custom endpoint 先经过同一个 HTTP(S) validator：拒绝无 host、userinfo、query/fragment（含空 delimiter）、控制字符、反斜线、外层空白和非法 host/port，再调用 network policy 或 SDK；合法 custom path/port 原样保留。

OpenAI / DeepSeek / OpenRouter / custom Chat 记录都精确构造 `OpenAIProtocolProvider`；其 `OpenAIProtocolOptions` 为 frozen dataclass，headers 映射也做只读冻结，preset、reasoning effort 与 attribution headers 不进入 repr，因此不同连接或并发调用之间不会串用或意外展示这些 hook。Protocol adapter 只在 SDK catch 边界把原始 400 解析成私有布尔信号，在不保留原文的前提下继续支持 Responses temperature 降级和 Chat / Responses JSON format 兼容降级；无关 400 不重试、不触发降级，原生 `openai.APITimeoutError` 与 built-in/httpx timeout 一样映射为可重试 `LLMTimeoutError`。Anthropic 官方 / custom 都精确构造 `AnthropicCompatibleProvider` 并透传已校验的 custom base URL；timeout 与 5xx/connection 属于可重试 transient，429、401/403 和其他 4xx 直接返回固定 ID 文本，终态异常不保留可能含密钥的上游 cause/context，usage total 会计入 cache read/create token。Gemini 与新 OpenAI protocol adapter 的未知 SDK/transport 异常同样只产生固定 ID 文本并切断 secret-bearing chain。Gemini 仍使用原生 `google-genai`，Ollama 保留 native `num_ctx` 路径。旧 Provider 类和 registry builder 仍作为兼容 API 保留，但 production runtime、CLI 与 OpenClaw 只使用 connection-record factory 和 ordered route。

### Ordered Chat route

```python
from openbiliclaw.llm.connection_factory import AdapterRuntimeOptions, build_chat_adapter
from openbiliclaw.llm.route import OrderedLLMRoute, RouteConnection
from openbiliclaw.model_config import compute_model_revision

runtime_options = AdapterRuntimeOptions(timeout_seconds=models.chat.timeout_seconds)
connections = tuple(
    RouteConnection(
        connection=record,
        adapter=build_chat_adapter(record, runtime_options),
    )
    for record in models.chat.connections
)
route = OrderedLLMRoute(
    connections,
    revision=compute_model_revision(models),
    timeout_seconds=models.chat.timeout_seconds,
)

response = await route.complete([{"role": "user", "content": "hello"}])
print(response.connection_id, response.connection_type, response.preset)

# 精确探测稳定 ID；即使 circuit 已开也可显式绕过，成功会关闭该 circuit。
probe = await route.complete_connection(
    "chat-primary",
    [{"role": "user", "content": "reply OK"}],
    ignore_circuit=True,
)
```

数组位置是唯一执行顺序：index 0 为 primary，其后依次为 fallback；相同 `type` / `preset` 的记录仍是由 ID 隔离的独立 peer。Provider 自身 transport retry 完成后才进入下一条 connection。`timeout_seconds` 是整条 route 的总 deadline，每次 attempt 只获得剩余时间，耗尽后不启动新 fallback；调用方取消、请求 schema 错误和编程错误立即传播。

| failure kind | circuit 行为 |
|---|---|
| rate limit / quota | 使用安全解析后的 `Retry-After`，缺失或非法时 60 秒 |
| auth failed / model not found | 保持 open，直到 config revision 改变或 exact probe 成功 |
| timeout / connection / server error | 15、30、60、120、240 秒，随后固定 300 秒；同 revision 的普通成功清零 |
| invalid response / moderation | 本次 fallback，不打开跨请求 circuit |

`CircuitTable` 以 `(connection_id, config_revision)` 精确二元组隔离状态：新旧 runtime 即使共享表，也不会通过查询、失败或成功互相删除状态。普通请求成功只清除同 revision 的 timed/transient circuit；即使它在并发配置失败前已经发出，也不能清除后来打开的永久 circuit。只有针对同一 ID + revision 的 exact probe 成功拥有关闭永久 circuit 的权限。失败的 exact probe 采用单调合并，永久 circuit 不被削弱，仍在 open 的 timed circuit 只会保留或延长 `retry_at`，并保留实际生效状态对应的 failure kind/count。

路由耗尽时抛 `LLMRouteExhaustedError`。其 `attempts` 只包含 `connection_id`、`connection_type`、`preset`、`route_position`、`failure_kind` 与固定英文摘要，不保存原始异常、响应体、credential 或含 userinfo 的 URL。成功响应保留既有 `provider` / `model` 计价字段，同时写入四个 connection metadata 字段。

### Ordered Embedding route

```python
from openbiliclaw.llm.connection_factory import AdapterRuntimeOptions, build_embedding_adapter
from openbiliclaw.llm.embedding_route import OrderedEmbeddingRoute
from openbiliclaw.model_config import compute_model_revision

settings = models.embedding.settings
providers = tuple(
    build_embedding_adapter(record, settings, AdapterRuntimeOptions())
    for record in models.embedding.providers
)
route = OrderedEmbeddingRoute(
    providers,
    settings=settings,
    revision=compute_model_revision(models),
)

vector = await route.embed("repository-owned probe text")
probe = await route.probe_provider("embedding-primary")
print(probe.observed_dimension, probe.image_probe_performed)
```

`OrderedEmbeddingRoute` 要求每个 adapter 的 `settings is settings`，因此 Provider 只能改变 credential、endpoint 与 transport，不能覆盖 model、维度、相似度阈值或多模态开关。调用严格按数组位置执行；单个 Provider 内部 retry 完成后才尝试下一项。同类型 Provider 由稳定 ID 独立隔离。`EmbeddingService` 注入该 route 后忽略 legacy 构造参数中的 model/cache namespace/阈值/多模态值，统一从共享 settings 派生，避免调用方制造第二套向量空间。

OpenAI、Gemini（文本与图像）和 DashScope 的 Embedding transport 每次最多尝试 3 次，Ollama 最多 2 次。终态 timeout、connection、auth、rate-limit、5xx 与响应解析失败会抛出不携带密钥、URL userinfo、响应体或 raw cause/context 的类型化异常，交由 route 分类、熔断与 fallback。DashScope 的 HTTP 200 error envelope 仍是 Provider 失败，malformed JSON 是 invalid response；两者都不会伪装成成功空向量。只有结构有效的上游成功响应确实没有向量时才返回 `[]`，由 route 按 invalid response 处理。

成功向量必须是非空、非布尔的有限数值列表。空向量、非数值、`NaN`/`inf` 和错误 shape 只触发本次 fallback，不跨请求熔断。非零 `output_dimensionality` 与返回维度不一致时记录 `config_error` 永久 circuit；维度为 `0` 且开启多模态时，exact probe 还要求同一 Provider 返回的文本与固定图片向量长度一致，不一致同样证明共享空间配置不可用并打开永久 `config_error`。Circuit 继续以 `(provider_id, config_revision)` 隔离：普通成功只清除 timed/transient 状态，不能清除并发打开的永久状态；失败精确探测不会削弱已有状态，只有成功精确探测可关闭目标 Provider 的当前 revision。维度为 `0` 且探测维度一致时，报告 Provider 原生维度。

`probe_provider(id)` 绕过目标 circuit、只调用该 ID、不走 fallback、不读写 embedding cache，也不修改配置。多模态开启时，它还用仓库内固定 1×1 PNG 与固定 `image/png` MIME 探测图像向量；报告只能证明该 endpoint 在本次调用返回了可验证且模态长度一致的维度，不能证明不同 endpoint 的远端模型权重完全相同。建议在启用原生 route 前逐项探测所有 Provider，尤其是维度设为 `0` 时。

`EmbeddingModelSettings.cache_namespace()` 只散列共享 model、维度、阈值和多模态开关，因此兼容 endpoint 重排或更换稳定 ID 可复用缓存；任一共享设置变化都会切换 namespace。只有类型化 Provider 失败和明确识别的 transport 失败参与 fallback/circuit；取消、请求/调用方错误、同步图像能力 property/checker 错误及未知编程错误会原样传播。能力检查只有明确返回 `False` 时才表示不支持，不会调用后续 Provider、写 circuit/cache 或设置不可用原因。`EmbeddingService` 仅对已识别请求失败导致的 route 耗尽保持既有产品降级语义：返回 `[]`、不写缓存，并通过 `last_unavailable_reason` 暴露固定安全原因，不保留上游异常或用户文本。

### 统一协议 Adapter

生产代码不直接按厂商实例化子类。所有连接都先成为 `ChatConnection` 或 `EmbeddingProviderConfig`，再由 `build_chat_adapter()` / `build_embedding_adapter()` 选择协议实现：

- OpenAI、DeepSeek、OpenRouter 与自定义兼容网关统一构造 `OpenAIProtocolProvider`，差异封装在不可变 `OpenAIProtocolOptions` 中。
- Anthropic 官方与自定义 Messages 网关统一构造 `AnthropicCompatibleProvider`。
- Gemini、Ollama 与 DashScope 保留各自原生协议 adapter。
- adapter 的公开名称始终是稳定 connection ID；preset、额外 header、credential 与 endpoint 不进入 `repr`。

`DeepSeekProvider`、`OpenRouterProvider` 与旧 vendor registry 已删除。新增协议行为应扩展 descriptor、connection factory 或统一 adapter options，不应重新引入厂商 bucket / subclass 构造。
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

### 原生 route factories

```python
from openbiliclaw.llm.connection_factory import AdapterRuntimeOptions
from openbiliclaw.llm.registry import (
    build_ordered_chat_route,
    build_ordered_embedding_service,
)
from openbiliclaw.model_config import compute_model_revision

revision = compute_model_revision(models)
options = AdapterRuntimeOptions(
    timeout_seconds=models.chat.timeout_seconds,
    environment=environment,
)
chat_route = build_ordered_chat_route(
    models.chat,
    revision=revision,
    runtime_options=options,
)
embedding_service = build_ordered_embedding_service(
    models.embedding,
    revision=revision,
    runtime_options=options,
)
```

两个 factory 都只接收原生 route 值、revision 与 runtime options，不读取 `Config.llm`，也不复制 schema、credential 或 endpoint 解析。任一记录构造失败时抛 `RegistryBuildError` 且不返回部分 route；Embedding 关闭时返回 `None`。
### 权威模型草稿探测 API

```http
POST /api/model-config/probe
```

该接口面向模型列表/详情编辑器，不写配置文件。请求必须携带 `GET /api/model-config` 返回的 revision，并且只提交一个目标：

- `kind="chat"`：提交一条完整 `connection`；临时构造单项 `OrderedLLMRoute` 并发送最小 chat completion。
- `kind="embedding"`：提交一条完整 `provider` 与共享 `settings`；临时构造单项 `OrderedEmbeddingRoute`，绕过产品 L1/L2 cache，并在启用多模态时验证固定 PNG 与文本维度一致。

普通 API-key/env 类型的 `keep` 只允许当前 revision 已存在的同一稳定 ID。`codex_oauth + keep` 是唯一例外：保存服务把它解析为导入的 `oauth/codex` 引用，所以新建 OAuth 记录或从 API-key 类型切换时无需提交 token；反向切到非 OAuth 类型而保留该引用会以 `invalid_oauth_reference` 失败。服务先在 model path lock 内重读 revision 并解析该 revision 的凭据，再释放锁执行网络；网络完成后再次重读，只有 revision、持久化记录和 Embedding 共享 settings 仍完全一致，结果才会进入 GET probe summary，成功才可关闭同 ID/capability/revision 的 live circuit。若 gate 等待期间或网络调用期间配置变化，返回 `409 revision_conflict` 与最新脱敏 snapshot；旧 secret 不会借给新 revision，新结果也不会附着到旧/新错误身份。

旧 `POST /api/config/probe-service` 不再接受 `llm`、`llm_fallback` 或 `embedding`；它仅保留通用设置页的 `kind="network_proxy"` 出站代理探测。详见 [配置参考](config.md)。

### LLMService

```python
from openbiliclaw.llm import LLMService

service = LLMService(
    registry=route,
    memory=memory_manager,
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

#### 全局路由与 runtime bundle

`LLMService` 不再包含 caller-prefix / module bucket 选择逻辑。`soul.*`、`discovery.*`、`recommendation.*`、`eval.*` 等 caller 全部调用注入对象的同一个 `complete()`；caller 继续参与 concurrency priority 与 usage ledger，不改变 connection、model 或 fallback 顺序。

`build_runtime_model_bundle()` 从 `Config.models` 一次性构造 `OrderedLLMRoute`、有序 Embedding service、`UsageRecorder` 与 `LLMService`。API runtime、CLI、OpenClaw 和独立评测脚本都复用这条 composition；Soul、Discovery、Recommendation 与 Dialogue 共享同一 Chat route 和稳定 gate。旧 module override DTO/parser、vendor registry 和 provider bucket builder 已删除；legacy 仅存在于 `model_config` 的只读迁移 adapter 与 `/api/config` 的无凭据投影。

### 异常体系

```
LLMProviderError          # 基类
├── LLMRateLimitError     # 429 / rate limit
├── LLMTimeoutError       # 请求超时
└── LLMResponseError      # 响应无效（空内容）

LLMFallbackError          # 所有 provider 都失败
├── LLMRouteExhaustedError  # Chat route 耗尽；携带安全 RouteAttempt
└── EmbeddingRouteExhaustedError  # Embedding route 耗尽；携带安全 EmbeddingRouteAttempt
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
[models]
schema_version = 1

[models.chat]
concurrency = 4
timeout_seconds = 300

[[models.chat.connections]]
id = "deepseek-main"
name = "DeepSeek"
type = "openai_compatible"
preset = "deepseek"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
api_mode = "chat_completions"
reasoning_effort = "max"

[models.embedding]
enabled = false

[models.embedding.settings]
model = "bge-m3"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = false
```

Chat connection 与 Embedding provider 的完整字段、凭据来源、`config.local.toml`、revision 和 legacy 迁移规则见 [配置参考](config.md)。生产 LLM 模块只读取 `Config.models`；旧 `[llm]` 仅由 `model_config` migration adapter 在加载时转换为内存候选。
## 托管 Ollama 生命周期（v0.3.162+）

`runtime/ollama_supervisor.py` 负责本进程"拥有"的 Ollama daemon 的完整生命周期。

**记录的 daemon 规格**：模块级 `_ManagedDaemon(proc, base_url, models_dir)` 取代了旧的裸
`_managed_proc` 句柄。`proc` 为我们 spawn 的 `Popen`（可发信号），或 `None` 表示"收养"——
仅限专用私有端口（`with-embedding` 的 `127.0.0.1:11435`）在启动时已有 daemon 应答的
force-quit 残留场景；收养只做记录、绝不发信号，但让 watchdog 能在它死后按记录的
`(host, models_dir)` 拉起新 daemon。任何 restart 都复用记录规格：私有 daemon 永远不会
回到 11434、也不会丢私有模型目录。`stop_managed_ollama` 清整条记录。

**单 daemon 与端点判定**：supervisor 每个进程只记录一个 managed daemon。
`configured_ollama_endpoints(config)` 按 Chat connection 顺序、再按 Embedding provider 顺序列出
去重后的 daemon roots；`effective_ollama_endpoint(config)` 只选第一条，形成 general startup 的
显式 Chat-first 单目标策略。其它不同 endpoint 必须已由外部或专用 desktop owner 管理。
`is_managed_endpoint(endpoint)` 做 host:port 归一化比较（`localhost` ≡ `127.0.0.1` ≡ `::1`，
scheme / `/v1` path 不敏感）。没有记录时 `may_manage_ollama_endpoint()` 只允许默认 11434；
已有记录后只允许精确匹配该记录，不会用 11434 覆盖 11435 的 ownership 或反向覆盖。

Embedding repair 不使用 Chat-first `effective_ollama_endpoint()`：它把当前被诊断 provider 的
`base_url` 交给 `ollama_daemon_endpoint()` 得到精确 root，并让 not-running start、
provider-error restart gate 与 model-path migration gate 全部检查这个 root。
`ensure_managed_ollama(endpoint)` 再按匹配记录路由启动动作（私有 →
`start_managed_ollama_at(记录目录, 记录端口)`，无记录默认 root → 默认路径）。

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
| `localhost:11434`（默认） | 无记录或匹配的 11434 记录 | 默认路径启动（同旧行为） | spec-aware restart |
| `127.0.0.1:11435`（with-embedding 私有） | 有记录（spawn 或收养） | `start_managed_ollama_at(记录目录, 记录端口)` | spec-aware restart（私有路径） |
| 任一 loopback root | 已记录另一个 host:port | 409（单 daemon ownership 不匹配） | 409，不重启另一 endpoint |
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

1. **retry 策略**：每个协议 adapter 先完成自己的有界 transport retry；Embedding 中 OpenAI / Gemini / DashScope 最多 3 次，Ollama 最多 2 次，终态失败统一进入 secret-safe typed failure 边界。DeepSeek preset 对 HTTP 200 空 content 额外重试一次，并在 `reasoning_effort=""` 时显式关闭 thinking。HTTP 402 账单失败映射为无原文的 provider backoff。
2. **fallback 顺序**：生产 Chat / Embedding route 严格使用 `models.chat.connections` / `models.embedding.providers` 数组顺序并允许最多十条同类 connection；adapter retry 总在跨 connection fallback 之前完成。Embedding Provider 共用一个 settings 对象与 Provider-order-invariant cache namespace。
   - Chat 仅对已分类的 provider、transport、无效响应和 moderation 失败尝试下一项；取消、调用方/schema 错误和未知编程错误立即传播。
   - `complete_connection(id, ..., ignore_circuit=True)` 与 `probe_provider(id)` 是 exact path，只调用目标稳定 ID，不转向其它 connection。
   - guided init 按 route 原序做 exact health，第一条健康 connection 即通过；配置编辑器则通过 `/api/model-config/probe` 探测提交的单项草稿。
3. **Protocol DI**：`SupportsComplete` Protocol 解耦了调用方和具体实现，测试时可注入 Fake
4. **Prompt 集中管理**：所有 prompt 在 `prompts.py` 中定义，不散落在各模块
5. **统一上下文注入**：`complete_with_core_memory()` / `complete_structured_task()` 默认负责把核心记忆注入到 Soul 相关任务里；已在 `user_input` 自带完整结构化上下文的高频任务可传 `inject_core_memory=False`，或通过 `llm.task_options.without_core_memory_kwargs()` 在兼容旧 stub 的前提下关闭注入，避免动态 core memory 破坏 provider prompt-cache 前缀
6. **OpenAI-compatible 复用**：connection-record factory 对 OpenAI、DeepSeek、OpenRouter 和 custom 只构造 `OpenAIProtocolProvider`，以每实例 frozen `OpenAIProtocolOptions` 注入 API mode、reasoning body 与额外请求头；vendor 子类已删除，per-call override 不修改实例字段
7. **Gemini 独立适配**：Gemini 走官方 `google-genai` SDK，不强行复用 OpenAI-compatible 抽象；provider 内部负责把统一 `messages` 渲染成 quickstart 风格的单文本 prompt
8. **Gemini 可选依赖降级**：环境里缺少 `google-genai` 时，`llm` 包和 registry 仍可正常导入；只有真正实例化 Gemini provider 时才会给出明确缺依赖错误。守卫捕获的是 `ImportError` 而非仅 `ModuleNotFoundError`（issue #80）——SDK 装上了但其原生传递依赖加载失败（如 Termux/Android 下 `cryptography` 的 manylinux 轮子 dlopen 失败）同样降级而不是让 CLI 启动即崩，实例化报错会附带底层 import 失败详情
9. **Prompt 风格集中收口**：推荐、画像和聊天的“老B友”语气由共享 `ToneProfile` 驱动，不允许各模块各自发散成不同人格
10. **Prompt-cache 约定**：高频结构化 builder 的 system prompt 必须保持静态；user prompt 按“tone / 画像 / 长期偏好 / 来源上下文 / 本批内容或历史”从稳定到易变排序，并使用确定性 JSON。使用完整 `profile_summary` 的高频链路优先经 `profile_prompt_layers()` 分层渲染，稳定层放前、recent 层放后；调用方不得再把同一份动态画像通过 core memory 追加进 system prompt，便于 DeepSeek / Claude / OpenAI / Gemini 的 provider-side prompt cache 复用前缀
11. **结构化输出只在 helper 处放宽**：业务模块不再各自手写 JSON 截取逻辑；容错集中在 `json_utils.py`，模块侧用 predicate 收紧语义，避免一个 provider 的异常 shape 修复污染其他任务。
12. **caller 不选择模型连接**：所有 `LLMService` 路径共享同一全局 route；模块 caller 只用于并发和 usage。模块覆盖构造参数已删除。
13. **Codex OAuth 只做认证层**：legacy `auth_mode="codex_oauth"` 只由 migration adapter 映射成内存中的 `codex_oauth + credential_ref="codex"` 候选，不再拥有独立运行时注册路径。connection-record factory 只接受该原生组合，并在 token lookup 前验证精确官方 endpoint；token 不会发送给 OpenAI-compatible 代理。
14. **失败分类先于批响应解析**：共享 classifier 保持 rate-limit / no-provider / auth / invalid-response 的特定语义优先级，并额外识别连接失败与 HTTP 500/502/503/504；调用方只把 provider transient 交给协调器退避，不把 JSON shape 错误误判成网络失败。
