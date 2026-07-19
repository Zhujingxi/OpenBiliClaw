# 配置参考

> 生产运行时并发只读取 `[models.chat].concurrency`；缺省值为 4，原生模型配置的权威校验只接受 `1..16` 的整数。后台容量为 `max(1, total-1)`；`candidate_eval_concurrency` 仍默认 3。legacy `[llm].concurrency` 只参与只读迁移候选，不再覆盖原生值。

> `config.toml` 所有配置段落详解。

## 快速开始

```bash
cp config.example.toml config.toml
# 推荐：用原生 ordered-route CLI 配置 Chat / Embedding
openbiliclaw models list
openbiliclaw models add --kind chat
# 也可以直接编辑 config.toml；OpenAI API-key 家族与 Codex OAuth 是不同 connection type
```

## 配置文件位置与恢复

源码 / AI 一键安装 / 桌面安装包默认都使用同一个运行目录：macOS / Linux 为 `~/OpenBiliClaw`，Windows 为 `%USERPROFILE%\OpenBiliClaw`。`config.toml` 保存主配置，`config.local.toml` 是可选本机覆盖文件，加载时后者覆盖前者。

桌面安装包启动时会先检查 `config.toml` 与 `config.local.toml` 是否可解析、是否能构建运行时 `Config` 对象。若发现 TOML 语法错误、文件编码错误，或结构形状导致配置对象无法构建，入口会把坏文件改名为 `config.toml.invalid` / `config.local.toml.invalid`（已有同名备份时追加 `.1`、`.2`），再从随包 `config.example.toml` 重新生成默认 `config.toml` 并打开 `/setup/` 重新初始化。这个恢复流程只处理配置文件，不会移动或删除 `data/`、数据库、Cookie 缓存或日志。

CLI / 源码运行仍按普通错误处理：配置文件损坏时直接暴露异常，方便开发和部署排查。

## 模型连接领域与持久化契约

`openbiliclaw.model_config` 提供配置重构使用的标准库领域层、原生 `[models]` 持久化边界，以及 legacy `[llm]` 的只读 compatibility adapter。`Config.models` 可以严格读取 `schema_version=1` 的原生配置；仅存在 `[llm]` 时，loader 会从原始 table 在内存中构造确定性迁移候选和脱敏 `MigrationReport`，但启动与普通保存都不会把它写回磁盘。生产 `RuntimeContext`、CLI 调用链与 OpenClaw bootstrap 均从 `Config.models` 构造同一个全局有序 Chat route 和共享设置 Embedding route。模型编辑的权威后端入口为 `/api/model-config`，桌面 Web、移动 Web 与浏览器插件直接消费该 API；`openbiliclaw models` 与交互式初始化则直接调用同一个 `ModelConfigService`。legacy `/api/config` 只提供非权威兼容投影。

四端共享同一份状态模块，渲染层按表面收敛：`src/openbiliclaw/web/shared/model-config-state.js` 承载 DOM-free 状态转移（hydrate、route 增删改移、credential keep/set/env/clear、probe fingerprint、revision 冲突、circuit 视图、override 锁、changed-since-probe 检测、单连接向导 draft lens），`src/openbiliclaw/web/shared/model-config-render.js` 承载 descriptor-driven 字段 / credential / 连接类型分组 DOM 构造与 `escapeHtml` 等转义原语；桌面与移动通过 ES module 同时引用两者，渲染器接受 `classPrefix`（桌面默认 `model`，移动传入 `mobile-model`）以输出各表面 stylesheet 实际覆盖的类名，`/setup/` 向导引用共享 state 模块与共享 render 模块的 `escapeHtml` 原语（其 descriptor/credential/连接类型渲染因向导布局保留本地实现，统一收敛为计划 §10 的后续项），插件 popup 通过 `extension/scripts/sync-model-config-state.mjs` 生成的逐字节副本对齐状态机（`tests/js/model-config-parity.test.mjs` 守卫）。`toModelConfigPayload` 对 `enabled=false` 的 Embedding route 序列化空 providers（后端以 `embedding_disabled_with_providers` 拒绝 disabled-with-providers），路由项仍保留在客户端状态供同会话重新启用。`/setup/` 向导在 snapshot 报 `migration.state != "none"` 时改为渲染迁移 interstitial 并指向完整设置页的迁移面板，不再内联尝试 legacy 迁移，并在保存前披露将被保留的既有 fallback 连接数量。

- `ModelConfig` 固定使用 `schema_version=1`，包含一个 Chat route 和一个 Embedding route；所有领域 dataclass 均为不可变值。
- Chat route 使用 `tuple` 保存 1–10 个等价结构的 `ChatConnection`。第 0 项派生为 `primary`，后续项依次派生为 `fallback_1`…`fallback_9`；连接记录不存储 role、priority 或 `fallback_enabled`。
- Embedding route 关闭时 Provider 列表为空；开启时使用 1–10 个有序 `EmbeddingProviderConfig`。`model`、`output_dimensionality`、`similarity_threshold` 与 `multimodal_enabled` 只存在于共享的 `EmbeddingModelSettings`，Provider 记录没有 model 槽位。
- `EmbeddingModelSettings.cache_namespace()` 只由上述四个共享设置计算，Provider ID、endpoint 与数组顺序都不参与；因此兼容 endpoint 重排不会清空缓存，而任一共享设置变化都会隔离到新 namespace。原生 `OrderedEmbeddingRoute` 要求每个 adapter 持有同一个 settings 对象；非零配置维度不匹配会形成跨请求 `config_error` circuit，维度为 `0` 且开启多模态时，exact probe 的文本/图片向量长度不一致也形成相同永久保护。
- `connection_type_registry()` 返回代码内定义的只读注册表。类型按 `api_protocol`、`local_runtime`、`oauth` 分组，并声明 Chat / Embedding capability、字段描述、preset、默认值和帮助文案；`public_descriptors()` 只输出 JSON-safe 数据，不暴露 Python 类名、callable 或凭据。
- 数值领域不变量在 parser、`ModelConfigService`、API 与 CLI 的权威保存边界共享：Chat `concurrency` 必须是 `1..16` 的非布尔整数，`timeout_seconds` 必须是至少 10 秒的非布尔整数，Embedding `output_dimensionality` 必须是非负的非布尔整数，`similarity_threshold` 必须是 `0..1` 内的有限数值。失败以稳定 path/code 返回，且在 runtime build 或写盘前停止。
- `validate_model_config()` 返回带 `path`、`code`、`message`、`severity` 和可选 `connection_id` 的 `ModelConfigIssue`，统一检查数量、全局唯一 ID、类型 / preset capability、类型专属字段及 credential source。所有已填写的原生 `base_url` 还会经过同一个严格 HTTP(S) 策略：userinfo、query、fragment、控制/空白字符、反斜线及无效 host/port 均以固定 `invalid_endpoint` 拒绝；该检查发生在公开 snapshot、持久化、credential/proxy callback 与 SDK 构造之前，错误链不携带 URL 或凭据原值。读取时会分别检查 base persistence 与 base+local effective 两个模型视图，因此 local 的安全 Chat/Embedding 整数组也不能遮住 base 中的不安全 endpoint。每次初始 split 与提交前 rebase 后还会对待持久化模型执行 endpoint-only 检查，并保留 effective 模型的完整 validation；这样既不会回写被 local 遮住的不安全 base，也不会错误要求 base 自身具备由合法 local layer 补齐的 credential/字段。若磁盘上已有不安全 endpoint，`read()` 会字段化失败关闭；管理员需直接修复 base/local TOML 后再读取，服务不会为了展示而回显原值。
- `default_model_config()` 提供一个无内置密钥、可编辑的 DeepSeek Chat 起始连接，并保持 Embedding 关闭。`CredentialConfig.value` 不参与 `repr`，公开输出不得对内部 `ModelConfig` 直接使用 `dataclasses.asdict()`。
- `parse_model_config()` 只接受 `schema_version=1`，保留 Chat connection / Embedding provider 数组顺序，并拒绝未知、过期或形状错误的字段。凭据在 TOML 中扁平化为 `api_key`（inline）、`api_key_env`（env）或 `credential_ref`（oauth）；同一连接出现多个来源会阻断解析，错误信息不包含凭据内容。
- `render_model_config()` 仅接受精确的整数 `schema_version=1`，以固定字段和数组顺序生成原生 `[models]`，不会写空的 inline secret 占位；原生 renderer 与 raw-preservation emitter 共用 TOML basic-string 编码，包含 DEL 在内的禁止控制字符会转义后再落盘。`compute_model_revision()` 对归一化值、连接顺序和凭据指纹计算稳定 revision，revision 本身不包含凭据。
- `migrate_legacy_llm(raw_llm, env)` 直接检查原始 mapping，已知 table / string / bool / int / float 字段必须保持精确类型，列表、布尔值、浮点截断或任意字符串化都不会被静默接受。endpoint 统一经过一条脱敏检查路径：只有真正缺省的空字符串会使用默认值，显式空白或首尾空白均阻断；官方 endpoint 只在 HTTPS、规范 host、允许路径与默认端口下识别，单个 DNS 末尾点会先规范化再执行同一官方规则，因此不能绕过端口/路径限制。userinfo、query、fragment、控制字符、非法端口或官方 host 的非规范路径同样阻断且不保留原 URL。未知 Provider、无效 auth mode、未路由 credential、模块覆盖和无法共享空间的 Embedding fallback 等诊断进入报告；Embedding 借用 `llm.<provider>` 的 credential/endpoint 时保留真实源字段路径，同一原值被 Chat 与 Embedding 消费只产生一个稳定 issue，一次确认会应用该 issue 关联的全部 route 移除；专属 `llm.embedding.*` 值仍保持独立 issue。`MigrationIssue` 只包含字段名、Provider 标识、布尔状态、原因和封闭 action，不包含 credential 或 URL 原值。legacy connection ID 由 kind + Provider 确定，并在 Chat / Embedding 全局去重，因此重复加载不会造成 revision 抖动。
- `apply_migration_resolutions()` 只在内存中应用完整、类型化的 `MigrationResolution`：未路由 credential 选择加入 Chat route 时可省略 `position`，后端会先应用全部确认删除，再把自动项按 issue 顺序分配到最高一组剩余位置；调用方仍可提供显式 1-based `position`，其范围与冲突按删除后的最终 route 校验。另一选择是确认备份后移除；已进入 route 但不可用的连接在确认移除时会从候选 route 删除，删除唯一 Chat 连接会失败；模块覆盖需接受全局 route；Embedding mismatch 需显式给出一份有效共享设置后加入 fallback，或移除 fallback。应用共享设置前会用不含 credential/URL 的私有 capability 元数据重新证明最终 route 中每个保留及待加入 Provider 的 model、维度、multimodal capability 与 endpoint 可用性；缺少证明或远端能力未知时失败关闭。缺少 choice、未知 issue/action、无效 payload、route 溢出和 `cancel` 均以不含原值的 `MigrationResolutionError` 阻断。所有决定完成后还会调用权威 `validate_model_config()`；存在 blocking issue 时不返回部分或无效候选。本阶段的 `confirm_remove_after_backup` 只是确认语义，不创建 backup。
- `ModelConfigService.read()` 只返回脱敏的 `ModelConfigSnapshot`：inline 值不会进入 DTO，env 只公开变量名，OAuth 只公开 credential ref。`add()` / `edit()` / `remove()` / `move()` 都按全局稳定 ID 返回完整不可变候选，位置使用 1-based 语义，不能删除最后一个 Chat connection；`probe()` 只调用指定 draft，不走 fallback、不落盘。HTTP probe 还使用显式 `ModelConfigProbeCapture`：取得短 model path lock 后先再次检查 init，再校验 revision 并解析该版本的 `keep` credential；因此排队等待 path lock 期间开始的初始化会在任何 credential/disk capture 前返回安全 `409`。释放锁后才发网络请求，完成后再次重读 revision；stale 完成返回最新 snapshot，不进入 history、不修改 live circuit。
- `CredentialAction(action, value)` 使用封闭的 `keep / set / clear / env` 语义。普通连接的 `keep` 只保留同一稳定 ID 的已有凭据；`codex_oauth + keep` 会确定性解析为导入的 `oauth/codex` 引用，因此新建或从 API-key 类型切换到 OAuth 时无需提交 token。反向切到非 OAuth 类型若仍 `keep`，权威校验会以 `invalid_oauth_reference` 拒绝；桌面编辑器按 descriptor `category` 把跨 OAuth 边界视作不兼容字段，确认后清除旧凭据并在 OAuth 方向恢复 imported-reference 动作。保存不会把遮罩串、空值、首尾空白、控制字符或非法环境变量名当成新 secret；错误只携带字段路径、稳定 code 与固定文案。revision 冲突在 credential 校验和 runtime 构造前返回最新公开快照。
- `api/model_config_models.py` 与 `api/model_config_routes.py` 将领域 DTO 映射为严格、拒绝额外字段的 HTTP 契约。`GET /api/model-config` 返回 revision、原序 route、迁移、override、最近一次精确探测与 live circuit 摘要；`PUT /api/model-config` 是唯一模型写入口；`GET /api/model-connection-types` 从 registry 输出可按 capability 过滤的分组 descriptor；`POST /api/model-config/probe` 只探测提交的一个 Chat draft，或一个 Embedding provider 加完整共享设置。响应 credential 只有 `source/configured/env_name/credential_ref/oauth_logged_in`，写入 value 标记为 write-only，所有公开响应与验证错误都不携带 secret。路由专用 `_AppModelRuntimeCoordinator` 在写盘前使用可选 async capture capability，等待 `RuntimeContext` lifecycle lock 下的 settled runtime/task 快照；随后明确拥有「无事件 publication → 串行清退 registry 中除 `guided_init` 外的旧 graph 工作 → 新 graph app loops 重启 → 清除 degraded flags → 单次 `config_reloaded`」顺序。直接调用 `RuntimeContext.swap_model_candidate()` 的既有行为保持不变。
- `ModelConfigService.save()` 用 model-scoped path lock 串行同一路径的模型请求，但把可能等待的完整 runtime candidate 构造放在 canonical commit boundary 之前。提交时进入与普通异步配置入口共享的 `config_write.py` path boundary，先执行 guided-init precommit guard，再立即重读 base/local 层并同时比较公开 revision 与不公开 authority fingerprint：仅普通字段变化会基于最新原字节 rebase 后保存，`[models]` / `[llm]` 的来源、内容或 local authority 变化会返回最新 snapshot 的 conflict。restage 后、磁盘替换前，service 优先调用 coordinator 的 async lifecycle-aware capture；legacy/fake coordinator 仍兼容同步 `current_model_candidate` property。canonical writer → lifecycle lock 是单向顺序，lifecycle helper 不取得 writer；等待快照时取消会原样传播，且不创建 backup、不改磁盘、不发布 runtime。legacy 首次显式保存随后创建 mode-preserving、绝不覆盖同名文件的 `config.toml.pre-model-refactor*.bak`，并用同目录临时文件执行 flush、文件 `fsync`、`os.replace` 与目录 `fsync`，最后由 app lifecycle coordinator 激活 runtime candidate。所有 public stop/restart 在稳定 lock 下整段串行 slot clear、registry drain、loop creation 与 post-reload one-shot scheduling，避免重复 loop set 和 orphan registry task；旧 loop 自身已经异常/取消只作为 cleanup 结果。写盘后的 restart/swap 失败或取消会恢复旧字节、mode、完整 normal/degraded runtime identity，shielded rollback 重新取得 lifecycle ownership 后按旧 graph 重建等价 app loops；已清退的 detached 旧 graph one-shot 保持取消，调用方取消继续向上传播。另一 task 的同步 `save_config()` 在此窗口快速返回固定 busy 错误，不会阻塞事件循环或插入半事务。
- `render_model_config_document()` 是 service 的唯一 model-authority source editor：完整 TOML 先解析，`[models...]` / `[llm...]` 表头用 `tomllib` 语义识别，只替换模型 authority；扫描器会跟踪 multiline basic/literal string，字符串内容中的 `[models]`、`[llm]` 或 array header 不会被误当成 table。未知 table、注释、空白、混合位置与 CRLF 原字节保留。inline/dotted authority、损坏 TOML 或无法证明唯一 `[models]` 时失败关闭。私有 `_service_storage.py` 承担 base/local layer、原子写、恢复与 backup 细节，`endpoints.py` 承担原生 endpoint 策略，公开 DTO、协议和 facade 保持在 `service.py`。
- base persistence authority 与 base+local 合并后的 effective authority 分开记录：local-only legacy 及 base+local legacy 都从有效合并值生成候选、迁移状态和脱敏报告；任一有效 local legacy authority 会阻止首次保存，直到先显式转换该本机 layer。有效 `[models]` 按 TOML 分层优先级胜过 `[llm]`，被忽略的 local legacy 不产生虚假 provenance。`config.local.toml` 的有效 `[models]` 仍是只读高优先级 layer：快照公开精确 override path 与 source，保存 shadowed field 会得到字段化错误；其它字段可以写入 base，且 local 值不会被烘焙进 `config.toml`。
- `migration.py` 保持稳定的公开 facade；raw/endpoint inspection、Chat 映射、Embedding 空间判定、迁移 DTO、映射编排和 resolution 分别位于 `_migration_inspection.py`、`_migration_chat.py`、`_migration_embedding.py`、`_migration_types.py`、`_migration_mapping.py` 与 `_migration_resolution.py`，避免安全边界重新聚合为单一大文件。
- `Config.model_meta` 是不落盘的 `ModelConfigMeta(source, migration, override_paths, migration_report)`，记录 `native / legacy / default` 来源、`none / ready / pending` 迁移状态、只存在于内存的脱敏报告，以及 `config.local.toml` / 环境变量贡献的精确覆盖叶路径；`models_meta` 与 `model_config_meta` 是只读别名。diagnostics 会说明 legacy 只读加载、待确认决定、`[models]` 对 `[llm]` 的优先级和覆盖路径。
- `save_config(..., models_authoritative=False)` 是普通调用默认值，并统一进入 `config_write.py` 的 path-keyed bounded disk section；FastAPI 的 auth admin、guided-init source opt-in、autostart apply 与 `PUT /api/config` 还持有同一路径的完整异步事务边界。guided init 的 `try_start` CAS 也在该 path writer 内完成：init 先赢时后续模型/通用写入看到 active 并返回 409，配置事务先赢时 init 等它完整提交后再 reservation，消除「handler 初检通过、commit 前 init 启动」窗口。保存前必须成功读取并解析已有目标文件，否则以不包含原始内容或底层错误详情的 `ConfigError` 中止且保持原字节不变；已有 `[models]`、`[llm]` 或两者会从目标文件的已解析原始表重新发射，保留未知 scalar / list / nested table、array-of-tables 顺序与凭据。高优先级 layer 保护其 `override_paths`，普通 `Config` 调用方没有 legacy 模型字段编辑面；base 中的 raw `[llm]` 只会原样保留，local 覆盖值也不会被烘焙进主文件。`models_authoritative=True` 才只按 `Config.models` 写 `[models]` 并移除 `[llm]`；生产中的权威调用方是 `ModelConfigService` 及其 `/api/model-config` 路由。该边界只协调当前进程；没有跨进程文件锁。替换前重读只能发现该次读取前已经可见的外部变化，外部 writer 仍可能在重读到 `os.replace` 的窄窗口内竞争。
- 普通 `PUT /api/config` 的表单草稿在进锁前生成；取得 canonical writer 后会立即重读完整有效配置栈（base、`config.local.toml` 与环境覆盖），以最新 effective `models` / `model_meta` 重基并重做可构建校验。因此等锁期间已提交的权威模型修改和本地覆盖会同时进入磁盘保护、响应与热重载 runtime，不会被陈旧通用快照取消，也不会把 local 覆盖烘焙进 base 文件。

