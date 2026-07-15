# 模型配置全栈重构设计

**日期：** 2026-07-15
**范围：** 配置模型、LLM / Embedding 运行时、配置 API、桌面 Web、移动 Web、浏览器扩展、CLI、安装与初始化流程
**状态：** 已确认设计，待实施

## 问题

当前 `[llm]` 同时承担 Provider 凭据仓库、默认路由、单一 fallback、Embedding、模块覆盖和运行参数。桌面与扩展设置页因此必须同时展示大量互不相关的字段，并通过 Provider 名称在一个扁平对象中读写配置。

主要问题如下：

1. Provider 名既表示协议，又表示服务商和认证方式。`openai`、`deepseek`、`openrouter` 与 `openai_compatible` 实际共享 OpenAI 协议，却占据不同存储槽；Codex OAuth 又藏在 `openai.auth_mode` 内。
2. 每种 Provider 只能配置一个实例，无法同时使用两个 OpenAI-compatible endpoint。
3. 主模型与备选模型使用不同表单和字段路径，只支持一个 fallback；优先级无法自然扩展。
4. Embedding fallback 可切到不同模型或维度，无法保证向量空间一致。
5. 画像、发现、推荐表达和评估覆盖把八个高级字段直接暴露给所有用户，并绕过全局 fallback 语义。
6. 桌面 Web 与扩展各自硬编码 Provider 列表、条件字段和序列化逻辑，新增连接类型会继续扩大漂移面。
7. `GET /api/config` 返回掩码 secret、`PUT /api/config` 再发送整份设置，容易造成 secret 语义含糊和陈旧草稿覆盖无关配置。

## 目标

- 用户先选择**连接类型**，界面只显示该类型和所选 preset 需要的字段。
- OpenAI、DeepSeek、OpenRouter 和自定义网关统一为 `openai_compatible`；Anthropic 官方服务和自定义网关统一为 `anthropic_compatible`。
- 每种 OAuth 认证是独立连接类型。首版只实现仓库已有的 Codex OAuth，不新增未经验证的 OAuth 流程。
- Chat 使用一个最多 10 项的有序连接列表；第 1 项是 primary，其余项按顺序 fallback。所有项使用完全相同的结构和编辑器。
- Embedding 允许多个有序 Provider，但整个列表共享唯一的 model、维度、相似度阈值与多模态设置。
- 删除画像、发现、推荐表达和评估模块覆盖；所有 LLM 任务使用同一全局有序路由。
- 桌面、移动、扩展、CLI、安装器与初始化流程使用同一类型注册表、验证器和模型配置服务。
- 旧配置可安全读取、明确迁移并备份，不在启动时自动改写。

## 非目标

- 不实现 Claude Code、Gemini CLI 或其他新的 OAuth 登录流程。
- 不做按任务、按模块、按 prompt 或按成本动态路由。
- 不自动选择“最便宜”或“最快”的连接。
- 不允许每个 Embedding Provider 使用不同 model alias；所有 Provider 收到同一个共享 model 字符串。
- 不声称能够从远端响应证明服务端实际加载了某个权重。系统强制共享配置并验证维度 / 能力，但远端 model 名与真实权重的对应关系仍由服务端保证。
- 不把连接类型做成可从不受信任目录动态加载并执行代码的插件系统；类型注册表由仓库代码定义。

## 核心概念

### Connection type

连接类型描述调用协议或认证机制，而不是厂商品牌：

| ID | 类别 | 用途 |
|---|---|---|
| `openai_compatible` | API protocol | OpenAI Chat Completions / Responses 兼容接口 |
| `anthropic_compatible` | API protocol | Anthropic Messages 兼容接口 |
| `gemini_api` | API protocol | Google Gemini 原生 SDK / API |
| `dashscope_api` | API protocol | 阿里百炼原生多模态 Embedding；仅出现在 Embedding 类型列表 |
| `ollama` | Local runtime | Ollama 本地原生能力与兼容接口 |
| `codex_oauth` | OAuth | 已导入的 Codex OAuth 凭据 |

连接类型由后端注册表定义 label、分组、能力、字段 schema、preset 和验证规则。前端从注册表渲染纵向、可搜索列表，不在 HTML 中写死横向 tab。

