# Embedding 冷加载健康误报修复设计

## 背景

`GET /api/health`、`GET /api/init-status` 和 `POST /api/init` 当前复用
`api/app.py::_health_embedding_ready()` 的同一个布尔结果。该函数给真实
`EmbeddingService.probe()` 15 秒上限，超时统一写成 `False`。本机实测显示，Homebrew
Ollama 在默认 `OLLAMA_KEEP_ALIVE=5m` 下会卸载 `bge-m3`，而繁忙或冷加载时向量请求可耗时
16–29 秒后仍以 HTTP 200 成功。因此普通健康页会把“本地模型仍在加载”误报为“embedding
未启动”，插件据此反复展示故障横幅。

项目给自己拉起的托管 Ollama 设置 `OLLAMA_KEEP_ALIVE=24h`，但不会也不应修改已经由
Homebrew、官方 App、Docker 或用户自行管理的外部 Ollama。仅依赖 keep-alive 无法覆盖首次
加载、系统唤醒或外部守护进程策略，因此代码仍需正确表达冷加载状态。

## 目标

- 普通 `GET /api/health` 不把本机 loopback Ollama 的 probe 超时误报为服务故障。
- `GET /api/init-status` 和 `POST /api/init` 保持严格：只有真实非空向量返回才允许初始化。
- 明确失败（连接拒绝、404/500、空向量、provider 异常）在所有入口继续报告未就绪。
- 非本机 Ollama 和其他远程 embedding provider 的超时继续报告未就绪，不扩大乐观判断范围。
- 保留现有 TTL、single-flight 和修复诊断接口，不新增对外字段。

## 非目标

- 不接管或修改 Homebrew、官方 App、Docker 等外部 Ollama 的环境变量或生命周期。
- 不把全局 probe 超时简单提高到 30–60 秒，也不让健康接口长期阻塞。
- 不改变 `HealthResponse`、`InitStatusOut` 或插件横幅的响应结构。
- 不改变模型下载、一键修复、watchdog 或 embedding provider 的调用重试语义。

## 方案比较

### A. 原始探测三态 + 分入口解释（采用）

缓存原始结果 `ready / failed / timed_out`，而不是提前压成一个布尔值。普通健康检查只对
loopback Ollama 的 `timed_out` 乐观解释为可用；初始化入口把同一结果严格解释为未就绪。
并发入口仍共享一次真实 probe，但不会把普通健康页的乐观结果污染初始化门禁。

### B. 全局把超时提高到 45 秒

改动较小，但 `/api/health` 最坏会阻塞 45 秒，机器更慢或队列繁忙时仍会复发，也没有解决
普通状态展示与初始化硬门禁语义不同的问题。

### C. 强制所有 Ollama 使用 24 小时 keep-alive

只能减少空闲后的冷加载，无法可靠控制外部 daemon，也无法覆盖首次加载、系统唤醒和模型
切换。越权修改用户管理的服务还会破坏现有 supervisor 的所有权边界。

## 组件与数据流

### 原始探测层

`create_app()` 内保留一套 TTL + `asyncio.Lock` single-flight。真实 probe 完成后记录三态：

- `ready`：provider 返回非空向量；
- `failed`：provider 正常返回失败/空向量，或抛出明确异常；
- `timed_out`：外层 15 秒 `asyncio.wait_for()` 到期，无法在窗口内确认最终结果。

缓存保存原始三态，不保存某个调用入口解释后的布尔值。`ready` 使用现有 30 秒成功 TTL；
`failed` 与 `timed_out` 使用现有 8 秒失败 TTL，以便冷加载完成后快速重探。

### 解释层

内部 readiness helper 接收显式 strict 语义，并按以下矩阵返回布尔值：

| 原始结果 | 普通 `/api/health` | init status / init POST |
| --- | --- | --- |
| `ready` | `true` | `true` |
| `failed` | `false` | `false` |
| `timed_out` + loopback Ollama | `true` | `false` |
| `timed_out` + 其他 provider/远端 | `false` | `false` |

“loopback Ollama”由当前 `[llm.embedding]` 的 provider 和 base URL 判定，复用现有 Ollama
endpoint 归一化/loopback 语义，不把云端 provider 的长时间无响应伪装成健康。

调用点约束：

1. `GET /api/health` 使用普通模式，避免插件故障横幅在冷加载时闪现。
2. `GET /api/init-status` 无论画像是否已存在，都使用 strict 模式；其前置清单只在真实成功时
   显示向量模型可用。
3. `POST /api/init` 临界区复验使用 strict 模式，超时继续返回 409
   `embedding_not_ready`。

### 既有兼容行为

- embedding service 不存在时继续返回 `false`。
- 旧 service 没有 `probe()` 时继续采用“成功构建即就绪”的兼容行为。
- `_diagnose_embedding()`、模型修复进度和 `/api/embedding/repair` 不改变协议；strict init
  收到 `false` 后仍走现有诊断与修复提示。
- health 的乐观解释只改变用户可见横幅，不会写入 embedding 缓存或让业务调用跳过真实请求。

## 错误处理

- `TimeoutError` 单独记录为 `timed_out`，DEBUG 日志明确说明普通健康与 strict init 的解释可能
  不同。
- provider 返回 `False`、空向量或抛出非超时异常均记录为 `failed`，不得乐观放行。
- 三态缓存必须在配置热重载和修复成功时沿用现有失效机制，下一次调用重新探测新 provider。
- 同一时刻 health 与 init 并发时只允许一个 probe；等待者读取相同原始结果后各自解释。

## 测试

采用 TDD，先把当前严格 health 超时测试改写/扩展为失败的目标契约：

1. loopback Ollama probe 超时，`GET /api/health.embedding_ready == true`。
2. 同一 loopback Ollama probe 超时，`GET /api/init-status` 的
   `prerequisites.embedding_ready == false` 且 `can_start == false`。
3. `POST /api/init` 临界区遇到同一超时仍返回 409 `embedding_not_ready`。
4. 远端 Ollama 或非 Ollama provider 超时，普通 health 仍返回 `false`。
5. probe 明确返回 `false` 或抛出异常，普通 health 与 init 都返回 `false`。
6. 超时原始结果使用短 TTL；并发 health/init 共享 single-flight，解释结果互不污染。

定向测试通过后运行 `tests/test_api_app.py`，再执行仓库要求的 Ruff、MyPy 与完整 Pytest。

## 文档范围

- 更新 `docs/modules/runtime.md`，记录三态缓存和 health/init 分入口解释。
- 更新 `docs/modules/init.md`，明确初始化始终使用 strict readiness。
- 在 `docs/changelog.md` 当前版本块加入本次用户可见修复。
- 不新增 API 字段、不改变模块边界或跨模块数据流，因此无需更新架构图、CLI、配置或安装器文档。

## 验收标准

- 外部 Homebrew Ollama 即使在 15 秒内未完成 `bge-m3` 冷加载，普通健康响应也不会触发
  “embedding 未启动”横幅。
- 初始化在真实向量成功前不放行，严格门禁无回退。
- 真正停服、缺模型、坏模型、远程超时和空向量仍能被识别为未就绪。
- TTL 与 single-flight 行为保持，冷加载结束后 8 秒内可重新探测并转为确认成功。
- 定向与完整测试、Ruff、MyPy 全部通过，模块文档和 changelog 与行为一致。