### Legacy `[llm]` 只读映射

Chat 只按 `default_provider → fallback_provider` 的显式顺序进入 route；其它已保存 credential 只进入待处理报告，不会自动变成更多 fallback。

| Legacy Provider | 内存候选 |
|---|---|
| OpenAI 官方 endpoint | `openai_compatible` + `openai` preset |
| OpenAI 自定义 endpoint | `openai_compatible` + `custom` preset |
| `openai.auth_mode = "codex_oauth"` | `codex_oauth` + `credential_ref = "codex"`；inline key 不复制 |
| DeepSeek / OpenRouter / generic OpenAI-compatible | `openai_compatible` + `deepseek` / `openrouter` / `custom` preset |
| Anthropic 官方 / 自定义 endpoint | `anthropic_compatible` + `anthropic` / `custom` preset |
| Gemini / Ollama | `gemini_api` / `ollama` |

Embedding primary 生成唯一共享的 model、维度、阈值和 multimodal 设置。Legacy fallback 只有在 `fallback_enabled` 为精确 `true`、Provider 本身可用，且有效 model、固定/可配置维度与 model 级 multimodal capability 都和共享空间完全兼容时才进入 `providers[1]`；关闭、不可用或不兼容项保留为 blocking 诊断，不会把死 fallback 或两个向量空间静默混在一起。用户选择用新共享设置加入 mismatch fallback 时，兼容性检查覆盖整个最终 route，而非只检查新 fallback；已保留 Ollama 的固定空间或能力未知的远端若无法证明兼容，候选保持不变并失败关闭。官方 Gemini / DashScope 可凭有效 credential 使用默认 endpoint，无需伪造 `base_url`；自定义/本地类型仍遵循各自的 endpoint 要求。Gemini 继续识别 `GOOGLE_API_KEY` / `GEMINI_API_KEY`，候选只保存环境变量名，不复制环境变量值。

当 `[models]` 与 `[llm]` 同时存在时，native `[models]` 是唯一权威来源；legacy table 即使形状错误也不会向 `Config.models` 提供值或 credential，只产生一条安全的 ignored diagnostic。`config.local.toml` 与环境覆盖仍先按既有优先级合并，具体叶路径记录在 `override_paths`；legacy-only 候选会反映合并后的有效值，但普通保存不会把覆盖层烘焙进主文件。

当前 runtime composition、权威模型 HTTP API、桌面 Web / 移动 Web / 浏览器插件模型编辑器、安装包 `/setup/`、agent bootstrap / 一句话安装器、Docker 首次 seed、桌面打包 helper，以及 CLI 的 `models`、`init`、`setup-embedding` 写入路径均已切换到 `[models]`。bootstrap 先选 connection type，再按 descriptor 选择 preset；可重复的 Embedding endpoint 共用一份 model/settings。CLI 与所有 fresh-install writer 都不再创建 legacy Provider 段或 per-module override；普通非模型保存仍按上文契约保留 parsed raw authority，不会静默改写模型配置。

首批内置 connection type 为 `openai_compatible`、`anthropic_compatible`、`gemini_api`、`dashscope_api`、`ollama` 与 `codex_oauth`。OpenAI / DeepSeek / OpenRouter / custom 是 `openai_compatible` preset；Anthropic / custom 是 `anthropic_compatible` preset。`dashscope_api` 仅支持 Embedding，DeepSeek 与 OpenRouter preset 仅支持 Chat。

原生持久化形状如下；示例只引用环境变量，不包含 inline secret：

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

## 配置段落

插件、桌面 Web 和移动 Web 的「保存时自动同步到对应平台」都从 API 读取，默认关闭。插件与移动 Web 的配置 GET/PUT 使用 AbortController 有界 timeout；插件的同一 deadline 从后端地址解析开始，覆盖初次设备会话交换、401 强制换票、受保护请求与响应解析，认证 fetch 接收同一 AbortSignal。移动 Web 使用模态设置对话框：Escape 可关闭、Tab 焦点留在对话框内，关闭后回到原设置按钮；配置 GET 超时或失败时保存与开关保持禁用，用户必须通过「重试加载」成功取得当前值后才能写回，避免用默认 false 覆盖未知远端状态。

### `[general]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `language` | string | `"zh"` | Agent 输出语言（`zh` / `en`） |
| `data_dir` | string | `"data"` | 数据目录（记忆、Cookie、数据库） |

### `[api]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `host` | string | `"0.0.0.0"` | 后端 API 监听地址。默认绑定所有网卡，方便同局域网手机访问 `/m/`；如只允许本机访问可改为 `"127.0.0.1"` |
| `port` | int | `8420` | 后端 API 监听端口 |

`openbiliclaw start` 和桌面安装包入口默认读取这里的 host / port；显式设置 `OPENBILICLAW_HOST` / `OPENBILICLAW_PORT` 时环境变量优先。浏览器插件的手机二维码入口会在后端地址仍是 loopback 时调用轻量端点 `GET /api/qr-info`（不触发 embedding readiness probe）并读取响应中的 `lan_ip` 字段，用局域网 IP 生成 `/m/` 二维码；但后端仍需要绑定 `0.0.0.0`，手机才能连上。

### `[api.auth]`

局域网 / 远程访问的**可选密码门禁**（`ApiAuthConfig`）。仅当 `enabled=true` 且请求非可信本机时生效；本机（loopback 且无转发头）默认免登录。远程浏览器扩展必须另行启用设备密钥认证。详见 [`docs/modules/api-auth.md`](api-auth.md)。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否为局域网 / 远程访问开启密码门禁。`true` 且 `password_hash` 为空时按配置错误处理（blocking） |
| `password_hash` | string | `""` | scrypt 密码哈希。**请勿手填明文**；用 `openbiliclaw set-password` / `init` / 环境变量设置 |
| `session_secret` | string | `""` | 登录态 HMAC 签名密钥。首次启用为空时自动生成并写回 config；请勿外泄 |
| `session_ttl_hours` | int | `0` | 登录态有效期（小时）。`0` = 永不过期（默认，「记住登录」）；`>0` = 限时登录 |
| `trust_loopback` | bool | `true` | 本机请求是否免登录（扩展 / CLI 依赖此项）。设 `false` 连本机也要登录。带代理转发头（`X-Forwarded-For` 等）的请求不算本机 |
| `trusted_proxies` | list[string] | `[]` | 受信任的同机 / 前置反向代理 IP；仅当直接对端命中此列表，才采信 `X-Forwarded-For`（从右向左）解析真实客户端 IP。**仅 TOML**（env 不支持列表）。同机反代必须配置，否则远程会被误判为本机 |
| `allowed_bearer_origins` | list[string] | `[]` | 允许「跨源 Bearer 登录」的 Origin 白名单。默认空 = 只允许同源 Cookie 登录，绝不向 JS 返回 token。**仅 TOML** |
| `extension_access_enabled` | bool | `false` | 远程扩展设备认证总开关；默认关闭。至少生成一个设备密钥后才能用 `ext-key enable` 开启 |
| `extension_access_keys` | list[string] | `[]` | `<12位 key ID>:<SHA-256 digest>` 记录，仅存高熵设备 secret 的摘要。使用 CLI 管理，不要写入明文密钥；不会由 `GET /api/config` 返回 |
| `extension_token_ttl_hours` | int | `24` | 扩展短会话有效期，范围 `1..168` 小时；长期设备密钥仅用于换取短会话 |

> **环境变量覆盖（显式读取）**：`OPENBILICLAW_API_AUTH_ENABLED` / `_PASSWORD`（明文，启动时即 hash）/ `_PASSWORD_HASH` / `_SESSION_SECRET` / `_SESSION_TTL_HOURS` / `_TRUST_LOOPBACK`。`trusted_proxies` 与 `allowed_bearer_origins` 是列表，**只支持 TOML**，没有 env 覆盖。
>
> 撤销纪元 `auth_epoch` 与密码指纹 `password_fingerprint` 是运行时高频可变状态，**不在 config.toml**，由后端写在 SQLite `data/openbiliclaw.db` 的 `auth_state` 表（改密 / 登出所有设备 / 轮换密钥时自增，使旧登录态立即失效）。`session_secret` / `password_hash` 也**永不经 `GET /api/config` 返回**（即便 `reveal_keys=true`）。

### `[saved_sync]`

```toml
[saved_sync]
auto_sync_enabled = false
```

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `auto_sync_enabled` | bool | `false` | 是否在 OpenBiliClaw 本地收藏 / 稍后再看成功后创建对应平台账号写入任务。默认关闭；首次从插件、桌面 Web 或移动 Web 开启时必须确认外部账号修改警告。关闭不影响保存页手动同步。 |

插件 side panel 设置、桌面 Web 和移动 Web 都从 `GET /api/config` 回读该值，并以 `PUT /api/config` 的 `{saved_sync: {auto_sync_enabled}}` 严格保存。卡片保存始终先写本地；平台失败不回滚本地成功。列表页移除只删除 OpenBiliClaw membership，不反向删除平台收藏、书签、Saved、播放列表或稍后观看记录。

六平台授权 E2E 同样从 `auto_sync_enabled = false` 开始并在退出时恢复原值。手动 favorite / watch-later 不修改该开关；自动同步用例只有在用户对 exact platform、action、public content ID 和 expected target 明确同意后才临时开启。配置同意不能替代当次 `allow_state_changing=true` 精确授权。

推荐卡保存不会在前端按平台决定是否同步：只有后端读到 `auto_sync_enabled = true` 才创建 native task。关闭时响应中的 `pending` 不带 task ID，三个图形化保存页仍保留手动同步；带 task ID 的 `pending` / `syncing` 才表示已有任务并禁用重复提交。

旧 `config.toml` 缺少该段时，加载、`GET /api/config` 与 `openbiliclaw config-show` 都按
`false` 解析；保存其它配置字段不会意外把它改成 `true`。首次在任一图形界面从关闭切到
开启，必须先确认外部账号写入警告。列表页的手动单项 / 批量同步是独立的显式授权入口，
即使这里仍为 `false` 也可用。