### Preset

Preset 是连接类型内的默认值与条件字段集合，不是新的 Provider 类型或存储槽：

- `openai_compatible`：`openai`、`deepseek`、`openrouter`、`custom`
- `anthropic_compatible`：`anthropic`、`custom`

Preset 可填入默认 endpoint、API mode 和厂商扩展字段。它只填充尚未被用户修改的值，绝不覆盖自定义 model 或 base URL。

### Ordered route

Chat route 是 1–10 个连接记录的有序数组：

- `connections[0]`：primary
- `connections[1]`：fallback 1
- `connections[n]`：fallback n

不存储 `primary`、`fallback`、`priority` 或 `fallback_enabled` 字段。角色只由数组位置派生，避免标签、数字与真实顺序漂移。

## 持久化模型

新配置使用独立的 `[models]` 根段并带 schema version：

```toml
[models]
schema_version = 1

[models.chat]
concurrency = 4
timeout_seconds = 300

[[models.chat.connections]]
id = "deepseek-main"
name = "DeepSeek Flash"
type = "openai_compatible"
preset = "deepseek"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
api_key = "sk-..."
api_mode = "chat_completions"
reasoning_effort = "max"

[[models.chat.connections]]
id = "openrouter-qwen"
name = "OpenRouter Qwen"
type = "openai_compatible"
preset = "openrouter"
model = "qwen/qwen3-235b-a22b"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
api_mode = "responses"
http_referer = "https://openbiliclaw.local"
x_title = "OpenBiliClaw"

[[models.chat.connections]]
id = "codex-login"
name = "Codex login"
type = "codex_oauth"
model = "gpt-5-nano"
credential_ref = "codex"

[models.embedding]
enabled = true
model = "bge-m3"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = false

[[models.embedding.providers]]
id = "ollama-local"
name = "Local Ollama"
type = "ollama"
base_url = "http://127.0.0.1:11434/v1"

[[models.embedding.providers]]
id = "bge-remote"
name = "Remote bge-m3"
type = "openai_compatible"
base_url = "https://embedding.example.com/v1"
api_key_env = "BGE_REMOTE_API_KEY"
```

### Chat connection union

所有 Chat 项共享以下字段：

- `id`：稳定、唯一、不可为空；重排时不变。
- `name`：用户可读名称；可自动从 preset + model 生成。
- `type`：连接类型 ID。
- `model`：该连接实际调用的 chat model。

类型相关字段只在适用时出现：

- OpenAI-compatible：`preset`、`base_url`、credential、`api_mode`；DeepSeek 可加 `reasoning_effort`，OpenRouter 可加 `http_referer` / `x_title`。
- Anthropic-compatible：`preset`、`base_url`、credential。
- Gemini API：credential 与可选原生 endpoint 设置。
- Ollama：`base_url`、`num_ctx`。
- Codex OAuth：`credential_ref="codex"`，不持久化 OAuth token。

未知字段按 blocking validation 拒绝，防止切换类型后遗留字段继续影响运行时。

### Embedding invariant

Embedding 的 `model`、`output_dimensionality`、`similarity_threshold` 和 `multimodal_enabled` 只存在于 `[models.embedding]`。Provider 项没有 `model` 字段，因此无法在同一 fallback route 内声明不同模型。

`enabled=false` 时 Provider 数组可为空；`enabled=true` 时必须有 1–10 个 Provider。每次调用把同一个共享 model 传给当前 Provider。缓存 namespace 由共享 model 设置生成，不包含当前 Provider ID，使兼容 endpoint 可共享缓存；任何共享 model 设置变化都会生成新 namespace。

## 类型注册表

后端维护一份代码级 `ConnectionTypeDefinition` 注册表，至少包含：

```json
{
  "id": "openai_compatible",
  "category": "api_protocol",
  "label": "OpenAI-compatible",
  "capabilities": ["chat", "embedding"],
  "presets": ["openai", "deepseek", "openrouter", "custom"],
  "fields": ["model", "base_url", "credential", "api_mode"]
}
```

注册表承担四个职责：

1. 为桌面、移动和扩展提供分组、搜索、label、帮助文案和条件字段 metadata。
2. 为 API 和 TOML loader 提供同一套字段白名单与必填规则。
3. 把连接记录交给对应协议 adapter 构建运行时客户端。
4. 声明 Chat / Embedding 能力，页面按 capability 过滤类型与 preset。`dashscope_api` 只支持 Embedding；DeepSeek preset 不会因所属的 OpenAI-compatible adapter 支持 Embedding 就出现在 Embedding preset 列表。

OpenAI、DeepSeek 与 OpenRouter 共用一个 OpenAI-compatible adapter；preset hook 只提供默认 endpoint、请求扩展和额外 headers / body。Anthropic 官方与兼容网关共用 Anthropic-compatible adapter。

## 配置 API

模型配置从整份 `/api/config` 写入流程拆出：

### `GET /api/model-config`

返回：

- `revision`：模型配置规范化内容与 secret fingerprint 的 SHA-256 版本标识；响应只暴露 digest，不暴露参与计算的 secret。
- `models`：Chat 与 Embedding 配置。
- `migration`：是否来自 legacy、未解决项和确认状态。
- 每个 connection / provider 的最近 probe 与 circuit 摘要。

API 永不返回 secret 值。Credential 以状态返回：inline secret 是否已设置、引用的环境变量名、OAuth credential ref 与登录状态。

### `PUT /api/model-config`

请求必须携带读取时的 `revision`。陈旧 revision 返回 `409` 和最新 revision，不写盘、不热重载。

Secret 更新使用显式动作，避免把掩码误存为新 key：

- `keep`：保留现有 secret。
- `set`：写入请求携带的新 secret。
- `clear`：明确删除。
- `env`：保存环境变量名，不复制环境变量值。

保存顺序：

1. 合并 `keep` secret 与当前持久化值。
2. 执行结构、类型、数量、唯一 ID、必填字段和 credential validation。
3. 在内存中构建完整候选 runtime bundle；构建失败立即返回字段化错误。
4. 使用同目录临时文件、flush / fsync 和 `os.replace()` 原子写入。
5. 原子替换 `RuntimeContext` 中的 swappable bundle，再按现有协调机制重启后台任务。
6. 广播带新 revision 的 `config_reloaded` 事件。

候选构建失败时磁盘和现有 runtime 均不变。写盘成功但 runtime swap 出现意外异常时，恢复旧文件与旧 bundle，并返回未重载状态。

新客户端不再通过 `PUT /api/config` 写模型。为避免版本错配的旧扩展在保存其它设置时破坏新 route，`GET /api/config` 在一个兼容发布周期内保留只读 legacy `llm` projection；当 `[models]` 已生效时，`PUT /api/config` 忽略来包中的 `llm`、完整保留 `[models]`，并返回 `model_config_not_updated` warning。模型编辑只有 `/api/model-config` 是权威入口。兼容周期结束后再单独删除 projection。

### `GET /api/model-connection-types`

返回注册表的安全 metadata，不返回 Python 类名、secret 或可执行内容。桌面与扩展按 category 渲染纵向、可搜索列表。

### `POST /api/model-config/probe`

探测一个精确的 Chat connection 或一个 Embedding provider draft，不遍历 fallback，不落盘。Embedding probe 同时收到共享设置并验证：

- 返回向量非空。
- 维度等于 `output_dimensionality`（配置为 `0` 时接受 Provider 原生维度并在结果中报告）。
- `multimodal_enabled=true` 时必须完成一条固定小图的 image-only probe。

远端 Provider 实际权重无法仅凭 probe 证明；probe 不把“同维度”表述成“已证明同模型”。

## Chat 运行时

现有以 Provider 名为 key 的 `LLMRegistry` 改为以 connection ID 为 key 的 `OrderedLLMRoute`。同一种连接类型可注册多次。

调用流程：

1. 从 index 0 开始，跳过仍处于 open circuit 的连接。
2. 调用连接自身的 retry 策略。
3. 成功时返回并清除该连接的 circuit 状态。
4. Provider 范围内的失败进入下一项。
5. 全部失败时抛出 aggregate error，保留每个 connection ID、route position、failure kind 与安全摘要。