### `[autostart]`

当前用户作用域的**开机 / 登录自启动**配置（`AutostartConfig`）。该功能只注册当前用户的桌面登录项，不写系统级服务、不要求管理员权限；Docker / 容器环境和未知平台会显示为不支持。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否期望系统登录后自动拉起 `openbiliclaw start`。可通过插件 / 桌面 Web 设置页或 `openbiliclaw autostart enable/disable` 修改 |
| `manage_ollama` | bool | `true` | `start` 使用单托管 daemon 策略：优先取第一条 Ollama Chat connection，没有时取第一条 Ollama Embedding provider；选中默认 `127.0.0.1:11434` 且未运行时才尝试后台拉起 `ollama serve`。其它不同、自定义或远端 endpoint 需由外部/专用 owner 管理；Embedding repair 始终按自身 provider 精确 endpoint 处理 |

`save_config()` 默认会保留磁盘上已有的 `[autostart].enabled`，避免普通配置保存用陈旧快照覆盖用户刚从 API / CLI 改过的自启动开关。只有 `/api/autostart/apply` 和 `openbiliclaw autostart enable/disable` 会以 `autostart_authoritative=true` 权威写入该字段。

如果当前进程依赖 `OPENBILICLAW_*`、`GOOGLE_API_KEY` / `GEMINI_API_KEY`、或配置的抖音 Cookie 环境变量，自启动开启会被拒绝：登录会话通常拿不到交互式 shell 环境变量，应先把这些值写进 `config.toml`。

### `[models]`（权威模型配置）

所有新配置只写 `[models]`。Chat 与 Embedding 都是有序 route；数组中的第 1 项是首选连接，后续项是等价结构的逐项回退，不存在额外的 role、priority 或 fallback 开关。

完整示例：

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

[[models.chat.connections]]
id = "openrouter-backup"
name = "OpenRouter backup"
type = "openai_compatible"
preset = "openrouter"
model = "openai/gpt-5-nano"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
api_mode = "chat_completions"
http_referer = "https://github.com/whiteguo233/OpenBiliClaw"
x_title = "OpenBiliClaw"

[models.embedding]
enabled = true

[models.embedding.settings]
model = "text-embedding-3-small"
output_dimensionality = 1536
similarity_threshold = 0.82
multimodal_enabled = false

[[models.embedding.providers]]
id = "openai-embedding"
name = "OpenAI embedding"
type = "openai_compatible"
preset = "openai"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

`[models.chat]` 的 `concurrency` 是全局 LLM 总并发（合法范围 `1..16`），后台容量派生为 `max(1, concurrency-1)`；`timeout_seconds` 是整条有序 route 的总 deadline，而不是每个连接各自重新计时。Chat 必须保留 1–10 个连接，Embedding 关闭时 Provider 数组为空，开启时必须有 1–10 个 Provider。

每条 Chat connection 支持以下标准字段：

| 字段 | 含义 |
|------|------|
| `id` / `name` | 全局稳定 ID 与展示名；ID 用于编辑、精确 probe、circuit 和 usage 归因。 |
| `type` / `preset` | 连接协议类型与预设；合法字段、默认 endpoint 和 capability 来自 descriptor registry。 |
| `model` / `base_url` | Chat 模型与 HTTP(S) endpoint。官方 preset 可使用其默认 endpoint；自定义 endpoint 必须通过安全校验。 |
| `api_key` / `api_key_env` / `credential_ref` | 三选一的 inline、环境变量名或 OAuth 引用；也可以全部省略表示无凭据。 |
| `api_mode` | OpenAI 协议的 `chat_completions` 或 `responses`。 |
| `reasoning_effort` | DeepSeek 推理强度只允许空字符串、`high`、`max`；界面把空字符串显示为 `disabled`，adapter 随后发送官方的 `thinking.type=disabled`。`off` 不是 DeepSeek 的 `reasoning_effort` 值，保存时会被拒绝。 |
| `http_referer` / `x_title` | OpenRouter 可选请求头。 |
| `num_ctx` | Ollama 原生 Chat 上下文；`0` 使用服务端默认值。 |

Embedding 的 `model`、输出维度、相似度阈值与多模态开关只允许出现在唯一的 `[models.embedding.settings]`；Provider 仅包含 `id/name/type/preset/base_url` 和凭据来源。所有 Provider 必须能够产生同一向量空间，route 顺序变化不会改变 cache namespace，共享设置变化则会生成新 namespace。若启用封面图向量，所有实际参与的 Provider 还必须与同一共享模型的图像能力兼容。

常见类型由 `GET /api/model-connection-types` 与 `openbiliclaw models` 动态展示：

- `openai_compatible`：OpenAI、DeepSeek、OpenRouter 与自定义兼容网关通过不同 preset 表达。
- `anthropic_compatible`：Anthropic 官方或兼容 Messages API。
- `gemini_api`、`dashscope_api`：各自原生协议。
- `ollama`：本地运行时，Chat 可用 `num_ctx`。
- `codex_oauth`：独立 OAuth 类型；token 只能发送到受限官方 endpoint。

凭据建议优先使用 `api_key_env`。内联 `api_key` 会写入本机 `config.toml`，但不会出现在公开 snapshot、日志、异常、revision 或 `repr` 中；OAuth 只保存引用。普通 `save_config()` 不会改写现有 `[models]` 或 `[llm]` authority，只有 `ModelConfigService`（`/api/model-config` 和 `openbiliclaw models`）可以权威保存模型配置。

#### 本地 Ollama Chat 示例

```toml
[[models.chat.connections]]
id = "ollama-local"
name = "Local Ollama"
type = "ollama"
model = "qwen3:8b"
base_url = "http://127.0.0.1:11434/v1"
num_ctx = 8192
```

Ollama 不需要 API Key。`num_ctx > 0` 时 adapter 使用原生 `/api/chat` 并显式传递上下文设置；`0` 使用兼容端点与服务端默认值。

#### DashScope / Qwen 多模态 Embedding 示例

```toml
[models.embedding]
enabled = true

[models.embedding.settings]
model = "qwen3-vl-embedding"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = true

[[models.embedding.providers]]
id = "dashscope-main"
name = "DashScope"
type = "dashscope_api"
base_url = "https://dashscope.aliyuncs.com/api/v1"
api_key_env = "DASHSCOPE_API_KEY"
```

DashScope 多模态向量走原生 multimodal-embedding 接口，不走 OpenAI compatible-mode。`base_url`
既可填写公共服务根地址，也可直接粘贴阿里控制台给出的业务空间原生
`https://<workspace>.cn-beijing.maas.aliyuncs.com/api/v1`；运行时会规范化后只拼接一次
`/api/v1/services/...`。若使用 OpenAI-compatible 文本向量模型，应改选
`openai_compatible`，并填写 `/compatible-mode/v1` 地址。

#### 权威模型配置与精确探测 API

模型后端使用四个独立 HTTP 接口；桌面 Web、移动 Web 与浏览器插件直接接入。CLI 不经过 HTTP 或 API 私有转换 helper，而是用公开 DTO 显式还原领域值并调用同一个 `ModelConfigService`。

| 接口 | 契约 |
|------|------|
| `GET /api/model-config` | 返回当前公开 snapshot：revision、原序 Chat/Embedding 列表、共享 Embedding 设置、迁移 issue、local override、最近一次精确探测和 live circuit 摘要。 |
| `PUT /api/model-config` | 接收完整 route、上一版 revision、逐 ID credential action 和迁移 resolution；`add_to_chat_route` 省略 `position` 时，由后端在应用全部确认删除后按 issue 顺序确定性追加，显式 one-based `position` 仍受支持并按删除后的最终 route 校验范围与冲突。canonical precommit 再检查 guided init。写盘前等待 lifecycle-locked settled runtime/task 快照；所有 public stop/restart 整段串行，成功顺序为写盘、无事件发布完整 graph、清退 registry 中除 `guided_init` 外的旧 graph 工作、重启新 graph app loops、清除 degraded、单次 final-slot reload event。等待快照时取消不改磁盘/runtime；写盘后的失败/取消由 shielded rollback 重取 lifecycle ownership 并恢复旧字节/runtime 与旧等价 app loops，已取消 detached one-shot 不复活。revision/init 冲突返回 `409`。 |
| `GET /api/model-connection-types?capability=chat|embedding` | 从代码 registry 返回按 `api_protocol / local_runtime / oauth` 分组、可按 capability 过滤的字段与 preset descriptor。 |
| `POST /api/model-config/probe` | 对请求中的一个精确 draft 发起真实探测；DeepSeek 的 `high / max` 仅保留在草稿身份中，网络探测固定关闭 thinking 并请求 8 token，防止长推理造成测试假失败。gate admission 后重查 init，取得 model path lock 后再次重查 init，凭据捕获和完成回写都受同一 revision 校验；不走 fallback、不读写产品 cache、不保存配置，init/stale 结果返回 `409` 且无 credential/network 或 history/circuit 副作用。 |

#### 原生模型 CLI

`openbiliclaw models list|add|edit|remove|move|probe` 提供同一 ordered route 的终端入口。Chat 的第 1 项只在展示层派生为 primary，后续项依次为 fallback，整个列表最多 10 项；Embedding 允许 1–10 个有序 Provider，但 model、输出维度、相似度阈值和多模态开关始终属于唯一共享 settings。`move ID --position N` 使用 1-based 位置，Chat 不能删除到 0 项。

CLI 按 connection-type descriptor 选择 type、preset 和适用字段：OpenAI / DeepSeek / OpenRouter / 自定义网关属于 `openai_compatible` presets，Anthropic API-key 家族属于 `anthropic_compatible` presets，Codex OAuth 等每种 OAuth 是独立 type。公开列表和 `config-show` 只展示 credential source / 环境变量名 / OAuth 引用，不回显 inline secret。离线 CLI 没有 live runtime circuit 时显示 `circuit=unknown`；应用注入状态源后只显示 `closed / open / half_open / unknown`。

每次 CLI 保存携带 snapshot revision；首次冲突会在最新 ordered route 上按稳定 ID 重放同一 mutation，第二次冲突停止。legacy migration issue 通过可重复的 `--resolve ISSUE=ACTION[@POSITION]` 显式处理。`models probe ID` 捕获并只探测该稳定 ID，Embedding 同时绑定完整共享 settings；探测期间不持配置锁，完成时 revision 已变化则丢弃结果并最多重试一次，不会转向 fallback。

桌面 Web、移动 Web 与浏览器插件「设置 → Models」都按 Chat / Embedding / Runtime 三个 tab 组织。Chat 把 primary 与 fallback 视作同一种稳定-ID 记录，按数组顺序显示并提供移动控件，第 1 项只在展示层派生为 Primary，最多 10 项；Embedding 使用同样的 1–10 项 Provider 顺序，但 model、维度、阈值与多模态开关只存在于唯一共享设置区。桌面 Web 选中记录后在右侧 inspector 编辑并在窄屏退化为 list → detail；移动 Web 与插件固定采用顺序式 list → detail，Back 返回列表且不丢草稿或 selected ID。移动 Web 用触摸友好的 Move Up / Move Down 作为主要排序动作，不依赖拖拽。

连接类型选择器完全消费 `GET /api/model-connection-types` 的 group、capability、field、preset、default 与 help descriptor：分组纵向列表可搜索，不在前端硬编码 Provider 卡片。OpenAI API-key 家族归入 `openai_compatible` presets，Anthropic API-key 家族归入 `anthropic_compatible` presets，每个 OAuth 类型保持独立选项。类型或 preset 会清理已填写字段时先确认；跨 OAuth/non-OAuth 还会清理不兼容 credential 语义。

模型保存只发送 revisioned `PUT /api/model-config`，通用设置仍独立发送 `PUT /api/config`，三端通用 payload 都不再包含 legacy `llm`。移动 Web 的 Saved Sync 与 Models 是独立 section，前者只发送 `{saved_sync: {auto_sync_enabled}}`，后者只发送完整 revisioned 模型 payload。保存请求在途时整个模型 fieldset 锁定且拒绝第二次保存或草稿 mutation；失败保留原草稿，成功只用响应 snapshot 重新 hydration。移动端在共享 payload serializer 之前统一校验 Runtime Chat concurrency 为 `1..16` 的整数、timeout 为至少 10 秒的整数、每个 Chat connection 的 `num_ctx` 为非负整数、Embedding 输出维度为非负整数、相似度阈值为 `0..1` 的有限数值；非法草稿就地显示字段/路径错误且不发 PUT，也不会以被 serializer 改写后的值发起 exact probe。清空数值输入会保持为空并显示错误，只有显式输入 `0` 才按 `num_ctx`、输出维度或相似度下界的合法值处理。

`config_reloaded` 到达时，干净草稿自动 hydration 并保留仍存在的 active tab/selected stable ID；有未保存修改时只显示远端 revision 冲突提示。字段错误按 connection ID 映射，不依赖可变数组位置；Pydantic `422 detail` 只投影安全路径、固定消息和当前稳定 ID，错误索引以 own-property 写入防御 prototype-like ID。精确 probe 用 request generation、revision、route kind、stable ID 和精确 draft fingerprint 关联完成结果；Embedding fingerprint 额外包含共享 settings，因此只重排或切换 inspector 可把结果挂回原 ID，但任何相关编辑或 revision 变化都会丢弃旧结果，且不会把 A 的状态渲染到 B。移动 Web 的 production controller 分别跟踪 snapshot 与 descriptor readiness：Saved Sync 中先到的 reload snapshot 不会跳过首次 descriptor load，descriptor 失败可由显式重试按钮或重新进入 Models 单独恢复，重叠的完整加载与较新 reload 会保留最新 snapshot 并安装当前 descriptor；如果完整加载因旧 snapshot 被取代而在胜出 reload 失败/未完成时先结束，界面会显示显式重试并保持锁定，后续重试或迟到的胜出 reload 可自动恢复；两项都 ready 前编辑器保持锁定。每次草稿 mutation 都重新投影数值错误，单纯 Back 导航保留仍有效的服务端字段错误；放弃本地草稿或保存成功后的权威 hydration 会清除或重建本地错误。shell 重绘替换设置按钮后，关闭 overlay 会按稳定 DOM ID 恢复到当前 live opener。

只有浏览器插件提供「一键启用本地 Ollama」模型 route 动作：它仅在当前 snapshot 与 descriptor 都成功应用、且加载期间没有编辑或其他 save 时创建或复用 Ollama Embedding route 并发出 PUT。桌面端的 Embedding repair 是独立的运行时修复流程，不是模型 route 快捷动作；移动 Web 也不提供该捷径。

HTTP probe 只走 `capture_probe()` → 无锁网络 `probe_captured()` → `revalidate_probe_capture()` 三段式契约。`capture_probe()` 内部的 init guard 与 revision/credential capture 同处 path lock 临界区，堵住 probe 在慢保存后排队、init 随后 reservation 的窗口；它不引入 network lock。`ModelConfigService.probe()` 仅保留给不产生 history/circuit 副作用的 legacy 进程内调用；它没有 caller-supplied revision，因此不能作为 HTTP endpoint 的实现。

写请求从不回传已有 secret。每条记录必须显式选择 credential 动作：

| action | value 含义 |
|--------|------------|
| `keep` | 普通类型保留同一 revision 下同一稳定 ID 的现有凭据；`codex_oauth` 例外，始终解析为导入的 `oauth/codex` 引用，可用于新 ID 或类型切换。 |
| `set` | 写入新的 inline secret。 |
| `env` | `value` 是环境变量名，保存的只是该名称。 |
| `clear` | 清除当前凭据，`value` 为空。 |

Chat probe 提交 `kind="chat" + revision + connection`；Embedding probe 提交 `kind="embedding" + revision + provider + settings`，其中 settings 必须携带共享的 model、维度、相似度阈值和多模态开关。示例使用环境变量引用，不包含 inline secret：

```json
{
  "kind": "embedding",
  "revision": "<GET snapshot revision>",
  "provider": {
    "id": "embedding-main",
    "name": "OpenAI embedding",
    "type": "openai_compatible",
    "preset": "openai",
    "base_url": "https://api.openai.com/v1",
    "credential": {"action": "env", "value": "OPENAI_API_KEY"}
  },
  "settings": {
    "model": "text-embedding-3-small",
    "output_dimensionality": 1536,
    "similarity_threshold": 0.82,
    "multimodal_enabled": false
  }
}
```

业务探测失败返回 `200` + `ok=false`、安全 error code/message、`probed_at` 与 revision；schema 错误为不含请求 input 的 `422`，领域校验为字段化 `400`，stale revision 为带最新公开 snapshot 的 `409`。GET 只附着与当前持久化记录完全一致的探测摘要：稳定 ID 重排不会丢失摘要，但字段、credential action、revision 或任一共享 Embedding 设置不一致的 draft 只在本次 POST 响应显示，不覆盖已保存 route 的最近摘要。成功探测当前精确持久化记录时，只关闭该稳定 ID、该 capability、当前 revision 的 live circuit，不影响其它连接或旧 revision。

Legacy `POST /api/config/probe-service` 现在只接受 `kind="network_proxy"`；`llm`、`llm_fallback` 与 `embedding` 会按 schema 返回 `422`。该接口继续用于 direct/system/custom 出口策略探测。

#### 启用本地 Ollama embedding（v0.3.0+，**v0.3.3 起真实生效**）

> ⚠️ **如果你装的是 v0.3.0~v0.3.2**：`setup-embedding` 当时虽然写了 `[llm.embedding] provider="ollama"`，但 LLM 注册表静默回退到 default provider，embedding 实际仍走 Gemini。
> **升级到 v0.3.3+ 重启 backend** 即可让旧配置生效，不需要改配置；当前版本的 `openbiliclaw setup-embedding` 会打开原生 `[models.embedding]` route 编辑器，不再写 `[llm.embedding]`，也不自动安装、启动或下载 Ollama/模型。

不想再多一份 embedding API Key、或要支持离线，可以用 Ollama + bge-m3 跑本地 embedding：

```bash
# 1. 装 Ollama（一次性）
# Mac
# 安装并启动官方 Ollama.app（会创建 ollama 命令行入口）
open https://ollama.com/download/mac
# Windows: 从 https://ollama.com/download 下载安装包
# Linux
curl -fsSL https://ollama.com/install.sh | sh && ollama serve &

# 2. 手动准备模型（一次性）
ollama pull bge-m3

# 3. 打开原生 Embedding route 向导，只写配置
openbiliclaw setup-embedding
```

或手动改 `config.toml`：

```toml
[models.embedding]
enabled = true

[models.embedding.settings]
model = "bge-m3"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = false

[[models.embedding.providers]]
id = "ollama-embedding"
name = "Local embedding"
type = "ollama"
base_url = "http://127.0.0.1:11434/v1"
```

CPU 即可跑（~100-200ms/次），跨 Mac / Win / Linux 一致。

### Legacy `[llm.soul]` / `[llm.discovery]` / `[llm.recommendation]` / `[llm.evaluation]`

这些段只为读取旧配置和生成迁移 issue 保留。生产 runtime、原生 CLI、
初始化向导与安装链路都使用唯一的全局 ordered Chat route；caller 只参与
并发准入与 usage 归因，不再选择模块专属 Provider 或 model。旧文件出现
这些字段时，运行 `openbiliclaw models list` 查看封闭迁移决定，再在模型
写命令中用 `--resolve ISSUE=ACTION[@POSITION]` 明确接受全局 route 或其它
允许动作。`agent_bootstrap.py` 已删除 `--module-override`，不会写入这些段。

### `[bilibili]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `auth_method` | string | `"cookie"` | 认证方式：`cookie` / `qrcode` / `none` |
| `cookie` | string | `""` | 浏览器 Cookie（推荐通过 `auth login` 命令设置） |
| `proxy` | string | `""` | B站 请求专用代理（v0.3.153+）。留空 = 恒直连：客户端忽略环境变量与系统代理（代理出口 IP 常触发 B站 风控，导致已登录仍显示"未登录"）。仅当网络无法直连 B站 时才填，如 `"http://127.0.0.1:7890"` |

### `[bilibili.browser]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `executable` | string | `""` | agent-browser 路径（留空使用全局安装） |
| `headed` | bool | `false` | 是否显示浏览器窗口（调试用） |

> 运行时行为：
> 如果 `bilibili.cookie` 留空，CLI 命令和本地 API 服务会自动回退到 `auth login` 保存的 `data/bilibili_cookie.json`。
> 只有在你想显式覆盖本地登录态时，才需要把 cookie 直接写进 `config.toml`。

### `[network]` (v0.3.164+，v0.3.165 路由模式补强，v0.3.166 国内网关豁免)

海外网络路由。仅作用于**海外客户端**：OpenAI / Claude / Gemini / OpenRouter / openai_compatible 的 chat + embedding SDK、YouTube（yt-dlp、scrapetube、InnerTube / 页面 fallback）、GitHub 自动更新、Codex OAuth 令牌刷新。**注意**：`openai_compatible` / `openai` 若指向的是国内网关或本机地址，则按下方「国内网关豁免」强制直连，不受本节代理影响。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `mode` | string | `"direct"` | `direct` 显式忽略环境 / 系统代理；`system` 明确继承 `HTTP(S)_PROXY` / OS 代理；`custom` 只使用下方 `proxy` |
| `proxy` | string | `""` | `custom` 模式的代理 URL。支持 `http://` / `https://` / `socks5://` / `socks5h://`，如 `"socks5://127.0.0.1:1080"` |

> 与 `[bilibili].proxy` 的区别：`[network].proxy` 是「海外出口」，`[bilibili].proxy` 是「B站专用」，两者语义相反、互不影响。
>
> **国内直连隔离**：B站 / 抖音 / Ollama / 国内 CDN 图片缓存等所有 `trust_env=False` 客户端**永远不使用**此代理（继承代理曾触发 B站 风控，`df626f3f`）。该隔离由 `tests/test_network_proxy_isolation.py` 守卫测试钉死。
>
> **国内大模型网关豁免（v0.3.166）**：即使 `mode` 为 `system` / `custom`，指向国内网关的 LLM 请求也会被识别并**强制直连**——DeepSeek（`api.deepseek.com`）、商汤 SenseNova（`.cn`）、通义千问（`aliyuncs.com`）、智谱、文心千帆、混元、火山方舟、Kimi、MiniMax、阶跃、百川、硅基流动、无问芯穹、PPIO 等，以及 `localhost` / 内网自建端点（cpa、vLLM 等）。识别覆盖 `.cn` 顶级域、已知厂商的非 `.cn` 域名白名单、loopback / 私有 / link-local IP，由 `openbiliclaw.network.is_domestic_endpoint` 裁决。避免「为连墙外模型开了代理 → 国内模型请求被绕道境外 → 总是超时」。豁免按 endpoint 生效，genuine 墙外网关仍走上面的代理策略。
>
> 旧配置只有非空 `proxy` 而没有 `mode` 时自动迁移为 `custom`；空旧配置迁移为 `direct`。保存时校验模式、协议与主机，`custom` 缺地址或非法值经 `PUT /api/config` 返回 400、不落盘。桌面 Web 与扩展 popup 都提供模式选择、地址输入和按当前模式真实探测；CLI `config-show` 分别显示模式与地址；移动 Web 设置页当前不暴露网络代理字段。

### `[sources.browser]`

通用 Web / 自定义网页源使用的浏览器配置。与 `bilibili.browser` 独立 —— 后者控制 B 站登录 / 扫码用的 agent-browser CLI。

> 当前小红书和抖音稳定链路都走 Chrome 插件任务，不依赖 `[sources.browser].cdp_url`。这里的 CDP 配置主要用于没有专用插件 / API adapter 的网页源。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `cdp_url` | string | `""` | 预启动 Chrome 的 CDP 端点，例如 `"http://localhost:9222"`。设置后优先走 Playwright `connect_over_cdp` 复用你手动登录的会话；留空则回退到 agent-browser（无登录态） |
| `headed` | bool | `false` | agent-browser 回退路径是否显示窗口 |

> **仅在通用 Web / 自定义网页源需要登录态时使用 CDP。** 普通 B 站 / 小红书 / 抖音使用路径不需要配置这里。
>
> 启动步骤：
> 1. 安装 Playwright：`pip install 'openbiliclaw[browser]'`
> 2. 启一个独立 profile 的 Chrome：
>    ```bash
>    open -na "Google Chrome" --args \
>      --remote-debugging-port=9222 \
>      --user-data-dir="$HOME/.openbiliclaw-chrome"
>    ```
> 3. 在这个 Chrome 里手动登录目标网页源，profile 会记住，后续复用
> 4. 在 `config.toml` 里填 `cdp_url = "http://localhost:9222"`
>
> `127.0.0.1` 与 `localhost` 并非总是等价：macOS 上 Chrome 常只绑定 IPv6 `::1:9222`，而 Python urllib 默认走 IPv4。用 `localhost` 最稳妥（`getaddrinfo` 会同时尝试两边）。

> **关于 `daily_*_budget`：** 这些字段是**每 UTC 日、按任务类型的入队次数上限**，不是启用 / 关闭该来源的开关（来源开关是各段的 `enabled`）。`0`（或留空）表示不设每日上限，补池只受平台缺口 / `discovery_limit` / producer 节流控制。填 `1` 只会把该任务类型限制到每天 1 次——配置加载时对落在 1–4 的可疑值会打印一次 WARN 提示。

### `[sources.bilibili]`

Bilibili discovery 的平台级开关。B 站账号登录 / Cookie 获取仍由 `[bilibili.auth]` 和 `[bilibili.browser]` 控制；本段只决定后台候选池是否继续调度 B 站 `search` / `related_chain` / `trending` / `explore` 策略。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 是否启用 Bilibili discovery。设为 `false` 后，B 站候选池占比会从运行时有效配比中剔除，已保存的 `scheduler.pool_source_shares.bilibili` 数值仍保留，重新开启后继续使用 |

### `[sources.xiaohongshu]`

小红书专用配置。内容发现和元数据提取都由浏览器扩展在真实登录态下完成：被动收集、后台标签页搜索和创作者订阅都会通过扩展任务桥回写后端。主后端不主动爬取小红书，也不再依赖 `sidecar_url`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否启用小红书 discovery 和 init bootstrap；默认关闭，`init` 选 Yes、`--yes-xhs` 或插件设置页打开后才会写回 `true` |
| `daily_search_budget` | int | `0` | 每天后端允许入队的 Soul 驱动搜索任务数上限；`0` 表示不设每日上限，持续补池只受平台缺口、单轮 `discovery_limit` 和 producer 节流控制 |
| `daily_creator_budget` | int | `0` | 每天订阅创作者抓取任务上限；`0` 表示不设每日上限 |
| `task_interval_seconds` | int | `45` | 扩展分发器两次任务之间的最小间隔（秒） |

> **安全设计要点：** 后端从不直接调用小红书搜索 / Feed API。所有"主动发现"（关键词搜索、创作者主页浏览）都在用户自己的浏览器中以后台标签页形式执行，由扩展代理完成。被动发现则利用用户正常浏览时已经加载的卡片 URL，零额外请求。

### `[sources.douyin]`

抖音专用 discovery 配置。初始化画像仍由浏览器扩展执行；本段控制 `openbiliclaw discover --source douyin` / `discover-douyin` 的内容发现。Cookie 不写进 `config.toml`：`cookie_env` 指向的环境变量优先；未设置时，后端读取浏览器扩展通过 `/api/sources/dy/cookie` 同步到 `data/douyin_cookie.json` 的值。设置页（插件 / 桌面 Web）的抖音卡片是 write-only 粘贴入口：`GET /api/config` 的 `sources.douyin.cookie`（API-only 字段，非 `config.toml` 键）只返回 masked 凭据（前端不再请求 `reveal_keys=true`），输入框渲染为空、placeholder 显示已保存 / 未保存状态；空输入在 `PUT /api/config` 里整键省略，非空新值路由到 `data/douyin_cookie.json`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否启用抖音 discovery。默认关闭，必须显式 opt-in |
| `mode` | string | `"direct"` | 当前仅支持 `direct`，保留字段用于后续 extension/direct 切换 |
| `cookie_env` | string | `"OPENBILICLAW_DOUYIN_COOKIE"` | douyin.com Cookie header 的环境变量覆盖名；为空时使用扩展同步文件 |
| `daily_search_budget` | int | `0` | 每日搜索插件任务预算，限制 `dy_tasks(type="search")` 入队次数；`0` 表示不设每日上限 |
| `daily_hot_budget` | int | `0` | 每日热点插件任务预算，限制 `dy_tasks(type="hot")` 入队次数；`0` 表示不设每日上限，正数时 runtime 抖音缺口较大时会把有效预算临时抬高到 `max(配置值, min(缺口, 60))` |
| `daily_feed_budget` | int | `0` | 每日首页推荐流插件任务预算，限制 `dy_tasks(type="feed")` 入队次数；`0` 表示不设每日上限 |
| `request_interval_seconds` | int | `2` | direct 诊断请求的建议最小间隔；当前默认 discovery 走插件 DOM-first 链路，主要由任务预算和 runtime producer 节流保护 |