`timeout_seconds` 是整条 route 的总 deadline。每个新 attempt 只获得剩余时间；deadline 耗尽后不再启动新的 fallback。调用方取消、请求 schema 错误和内部编程错误不触发 fallback。

### Circuit 状态

Circuit 状态只存在于 runtime，不写入 TOML：

| failure kind | 行为 |
|---|---|
| rate limit / quota | 优先使用上游 `Retry-After`；缺失时沿用当前 60 秒 cooldown |
| auth failed / model not found | 跳过到配置 revision 改变或该连接 exact probe 成功 |
| timeout / connection / server error | 15 秒起的指数 cooldown，最大 5 分钟；成功后清零。该初始值用于避免最多 10 个死 endpoint 在每次后台调用中重复阻塞，实施后根据本地日志重开校准 |
| empty / invalid response / moderation | 本次调用 fallback，不打开跨请求 circuit，因为失败可能与 prompt 有关 |

Exact probe 忽略现有 circuit 并只调用目标连接；成功后关闭该连接 circuit。

### Response 与观测

`LLMResponse` 和 usage / cost 记录增加：

- `connection_id`
- `connection_type`
- `preset`
- `route_position`
- `model`

日志不输出 API key、OAuth token 或含 userinfo 的 proxy URL。UI 只显示安全 failure kind 与操作建议；详细上游异常保留在本地日志的脱敏版本。

## Embedding 运行时

Embedding 使用同样的有序遍历与 circuit 基础设施，但 Provider adapter 接收不可变的共享 `EmbeddingModelSettings`。

- 每个 Provider 的 transport retry 完成后才 fallback。
- Provider 返回空向量、维度不符或缺少已启用的多模态能力时，本次 Provider 失败并尝试下一项。
- 维度不符同时把该 Provider circuit 标记为配置错误，直到配置 revision 改变或 exact probe 成功。
- 所有 Provider 失败时 Embedding 服务按现有产品语义降级，明确上报不可用原因，不缓存空向量。
- 共享 model 设置变化会切换 cache namespace，旧 cache 保留但不再命中；不把不同模型向量混入同一比较空间。

## UI 设计

### 桌面 Web

模型页顶部为 `Chat route`、`Embedding route`、`Runtime` 三个 tab。

Chat / Embedding route 页面左侧是可拖动排序的连接列表，显示位置、派生角色、名称、类型和健康状态；右侧是当前项 inspector。第 1 项标记 Primary，后续项依次标记 Fallback 1–9。列表同时提供 Move Up / Move Down 键盘操作。

Inspector 的连接类型是分组、纵向、可搜索列表，不使用随类型数量横向增长的 tab。选中类型后只显示该类型与 preset 的字段。更换类型会先确认将被清除的不兼容字段；名称与 ID 保留。

### 窄屏、移动 Web 与扩展

使用相同的 list/detail 信息架构，但不压缩成双栏：先显示 route list，点选后进入 / 展开 detail editor。返回列表不丢草稿。字段 schema、validation path、probe 和 secret action 与桌面完全相同。

### 通用交互

- 新建项插到列表末尾并自动打开 inspector。
- Chat 最后一项不可删除；超过 10 项禁止继续添加。
- 重排只改变数组顺序，不改变 ID、secret、probe history 或 usage 归属。
- Preset 只填空值 / 未编辑默认值。
- 保存错误绑定到 `connection_id + field path`，顶部同时显示汇总。
- 切页或关闭含未保存草稿的页面时显示离开确认。
- Probe 状态含结果、延迟和时间戳，但不把短期健康状态写入配置。

## CLI、安装与初始化

CLI 新增统一 `openbiliclaw models` 命令组：

- `models list`
- `models add --kind chat|embedding`
- `models edit <id>`
- `models remove <id>`
- `models move <id> --position <1-10>`
- `models probe <id>`

所有写命令调用同一 model-config service，并遵守 ID、secret、revision、migration 与 backup 规则。`config-show` 输出新结构并继续脱敏。