当前 `search` 子来源使用浏览器插件的登录会话，从抖音首页通过 DOM 搜索框输入 / 提交触发页面加载，并以 `dy-plugin-search` 进入 discovery；`hot` 子来源同样从首页点击热榜 / 热点入口和目标热词，并以 `dy-plugin-hot-related` 进入 discovery；`feed` 子来源在首页推荐流滚动触发加载，并以 `dy-plugin-feed` 进入 discovery。插件只被动监听页面自己发出的响应和已渲染 DOM，不主动跳 `/search/...`、`/hot/...` 快捷 URL，也不主动调用 search / related / feed API bridge。插件任务空 / 失败时默认返回 0 条；direct-cookie fallback 仅保留给显式 `allow_direct_fallback=True` 的诊断代码。因 daemon 重启或插件未及时消费而被清理的 `failed/stale_pending` 任务不消耗正数每日预算。runtime 大缺口补池会优先 search / hot，feed 只用于小缺口补零散名额。`msToken` 如果存在会随 Cookie 一起使用，但扩展同步不再硬依赖它。若 Cookie 过期、页面布局变化或插件未在线，命令可能返回 0 条并提示检查登录态。

### `[sources.youtube]`

YouTube discovery 配置。初始化画像由浏览器扩展读取观看历史 / 订阅 / 点赞，也可通过 `import-youtube` 导入 Google Takeout；steady-state discovery 由后端 `YoutubeDiscoveryProducer` 独立调度 `yt_search` / `yt_trending` / `yt_channel` 三个策略。这里的预算是可选每日执行上限；默认 `0` 表示不设每日上限，每轮执行规模由平台缺口和 `scheduler.discovery_limit` 决定，行为与 B 站补池保持一致。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 YouTube 参与候选池配比和后台 discovery；`init --yes-youtube` 会写回 `true`，`--no-youtube` 或 `OPENBILICLAW_NO_YOUTUBE=1` 会写回 `false` |
| `daily_search_budget` | int | `0` | `yt_search` 每天最多生成 / 执行的 YouTube 搜索 query 数；`0` 表示不设每日上限，本轮 query 数由平台缺口 / `discovery_limit` 决定 |
| `daily_trending_budget` | int | `0` | `yt_trending` 每天最多拉取的热门候选数；`0` 表示不设每日上限，本轮拉取规模由平台缺口 / `discovery_limit` 决定 |
| `daily_channel_budget` | int | `0` | `yt_channel` 每天最多选择的订阅频道数；`0` 表示不设每日上限，本轮频道数由平台缺口 / `discovery_limit` 决定 |
| `request_interval_seconds` | int | `2` | 预留的 YouTube 请求间隔配置；当前策略主要由单轮预算和 runtime 补池节奏控制 |
| `min_interval_minutes` | int | `60` | `YoutubeDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

### `[sources.twitter]`

X (Twitter) discovery 配置。X 是第六个内容源，发现走**服务端 cookie 重放**（对标 `[sources.douyin]` 的 direct 模式），由后端 `XDiscoveryProducer` 调度 `search`（画像驱动关键词）/ `feed`（推荐流 For-You）/ `creator`（账号订阅）三个策略，把推文灌入统一候选池。行为采集（用户在 x.com 上自己的点赞 / 收藏 / 回复）走浏览器扩展 MAIN-world tap，与本段无关。Cookie 不写进 `config.toml`：`cookie_env` 指向的环境变量优先；未设置时，后端读取浏览器扩展通过 `/api/sources/x/cookie` 同步到 `data/x_cookie.json` 的 `auth_token` + `ct0`。设置页（插件 / 桌面 Web）的 X 卡片是 write-only 粘贴入口：`GET /api/config` 的 `sources.twitter.cookie`（API-only 字段，非 `config.toml` 键）只返回 masked 凭据（前端不再请求 `reveal_keys=true`），输入框渲染为空、placeholder 显示已保存 / 未保存状态；空输入在 `PUT /api/config` 里整键省略，非空新值路由到 `data/x_cookie.json`，含 `auth_token` + `ct0` 的有效粘贴会同时解除 re-login 健康封锁。X 客户端 `XClient` 封装默认安装自带的 `twitter-cli`，只在 `enabled=true` 且真正 fetch 时 lazy import，`enabled=false` 路径绝不 import；`openbiliclaw[x]` 仍保留为兼容旧脚本的安装别名。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 X 参与候选池配比和后台 discovery。默认关闭，必须显式 opt-in；`init --yes-x` / 插件设置页 X 源卡 / `--no-x` 会写回对应值。关闭后 `XDiscoveryProducer` 不下发任何任务，`pool_source_shares.twitter` 配额从有效配比中剔除，`twitter-cli` 也不会被 import |
| `mode` | string | `"cookie"` | 当前仅支持 `cookie`（服务端 cookie 重放）；保留字段 |
| `cookie_env` | string | `"OPENBILICLAW_X_COOKIE"` | x.com Cookie（含 `auth_token` + `ct0`）的环境变量覆盖名，优先级高于 `data/x_cookie.json`；为空时使用扩展同步文件 |
| `daily_search_budget` | int | `0` | `search` 策略每日抓取预算；`0` 表示不设每日上限，本轮规模由平台缺口 / `discovery_limit` 决定 |
| `daily_feed_budget` | int | `0` | `feed`（推荐流 For-You）每日拉取预算；`0` 表示不设每日上限。For-You 抓首页 home timeline 最易被注意，建议压低；producer 还会把 For-You 节流到很低的每日频次，并在连续失败后自动暂停 |
| `daily_creator_budget` | int | `0` | `creator`（账号订阅）每日抓取预算；`0` 表示不设每日上限 |
| `request_interval_seconds` | int | `3` | 两次 X 请求之间的最小间隔（抗检测）；TLS 指纹由 `twitter-cli`（`curl_cffi`）负责 |
| `min_interval_minutes` | int | `60` | `XDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

X 源健康状态（`ok` / `missing_cookie` / `expired_cookie` / `rate_limited` / `blocked`）由 `storage/x_health.py` 持久化，按 401 / 403 / 429 分别退避，连续 For-You 失败会自动暂停 For-You 拉取，状态经 `GET /api/sources/x/status` 暴露到插件 / 桌面 Web 设置页。账号订阅用 `x_creator_subscriptions` 表持久化，经 `GET/POST/DELETE /api/sources/x/creators` 管理。

### `[sources.zhihu]`

知乎 discovery 配置。知乎是浏览器插件登录态源：后端入队 `zhihu_tasks`，插件在已登录 `zhihu.com` 标签页中执行 `search` / `hot` / `feed` / `creator` / `related` 任务并把 `zhihu_*` 候选回写，后端再转换为 `source_platform="zhihu"` 的 `DiscoveredContent` 写入统一待评估候选池。`fetch-zhihu` 的事件 smoke 也复用同一张 `zhihu_tasks` 表，但任务类型是 `bootstrap_events`，命令本身只打印计数、不写 memory；guided init 里选择知乎时会显式收集同一类 `bootstrap_events` 结果，把浏览 / 收藏 / 点赞 / 动态收藏转换为首轮画像信号，并写回 `enabled=true`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让知乎参与候选池配比和后台 discovery。默认关闭，必须显式 opt-in；关闭后 `ZhihuDiscoveryProducer` 不入队任务，`pool_source_shares.zhihu` 配额从有效配比中剔除 |
| `source_modes` | list[str] | `["search", "hot", "feed", "creator", "related"]` | 后台和 `openbiliclaw discover --source zhihu` 允许调度的知乎 discovery 分支。插件 side panel 与桌面 Web 配置页都提供五个显式勾选项。`search` 使用统一关键词 planner；`hot` 拉热榜；`feed` 拉首页推荐；`creator` 优先用最近任务结果里的作者主页作种子，没有历史种子时使用本轮 search / hot / feed 返回的作者页；`related` 优先用最近知乎候选 URL，没有历史种子时使用本轮已返回内容 URL 作相关扩展种子 |
| `daily_search_budget` | int | `0` | 知乎搜索 discovery 每日任务预算；`0` 表示不设每日上限，本轮关键词数由统一关键词 planner / fallback 画像兴趣和平台缺口决定 |
| `daily_hot_budget` | int | `0` | 知乎热榜 discovery 每日任务预算；`0` 表示不设每日上限 |
| `daily_feed_budget` | int | `0` | 知乎首页推荐 discovery 每日任务预算；`0` 表示不设每日上限 |
| `daily_creator_budget` | int | `0` | 知乎作者 discovery 每日任务预算；`0` 表示不设每日上限 |
| `daily_related_budget` | int | `0` | 知乎相关扩展 discovery 每日任务预算；`0` 表示不设每日上限 |
| `request_interval_seconds` | int | `3` | 后端等待任务时的轮询间隔 / 插件搜索节奏提示；真实平台请求仍发生在用户已登录浏览器内 |
| `min_interval_minutes` | int | `60` | `ZhihuDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

### `[sources.reddit]`

Reddit 来源配置。Reddit 日常 discovery 默认走随 OpenBiliClaw 安装的 `rdt-cli` 登录态命令后端；已连接浏览器插件会把 `reddit_session` 自动同步到 `~/.config/rdt-cli/credential.json`，插件不可用时才需要手动运行 `rdt login`。Cookie 不写进 `config.toml`：桌面 Web 设置页的 Reddit Cookie 覆盖输入框可手动粘贴（`PUT /api/config` 的 `sources.reddit.cookie` 为 API-only 字段，非 `config.toml` 键），非空新值路由到 rdt-cli credential store，与插件自动同步同一存储；粘贴内容缺少 `reddit_session` 时保存以 400 `missing_reddit_session` 显式拒绝，不静默丢弃。后端会拉取 `search` / `hot` / `subreddit` / `related` 候选后转换为 `source_platform="reddit"` 的 `DiscoveredContent` 并只写入统一待评估候选池；LLM 评估和入正式推荐池由后台 `DiscoveryCandidatePipeline` 统一处理。初始化阶段仍可入队 `reddit_tasks(type="bootstrap_events")`，插件在已登录 `reddit.com` 会话里读取 saved / upvoted / subscribed subreddit 并转换为 `favorite` / `like` / `follow` 画像信号。`extension` 可显式作为浏览器登录态 discovery 后端；默认 `rdt` / `opencli` 命令后端不可用或未登录时也会自动 fallback 到插件任务。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 Reddit 参与初始化 opt-in、候选池配比和后台 discovery。默认关闭，必须显式 opt-in；关闭后 `RedditDiscoveryProducer` 不入队任务，`pool_source_shares.reddit` 配额从有效配比中剔除 |
| `backend` | string | `"rdt"` | Reddit 取数后端。`rdt` 使用默认安装的 rdt-cli 登录态命令后端，并优先使用插件同步的 `reddit_session` credential；`rdt login` 仅作为手动 fallback；`extension` 使用 OpenBiliClaw 浏览器插件和当前浏览器登录态，且仍负责 bootstrap 初始化信号；`opencli` / `auto` 为兼容命令路径。命令后端状态不是 `ready` 时，CLI / producer 会自动 fallback 到插件任务 |
| `source_modes` | list[str] | `["search", "hot", "subreddit", "related"]` | 后台和 `openbiliclaw discover --source reddit` 允许调度的 Reddit discovery 分支。`search` 使用统一关键词 planner，关键词池为空时回退画像兴趣；`hot` 默认拉 `r/all`；`subreddit` 优先用最近 Reddit 候选里的 subreddit 作种子；`related` 优先用最近 Reddit 内容 URL 作相关扩展种子 |
| `daily_search_budget` | int | `300` | Reddit 搜索 discovery 每日条目预算 |
| `daily_hot_budget` | int | `300` | Reddit 热门 discovery 每日条目预算 |
| `daily_subreddit_budget` | int | `300` | Reddit subreddit discovery 每日条目预算 |
| `daily_related_budget` | int | `300` | Reddit related discovery 每日条目预算 |
| `request_interval_seconds` | int | `3` | 后端等待任务时的轮询间隔 / 插件任务节奏提示；真实平台请求发生在用户已登录浏览器内 |
| `min_interval_minutes` | int | `60` | `RedditDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

#### 配置页来源状态契约

插件 side panel 与桌面 Web `/web` 的平台源配置页统一读取 `GET /api/sources/status`。这个端点是**纯本地读取**：不会访问 Bilibili、小红书、抖音、YouTube、X、知乎或 Reddit，也不会运行 `rdt` / `opencli` 命令。页面可见时每 30 秒刷新一次，但请求只到 OpenBiliClaw 本地后端；真实平台请求仅由用户显式初始化、发现、诊断任务或已启用的后台 producer 发起。

状态语义如下：

| 状态 | 配置页文案 | 含义 |
|------|------------|------|
| `ok` | 接入可用 | 之前的真实任务 / 健康检查已验证（当前仅 X 健康状态机使用）；读取状态页本身不会再验证 |
| `ready` | 凭据已就绪 | 本地凭据结构完整，或浏览器刚同步为已登录；不等于本次刷新访问平台成功 |
| `unverified` | 状态待验证 | 已配置凭据但尚未由实际任务验证，或浏览器登录态从未同步 |
| `missing` / `login_required` | 需要登录 | 本地无凭据，或浏览器最近明确同步为未登录 |
| `partial` | 部分可用 | 本地凭据不完整 |
| `stale` | 需要刷新 | 最近同步的浏览器登录态或 credential 已过期 |
| `error` | 检查失败 | 本地 credential 文件不可读或格式无效 |
| `no_auth` | 无需登录 | 公开来源 |

平台特例：抖音只要本地 Cookie 存在即显示 `unverified`，必须由实际抖音任务确认；小红书 / 知乎优先使用插件上报的 `logged_in + updated_at`，知乎仅在从未收到浏览器心跳时回落最近任务历史；Reddit `backend="rdt"` 只读取本地 credential 文件，非 rdt 命令后端在状态页显示 `unverified`。`xsec_token` 只是小红书内容 URL 的访问令牌，配置页即使能展示它也不会据此判断账号已登录。

### `[scheduler]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 后台 LLM / embedding 工作总开关；插件设置页显示为「停止后台 LLM 请求」。关闭后 runtime 的刷新、补池预计算、账户同步、猜测兴趣和主动推送等 daemon-owned 后台任务都会跳过；手动 CLI / API 请求仍按显式操作执行。若候选池为空，推荐页可能暂时没有内容 |
| `pause_on_extension_disconnect` | bool | `false` | 开启后，daemon-owned 后台 LLM / embedding 工作只在浏览器插件有 `/api/runtime-stream` 连接、或刚断开仍处于宽限窗口内时运行；离线期间不会自动补新内容 |
| `extension_disconnect_grace_seconds` | int | `90` | 插件最后一个 `runtime-stream` 连接断开后的宽限秒数；小于等于 0 或无法解析时回退到 `90` |
| `discovery_cron` | string | `"0 */8 * * *"` | 兼容旧配置的保留字段；当前 runtime 不消费这个 cron，发现补池由轮询、候选池缺口、行为阈值和下方策略间隔驱动。插件与桌面 Web 设置页均不再暴露该字段，只能通过手改 `config.toml` 保留 |
| `pool_target_count` | int | `300` | 前端真实可换候选目标；允许范围 `1..600`。`count_pool_candidates()`（含预生成 / 分类 / 可打开 / 最近看过过滤 / topic window）达到目标时 refresh（含 `force_refresh`）返回 `pool_at_cap` 不再 discover；后台定时 refresh 采用约 90% 的低水位，略低于目标时不立即跑 discovery，等库存真正低于水位再补货。raw 素材库存由独立 raw ceiling `max(pool_target_count * 2, pool_target_count + 120)` 控制，不再被压成与可换目标相同 |
| `account_sync_interval_hours` | int | `6` | 账户侧长期信号同步间隔；运行时会低频拉取 history / favorites / following |
| `refresh_check_interval_seconds` | int | `60` | `ContinuousRefreshController` 主循环轮询间隔；小于 `15` 或无法解析时回退默认值 |
| `signal_event_threshold` | int | `6` | 累计多少条新行为事件后触发 `search + related_chain` 补池；小于 `1` 时回退默认值 |
| `trending_refresh_hours` | int | `3` | `trending` 策略的最小刷新间隔；小于 `1` 时回退默认值 |
| `explore_refresh_hours` | int | `12` | `explore` 策略的最小刷新间隔；小于 `1` 时回退默认值。统一关键词 planner 复用同一时钟：当该间隔已到或距到期不足一个 `refresh_check_interval_seconds`，且 B 站仍有补货空间时，会把探索 query 生成合并进当轮关键词调用 |
| `discovery_limit` | int | `30` | 单轮 discovery wave 的候选上限；允许范围 `1..60` |
| `delight_queue_limit` | int | `20` | 惊喜推荐队列默认加载数量；允许范围 `1..100`。桌面 Web、移动 Web 和浏览器插件默认调用 `/api/delight/pending-batch` 时共享该值，显式 query `limit` 可临时覆盖 |
| `proactive_push_interval_seconds` | int | `120` | 主动推荐 / probe 推送循环间隔；小于 `30` 时回退默认值 |
| `speculator_idle_interval_minutes` | int | `30` | `ProfileUpdatePipeline` 空闲时检查猜测兴趣生命周期的间隔；小于 `5` 时回退默认值 |
| `profile_consolidation_enabled` | bool | `true` | 是否启用 12 小时画像整理（LLM 合并重复的喜欢 / 讨厌主题，见 soul 模块 `ProfileConsolidator`） |
| `profile_consolidation_interval_hours` | int | `12` | 画像整理的最小间隔（小时）；输入未变化（digest 相同）且 active likes 未超过库存上限时该轮零 LLM 调用 |
| `profile_consolidation_like_target_upper` | int | `512` | active likes 目标上限；超过该值时整理会临时使用 full boundary，并在合并后尝试归档低权重长尾 |
| `profile_consolidation_like_target_soft` | int | `450` | active likes 整理水位；归档开启时会尽量把 active likes 降到该值（实际使用 `min(soft, upper)`） |
| `profile_consolidation_archive_enabled` | bool | `true` | 合并后仍超过上限时，是否把低权重、非用户保护的兴趣移入 `archived_interests` |
| `speculation_interval_minutes` | int | `10` | 猜测兴趣推测的运行间隔（分钟） |
| `speculation_ttl_days` | int | `3` | 猜测兴趣的默认存活天数 |
| `speculation_cooldown_days` | int | `7` | 猜测兴趣被否定后的冷却天数 |
| `speculation_confirmation_threshold` | int | `3` | 需要多少次正向信号确认猜测兴趣 |
| `speculation_max_active` | int | `5` | 最多同时活跃的猜测兴趣数 |
| `speculation_max_primary_interests` | int | `15` | 主要兴趣域的最大数量 |
| `speculation_max_secondary_interests` | int | `60` | 次要兴趣域的最大数量 |
| `avoidance_speculation_interval_minutes` | int | `10` | 不喜欢领域探针生成间隔（分钟），与正向兴趣探针独立 |
| `avoidance_speculation_ttl_days` | int | `3` | 不喜欢领域探针默认存活天数 |
| `avoidance_speculation_cooldown_days` | int | `7` | 不喜欢领域探针被否认或过期后的冷却天数 |
| `avoidance_speculation_confirmation_threshold` | int | `3` | 自动确认不喜欢领域所需显式负向信号数；用户直接确认不受此阈值限制 |
| `avoidance_speculation_max_active` | int | `5` | 最多同时活跃的不喜欢领域探针数，不占 `speculation_max_active` |
| `auto_update_enabled` | bool | `false` | 是否启用后端自动检查并应用新版本；默认关闭，只影响后端源码，不更新浏览器插件 |
| `auto_update_check_interval_hours` | int | `6` | 后端自动更新检查间隔（小时）；手动检查不受该间隔限制 |
| `auto_update_allow_prerelease` | bool | `false` | 是否允许 `backend-vX.Y.Z-rc/beta/dev` 预发布 tag 被后端自动更新选择；默认忽略 |
| `auto_update_allowed_remotes` | list[str] | OpenBiliClaw GitHub HTTPS / SSH | 允许自动更新快进的 `origin` allowlist；按规范化形式比较（`.git` 后缀可选、HTTPS/SSH 拼法等价、大小写不敏感），带 userinfo/credential 的 URL 或未匹配的 remote 以 `untrusted_remote` 拒绝并写日志（含实际 remote 地址）；走 GitHub 镜像克隆的安装把镜像 URL 加入此列表即可 |

> 运行时护栏：
> 即使 `pool_target_count` 设得较高，单次 refresh 里的 discover wave 也由 `discovery_limit` 控制（默认 `30`，最大 `60`），避免一次性把全部缺口都打满。
> 后台 refresh 还会使用约 90% 的可换池低水位；池子只是轻微低于 `pool_target_count` 时不跑 discovery。B 站完整四策略补货在小缺口阶段优先只给 `search + related_chain` 预算，`trending/explore` 延后到更深缺口。
> `pause_on_extension_disconnect` 只约束后端 daemon 自己发起的后台 LLM / embedding 工作；用户手动点击刷新、CLI 显式命令、配置保存和普通读取接口不因为插件离线而被拦截。`runtime-stream` 连接断开由后端 receive-side detector 记录，浏览器 idle disconnect 后不会让 presence 状态卡住。

### `[scheduler.pool_source_shares]`

候选池按平台族做保底配比，默认保存的 share 是 `bilibili:xiaohongshu:douyin:youtube:twitter:zhihu:reddit = 5:1:1:1:1:1:1`。旧配置文件若已有本段但缺少后续新增的平台 key，加载时会自动补齐默认 share（例如 `reddit = 1`）。关闭的平台会保留配置值但在运行时从有效配比中剔除，剩余平台重新归一化吃满 `pool_target_count`；默认安装里小红书 / 抖音 / YouTube / X / 知乎 / Reddit 都关闭，所以默认有效配比只有 Bilibili。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `bilibili` | int | `5` | B 站平台族占比；`search` / `related_chain` / `trending` / `explore` 四个策略统一计入该族 |
| `xiaohongshu` | int | `1` | 小红书平台族占比；`xhs-extension-*` 原始来源统一计入该族 |
| `douyin` | int | `1` | 抖音平台族占比；`dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` 等统一计入该族 |
| `youtube` | int | `1` | YouTube 平台族占比；`yt_search` / `yt_trending` / `yt_channel` 统一计入该族 |
| `twitter` | int | `1` | X (Twitter) 平台族占比；`search` / `feed`（For-You）/ `creator`（账号订阅）三个策略统一计入该族 |
| `zhihu` | int | `1` | 知乎平台族占比；插件 `zhihu-search` / `zhihu-hot` / `zhihu-feed` / `zhihu-creator` / `zhihu-related` 候选统一计入该族 |
| `reddit` | int | `1` | Reddit 平台族占比；插件 / 命令后端 `reddit-search` / `reddit-hot` / `reddit-subreddit` / `reddit-related` 候选统一计入该族 |

运行时会拆分两套 quota：前端可换来源目标用于补货和 `reactivate_under_quota_pool_sources()` 的缺口判断；raw ceiling 来源目标用于 `trim_pool_source_overflow()` / `trim_pool_to_target_count()` 的硬成本边界。小平台低于可换目标时，会优先保护 / 复活它们的候选，但不会超过 raw headroom；任一平台族 raw material 高于 raw ceiling 配额时，才会先压回配额内。B 站低于后台低水位且 `[sources.bilibili].enabled=true` 时，才由 B 站 discovery 补货；小缺口优先 `search + related_chain`，更深缺口再跑 `trending/explore`。抖音低于目标且 `[sources.douyin].enabled=true` 时，后台 `DouyinDiscoveryProducer` 会通过 `DouyinDiscoveryService(cache=True)` 触发 search / hot / feed 补池；YouTube 低于目标且 `[sources.youtube].enabled=true` 时，后台 `YoutubeDiscoveryProducer` 会在独立 loop 中触发 `yt_search` / `yt_trending` / `yt_channel`，主 refresh replenishment plan 不再 inline 调度 YouTube；X 低于目标且 `[sources.twitter].enabled=true` 时，后台 `XDiscoveryProducer` 会在独立 loop 中按预算和源健康触发 `search` / `feed` / `creator` 三个策略补池；知乎低于目标且 `[sources.zhihu].enabled=true` 时，后台 `ZhihuDiscoveryProducer` 会通过浏览器插件按 `source_modes` 触发 search / hot / feed / creator / related 补池；Reddit 低于目标且 `[sources.reddit].enabled=true` 时，后台 `RedditDiscoveryProducer` 默认通过 `rdt-cli` 按 `source_modes` 触发 search / hot / subreddit / related 补 raw candidates；命令后端不可用或显式切到插件后端时，入队 OpenBiliClaw 插件任务。

`openbiliclaw init` 会根据用户是否接入小红书 / 抖音 / YouTube / X / 知乎 / Reddit 写回对应 `enabled`。其中知乎在 `fetch-zhihu` 命令下仍只是事件爬取 smoke；在 guided init 勾选知乎或传 `--yes-zhihu` 时，`bootstrap_events` 会作为首版画像信号参与 `analyze_events()` / `build_initial_profile()`。Reddit 同样支持 guided init：勾选 Reddit 或传 `--yes-reddit` 时，插件读取 saved / upvoted / subscribed subreddit，每个 scope 默认最多 300 条，并把事件纳入首版画像；`fetch-reddit --mode bootstrap` 可单独验证这条事件拉取链路。Bilibili 默认启用，也可在插件设置页或 `config.toml` 里手动关闭。交互式初始化在采集完各平台事件后，会按事件量给出一组推荐比例，用户可确认使用或手动输入。插件设置页也可开关七个平台、编辑七个平台占比，并通过 `/api/config/source-share-suggestion` 按已有事件重新生成建议值；GET 使用已保存配置，POST 可接收设置页当前尚未保存的 `enabled_sources` / `configured_shares`。

### `[discovery]`

**统一关键词规划器 / Discover 背压 / 评估输入**（`DiscoveryConfig`）。把"每平台各自定时调 LLM 生成搜索词"换成**缺口拉动的双缓冲背压模型**：一个关键词存储（cache + 历史 + 产出）夹在「生成」与「抓取」之间，生成只在缓存见底且池子有真实缺口时触发（一次合并 LLM 调用覆盖所有缺货平台，带历史去重 + 池子分布避让）。B 站 explore 方向也复用这条关键词存储：到达 `[scheduler].explore_refresh_hours` 的 refresh plan 窗口且 B 站有补货空间时，planner 会把 `explore_domains` 合并进同一次关键词生成，而不是新增配置项或单独 caller。同一段也承载 discovery evaluator 的可选封面图输入开关。模型选择统一来自 `[models.chat]` 的全局 ordered route；本段只负责规划器 / 背压 / 评估输入调参，不再存在 discovery 专属 Provider override。完整设计见 [`docs/plans/2026-06-14-discover-backpressure-refactor-design.md`](../plans/2026-06-14-discover-backpressure-refactor-design.md) §6 参数表。