安装器、setup 和 guided init 不再直接写 `[llm.<provider>]`。它们先选择 connection type，再选择 preset / OAuth，然后构造一项 Chat route；Embedding setup 构造共享设置和至少一个 Provider。初始化前的真实探测继续存在，但改为调用 exact probe service。

## Legacy 迁移

### 读取与映射

仅存在 `[llm]` 时，loader 通过 compatibility adapter 构造内存模型；启动不写盘。若 `[models]` 与 `[llm]` 同时存在，`[models]` 是唯一权威值，diagnostics 明确报告 legacy `[llm]` 已忽略。

| Legacy | 新连接 |
|---|---|
| `openai` + API key，base URL 为空或为 OpenAI 官方地址 | `openai_compatible` + `openai` preset |
| `openai` + API key，自定义 base URL | `openai_compatible` + `custom` preset |
| `openai.auth_mode="codex_oauth"` | `codex_oauth` |
| `deepseek` | `openai_compatible` + `deepseek` preset |
| `openrouter` | `openai_compatible` + `openrouter` preset |
| `openai_compatible` | `openai_compatible` + `custom` preset |
| `claude`，base URL 为空或为 Anthropic 官方地址 | `anthropic_compatible` + `anthropic` preset |
| `claude`，自定义 base URL | `anthropic_compatible` + `custom` preset |
| `gemini` | `gemini_api` |
| `ollama` | `ollama` |

Legacy `default_provider` 成为 index 0；有效且不同名的 `fallback_provider` 成为 index 1。系统不会把其它已配置 Provider 自动追加为 fallback。

Legacy Embedding primary 映射为共享设置与 `providers[0]`。旧 fallback 只有在有效 model、维度和多模态要求与共享设置完全一致时才映射为 `providers[1]`；否则进入未解决项，不会静默切换向量空间。

### 未解决项

以下内容进入 migration report：

- 非空的 soul / discovery / recommendation / evaluation 模块覆盖。
- 已保存 credential 但未进入旧 primary / fallback route 的 Provider。
- 使用不同有效 model / 维度的旧 Embedding fallback。
- 无法映射的未知 Provider、翻译污染值或无效 auth mode。

报告显示原字段、原因和明确处理选择：未路由 credential 可加入指定 route 位置，或确认仅保留在 backup 后从新配置删除；模块覆盖可取消迁移，或确认删除覆盖并改用全局 route；Embedding 不兼容项可调整共享设置、移除该 fallback，或取消迁移。保存前必须解决或显式确认；系统不静默启用、丢弃或改写这些值。

### 首次保存

首次显式保存新 schema 时：

1. 重新校验 migration report 与当前磁盘 revision。
2. 在同目录创建一次性 `config.toml.pre-model-refactor.bak`；若已存在则不覆盖，并使用带时间戳的后缀。Backup 继承原配置文件权限，路径与内容不写入普通日志。
3. 原子写入 `[models]`，不再渲染 legacy `[llm]`。
4. 热重载成功后返回新 revision 与 backup path。

旧环境变量由 adapter 继续读取；用户编辑并保存对应连接后转换为 `api_key_env` 引用。由环境变量管理的 secret 在 UI 中只读，除非用户明确切换 credential source。

## 并发与一致性

- Desktop、移动、扩展和 CLI 都必须携带 revision；只有一个写入者能提交同一版草稿。
- Reorder 使用完整目标数组提交，后端校验 ID 集合未丢失 / 重复。
- Runtime bundle swap 在单一锁内完成；进行中的请求继续持有旧 route 引用，新请求读取新 route。
- 配置重载事件携带 revision；前端只在没有本地脏草稿时自动 hydrate，有草稿时提示远端已更新。
- `config.local.toml` 若包含 `[models]`，继续作为只读覆盖层显示来源；被覆盖字段不能从 UI 写入 base config 制造“保存成功但不生效”的假象。

## 测试策略

实施按 TDD 分层推进。

### 配置与迁移