> ✅ `unified_keyword_planner_enabled` **v0.3.124 起默认 `true`**：搜索词走统一规划器 + 关键词存储，本段其余字段随之生效。设为 `false` 可逐字回退到旧的逐平台搜索词生成路径（旧路径保留、回退无副作用）。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `unified_keyword_planner_enabled` | bool | `true` | 统一关键词规划器总开关（v0.3.124 起默认 `true`）。`true` = 走 planner + 关键词存储；`false` = 回退旧逐平台搜索词生成。其余字段仅在 `true` 时生效 |
| `kw_cache_high` | int | `30` | 每平台关键词缓存高水位；生成补到这个数。小于 `1` 或无法解析时回退默认值 |
| `kw_cache_low` | int | `10` | 每平台关键词缓存低水位；`pending < low` 且有真实缺口时触发生成。小于 `1` 时回退默认值 |
| `gen_batch` | int | `30` | 单平台单次合并 LLM 调用生成的关键词数。小于 `1` 时回退默认值 |
| `fetch_batch` | int | `5` | 单次原子领取（claim）的关键词数。小于 `1` 时回退默认值 |
| `history_window_size` | int | `150` | 去重窗口大小：最近最多这么多个关键词作为"别再出"喂给 planner。小于 `1` 时回退默认值 |
| `history_window_hours` | int | `48` | 去重窗口时长（小时），与 `history_window_size` 配合滚动过期。小于 `1` 时回退默认值 |
| `claim_lease_minutes` | int | `10` | 领取租约（分钟）：`claimed`/`executing` 超过这个时长未变会被回收成 `pending`，防 loop / 任务崩溃泄漏在途行。小于 `1` 时回退默认值 |
| `planner_poll_seconds` | int | `120` | 关键词规划器轮询间隔（秒）；空闲轮询近似零成本。小于 `1` 时回退默认值 |
| `plan_ttl_hours` | int | `12` | 兜底失效（小时）：即便画像 `profile_kw_digest` 未变，`pending` 关键词超过这个时长也会过期；同画像、同平台需求块、同池子避让提示的 merged keyword 生成结果也按这个 TTL 在进程内复用。小于 `1` 时回退默认值 |
| `admission_min_score` | float | `0.60` | 普通推荐池统一入池最低分。候选行 / raw payload 显式 `score_threshold` 可作为策略阈值覆盖；来源标签如 `admission_policy="observed"` 不能绕过该分数门。探索类策略可略低于该值，但平台 / 插件来源不能获得特权。必须在 `(0, 1]` 内，非法值回退默认值 |
| `candidate_eval_concurrency` | int | `3` | 候选 LLM 评估的期望 worker 数，合法范围 `1..3`；每个 worker 最多 30 条，因此总 raw 在途上限为 90。超出范围的手工 TOML / API 值按本段既有整型规则回退默认 `3`。有效值为 `min(本值, max(1, models.chat.concurrency-1))`，为聊天等交互保留一个全局 LLM 槽位；插件与桌面 Web 设置页可修改，CLI `config-show` 自动显示。移动 Web Models 的 Runtime tab 只编辑 Chat concurrency/timeout，不暴露该 discovery 字段。 |
| `inspiration_search_enabled` | bool | `false` | 是否启用 query inspiration 脑暴阶段。开启后 `KeywordPlanner` 会通过本机 mcporter 搜索 provider 链获取搜索预览，再让 `discovery.keyword_inspiration` LLM caller 做 Profile Curator / Detail Expander，最终把带 `aspect_id/inspiration_id/expansion_id` 元数据的关键词写入 `discovery_keywords` |
| `inspiration_search_backends` | list[str] | `["local_cache", "platform_sources", "exa", "you"]` | query inspiration 搜索后端顺序。`local_cache` 会先从本地 `content_cache` 抽取相关标题 / URL / 摘要作为 evidence，本地命中不消耗外部 grounding 预算；证据不足时才 fallback。`platform_sources` 会从用户已启用且当前可同步/可注入 bridge 的平台源里抽样做 inspiration-only grounding（B站 / YouTube / X / Reddit；抖音 direct client；小红书 / 知乎 bridge 可用时），只把标题 / URL / 摘要作为灵感证据，不写候选池；`exa` 调用 `mcporter call exa.web_search_exa`；`you` 调用 `mcporter call you.you-search`（You.com Free MCP profile）。某个后端报错 / 限流 / 返回空结果时会继续尝试后面的后端。远端 MCP server 需要先写入本机 `config/mcporter.json` |
| `inspiration_replace_merged_keywords` | bool | `false` | 实验性替换模式。仅在 `inspiration_search_enabled=true` 且 inspiration provider 可用时生效：due 平台跳过旧 `discovery.keyword_planner` merged call，只通过 search-backed inspiration flow 产词；当 B 站 explore 到期且有补货空间时，也会用同一轮共享 brainstorm / grounding stage 写入 `keyword_kind="explore"` 的探索词池。开 replace 前应先用 `keyword-inspiration-report` 跑 cohort 门禁，避免无质量数据直接替换 |
| `inspiration_breadth` | str | `"high"` | 探索广度档位（Phase 2 config 收敛，13→4）：`low` / `medium` / `high`。旧的 10 个 `inspiration_*` 细粒度旋钮已删除，其派生成内部常量的有效值由本档位决定（见下表）。**默认 `high`（更宽的素材/轴/关键词产量）**；`medium` 逐项等于旧的 `_DEFAULT_INSPIRATION_*` 默认值，需与收敛前行为逐项对齐时显式设 `medium`。注意 `high` 会把每轮真实 probe 搜索与 LLM 用量放大（daemon 常驻），成本敏感可设 `medium`/`low`。非法档位（非 `low`/`medium`/`high`）→ 配置错误（`ConfigError`），未设置回退 `high` |
| `multimodal_evaluation_enabled` | bool | `false` | 是否在 discovery batch evaluator 中加入候选封面图。默认关闭；开启后仅当当前 evaluation 路由支持图像输入且候选有 `cover_url` 时使用，否则自动退回纯文本评估 |
| `multimodal_batch_size` | int | `8` | 图文评估 batch 上限。合法范围 `1..12`，超范围回退默认值；纯文本评估仍使用调用方原 batch size |
| `multimodal_image_max_px` | int | `384` | 送入评估器前封面图压缩后的最大边。合法范围 `128..768`，超范围回退默认值 |
| `multimodal_image_quality` | int | `72` | JPEG 压缩质量。合法范围 `40..90`，超范围回退默认值 |
| `multimodal_image_timeout_seconds` | int | `6` | 单张封面抓取与压缩超时秒数。合法范围 `1..20`，超范围回退默认值 |

默认 `[models.chat].concurrency=4`、`[discovery].candidate_eval_concurrency=3`，因此有效候选 worker 为 3，并为对话等交互保留一个总槽。高吞吐本地 profile 还可配合 `[scheduler].pool_target_count=600`、`[scheduler].discovery_limit=60`；把原生 Chat concurrency 显式设为 3 时，后台与有效候选 worker 为 2。该 profile 不改变任何平台 `request_interval_seconds` / `min_interval_minutes`、daily budget、来源 share、raw ceiling 公式或 `admission_min_score`。

> **没有 `fetch_floor` 字段**：抓取最小间隔复用各平台已有的 `min_interval`（小红书 1h / 抖音 30m / YouTube·X 60m / B 站按风控），不在本段重复定义。
>
> **封面图评估能力边界**：当前通过 OpenAI-compatible `image_url` 消息格式发送压缩后的 `data:image/jpeg;base64,...`。`LLMService.supports_image_input()` 只会在当前 evaluation provider / model 明确看起来支持图像时开启；否则开关保持配置值，但运行时按文本 + 标题 / 描述 / 正文 / 标签 / 互动指标评估。
>
> **环境变量覆盖**：本段字段名都是多词键（如 `kw_cache_high`），与 `[scheduler]` 多词字段一样，**不被** 通用 `OPENBILICLAW_SECTION_KEY` 覆盖机制支持——`OPENBILICLAW_DISCOVERY_GEN_BATCH` 会被按 `_` 拆成 `discovery.gen.batch` 而落不到字段上（静默保持默认，不报错）。需要覆盖请直接改 `config.toml`。
>
> 非法 / 缺失 / 超范围的数值字段都会回退到上表默认值（与 `[scheduler]` 数值字段同一套 `_normalize_scheduler_int` 规范化）；`discovery` 写成非表（标量）时整段回退默认。

#### `inspiration_breadth` 档位派生表（Phase 2）

`inspiration_breadth` 一个键派生出下列 9 个内部常量（Task 4 删掉的 10 个旧旋钮里，`inspiration_max_expansions_per_seed` 因 Phase-1 死代码清扫后已无消费者，直接删除、不入派生表）。**发布默认档位为 `high`**；`medium` 列逐项等于旧 `_DEFAULT_INSPIRATION_*` 默认值（表驱动断言强制），需与收敛前行为逐项对齐时显式设 `medium`。注意 planner 内部另有 `selected_interests ≤ 4` 的调用预算 cap，`interest_sample_size` 派生 6 / 8 后有效值仍是 4。

| 内部常量（原 key） | low | medium（=旧默认） | high |
|---|---|---|---|
| `aspect_window_size` | 16 | **32** | 48 |
| `interest_sample_size` | 3 | **6** | 8 |
| `max_probe_searches_per_stage` | 6 | **12** | 20 |
| `platforms_per_probe` | 1 | **2** | 3 |
| `riskcontrolled_probe_budget` | 2 | **4** | 8 |
| `search_pages_per_probe` | 1 | **1** | 2 |
| `search_results_per_query` | 3 | **5** | 8 |
| `max_seeds_per_aspect` | 2 | **3** | 5 |
| `max_keywords_per_platform` | 8 | **12** | 16 |

> **已移除的 10 个键（无兼容 shim，写了也会被忽略）**：`inspiration_aspect_window_size`、`inspiration_interest_sample_size`、`inspiration_max_probe_searches_per_stage`、`inspiration_platforms_per_probe`、`inspiration_riskcontrolled_probe_budget`、`inspiration_search_pages_per_probe`、`inspiration_search_results_per_query`、`inspiration_max_seeds_per_aspect`、`inspiration_max_expansions_per_seed`、`inspiration_max_keywords_per_platform`。`load_config_with_diagnostics()` 会在构建 discovery 段之前扫描 raw `[discovery]`，命中任一移除键就往 `diagnostics.issues` 追加一条"`inspiration_xxx` 已移除，值被忽略，请改用 `inspiration_breadth`"提示（CLI 的"配置提示"面板自然渲染），**不 fail-fast**——值被忽略、其余配置照常加载。

#### `keyword_generation_mode`（搜索词生成模式，UI/API 派生便利层）

配置页（**桌面 Web `/web` 与插件 popup 设置区**）把 `inspiration_search_enabled` / `inspiration_replace_merged_keywords` 两个布尔收成**单一「搜索词生成模式」下拉**（经典 / 混合 / 灵感）。这**不是** `DiscoveryConfig` 新字段——`config.toml` 仍只存这两个布尔（单一真相源）；`keyword_generation_mode` 只是 API 层的派生便利：`DiscoveryConfigOut` 读出它、`PUT /api/config` 把它翻译回两布尔，两端 UI 只见一个下拉。

三档 ↔ 两布尔映射：

| 模式（下拉标签 / option value） | `inspiration_search_enabled` | `inspiration_replace_merged_keywords` | 语义 |
|---|---|---|---|
| 经典 / `legacy` | `false` | `false` | 只用合并关键词生成器 |
| 混合 / `hybrid` | `true` | `false` | 经典 + 叠加 search-backed 灵感轴链路（同时跑两套，**混合最贵**） |
| 灵感 / `inspiration` | `true` | `true` | 完全用灵感轴链路替代经典 |

- **读容忍**：`_derive_keyword_generation_mode(enabled, replace)` 在 `enabled=false` 时一律返回 `legacy`（无论 `replace` 取何值），避免过时的 `replace` 残留干扰显示。
- **写规范化（canonical）**：`PUT /api/config` 收到 `discovery.keyword_generation_mode` 时，每档都**显式写两个布尔**（legacy→`{false,false}`、hybrid→`{true,false}`、inspiration→`{true,true}`），不留 `replace` 残留旧值；`keyword_generation_mode` 本身**从不写入 `config.toml`**（config load 忽略未知 discovery 键，handler 也从不 setattr 它）。
- **非法值 → 422**：`ConfigUpdateIn.discovery` 是裸 dict，Pydantic 不校验嵌套 Literal，故 handler 手动校验，非 `legacy`/`hybrid`/`inspiration` 抛 `HTTPException(422)`。
- **mode 赢冲突**：同一 discovery 更新里若 mode 与显式 `inspiration_*` 布尔同时出现，**mode 赢**——mode 应用块在 discovery 段最后执行，且两个原始布尔本就不在该 handler 的显式白名单里。

### `[saved_sync]`

跨平台原生保存的同步配置契约（`SavedSyncConfig`）。默认值、TOML、配置 API、`config-show` 及桌面 / 移动 Web / 插件设置控件均已接入；平台中立保存 API 会在每次本地保存请求中读取当前热重载值。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `auto_sync_enabled` | bool | `false` | 是否允许把本地收藏自动同步到外部平台。默认关闭，只有用户明确开启后才为后续同步服务提供启用信号；本字段本身不执行同步 |

`GET /api/config` 返回 `saved_sync.auto_sync_enabled`；`PUT /api/config` 接受同形状的部分更新并保存到 `[saved_sync]`。输入采用 presence-aware 严格布尔校验：省略 `saved_sync` 仍是合法的部分更新；显式传 `saved_sync: null`、`auto_sync_enabled: null`、字符串 `"true"` 或数字 `1` 都返回 422。CLI `openbiliclaw config-show` 会显示解析后的「收藏自动同步」状态。

`false` 时 `POST /api/saved/{list_kind}` 只完成本地保存并返回 `pending`；`true` 时同一路径创建由 runtime registry 跟踪的后台平台任务，但仍立即返回本地成功，不等待 B 站网络。显式 `POST /api/saved/{list_kind}/sync` 是本批账号写入授权，始终无视该开关。旧 `/api/watch-later`、`/api/favorites` 不消费该开关。

### `[storage]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `db_path` | string | `"data/openbiliclaw.db"` | SQLite 数据库路径 |

### `[soul.preference]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `satisfaction_filter_enabled` | bool | `true` | v0.3.x 事件满意度信号：默认开启。偏好分析会在构 prompt 前忽略 `quick_exit` 等被动 negative 事件，保留 positive / neutral / unknown 上下文；`feedback_type=dislike` 或 `reaction=thumbs_down` 的显式负反馈会继续进入分析器，只能作为 `disliked_topics` / 避让证据，不能提取为正向 `interests` |

### `[logging]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `level` | string | `"INFO"` | 控制台日志级别 |
| `file_level` | string | `"DEBUG"` | 文件日志级别 |
| `directory` | string | `"logs"` | 日志目录 |
| `filename` | string | `"openbiliclaw.log"` | 日志文件名 |
| `max_file_size_mb` | int | `100` | 单个日志文件上限（MB），超过即轮转；`0` 禁用轮转 |
| `backup_count` | int | `1` | 保留的历史日志份数；设为 `1` 时总占用封顶 `max_file_size_mb * 2` MB |
| `aggregate_budget_mb` | int | `500` | `logs/` 目录里非托管日志文件的总预算；启动或手动清理时会从最老文件开始删除到预算内，`0` 关闭 |
| `unmanaged_truncate_mb` | int | `200` | 单个非托管日志文件超过该大小时启动时截断到 0，`0` 关闭 |
| `unmanaged_max_age_days` | int | `30` | 非托管日志文件超过该天数时启动时删除，`0` 关闭 |

启动时如果现有日志文件已经超过 `max_file_size_mb`，会被重命名为 `<filename>.1`（覆盖旧的 `.1`）并重新开始写入——这样意外堆积的大日志不会在下次启动时继续增长。运行时到达上限则由 `RotatingFileHandler` 正常轮转：`app.log` → `app.log.1` → `app.log.2` → …，超出 `backup_count` 的旧份自动丢弃。

文件日志使用标准 formatter 写入异常 traceback；`RotatingFileHandler`、plain `FileHandler` 和 `/api/config` 热重载异常路径都有回归测试覆盖，避免 Windows / 非轮转配置下只留下错误摘要而丢失 stack trace。

`GET /api/config` 会额外返回只读字段 `logging.file_path`；`config.toml` 仍只保存 `directory` 和 `filename`。出于 secret-safe 考虑，响应对日志路径做了不可逆脱敏，**永不返回绝对主机路径**：

- 相对 `directory`（如 `"logs"` / `"runtime-logs"`）按原样返回，`file_path` 形如 `<directory>/<filename>`。
- 绝对 `directory`（如 `/srv/private/openbiliclaw/logs`）、`~` 前缀的家目录相对路径、Windows 盘符绝对路径（如 `C:\Users\secret\logs`）、Windows 无盘符根路径（如 `\Users\alice\private\logs`，在 Windows 上会解析到当前盘根目录）与 UNC 路径（如 `\\fileserver\private\logs`）统一脱敏为其最后一段（basename）；根/卷根路径（`/`、`C:\`、`~`、`\`）、裸 `~user` 形式（如 `~alice`）与盘符限定形式（drive-only `C:` / `D:` 与 drive-relative `C:foo` / `C:private\logs`）先剥离盘符再按同一规则脱敏，drive-only 归为空、drive-relative 归为其 basename。`file_path` 只携带脱敏后的目录段与文件名（例如 `logs/backend.log`），不会把 `/srv`、`/Users/<user>`、`~`、`~alice`、`C:`、`C:foo`、`\Users` 或 `\\server` 等主机布局泄露到网络响应。
- 任何含路径分隔符的 `filename`（绝对或相对，如 `/srv/private/backend.log` 或 `srv/private/backend.log`）同样降为 basename；当脱敏后目录为空时，`file_path` 回退为仅文件名，不再产生前导 `/`。

该脱敏只作用于 `GET /api/config` 的 wire 输出；后端在磁盘上写入日志时仍使用 `LoggingConfig.directory_path` / `file_path` 解析的真实绝对路径。插件设置页展示和编辑「完整日志路径」时会在保存前拆回 `directory` / `filename` 两个字段，现有配置文件结构保持兼容。

**安全更新语义**：`PUT /api/config` 采用无歧义协议区分“未修改回显”与“有意编辑”：
- 客户端未修改日志路径时，在 `logging` 负载中回传 `file_path` 且与当前脱敏 wire 形式精确一致，后端识别为 echo 并跳过，因此前端只做 GET→PUT 全量保存不会把绝对路径覆盖成 basename。
- 客户端有意修改日志路径（包括改到与显示 basename 完全相同的相对路径）时，省略 `file_path`、直接提交 `directory`/`filename`，后端直接应用新值。
- 桌面 Web 与插件设置页通过**显式 dirty 标记**区分两种分支：字段的真实 `input` 事件会置位编辑意图（程序渲染不触发 `input`），后端配置渲染进表单（初次加载 / 热重载）与保存成功后复位。因此“改走再改回与显示值完全相同的相对路径”仍被识别为有意编辑并生效，而最终值相等不再被误当成未修改回显。

## 插件设置页覆盖范围

浏览器插件的设置页通过后端 `/api/config` 读取和保存配置。当前 UI 已覆盖常用和高风险易漏项：

- 基础：`language`、`data_dir`、`storage.db_path`
- Models：插件设置页直接读取 `/api/model-config` 与 `/api/model-connection-types`，以列表→详情编辑 Chat/Embedding 有序 route，并独立保存、精确 probe；通用 `/api/config` 保存不包含任何 model 字段或旧 module override。生产 runtime 只消费 `Config.models`
- B 站与多源：`bilibili.browser.*`、`sources.bilibili.enabled`、`sources.browser.*`、`sources.xiaohongshu.*`、`sources.douyin.*`、`sources.youtube.*`、`sources.twitter.*`、`sources.zhihu.*`、`sources.reddit.*`
- 调度：`scheduler.enabled`、`pause_on_extension_disconnect`、`extension_disconnect_grace_seconds`、`pool_target_count`、`account_sync_interval_hours`、refresh / signal / trending / explore / discovery limit / proactive push / speculator idle 等 runtime 频率参数、七个平台 `pool_source_shares`、猜测兴趣参数、不喜欢领域探针参数、自动更新参数；设置页可调用 `/api/config/source-share-suggestion` 按已有事件和当前表单开关填入建议比例
- 日志：控制台 / 文件级别、完整日志路径（保存时拆回 `directory` / `filename`）、轮转与非托管日志清理参数

`[saved_sync].auto_sync_enabled` 已在桌面 / 移动 Web 和插件设置控件中暴露，也可通过 `config.toml` 或严格校验的 `/api/config` 管理。保留但不单独暴露的字段还包括目前只有一个有效值的内部兼容项，例如 `[sources.douyin].mode = "direct"`；保存时插件会继续按当前支持值写回，不会删除其他高级字段。

## `/api/config` 保存与恢复语义

通用设置页和外部调用方继续使用 `/api/config`；模型 route 使用独立的 `/api/model-config`。`GET /api/config` 对普通敏感字段继续执行既有 mask/reveal 规则，但 legacy `llm` 投影中的 Chat/Embedding `api_key` 永远为空，即使传 `reveal_keys=true` 也不会返回模型 secret。该投影同时标记 `authoritative=false`、`read_only=true`、`projection="primary_and_first_fallback"`，只表示第一个 Chat connection 与第一个 fallback；重复类型和更后面的 fallback 不会被折叠进共享 legacy provider bucket。

`PUT /api/config` 只更新请求体里出现的通用字段，并遵循以下安全规则：

- 桌面 Web、移动 Web 与插件 popup 只读 masked `GET /api/config`，不再请求 `reveal_keys=true`；`GET /api/sources/credentials` 不再被任何前端消费，该路由保留在服务端；`/setup/` 向导整体不调用 `/api/config`（其模型与前置检查改走 `/api/model-config` 与 `/api/init-status`）。各表面的凭据输入框都是 write-only：渲染为空，placeholder 按 masked 值显示已保存 / 未保存状态。
- B 站 Cookie、来源 Cookie 与 network proxy 的 masked/empty 表单回显不会覆盖真实值；空凭据输入在请求体中整键省略，绝不把 stored secret 改写为空；模型 credential 不经本接口读写。
- `saved_sync.auto_sync_enabled` 只接受 JSON 布尔值；省略整个段表示“不更新”，但段或字段显式传 `null`、字符串或数字等非布尔输入都由 Pydantic 返回 422，不做 truthy / null 转换。
- 当原生 `[models]` 已生效时，请求中的整个 `llm` 对象和 `reset_fields` 中的 `llm.*` 项都会被忽略；其它通用字段仍正常保存，响应 `warnings` 包含 `model_config_not_updated`。这样旧客户端保存其它设置不会覆盖或重排权威模型 route。
- `reset_fields` 中的 `llm.*` 项与 legacy `llm` payload 一样被忽略并返回 `model_config_not_updated`；其它 reset path 没有通用写入口，返回 400。模型凭据清理必须通过 `/api/model-config` 的 credential action 完成。
- 安装包 `/setup/` 的模型步骤不调用本接口；它先 exact probe 当前 Chat draft，再携带 revision 和 credential action 调用 `PUT /api/model-config`。该页面整体不再调用 `/api/config`：全部请求经同一个有界 `apiFetch` 助手发出，B 站登录态依赖浏览器插件的 Cookie 同步，前置检查改读 `GET /api/init-status`。
- 写盘前会先用 `Config.models` 构建原生 ordered Chat / Embedding route；blocking issue 会返回 400 且不写入 `config.toml`。
- 写盘前会生成 `config.toml.bak`。正常模式下热重载失败会尝试恢复备份，并在响应里设置 `rollback_applied=true`；如果备份恢复也失败，接口返回 500 和人工恢复提示。

`PUT /api/config` 返回 `ConfigUpdateResponse`：

| 字段 | 说明 |
|------|------|
| `ok` | 请求是否完成。校验失败时为 `false`。 |
| `reloaded` | 是否已热重载运行时组件。 |
| `rollback_applied` | 热重载失败后是否已从 `config.toml.bak` 回滚。 |
| `restart_required` | 新配置是否已写入但需要重启 daemon 才能生效。降级模式保存会返回 `true`。 |
| `warnings` | 非致命兼容警告；旧客户端尝试修改原生模型时包含 `model_config_not_updated`。 |
| `config` | 保存后或回滚后的配置快照；模型 credential 永不出现在 legacy 投影。 |
| `message` | 给 UI 展示的人类可读状态。 |

当 daemon 因原生模型 route 构造错误进入降级模式时，`GET /api/config` 会返回 `degraded=true`、兼容值 `degraded_reason="llm_registry_unavailable"` 和 blocking issues；`PUT /api/config` 只保存通用配置且不尝试热重载，返回 `restart_required=true`。模型修复仍经 `/api/model-config`，完成后按响应提示重启 daemon。

## 环境变量

| 变量 | 说明 |
|------|------|
| `OPENBILICLAW_BILIBILI_COOKIE` | 集成测试用 B 站 Cookie |
| `GOOGLE_API_KEY` | Gemini 官方推荐 API Key 环境变量，优先级高于 `GEMINI_API_KEY` |
| `GEMINI_API_KEY` | Gemini 官方兼容环境变量；原生 `gemini_api` 连接可通过该环境变量保留 credential provenance |
| `OPENBILICLAW_PROXY_HOST` | Docker 运行时可选宿主机代理地址，默认 `host.docker.internal` |
| `OPENBILICLAW_PROXY_PORT` | Docker 运行时可选宿主机代理端口，默认 `7897` |
| `OPENBILICLAW_PROXY_TIMEOUT` | Docker 运行时代理探测超时（秒），默认 `1.0` |
| `OPENBILICLAW_DOUYIN_COOKIE` | 抖音 direct-cookie discovery 的显式 Cookie 覆盖；未设置时读取扩展同步的 `data/douyin_cookie.json` |
| `OPENBILICLAW_API_AUTH_ENABLED` | 覆盖 `[api.auth].enabled`（局域网密码门禁总开关） |
| `OPENBILICLAW_API_AUTH_PASSWORD` | 明文访问密码；启动时即 scrypt hash，优先于 `_PASSWORD_HASH`（适合 Docker / 多 worker 注入同一密码） |
| `OPENBILICLAW_API_AUTH_PASSWORD_HASH` | 预生成的 scrypt 密码哈希；覆盖 `[api.auth].password_hash` |
| `OPENBILICLAW_API_AUTH_SESSION_SECRET` | 登录态 HMAC 签名密钥；覆盖 `[api.auth].session_secret`（多进程共用同一密钥） |
| `OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS` | 覆盖 `[api.auth].session_ttl_hours`（0=永不过期） |
| `OPENBILICLAW_API_AUTH_TRUST_LOOPBACK` | 覆盖 `[api.auth].trust_loopback`（本机是否免登录） |
| `OPENBILICLAW_NO_XHS` | 设为 `1` 时永久跳过 `init` 的小红书接入，即使脚本传了 `--yes-xhs` |
| `OPENBILICLAW_NO_DOUYIN` | 设为 `1` 时永久跳过 `init` 的抖音接入，即使脚本传了 `--yes-douyin` |
| `OPENBILICLAW_NO_YOUTUBE` | 设为 `1` 时永久跳过 `init` 的 YouTube 接入，即使脚本传了 `--yes-youtube` |
| `OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS` | `init --yes-xhs` 收集小红书扩展任务结果的最大等待秒数，默认 `180`；`fetch-xhs --wait-seconds` 可覆盖单次 smoke 命令 |
| `OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS` | 小红书 `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用，`fetch-xhs --force` 可绕过单次复用 |
| `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS` | `init --yes-xhs` 的小红书每个 scope 最大滚动轮数，默认 `15` |
| `OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS` | `init --yes-xhs` 的小红书每个 scope 最多采集条目数，默认 `300` |
| `OPENBILICLAW_DY_BOOTSTRAP_WAIT_SECONDS` | `init --yes-douyin` 收集抖音扩展任务结果的最大等待秒数，默认 `180`；`fetch-douyin --wait-seconds` 可覆盖单次 smoke 命令 |
| `OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS` | 抖音 `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用 |
| `OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS` | `init --yes-douyin` 的抖音每个 scope 最大滚动轮数，默认 `15` |
| `OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS` | `init --yes-douyin` 的抖音每个 scope 最多采集条目数，默认 `300` |
| `OPENBILICLAW_YT_BOOTSTRAP_WAIT_SECONDS` | `init --yes-youtube` 收集 YouTube 扩展任务结果的最大等待秒数，默认 `240`；`fetch-youtube --wait-seconds` 可覆盖单次 smoke 命令 |
| `OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS` | YouTube `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用 |
| `OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS` | `init --yes-youtube` 的 YouTube 每个 scope 最大滚动轮数，默认 `10` |
| `OPENBILICLAW_YT_BOOTSTRAP_MAX_ITEMS` | `init --yes-youtube` 的 YouTube 每个 scope 最多采集条目数，默认 `300` |

## Docker 部署说明

使用仓库根目录下的 `docker-compose.yml` 时，默认会挂载：

- `openbiliclaw_config -> /app/runtime`
- `openbiliclaw_data -> /app/runtime/data`
- `openbiliclaw_logs -> /app/runtime/logs`

这意味着：

- 容器启动前不需要宿主机准备 `config.toml`
- 首次启动时会自动在 volume 中生成 `/app/runtime/config.toml`
- `data/` 会持久化 SQLite、画像、Cookie 和运行态文件
- `logs/` 会持久化后端日志，便于排查服务器问题
- 容器内运行时会把 `/app/runtime` 视为项目根目录，因此 `config-show` 中看到的路径应为 `/app/runtime/config.toml` 和 `/app/runtime/data`
- 容器启动时会自动尝试探测 `host.docker.internal:$OPENBILICLAW_PROXY_PORT`；可达时自动注入代理，不可达时直接回退直连
- 容器内每次执行 `openbiliclaw ...` 时也会重复这层探测，因此 `docker exec` 场景不需要额外手动补 `HTTP_PROXY`

如果你修改了 `[general].data_dir` 或 `[logging].directory` 为自定义绝对路径，需要同步调整 Docker volume 的挂载目标路径。

### Docker 最小配置示例

```toml
[general]
language = "zh"
data_dir = "data"

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

[models.embedding]
enabled = true

[models.embedding.settings]
model = "bge-m3"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = false

[[models.embedding.providers]]
id = "ollama-docker"
name = "Docker Ollama Embedding"
type = "ollama"
base_url = "http://ollama:11434/v1"

[bilibili]
auth_method = "cookie"
cookie = ""
```

建议：

- Docker 模式下的首选入口是 `python3 scripts/agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie`；它会确认配置、同步到容器 `/app/runtime`，并自动运行 init
- `docker exec -it openbiliclaw-backend openbiliclaw init` 是高级手动 fallback，用于重复初始化或排查
- 如果缺少 Chat connection credential 或 B 站 Cookie，bootstrap / init 会直接在终端里引导并写回 Docker volume
- ordered route、credential action 的结果和共享 Embedding settings 会写入 `/app/runtime/config.toml`
- B 站 cookie 会写入 `/app/runtime/data/bilibili_cookie.json`
- 首轮 `init` 和后续 `discover` 可能持续几分钟，因为它们会真实访问 B 站和当前 ordered Chat route
- 当前 discover 已启用保守受控并发；默认会并发处理少量 B 站请求和 LLM 评分，但不提供额外用户配置项
- `init` 的首轮补货会按 `search + related_chain -> trending -> explore` 分阶段推进，并尽量把 fresh 候选池补到至少 `100` 条
- 如不方便交互，可使用 `docker exec openbiliclaw-backend openbiliclaw auth login --cookie "..."`

补充：

- `docker compose up -d`、`build`、`down` 这类生命周期命令仍建议在项目目录执行
- 如果不在项目目录，可以显式传 `-f /path/to/docker-compose.yml`
- 如果你使用 Clash Verge 一类本机代理，并且对 Docker 暴露了 HTTP 代理端口，容器无需手动写 `HTTP_PROXY`
- 非交互终端不会进入引导；服务器脚本、CI 或批量部署仍需预置 `config.toml` 和 Cookie
- 如需手动编辑容器内配置，可使用 `docker cp` 导出 `/app/runtime/config.toml`，修改后再复制回去
- 如需彻底清空 Docker 内状态，可执行 `docker compose down -v`