- 新 schema load / render / round-trip。
- `[models]` 与 `[llm]` 同时存在时的新 schema 优先级，以及 `/api/config` legacy projection 的非权威写保护。
- Chat 连接数量 0、1、10、11；唯一 ID；顺序保持。
- 每种 connection type / preset 的字段白名单、必填与默认填充。
- Secret keep / set / clear / env；API 不回显 raw secret。
- Legacy 映射表、未路由 credential、模块覆盖、Embedding 不兼容项与 backup。
- 启动只读、首次显式保存、schema version 与重复迁移。

### Route 与 Provider adapter

- 相同 connection type 可注册多次并按 ID 区分。
- 精确顺序、Provider retry 先于 fallback、总 deadline。
- 每种 failure kind 的 circuit 行为、probe 绕过与成功复位。
- Aggregate error 保留完整 attempt 列表但安全用户文案不泄密。
- OpenAI-compatible preset 的 endpoint / api mode / DeepSeek reasoning / OpenRouter headers。
- Anthropic-compatible official / custom gateway。
- Codex OAuth credential ref 不把 token 发给自定义 endpoint。

### Embedding

- 所有 Provider 收到完全相同的共享 model 与设置。
- DashScope embedding-only capability 与 Chat 类型列表隔离；OpenAI-compatible preset 按 capability 过滤。
- 空向量、维度错误、多模态能力错误和 fallback 顺序。
- Cache namespace 随共享设置改变、空结果不缓存。
- `enabled=false` 无 Provider 与 `enabled=true` 1–10 Provider validation。

### API 与热重载

- GET secret 状态、PUT revision conflict、字段化 validation。
- Probe 精确命中 draft，不落盘、不 fallback。
- Candidate build 失败不写盘；写盘 / swap 异常回滚。
- 并发客户端、config.local override 与 `config_reloaded` revision。

### UI 与 CLI

- 桌面与扩展：添加、删除、重排、类型切换、preset 条件字段、probe、secret action、错误定位。
- Connection type 纵向列表分组、搜索与新增 descriptor 后无需改布局。
- 键盘 Move Up / Down、焦点恢复、窄屏 list/detail、脏草稿确认。
- CLI list / add / edit / remove / move / probe 与脱敏输出。
- Installer、setup、guided init 使用新 service 且不再写 legacy 字段。

### 完整验证

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
pytest
pytest --cov=openbiliclaw
cd extension && npm run test && npm run typecheck && npm run build
```

涉及真实模型的连通性保留为 opt-in integration / manual test；默认测试使用协议级 fake transport，不调用真实 API、OAuth 或本地 Ollama。

## 文档范围

实施提交按 `CLAUDE.md#documentation-requirements` 同步：

- `config.example.toml`
- `docs/modules/config.md`
- `docs/modules/llm.md`
- `docs/modules/cli.md`
- `docs/changelog.md`
- `docs/architecture.md`
- `docs/spec.md`
- `README.md` / `README_EN.md` 架构图
- `scripts/install.sh`、`scripts/install.ps1` 及相关安装 / Docker / agent-install 文档

## 验收标准

1. 新用户只看到所选连接类型 / preset 需要的字段，并能在同一列表中配置最多 10 个 Chat connection。
2. Chat primary 与 fallback 使用相同数据结构；重排后 runtime 顺序逐项等于 UI 顺序。
3. 可同时配置多个同类型 endpoint，例如 OpenAI primary + DeepSeek fallback + OpenRouter fallback。
4. Codex OAuth 是独立连接类型，OAuth token 永不进入 TOML 或自定义 endpoint。
5. Embedding 多 Provider 共享唯一 model / 维度 / 阈值 / 多模态配置，Provider 项不能覆盖 model。
6. 桌面、移动、扩展、CLI、setup 与 installer 对同一配置读写结果一致。
7. Legacy 启动不改盘；首次保存生成 backup，并对所有无法无损映射的字段给出明确报告。
8. 旧客户端保存其它配置时不能覆盖新模型 route；模型写入只有 `/api/model-config` 是权威入口。
9. 陈旧 revision、无效连接和候选 runtime 构建失败均不改变当前磁盘配置或运行时。
10. 类型注册表新增连接类型后，前端列表可自动显示且布局不需要新增横向 tab。
11. 定向、全量、类型、lint、覆盖率与扩展 build 验证通过，文档与架构图同步。
