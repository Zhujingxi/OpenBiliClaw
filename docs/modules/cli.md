# CLI 命令参考

> 所有已实现的 `openbiliclaw` CLI 命令。
>
> 当前 CLI 已统一使用 Rich 输出：
> - 页面标题采用统一标题面板
> - 状态反馈统一为成功 / 警告 / 失败 / 开发中几类状态块
> - 推荐列表使用卡片式展示
> - 用户画像使用分区块展示

## 全局选项

```bash
openbiliclaw [--log-level DEBUG|INFO|WARNING|ERROR] <命令>
```

## 命令一览

| 命令 | 说明 | 状态 |
|------|------|------|
| `config-show` | 显示当前配置、ordered routes 与脱敏凭据来源 | ✅ |
| `models list` | 按优先级列出 Chat / Embedding route、共享设置与迁移状态 | ✅ |
| `models add --kind chat\|embedding` | 按稳定 ID 添加 Chat connection 或 Embedding provider | ✅ |
| `models edit <id>` | 按稳定 ID 编辑连接及其凭据动作 | ✅ |
| `models remove <id>` | 删除连接；最后一条 Chat connection 受保护 | ✅ |
| `models move <id> --position <1-10>` | 在所属 route 内调整一项的 one-based 优先级 | ✅ |
| `models probe <id>` | 精确探测一项，不触发 fallback | ✅ |
| `health-check` | 检查 LLM Provider 可用性 | ✅ |
| `cost` | 按 ordered connection、Provider、日期或 caller 汇总本机 LLM token 与估算成本 | ✅ |
| `auth login` | 设置并验证 B 站 Cookie | ✅ |
| `auth status` | 查看认证状态 | ✅ |
| `login codex` | 导入 / 查看 / 删除 Codex CLI 的 ChatGPT OAuth 凭据（实验） | ✅ |
| `browser status` | 检查 agent-browser 安装 | ✅ |
| `browser open <url>` | 通过浏览器打开页面 | ✅ |
| `browser content <url>` | 获取页面文本内容 | ✅ |
| `start` | 启动本地 API 服务 | ✅ |
| `set-password` | 设置 / 修改局域网访问密码（`--disable` 关闭门禁 / `--logout-all` / `--rotate-secret`） | ✅ |
| `ext-key generate` | 生成并保存一个扩展设备访问密钥（明文只显示一次） | ✅ |
| `ext-key enable` | 开启远程扩展设备认证（默认关闭） | ✅ |
| `ext-key disable` | 关闭新会话交换但保留密钥摘要 | ✅ |
| `ext-key list` | 仅列出设备 key ID 和开关状态 | ✅ |
| `ext-key revoke <key-id>` | 撤销设备密钥并立即失效所有现有会话 | ✅ |
| `autostart status` | 查看开机自启动配置、系统注册和平台支持状态 | ✅ |
| `autostart enable` | 注册当前用户登录自启动并写入 `[autostart].enabled=true` | ✅ |
| `autostart disable` | 移除当前用户登录自启动并写入 `[autostart].enabled=false` | ✅ |
| `db-repair` | 检查、备份并修复本地 SQLite 数据库 | ✅ |
| `serve-api` | 启动容器友好的 API 服务 | ✅ |
| `init` | 首次初始化 | ✅ |
| `fetch-douyin` | 单独触发抖音 bootstrap 拉取（不重建画像；默认复用近期任务） | ✅ |
| `fetch-xhs` | 单独触发小红书 bootstrap 拉取（不重建画像；默认复用近期任务） | ✅ |
| `fetch-youtube` | 单独触发 YouTube bootstrap 拉取（不重建画像；默认复用近期任务） | ✅ |
| `fetch-zhihu` | 单独触发知乎事件拉取（默认 smoke；可选写入 memory / 重建画像） | ✅ |
| `fetch-x` | 单独触发 X（Twitter）点赞 / 收藏拉取（服务端 cookie 重放，无扩展任务，不需 daemon；`--dry-run` 只打印不入库） | ✅ |
| `fetch-reddit` | 单独触发 Reddit 插件 bootstrap 或搜索 smoke（默认不写 memory、不重建画像） | ✅ |
| `import-youtube <path>` | 从 Google Takeout 导入 YouTube 历史 / 订阅 / 点赞 | ✅ |
| `setup-embedding` | 打开原生 Embedding 共享设置 / Provider 编辑器 | ✅ |
| `recommend` | 查看推荐 | ✅ |
| `feedback <id> <like\|dislike\|comment\|dismiss>` | 对推荐提交反馈 | ✅ |
| `profile` | 查看用户画像 | ✅ |
| `keyword-inspiration-dry-run` | 真实调用当前 LLM + inspiration 搜索 provider 链，预览关键词生成中间链路，不写关键词池；支持 `--persist-axes` | ✅ |
| `keyword-inspiration-preview` | `keyword-inspiration-dry-run` 的等价别名；支持 `--persist-axes` | ✅ |
| `keyword-inspiration-report` | 输出 inspiration / merged 关键词 cohort 对比和 replace 启用门禁判定 | ✅ |
| `profile-consolidate` | LLM 整理合并画像里重复的喜欢 / 讨厌主题；也支持一级分类词表迁移（默认 dry-run；`--apply` 写入；`--revert <run_id>` 回滚） | ✅ |
| `discover` | 手动触发发现 | ✅ |
| `discover-douyin` | 单独调试抖音 search / hot / feed 内容发现 | ✅ |
| `discover-zhihu` | 单独触发知乎插件搜索 discovery，并把候选写入待评估池 | ✅ |
| `discover-zhihu-hot` | 单独触发知乎热榜 discovery，并把候选写入待评估池 | ✅ |
| `discover-zhihu-feed` | 单独触发知乎首页推荐 discovery，并把候选写入待评估池 | ✅ |
| `discover-zhihu-creator` | 单独触发知乎作者页 discovery，并把候选写入待评估池 | ✅ |
| `discover-zhihu-related` | 单独触发知乎相关内容 discovery，并把候选写入待评估池 | ✅ |
| `discover-reddit` | 单独触发 Reddit 搜索 discovery，并把候选写入待评估池 | ✅ |
| `discover-reddit-hot` | 单独触发 Reddit 热门 discovery，并把候选写入待评估池 | ✅ |
| `discover-reddit-subreddit` | 单独触发指定 subreddit discovery，并把候选写入待评估池 | ✅ |
| `discover-reddit-related` | 单独触发 Reddit 相关内容 discovery，并把候选写入待评估池 | ✅ |
| `search-douyin` | 通过浏览器插件调试抖音搜索召回 | ✅ |
| `chat` | 苏格拉底式对话 | ✅ |
| `delight` | 手动查看当前惊喜推荐候选 | ✅ |
| `probe` | 手动查看并确认猜测兴趣方向 | ✅ |
| `python -m openbiliclaw.integrations.openclaw.cli next-avoidance-probe` | OpenClaw JSON bridge：拉取下一条不喜欢领域探针 | ✅ |
| `python -m openbiliclaw.integrations.openclaw.cli respond-avoidance-probe` | OpenClaw JSON bridge：确认 / 否认 / 多聊避雷探针 | ✅ |

## 详细说明

### `openbiliclaw config-show`

显示当前加载的原生 `Config.models`、ordered Chat connection 列表和共享设置 Embedding route。primary 与 fallback 使用同一种 connection 记录结构，仅由列表位置决定优先级；inline 凭据只显示 `inline`，环境变量只显示变量名，OAuth 只显示引用，不输出 secret 值。

CLI runtime builder 每个进程只取一次缓存的 `RuntimeModelBundle` 来构造 `SoulEngine`：Chat route、Embedding service、usage recorder 与 concurrency gate 都来自该同一 bundle。这样 CLI 的手动 dislike / avoidance 写回与 API/OpenClaw 一样可以执行语义候选池清理，不会因为另建 registry 或漏接 embedding 而降级。
配置概览会直接显示「停止后台 LLM 请求」是否启用、「浏览器断开后暂停」是否启用和当前宽限秒数、「开机自启动」配置 / 系统注册状态、海外网络模式与自定义代理地址，以及默认关闭的「收藏自动同步」解析状态，方便确认实际网络路由和 `[saved_sync].auto_sync_enabled` 是否已经写入后端配置。

代理 URL 若含 `user:password@`，`config-show` 与默认 API 读取共用同一个脱敏器：仅显示 `***@`，保留 scheme、host 与 port 以便诊断，不输出用户名、密码或其 URL 编码形式。

```bash
$ openbiliclaw config-show
当前配置概览
配置项
  收藏自动同步  关闭
模型路由
```

`config-show` 只读取并展示配置，不创建保存任务，也不会执行平台账号写入。当前没有默认执行
原生保存写入的 CLI smoke；Bilibili `favorite` / `watch_later` 的真实 E2E 通过平台中立
`/api/saved/*` 明确选择命名 BV ID，并且必须先取得当次用户授权或使用测试账号。

### `openbiliclaw models`

统一模型路由编辑器直接调用 `ModelConfigService`，与桌面、插件和移动端共享同一 `[models]` schema、connection-type descriptor、稳定 ID、迁移决定、revision 冲突和 credential action 语义；CLI 不再写 legacy `[llm.<provider>]` 或模块 override。

```bash
# 查看 Chat primary/fallback、Embedding providers 与共享向量空间
openbiliclaw models list

# Chat：同一种 compatible type 可以重复添加，ID 必须全局唯一
openbiliclaw models add --kind chat --id deepseek-backup \
  --connection-type openai_compatible --preset deepseek \
  --name "DeepSeek backup" --model deepseek-v4-flash \
  --base-url https://api.deepseek.com --api-key-env DEEPSEEK_API_KEY

# Embedding：Provider 可有多项，但 model/维度/阈值/多模态属于同一共享 settings
openbiliclaw models add --kind embedding --id local-embedding \
  --connection-type ollama --name "Local embedding" \
  --model bge-m3 --base-url http://127.0.0.1:11434/v1 \
  --output-dimensionality 1024 --similarity-threshold 0.82 --no-multimodal

openbiliclaw models edit deepseek-backup --name "DeepSeek fallback"
openbiliclaw models move deepseek-backup --position 1
openbiliclaw models probe deepseek-backup
openbiliclaw models remove deepseek-backup
```

`list` 显示位置、`primary` / `fallback_n` 或 `embedding`、稳定 ID、type/preset、model/共享 model、脱敏 credential source，以及可注入 live runtime 时的 circuit 状态；普通离线 CLI 没有活跃 runtime circuit 时明确显示 `circuit=unknown`。写命令在非 TTY 下不发 prompt：缺少必填的 `--connection-type`、`--preset`、`--name`、`--model`、`--base-url` 或 credential flag 时安全失败；交互式终端才按 descriptor 依次询问。

凭据输入互斥：`--api-key` 写 inline secret（会进入 shell history，自动化优先使用环境变量）、`--api-key-env NAME` 只保存环境变量名、`--credential-ref codex` 只适用于 `codex_oauth`，`models edit` 另有 `--clear-credential`。已有凭据默认使用 `keep`，公开草稿中的 inline 值不会被读回或写成遮罩/占位符。`models` 命令组会在 Click/Typer 解析前把自身作用域内的 inline key 替换为一次性 handle；命令选择会按 Click 语义消费子命令前至多一个 group-level `--`，随后按有效子命令的真实 option 参数识别 credential，leaf command 内独立 `--` 后的参数仍保持字面值。已被其它 option 消费的 `--api-key=...` 形值同样不会误清理。真实分离写法中紧随 `--api-key` 的 token 始终是值，即使它以 `--` 开头；未知子命令则保守扫描到独立 `--` 为止。子命令拼写错误、参数校验提前退出或 sanitizer 中断都会回写脱敏 argv 并清理 partial handle；原生 `args=None` 路径仍由 Click 执行平台参数展开，顶层其它命令的同名 option 不属于该作用域。

每次保存携带读取到的 revision；若并发修改使 revision 过期，CLI 只在最新 ordered route 上按同一稳定 ID rebase 一次，第二次冲突即停止。legacy `[llm]` 有 blocking migration issue 时，`models list` 会显示 issue ID 与允许动作；非交互式写入用可重复的 `--resolve ISSUE=ACTION[@POSITION]` 完成封闭决定。`models probe ID` 只捕获并探测该 ID（Embedding 同时绑定完整共享 settings），网络期间不持配置锁，完成后再检查 revision；stale 结果被丢弃并最多重试一次，不走 route fallback。

### `openbiliclaw health-check`

逐个检查原生 ordered Chat connection 的连通性；展示稳定 connection ID，探测不会改变 route 顺序。

```bash
$ openbiliclaw health-check
Provider 健康检查
  openai (default): 可用
  deepseek: 可用
  ollama: 不可用
    原因: connection refused
```

### `openbiliclaw cost`

按连接、Provider/model、日期与 caller 汇总本机 `llm_usage`。默认显示全部视图；`--by connection` 可直接确认 primary/fallback 的真实调用量和成本，`--by provider` 保留原有汇总兼容性。

```bash
openbiliclaw cost --days 7
openbiliclaw cost --days 30 --by connection
openbiliclaw cost --by caller
```

连接视图展示 route position、稳定 connection ID、type/preset、model、调用数与估算成本。升级前的历史行显示为 `(legacy)`，不会被错误归入当前 primary。

### `openbiliclaw auth login`

交互式或非交互式设置 B 站 Cookie。验证通过后才保存。

```bash
# 交互式
$ openbiliclaw auth login
请输入 B 站 Cookie: SESSDATA=abc; bili_jct=xyz
登录成功
  用户名: alice
  UID: 10086

# 非交互式
$ openbiliclaw auth login --cookie "SESSDATA=abc; bili_jct=xyz"
```

### `openbiliclaw auth status`

检查当前保存的 Cookie 是否有效。

```bash
$ openbiliclaw auth status
认证概览
认证信息
  状态: 已认证
  用户名: alice
  UID: 10086
```

### `openbiliclaw keyword-inspiration-dry-run`

真实跑一轮 query inspiration 关键词生成，但不写入 `discovery_keywords`。`openbiliclaw keyword-inspiration-preview` 是同一命令的等价别名。命令会临时启用 inspiration preview，读取当前 Soul 画像、`config.toml` 的 discovery LLM 路由和搜索 provider 链（默认 local cache / 已启用平台源 / Exa / You.com），输出 JSON report。平台源只做灵感 grounding，不写候选池；被抽中的二级兴趣会写入独立的 preview selection scope，用于连续 preview 验证兴趣冷却轮转，不影响正式 production 抽样：

```bash
$ openbiliclaw keyword-inspiration-dry-run --platform bilibili --platform reddit --kind regular --limit 6 --interest-limit 4
$ openbiliclaw keyword-inspiration-preview --platform bilibili --persist-axes
```

输出包含：

- `selected_secondary_interests`：本轮从 like / accepted / profile-backed 兴趣里抽到的二级兴趣；
- `brainstorm_branches`：由轴库、二级兴趣标签和 pooled terms 确定性生成的 grounding probe query（字段名保留兼容旧 report）；
- `grounding_records`：搜索预览抽到的具体实体 / 社区词 / 证据标题；
- `grounding_ledger`：本轮 grounding 搜索次数、平台源命中分布、cooldown / risk budget / timeout，以及 Exa / You.com 等 fallback provider 的成功、失败、空结果和补充次数；
- `platform_keywords`：按平台生成并通过 quota / explore 校验后的最终搜索词；
- `materialize_telemetry`：coverage-first 装配过程中的 `deterministic_fill`、`coverage_shortfall`、硬闸拒绝和软分分布；
- `rejected_reasons`：按平台保留的硬闸拒绝明细；preview report 会继续过滤 `platform_style_mismatch`，因为平台 style 已改为软分，不再硬拒绝。

`--limit`（每平台关键词上限）和 `--interest-limit`（二级兴趣样本数）是**本次 preview 的一次性覆盖**（Phase 2 config 收敛后语义）：inspiration 的细粒度参数不再是 `config.toml` 字段，而是由 `[discovery].inspiration_breadth` 档位（默认 `medium`）派生成一个内部参数对象；不传这两个 flag 时该对象来自 `derive(breadth)`，传了则在派生对象上套一次性覆盖（`max_keywords_per_platform` / `interest_sample_size`），经 planner / pipeline 构造注入，**不写回 `config.toml`、不改四个兼容委托的签名**，用户可见行为与收敛前一致。真实画像很大时建议先用 `--interest-limit 2..4` 做 smoke，再放大窗口观察多样性。`--persist-axes` 会把本次 LLM 返回的新轴写入 / 合并到 `discovery_inspiration_axis`，但不增加 axis 使用计数，也不写关键词池；不传时 preview 只读轴库和 selection ledger。preview 永不触发 yield 回填 / 生命周期迁移（观测不改变被观测系统）。regular + explore 同轮触发时，runtime 会共用同一批 selected interests、grounding evidence 和单次 `discovery.keyword_inspiration` 输出；preview 单独预览指定 `--kind`。

### `openbiliclaw keyword-inspiration-report`

读取本地 `discovery_keywords`、`discovery_keyword_yield`、`content_cache` 和 `discovery_interest_selection_ledger`，按 `inspiration_id` 溯源把关键词分成 `inspiration` 与 `merged` 两组，输出认领率、每个被认领关键词的入池数、平均 delight、topic 多样性、production / preview 二级兴趣抽中分布和 replace 启用门禁：

```bash
$ openbiliclaw keyword-inspiration-report --window-days 14
```

报告内会同时输出本次使用的阈值。默认门禁要求：窗口至少 14 天、inspiration 组至少 200 个被认领关键词、准入率不低于 merged 的 `0.8x`、平均 delight 不低于 merged 的 `0.95x`，且 topic 多样性严格更高。未通过时不要开启 `[discovery].inspiration_replace_merged_keywords=true`，应只修改一个可测因素后继续附加模式观察。

### `openbiliclaw login codex`

管理实验性的 Codex OAuth 凭据。该命令不自建 OAuth 流程，而是复用官方 Codex CLI 的登录态：默认读取 `~/.codex/auth.json`，导入到 `~/.openbiliclaw/codex_auth.json`，供原生 `type="codex_oauth"` Chat connection 使用。

```bash
# 默认：先尝试导入 ~/.codex/auth.json；没有时调用官方 `codex login` 后再导入
$ openbiliclaw login codex

# 只导入现有 Codex CLI 凭据
$ openbiliclaw login codex --import

# 从指定路径导入
$ openbiliclaw login codex --import --source ~/.codex/auth.json

# 查看状态；不会显示 token 明文
$ openbiliclaw login codex --status

# 删除 OpenBiliClaw 本地副本，不会删除 Codex CLI 自己的登录态
$ openbiliclaw login codex --logout
```

导入后用模型命令新增或编辑独立 OAuth connection：

```bash
openbiliclaw models add --kind chat --id codex-main \
  --connection-type codex_oauth --name "Codex OAuth" \
  --model gpt-5 --credential-ref codex
```

这是非官方实验路径，OpenAI / Codex CLI 可能随时调整 token 权限或文件格式。`codex_oauth` endpoint 只能是 OpenAI 官方 API；factory 会在读取 token 前校验，避免把 ChatGPT OAuth token 发给第三方代理。

### `openbiliclaw browser status`

检查 agent-browser 是否已安装。

```bash
$ openbiliclaw browser status
浏览器集成状态
浏览器信息
  状态: 已安装
  可执行文件: /usr/local/bin/agent-browser
```

### `openbiliclaw browser open <url>`

通过 agent-browser 打开指定页面。

```bash
$ openbiliclaw browser open https://www.bilibili.com
浏览器已打开
目标地址
  URL: https://www.bilibili.com
```

### `openbiliclaw browser content <url>`

获取指定页面的可见文本内容。

```bash
$ openbiliclaw browser content https://example.com
页面内容
╭─ 页面内容 ─╮
│ Example Domain ... │
╰──────────────╯
```

### `openbiliclaw start`

启动本地 API 服务。默认读取 `config.toml [api]`，新安装默认监听 `0.0.0.0:8420`，方便同局域网手机访问 `/m/`；也支持显式传入 host/port 覆盖配置。

```bash
$ openbiliclaw start

$ openbiliclaw start --host 0.0.0.0 --port 9000
```

适合本地直接运行或调试场景。若只希望本机访问，把 `[api].host` 改为 `127.0.0.1`，或启动时传 `--host 127.0.0.1`。

启动前会先做两件事：

1. 检查 `data/openbiliclaw.db` 是否完整；如果检测到损坏，会拒绝启动并提示先执行 `openbiliclaw db-repair`
2. 在数据库健康且距离上次冷备超过 24 小时时，自动生成一份冷备到 `data/backups/`

数据库健康后、API server 启动前，`start` 还会执行自启动相关的轻量 reconcile：

- 如果当前 Chat / Embedding route 需要 Ollama 且 `[autostart].manage_ollama=true`，startup 使用显式的**单托管 daemon**策略：按 Chat connection 顺序选择第一条 Ollama endpoint；没有 Ollama Chat 时才选择第一条 Ollama Embedding endpoint。只有被选中的默认 `127.0.0.1:11434` 会在未运行时尝试后台执行 `ollama serve`；其余不同 endpoint 需已由外部或专用 desktop owner 管理。Embedding 一键修复不复用这个 Chat-first 选择，而是始终管理被诊断 Embedding provider 的精确 daemon root。
- 如果 `[autostart].enabled=true` 但系统登录项缺失，会在没有环境变量管理风险时重新注册当前用户登录项；发现 `OPENBILICLAW_*` / provider API key 等环境变量覆盖时只告警并跳过，避免注册一个下次登录拿不到配置的启动项。
- 如果 `[autostart].enabled=false` 但系统登录项仍残留，会尝试移除该当前用户登录项，让手动编辑配置后的下一次启动也能回到关闭状态。

如果引导初始化从未完成（soul 层为空的 best-effort 检查，检查失败时保持沉默），`start` 会在 uvicorn 启动前打印一个 WARN 面板，给出 `/setup/` 引导地址和无浏览器环境的 `openbiliclaw init` 替代命令；`serve-api` 打印容器版变体（`/setup/` 只做配置与前置检查 + `docker exec -it openbiliclaw-backend openbiliclaw init`）。

如果 `scheduler.pause_on_extension_disconnect=true`，`start` 会在 uvicorn 启动前打印一行 WARN：

```text
WARN extension presence required; backend will pause background LLM work after grace period if no extension client connects
```

这表示 daemon-owned 后台 LLM / embedding 工作需要浏览器插件保持 `runtime-stream` 在线，或仍处于断开后的宽限窗口内；手动 CLI/API 操作不受这个 WARN 影响。

如果配置导致原生模型 route 无法构建，`start` 不会直接让 popup 完全失联，而是以降级模式启动本地 API，并在 uvicorn 启动前打印 `降级模式 / Degraded mode` 面板。面板会列出兼容 reason `llm_registry_unavailable` 和 blocking issue，并提示打开扩展设置页保存修复配置后重启 daemon。

如果数据库已损坏：

```bash
$ openbiliclaw start
数据库损坏
检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。
```

当前 `start` 不只是提供静态接口，还会顺手启动候选池运行时：

- 监听插件上报的强信号行为
- 在阈值满足时自动刷新推荐候选
- 定时做榜单/探索补货
- 为插件 popup 和 service worker 提供 `/api/runtime-status` 与通知接口

启动后除了现有候选池刷新 loop，还会常驻一个低频账户同步 loop：
- 定期检查观看历史
- 定期检查收藏夹变化
- 定期检查关注 UP 主变化

这些账户侧长期信号会统一转成事件，再进入现有偏好/画像更新链。

当前 `start` 会启动这些接口：

- `GET /api/health`
- `POST /api/events`
- `GET /api/recommendations`

### `openbiliclaw serve-api`

启动更适合 Docker / 脚本调用的 API 服务入口。默认监听 `0.0.0.0:8420`。

```bash
$ openbiliclaw serve-api

$ openbiliclaw serve-api --host 0.0.0.0 --port 8420
```

推荐容器内使用该命令作为启动入口。
当 `scheduler.pause_on_extension_disconnect=true` 时，`serve-api` 与 `start` 一样会在 uvicorn 启动前打印 extension presence WARN，提醒容器后端若没有插件客户端连接，后台 LLM 工作会在宽限期后暂停。
当配置进入降级模式时，`serve-api` 也会打印同一张 `降级模式 / Degraded mode` 面板；容器或脚本可继续通过 `/api/config` 写入修复配置，再重启服务让新 registry 生效。

### `openbiliclaw set-password`

管理局域网 / 远程访问的密码门禁（写入 `[api.auth]`，见 [配置参考](config.md#apiauth) 与 [api-auth 模块](api-auth.md)）。本机（loopback）默认始终免登录，只有手机 / 其他设备走局域网访问时才需要密码。

```bash
# 交互式设置 / 修改密码（自动开启门禁，scrypt 落盘，首次启用生成签名密钥）
$ openbiliclaw set-password
设置访问密码: ********
确认: ********
已设置局域网访问密码

# 关闭密码门禁
$ openbiliclaw set-password --disable

# 立即让所有设备登录态失效（不改密码 / 密钥）
$ openbiliclaw set-password --logout-all

# 轮换会话签名密钥（最强撤销，需重启后端生效）
$ openbiliclaw set-password --rotate-secret
```

选项：

- 无参数：交互式设置或修改密码（需交互式终端；非交互场景用 `OPENBILICLAW_API_AUTH_PASSWORD` 环境变量）。设置成功会顺带启用门禁。
- `--disable`：关闭门禁（`enabled=false`），重启后端后生效。
- `--logout-all`：自增 SQLite `auth_state` 的 `auth_epoch`，使此前签发的全部登录态（含被复制 / 嗅探走的 token）立即失效，所有设备需重新登录。
- `--rotate-secret`：轮换 `session_secret` 并撤销所有登录态；新密钥需重启后端进程才完全生效。

> 改密码（无论走本命令、`init`、直接改 TOML、env、还是 `PUT /api/config`）都会在下次启动 / 重载时按密码指纹变化自动撤销旧登录态。永不过期（`session_ttl_hours=0`，「记住登录」）的会话不会因重启被误撤销。

### `openbiliclaw ext-key`

管理跨设备浏览器扩展的设备访问密钥。配置只保存密钥摘要；完整密钥只在生成时显示一次，由用户填入目标扩展的设置页。该能力默认关闭。

```bash
# 生成密钥（完整密钥只显示一次，总开关仍关闭）
$ openbiliclaw ext-key generate
设备访问密钥已生成
  Key ID: a1b2c3d4e5f6
  obc_ext_a1b2c3d4e5f6.<secret>

# 至少有一个密钥后显式开启
$ openbiliclaw ext-key enable

# 暂停签发新短会话，保留密钥摘要
$ openbiliclaw ext-key disable

# 只查看 key ID，不打印摘要或 secret
$ openbiliclaw ext-key list

# 撤销设备；同时使全部 Web / 扩展会话立即失效
$ openbiliclaw ext-key revoke a1b2c3d4e5f6
```

子命令：

- `generate`：生成 256-bit 随机 secret，配置只写 `key_id:sha256(secret)`；不会自动开启总开关。
- `enable` / `disable`：控制 `/api/auth/extension-token` 是否签发新短会话，密钥摘要保留。
- `list`：只显示 key ID。
- `revoke <key-id>`：删除一个摘要并提升 `auth_epoch`。若运行库不可写，配置会回滚且命令失败。

所有写命令在 auth 配置受环境变量或 `config.local.toml` 覆盖时拒绝执行，避免显示成功但重启后失效。

### `openbiliclaw autostart`

管理当前用户作用域的登录自启动（macOS LaunchAgent / Windows HKCU Run / Linux XDG autostart）。该命令不写系统级服务，不需要 root / 管理员权限；Docker / 容器和未知平台会拒绝注册。

```bash
# 查看配置意图、系统注册状态和平台机制
$ openbiliclaw autostart status

# 开启：先权威写 [autostart].enabled=true，再注册 OS 登录项
$ openbiliclaw autostart enable

# 关闭：先移除 OS 登录项，再权威写 [autostart].enabled=false
$ openbiliclaw autostart disable
```

`enable` 会拒绝当前进程依赖环境变量管理的配置（例如 `OPENBILICLAW_*`、`GOOGLE_API_KEY` / `GEMINI_API_KEY`、抖音 Cookie env），因为桌面登录会话可能拿不到这些 shell 变量。请先把必要配置写入 `config.toml`。

CLI 与 API 使用同一套方向化事务规则：开启时写配置成功且未被 `config.local.toml` 覆盖后才注册 OS；关闭时先注销 OS，再写配置。任一步失败都会尽量把配置和 OS 注册恢复到操作前状态。

### `openbiliclaw delight`

手动查看当前可推送的惊喜推荐候选。

```bash
$ openbiliclaw delight
惊喜推荐
【意外契合】阿B 觉得这条你会意外喜欢
  标题: ...
  惊喜分: 0.72
  理由: ...
```

行为说明：

- 先补一次 delight backlog，再从当前池子里取一条“文案已就绪”的候选
- 运行时与 CLI 共用同一套 delight 阈值口径：默认 `0.70`
- 如果当前只有分数、还没生成 `reason/hook`，CLI 不会把它当成可展示候选

### `openbiliclaw probe`

手动列出当前最值得确认的猜测兴趣方向，并支持确认 / 否认 / 多聊聊。

```bash
$ openbiliclaw probe
猜测兴趣方向
1. 城市空间叙事
2. 复杂系统
```

### OpenClaw JSON bridge: avoidance probes

不喜欢领域探针目前通过 OpenClaw bridge 暴露，而不是新增顶层 `openbiliclaw` 命令。它返回稳定 JSON，供 OpenClaw / Codex / Claude Code 等 agent 调用。

```bash
$ uv run python -m openbiliclaw.integrations.openclaw.cli next-avoidance-probe
{"ok": true, "data": {"probe": {"domain": "浅层热点复读", "question": "..."}}}

$ uv run python -m openbiliclaw.integrations.openclaw.cli respond-avoidance-probe \
  --domain "浅层热点复读" \
  --response confirm
{"ok": true, "data": {"ok": true, "action": "confirmed", "domain": "浅层热点复读"}}
```

`respond-avoidance-probe --response` 支持：

- `confirm`：用户确认“不喜欢 / 需要避开”，后端写入 `preference.disliked_topics`，同步 soul layer，并触发候选池清理。
- `reject`：用户否认“不排斥这个方向”，只进入 cooldown 和反馈历史，不写画像。
- `chat`：进入带 `avoidance_probe` scope 的上下文对话；明确确认或否认的聊天会转成对应反馈。

`listen` 默认转发 `delight.candidate`、`interest.probe` 和 `avoidance.probe`：

```bash
$ uv run python -m openbiliclaw.integrations.openclaw.cli listen
{"ok": true, "data": {"type": "avoidance.probe", "domain": "浅层热点复读", "...": "..."}}
```

### `openbiliclaw profile`

展示当前灵魂画像。若画像尚未初始化，会明确提示后续执行 `openbiliclaw init`。

```bash
$ openbiliclaw profile
用户画像概览
人格描述
这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人……

核心特质
  理性、谨慎、自驱

价值观
  成长、真实

当前阶段
  稳定积累阶段

深层需求
  被理解、持续成长
```

### `openbiliclaw profile-consolidate`

用 LLM 整理合并画像里重复的喜欢 / 讨厌主题。兴趣标签和避雷主题会不断积累措辞变体（「智能体开发」vs「智能体开发与实现」），把进入 prompt 的 top-64 名额挤占掉；本命令按「规则合并 → embedding 聚类 → LLM 裁决 → 校验执行 → active 库存归档」流水线做同义合并，默认整理 likes 权重 top-512 + 全量避雷主题。后台默认每 12 小时自动跑一轮（见 `[scheduler].profile_consolidation_*`），本命令用于手动触发与预览。

```bash
$ openbiliclaw profile-consolidate            # dry-run：只打印建议
$ openbiliclaw profile-consolidate --apply    # 写入；自动备份 + soul_changelog.md 审计
$ openbiliclaw profile-consolidate --migrate-categories          # dry-run：预览分类 → 词表映射
$ openbiliclaw profile-consolidate --migrate-categories --apply  # 写入分类迁移；自动备份
$ openbiliclaw profile-consolidate --full           # dry-run：likes 边界开到全量标签库
$ openbiliclaw profile-consolidate --full --apply   # 写入全量二级清理；单 run 可整体回滚
$ openbiliclaw profile-consolidate --revert 20260612-031500   # 按 run_id 回滚
```

要点：

- LLM 只能输出 merge / keep 操作，代码侧校验（members 逐字存在、簇内全覆盖、canonical 禁裸大词）后才执行；任何校验不过整簇放弃
- `--migrate-categories` 是一次性运维入口：LLM 只产出现存分类到 `CATEGORY_VOCAB` 的映射，代码侧强制完整覆盖、目标在词表内、词表内分类恒等；默认 dry-run，`--apply` 后可用同一个 `--revert <run_id>` 回滚
- `--full` 把 likes 整理边界从默认 top-512 开到全量标签库；嫌疑簇按最多 32 个/批送审，所有成功批次汇入一个 run 记录，可整体 `--revert`
- `--full` 与 `--migrate-categories` 互斥；推荐先 `--migrate-categories --apply`，再 `--full --apply`
- active likes 超过 `profile_consolidation_like_target_upper` 时，定时整理会自动临时开 full boundary，并按 `upper -> soft` 水位压力降低 likes embedding 聚类阈值（CLI 输出 `likes 动态聚类阈值`）；合并后仍超上限时，会把低权重且非用户保护的长尾兴趣归档到 `archived_interests`
- dry-run 会显示预计归档数量和库存说明；`--apply` 写入后 run record 可同时回滚 active / archived inventory
- 避雷主题只合真同义、严禁向上泛化（canonical 不得比成员更宽泛）
- 用户在画像编辑里手动 remove/add 的条目会随改名同步（rename map 穿透 overrides），不会被合并「借尸还魂」
- 回滚会把被回滚的合并对记入 no-merge 记忆，下一轮定时整理不会重做同一合并

### `openbiliclaw init`

首次运行编排命令。会顺序执行：

1. 检查运行时 LLM 配置
2. 检查 B 站认证（仅当包含 B 站来源时）
3. 拉取 B 站历史 / 收藏 / 关注（仅当包含 B 站来源时）
4. best-effort 等待插件导入小红书初始化信号
5. best-effort 等待插件导入抖音初始化信号
6. best-effort 等待插件导入 YouTube 初始化信号
7. best-effort 等待插件导入知乎初始化信号
8. 写入事件层并分析偏好
9. 生成初始画像
10. 按阶段自动补首轮内容池

> v0.3.118+：B 站不再是必选基座——`--no-bilibili`（或 `OPENBILICLAW_NO_BILIBILI=1`）可跳过 B 站，
> 但 init **至少需要一个数据来源**：全部来源都关闭时命令直接报错退出（exit 1）。
> 所有所选来源都没拉到任何信号时，流水线以 `empty_signals` 失败。

> v0.3.102+：第 3–9 步的核心抽成共享异步流水线 `cli.run_guided_init`，CLI 用单次 `asyncio.run(run_guided_init(...))` 驱动（交互提示 / 摘要仍在命令里），后端图形化初始化 `POST /api/init` 复用同一协程。CLI 行为 / 输出 / 退出码不变。**也可以不进终端**：插件「推荐」tab 未初始化时直接点「开始初始化」，详见 [init 模块文档](init.md) 与 [extension 模块文档](extension.md)。

安装渠道里的首选路径是 `scripts/agent_bootstrap.py` 自动运行 init：Bash / PowerShell 人类一行安装会先在终端向导里按顺序确认 LLM、embedding、B 站 Cookie 和各来源 opt-in；Docker / AI agent / CI 非交互安装则通过显式 flags 和 `BOOTSTRAP_STATUS` 推进，不会阻塞读 stdin。bootstrap 随后会对默认 LLM provider 与 embedding 服务各做一次轻量真实调用；两者都可用才触发本命令。若 bootstrap 返回 `service_check_failed`，说明 `openbiliclaw init` 尚未运行，应先修 API key / base_url / model / Ollama，再重跑 bootstrap。直接执行 `openbiliclaw init` 仍保留为高级手动 fallback 和重复初始化入口。

默认初始化信号上限：B 站观看历史最多 500 条、收藏最多 500 条（跨收藏夹总预算，单个收藏夹会按页补齐）、关注 UP 最多 100 人；小红书 / 抖音 / YouTube 的 `bootstrap_profile` 每个 scope 默认最多 300 条；知乎 `bootstrap_events` 的浏览历史、收藏夹条目、动态点赞、动态收藏四个分支默认各最多 300 条；Reddit `bootstrap_events` 的 saved、upvoted、subscribed 三个分支默认各最多 300 条。交互式 `init` 会让用户确认 B 站收藏 / 关注上限，收藏回车使用 500、关注回车使用 100；脚本化场景可传 `--bilibili-favorite-limit N` / `--bilibili-follow-limit N`，传 `0` 表示跳过对应信号。

模型配置重构后，交互式 `init` 缺少可用 route 时依次进入原生 Chat 与 Embedding 编辑器：先选择 connection type，再选择该类型的 preset / OAuth，最后只询问 descriptor 对当前 capability 生效的字段。Chat 引导生成一条 connection；Embedding 引导编辑一套共享 settings，并添加、编辑或关闭 Provider 列表。该路径直接提交 `[models]`，不再写 legacy `[llm]`、不再询问 per-module override，也不会在配置阶段自动安装、启动或探测真实 Provider。

```bash
$ openbiliclaw init
初始化 OpenBiliClaw
1/4 拉取数据
  浏览历史 500 条 / 收藏 128 个 / 关注 43 人
  小红书 收藏 20 个 / 点赞 20 个 / 浏览记录 0 个
  抖音 发布 24 条 / 收藏 13 个 / 点赞 12 个 / 关注 1 人
  YouTube 观看历史 40 条 / 订阅 12 个 / 点赞 20 个
  知乎 浏览 80 条 / 收藏 42 条 / 点赞 16 条
2/4 分析偏好
3/4 生成画像
4/4 发现内容
补货阶段 1/3: search + related_chain
当前池子 0/100，本轮请求上限 100
阶段完成: 当前池子 28/100，本轮发现 18 条
补货阶段 2/3: trending
当前池子 28/100，本轮请求上限 72
阶段完成: 当前池子 104/100，本轮发现 76 条
初始化完成
初始化摘要
  B 站观看历史: 500 条
  小红书 入库事件: 40 条
  抖音 入库事件: 50 条
  YouTube 入库事件: 72 条
  知乎 入库事件: 138 条
  画像建模总事件: 590 条
  灵魂画像: 已生成
  首轮发现内容: 94 条
  本次画像综合了 428 条 B 站信号 + 40 条小红书信号 + 50 条抖音信号 + 72 条 YouTube 信号 + 138 条知乎信号。
```

小红书导入依赖浏览器插件在用户已登录的小红书网页里执行 `bootstrap_profile` 任务。后端只入队任务并短暂等待结果，不直接登录或爬取小红书。插件会先定位当前用户 profile，再读取 profile state 里的收藏 / 赞过分组；这里的“浏览记录”指小红书网页自己明确暴露的浏览记录/足迹 state，不是读取 Chrome 浏览器历史，也不会把普通推荐流当成浏览记录。如果后端任务显式设置 `max_scroll_rounds`，插件会按任务 payload 中的 `scroll_wait_ms` 和 `max_stagnant_scroll_rounds` 做有限滚动和停滞判断。如果插件未连接、未登录或页面没有暴露对应 scope，`init` 会继续使用已有 B 站数据完成初始化。

抖音导入同样依赖浏览器插件在用户已登录的 `https://www.douyin.com` 页面里执行 `bootstrap_profile` 任务。后端入队 `dy_tasks`，插件依次访问 `dy_post / dy_collect / dy_like / dy_follow` 四个 scope，content script 结合 DOM、MAIN-world fetch tap 和 API harvester 采集发布 / 收藏 / 点赞 / 关注条目，以 `partial` 批次回写 `/api/sources/dy/task-result`。后端会转换为统一事件：发布 → `view`，收藏 → `favorite`，点赞 → `like`，关注 → `follow`，并带 `metadata.source_platform="douyin"`。`init --yes-douyin` 会把这些事件加入 `analyze_events()` 和 `build_initial_profile()`；插件未连接、未登录或抖音风控返回空数据时，初始化继续使用已有信号完成。后台会复用 6 小时内近期抖音 bootstrap 任务，并用 `source_bootstrap_state.json` 跳过跨任务旧视频 / 关注 identity key。

YouTube 导入依赖浏览器插件在用户已登录的 `https://www.youtube.com` 页面里执行 `bootstrap_profile` 任务。后端入队 `yt_tasks`，插件依次访问 `/feed/history`、`/feed/channels`、`/playlist?list=LL` 三个 scope，读取观看历史、订阅频道和点赞视频，以 `partial` 批次回写 `/api/sources/yt/task-result`。后端会转换为统一事件：观看历史 → `view`，订阅 → `follow`，点赞 → `like`，并带 `metadata.source_platform="youtube"`。`init --yes-youtube` 会把这些事件加入 `analyze_events()` 和 `build_initial_profile()`；非交互式终端默认跳过，`OPENBILICLAW_NO_YOUTUBE=1` 会压过 `--yes-youtube`，避免脚本环境误触发浏览器前台 tab。后台会复用 6 小时内近期 YouTube bootstrap 任务，并用 `source_bootstrap_state.json` 跳过跨任务旧条目。

知乎导入复用 `bootstrap_events` 任务。后端入队 `zhihu_tasks(type="bootstrap_events")`，插件在用户已登录的 `https://www.zhihu.com` 页面里读取最近浏览、收藏夹条目、个人动态点赞和个人动态收藏，以任务结果回写 `/api/sources/zhihu/task-result`。`init --yes-zhihu` 会把同一批任务结果转换为统一事件并加入 `analyze_events()` / `build_initial_profile()`，同时把 `[sources.zhihu].enabled=true` 写回配置。`fetch-zhihu` 默认仍只打印 smoke 计数；需要把本次抓取写入 memory 可显式加 `--write-memory`，需要写入后立即重建画像可加 `--rebuild-profile`。非交互式终端默认跳过知乎，`OPENBILICLAW_NO_ZHIHU=1` 会压过 `--yes-zhihu`。后台会复用 6 小时内近期知乎 `bootstrap_events` 任务；动态点赞和动态收藏各自独立使用单分支上限，不共享额度。

Reddit 导入也复用 `bootstrap_events` 任务。后端入队 `reddit_tasks(type="bootstrap_events")`，插件在用户已登录的 `https://www.reddit.com` 页面里先读取 `/api/me.json` 识别当前用户，再读取 saved、upvoted 和 subscribed subreddit，同步回写 `/api/sources/reddit/task-result`。`init --yes-reddit` 会把 saved → `favorite`、upvoted → `like`、subscribed → `follow` 的统一事件加入 `analyze_events()` / `build_initial_profile()`，同时把 `[sources.reddit].enabled=true` 写回配置；Reddit 可以作为唯一初始化来源，只要真实拉到至少一条信号。后台会复用 6 小时内近期 Reddit `bootstrap_events` 任务；三个分支各自独立使用单分支上限 300。

X (Twitter) 与其它平台不同：init 阶段**没有 bootstrap 导入任务**。X 的发现走服务端 cookie 重放，行为采集走浏览器扩展 MAIN-world tap，两者都在 init 之后才生效，所以 `init --yes-x` **只翻转 `[sources.twitter].enabled = true`**，不会在 init 期间打开 x.com 前台 tab 或拉取数据。启用后，用户登录 x.com → 扩展自动把 `auth_token` + `ct0` 同步到 `data/x_cookie.json` → 后台 `XDiscoveryProducer` 在下一个 refresh tick 按预算补 X 候选。非交互式终端默认跳过 X。

源开关：

- `--no-bilibili`：跳过 B 站数据接入（v0.3.118+，默认包含；至少需保留一个数据来源）。同时把 `[sources.bilibili].enabled` 持久化为 `false`，后台发现也不再跑 B 站。
- `--yes-xhs` / `--no-xhs`：跳过小红书交互式提问，直接启用或跳过。
- `--yes-douyin` / `--no-douyin`：跳过抖音交互式提问，直接启用或跳过。交互式提问默认 No；非交互式终端默认跳过抖音，脚本化 init 应显式传其中一个。
- `--yes-youtube` / `--no-youtube`：跳过 YouTube 交互式提问，直接启用或跳过。交互式提问默认 No；非交互式终端默认跳过 YouTube，脚本化 init 应显式传其中一个。
- `--yes-x` / `--no-x`：跳过 X (Twitter) 交互式提问，直接启用或跳过。只翻转 `[sources.twitter].enabled`，不在 init 期间拉取数据；非交互式终端默认跳过 X，脚本化 init 应显式传其中一个。
- `--yes-zhihu` / `--no-zhihu`：跳过知乎交互式提问，直接启用或跳过。`--yes-zhihu` 会执行 `bootstrap_events` 并把结果纳入本轮首版画像；非交互式终端默认跳过知乎，脚本化 init 应显式传其中一个。
- `--yes-reddit` / `--no-reddit`：跳过 Reddit 交互式提问，直接启用或跳过。`--yes-reddit` 会执行 `bootstrap_events` 并把 saved / upvoted / subscribed 结果纳入本轮首版画像，同时开启后续 Reddit discovery；非交互式终端默认跳过 Reddit。
- `--bilibili-favorite-limit N` / `--bilibili-follow-limit N`：覆盖 B 站收藏 / 关注初始化信号上限，默认各 `300`；`0` 表示跳过对应信号。
- `OPENBILICLAW_NO_BILIBILI=1` / `OPENBILICLAW_NO_XHS=1` / `OPENBILICLAW_NO_DOUYIN=1` / `OPENBILICLAW_NO_YOUTUBE=1` / `OPENBILICLAW_NO_X=1` / `OPENBILICLAW_NO_ZHIHU=1` / `OPENBILICLAW_NO_REDDIT=1`：永久跳过对应源。
- `OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS`：小红书 `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用。
- `OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS` / `OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS`：抖音 / YouTube `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用。
- `OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS`：知乎 `bootstrap_events` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用。
- `OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_ITEMS` / `OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_COLLECTIONS`：控制 `fetch-zhihu` 每个数据分支最多读取的条目数和最多扫描收藏夹数，默认分别为 `300` / `20`。知乎当前分支是浏览历史、收藏夹条目、动态点赞、动态收藏；动态点赞和动态收藏各自独立使用 300 条上限，不共享额度。

如果当前终端是交互式，且缺少可用 Chat route 或 B 站 Cookie，`init` 会进入对应引导：

```text
初始化前配置引导 · 按连接类型配置 Chat 与 Embedding
Available chat connection types:
  openai_compatible: OpenAI-compatible (api_protocol)
  anthropic_compatible: Anthropic-compatible (api_protocol)
  gemini_api: Gemini API (api_protocol)
  ollama: Ollama (local_runtime)
  codex_oauth: Codex OAuth (oauth)
Connection type [openai_compatible]:
Available presets:
  openai: OpenAI
  deepseek: DeepSeek
  openrouter: OpenRouter
  custom: Custom gateway
# 当前全新配置带有可编辑的 DeepSeek 默认草稿，因此默认保留该 preset
Preset [deepseek]:
Stable connection ID [deepseek-main]:
Connection name [DeepSeek Flash]:
# 后续仅显示所选 descriptor 的 model/base_url/api_mode/credential 等字段

Embedding providers: local-embedding
Action (add/edit/disable) [edit]:
# 共享 model/维度/阈值/多模态只配置一次；Provider 只保存 endpoint/credential
```

引导完成后会继续当前初始化流程，不需要再单独执行 `auth login` 或手动改配置。

交互式 `init` 在询问「是否允许局域网设备访问」之后，**仅当启用了局域网访问时**会追加一次「是否为局域网访问设置密码」（默认 `No`）。选 `Yes` 即走与 `set-password` 相同的交互设置流程，写入 `[api.auth]`；选 `No` 可随后再用 `openbiliclaw set-password` 设置。

> OpenAI、DeepSeek、OpenRouter 与自定义网关统一属于 `openai_compatible` 的不同 preset；Anthropic 官方与自定义中转统一属于 `anthropic_compatible`。Gemini、Ollama 和每一种 OAuth 登录保持独立 connection type。切换 type/preset 后只显示对应 descriptor 字段，避免 Provider 增加后让引导横向膨胀。
>
> `codex_oauth` 只引用 `openbiliclaw login codex` 导入的 `codex` credential，不接收 API key，也不会把 OAuth token 写进 `config.toml`。

首次 `init` 的 discover 阶段可能持续几分钟，因为它会真实访问 B 站接口并调用当前 provider 进行候选打分与表达生成。
当前实现已经对首轮 discover 做了保守受控并发优化，但默认并发上限仍偏保守，优先减少 B 站和 LLM 限流风险。
首轮补货会按 `search + related_chain`、`trending`、`explore` 的顺序推进，并尽量把 fresh 候选池补到至少 `100` 条后再结束。
运行时后台则会继续以 `scheduler.pool_target_count` 为目标持续补货；当前默认目标是 `300`，到达后停止 discover，直到候选池掉回目标以下再继续补货。
运行中会直接打印每一阶段的策略名、当前池子进度和该轮请求上限，便于你判断首轮补货是在持续推进还是确实失败。

如果当前终端不是交互式，`init` 不会等待输入，而是直接报出明确错误；这适合服务器脚本和 CI 场景。

如果 discover 阶段失败，但历史和画像阶段成功，命令会提示“部分完成”，并建议稍后手动执行：

```bash
openbiliclaw discover
```

### `openbiliclaw setup-embedding`

打开与 `init` 共用的原生 Embedding route 编辑器；引导选择为 `add/edit/disable`，分别新增 Provider、编辑现有 Provider 或关闭整个 Embedding route。它不再写 `[llm.embedding]`，也不自动安装或启动 Ollama：

```text
$ openbiliclaw setup-embedding
Embedding providers: local-embedding, cloud-embedding
Action (add/edit/disable) [edit]:
Provider ID [local-embedding]:
Available embedding connection types:
  openai_compatible: OpenAI-compatible (api_protocol)
  gemini_api: Gemini API (api_protocol)
  dashscope_api: DashScope API (api_protocol)
  ollama: Ollama (local_runtime)
Connection type [ollama]:
Provider name [Local embedding]:
Shared embedding model [bge-m3]:
Output dimensionality [1024]:
Similarity threshold [0.82]:
Enable multimodal embeddings? [y/N]:
Base URL [http://127.0.0.1:11434/v1]:
```

引导只提供 `add/edit/disable` 三种选择：`add` 会在现有列表末尾添加一个全局唯一稳定 ID，`edit` 保持 ID 和列表位置不变，`disable` 会清空整个 Embedding route。删除单个 Provider 使用 `openbiliclaw models remove <id>`，重排单个 Provider 使用 `openbiliclaw models move <id> --position <1-10>`。所有 Provider 始终共享同一 model、输出维度、相似度阈值和多模态开关，Provider 自身只保存 type/preset、endpoint 与 credential；需要真实连通性检查时显式运行 `openbiliclaw models probe <id>`。

本命令只负责配置。Ollama 二进制、daemon 与模型权重继续由用户、安装包或专用修复流程管理；CLI 不在配置提交中执行安装、后台启动或网络调用。

`setup-embedding` 需要交互终端；自动化脚本应改用带完整显式参数的 `openbiliclaw models add --kind embedding`。非交互调用会返回非零退出码，不会打开编辑器或等待输入。

### `openbiliclaw recommend`

读取推荐缓存，生成朋友式推荐表达，并把已展示条目标记为 `presented=1`。

```bash
$ openbiliclaw recommend
本轮推荐
推荐 1
  标题: 讲透城市与建筑的空间叙事
  UP 主: 城市观察局
  发布时间: 3 天前
  话题标签: 你最近那股想把结构想透的劲头
  推荐理由: 这条会对上你最近那种想把结构想透的劲头，它不是快餐内容，而是会慢慢把结构给你铺开。
  BV号: BV1REC
```

`发布时间` 复用后端统一 formatter：精确 `published_at` 按本地时区显示为“刚刚 / N 小时前 / N 天前 / 月日 / 年-月-日”，精确值缺失时回退到来源 `published_label`；两者都为空时整行不输出。CLI 不展示原始 UTC 字符串，也不以发现时间或推荐生成时间代替发布时间。

如果当前还没有可推荐内容，会提示先执行：

```bash
openbiliclaw discover
```

### `openbiliclaw feedback <id> <like|dislike|comment|dismiss>`

为一条已展示的推荐记录写入结构化反馈，可附带备注；`comment` 必须带 `--note`，`dismiss` 走软移除语义不要求备注。

```bash
$ openbiliclaw feedback 7 dislike --note "太浅了"
反馈已记录
反馈详情
  推荐ID: 7
  反馈: dislike
  备注: 太浅了

$ openbiliclaw feedback 7 comment --note "方向对，但我想看更深一点。"
```

每次反馈执行以下两个写入操作：

- 更新 `recommendations` 表中的 `feedback_type` / `feedback_note` / `feedback_at`
- 写入一条 `event_type="feedback"` 的事件，供后续记忆系统使用

### `openbiliclaw fetch-douyin`

单独触发抖音 `bootstrap_profile` 拉取，适合 smoke 测试扩展和补拉抖音信号。它只执行“入队 → 唤醒扩展 → 等结果 → 打印 scope counts”，不跑 B 站认证检查、不跑 `analyze_events()` / `build_initial_profile()` / discovery。事件由 daemon 在接收 `/api/sources/dy/task-result` partial 时写入 memory，CLI 自身不会再传播一次，避免重复入库。

```bash
$ openbiliclaw fetch-douyin
抖音 数据拉取
  抖音 发布 24 条 / 收藏 13 个 / 点赞 12 个 / 关注 1 人
  共 50 条事件已由 daemon 写入 memory。
```

默认最多等待扩展回传 `180s`；需要更长排查窗口时可显式加 `--wait-seconds 240`。命令默认复用 6 小时内已有的 pending / in-progress / completed / failed 抖音 `bootstrap_profile` 任务，避免反复打开前台抖音 tab 全量拉发布 / 收藏 / 点赞 / 关注；需要重新拉取时可设 `OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS=0`。

前提：

- `openbiliclaw start` 或 `serve-api` 后端正在运行。
- Chrome 扩展已安装并在线。
- 浏览器已登录 `https://www.douyin.com`。

### `openbiliclaw fetch-xhs`

单独触发小红书 `bootstrap_profile` 拉取，定位与 `fetch-douyin` 相同：用于单源验证 / 补拉，不隐式重建画像。

```bash
$ openbiliclaw fetch-xhs
小红书 数据拉取
  小红书 收藏 20 个 / 点赞 20 个 / 浏览记录 0 个
```

默认最多等待扩展回传 `180s`，与 `init --yes-xhs --yes-douyin` 的单源 collect 窗口保持一致，降低两源连续初始化时小红书未结束就启动抖音的概率。命令默认复用 6 小时内已有的 pending / in-progress / completed / failed `bootstrap_profile` 任务，避免重复打开前台小红书 tab 抓收藏 / 点赞；排查时需要强制重拉可加 `--force`，或用 `OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS=0` 关闭复用窗口。

### `openbiliclaw fetch-youtube`

单独触发 YouTube `bootstrap_profile` 拉取，用于验证浏览器扩展、登录态和 `/api/sources/yt/*` 后端任务桥是否联通。采集范围与 init 相同：观看历史、订阅频道、点赞视频。

```bash
$ openbiliclaw fetch-youtube --wait-seconds 240
YouTube 数据拉取
  YouTube 观看历史 40 条 / 订阅 12 个 / 点赞 20 个
  共生成 72 条事件。
```

这条命令只做单源 smoke / 补拉，不会隐式重建画像。profile 已初始化后，daemon 接收新增 partial 事件时会写入 memory 并进入增量画像更新链路。命令默认复用 6 小时内已有的 YouTube `bootstrap_profile` 任务，避免反复打开前台 YouTube 页面滚动历史 / 订阅 / 点赞；需要重新拉取时可设 `OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS=0`。

### `openbiliclaw fetch-zhihu`

单独触发知乎 `bootstrap_events` 拉取，用于验证浏览器扩展、知乎登录态和 `/api/sources/zhihu/*` 后端任务桥是否联通。默认采集最近浏览记录、收藏夹条目和当前知乎用户主页动态里的点赞 / 收藏动作；扩展会通过 `/api/v4/me` 自动识别当前用户，传入 `--profile-slug` 时可手动覆盖。

```bash
$ openbiliclaw fetch-zhihu --wait-seconds 240
知乎 数据拉取
  知乎 浏览 300 条 / 收藏 423 条 / 点赞 16 条
  共抓取并转换 739 条事件；未触发画像生成。
```

这条命令只做事件爬取 smoke，不会写入 memory，也不会触发画像初始化或增量画像更新。CLI 会把扩展回传的 `zhihu_read_history`、`zhihu_collection`、`zhihu_activity` 条目转换成统一事件并打印计数，方便先确认浏览 / 收藏 / 点赞链路是否真实可用。命令默认复用 6 小时内已有的知乎 `bootstrap_events` 任务，避免反复打开前台知乎 tab；排查时可加 `--force`，或设置 `OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS=0` 强制新建任务。

需要把真实抓到的知乎事件落到本地 memory 时加 `--write-memory`；命令会按 `source_platform + event_type + content_id / url / title` 做本地去重，只写入本次新增事件。需要在写入后立刻触发画像重建时加 `--rebuild-profile`，该选项隐含 `--write-memory`，会调用真实 LLM 完成偏好分析和初始画像生成，适合端到端验证，不适合只做登录态 smoke。

默认分支上限为：浏览历史 300、收藏夹条目 300、动态点赞 300、动态收藏 300；理论最大事件数为 1200 条，实际数量会受知乎接口返回、去重和收藏夹数量影响。

### `openbiliclaw fetch-x`

单独触发 X（Twitter）点赞 / 收藏拉取，对应 `fetch-xhs` / `fetch-douyin` / `fetch-youtube`，但 X 是**服务端 cookie 重放**（无扩展 bootstrap 任务、**不需要 daemon**）：直接用已同步的 `x.com` cookie（`data/x_cookie.json` 或 `OPENBILICLAW_X_COOKIE`）拉取你自己的点赞 + 收藏，经 `_x_tweet_to_event` 转成统一事件写入 memory，用于在不重跑完整 `init` 的情况下验证 X 历史偏好回填链路。

```bash
$ openbiliclaw fetch-x -n 50
拉取 X 点赞 / 收藏
  X 点赞 50 条 / 收藏 23 条 → 共 73 条事件。
  已写入 memory：73 条事件。 跑 `openbiliclaw rebuild-profile` 让画像吃进新信号。
```

`--limit/-n` 控制每类最多拉取条数（默认 50，`init` 回填用 200）；`--dry-run` 只拉取并打印、不写 memory。点赞 → `event_type="like"`、收藏 → `event_type="favorite"`（均为显式正向信号）。cookie 未同步时静默跳过（0 条事件、退出码 0），不报错；拉取本身 best-effort，单类失败（cookie 过期 / 限流 / 偶发 TLS）只打印告警、不中断。

### `openbiliclaw import-youtube <path>`

从 Google Takeout 导出的 `.zip` 或解压目录导入 YouTube 观看历史、订阅和点赞数据，适合扩展无法读取旧历史或用户想一次性补齐冷启动信号的场景。

```bash
$ openbiliclaw import-youtube ~/Downloads/takeout.zip --dry-run
导入 YouTube Takeout
  解析完成：
    观看历史  1200 条
    订阅频道  88 个
    点赞视频  320 个
    合计      1608 条事件
```

不带 `--dry-run` 时，命令会把解析出的 YouTube 事件传播到记忆层，并调用 `analyze_events()` 更新偏好画像；它不会重新跑完整 init，也不会自动补推荐池。

### `openbiliclaw discover`

读取当前画像并触发一次内容发现。默认跑 Bilibili 的全部策略并将结果写入 `content_cache`，支持通过 `--source` 切换到 xiaohongshu 关键词生产流程、douyin discovery、知乎插件 discovery 或 Reddit discovery，或通过 `--strategy` 限定只跑部分 Bilibili 策略。知乎正式流程会复用 runtime `ZhihuDiscoveryProducer`，按配置页 / `config.toml` 的 `[sources.zhihu].source_modes` 入队 search / hot / feed / creator / related 任务并进入统一待评估池；Reddit 正式流程复用 `RedditDiscoveryProducer`，默认用 `[sources.reddit].backend="rdt"` 的 rdt-cli 登录态命令后端，按 `source_modes` 抓 search / hot / subreddit / related 候选；命令后端不可用时自动 fallback 到 OpenBiliClaw 插件任务。Reddit 候选只入 `discovery_candidates`，评估由后台统一 evaluator 处理。

```bash
# 默认：Bilibili 全策略
$ openbiliclaw discover
本次内容发现
发现摘要
  发现条数: 12
  缓存状态: 已写入 content_cache
  来源: bilibili
  策略: 全部

# 只跑 search + trending
$ openbiliclaw discover --strategy search,trending --limit 20

# 触发 xiaohongshu 关键词生产（由扩展在后台抓取）
$ openbiliclaw discover --source xiaohongshu
小红书关键词生产
生产摘要
  入队关键词数: 5
  尝试关键词数: 5
  今日预算: 30
  节流开关: 4 小时节流

# 忽略 4 小时节流
$ openbiliclaw discover --source xiaohongshu --force

# 触发 douyin discovery
# Cookie 可由扩展自动同步；下面的环境变量仅用于调试时显式覆盖
$ export OPENBILICLAW_DOUYIN_COOKIE='msToken=...; ttwid=...; ...'
$ openbiliclaw discover --source douyin --limit 20
抖音内容发现
发现摘要
  发现条数: 8
  缓存状态: 已写入 content_cache
  来源: douyin
  策略: dy-plugin-search, dy-plugin-hot-related, dy-plugin-feed

# 触发知乎正式 discovery（使用设置页选中的 source_modes）
$ openbiliclaw discover --source zhihu --limit 20
知乎内容发现
发现摘要
  发现条数: 20
  入池候选: 20
  来源: zhihu
  来源分布: zhihu-feed:5, zhihu-hot:5, zhihu-related:10
  分支: search, hot, feed, creator, related

# 触发 Reddit 正式 discovery（使用设置页选中的 source_modes）
$ openbiliclaw discover --source reddit --limit 20
Reddit 内容发现
发现摘要
  发现条数: 31
  入池候选: 6
  来源: reddit
  来源分布: reddit-hot:2, reddit-related:15, reddit-search:4, reddit-subreddit:10
  分支: search, hot, subreddit, related
  后端: rdt
```

选项：

- `--source, -s`：`bilibili`（默认）、`xiaohongshu`、`douyin`、`zhihu` 或 `reddit`
- `--strategy, -S`：仅对 Bilibili 生效，可多次传或逗号分隔，取值 `search` / `trending` / `explore` / `related_chain`
- `--limit, -n`：发现结果条数上限，默认 `30`
- `--force`：xiaohongshu 专用，忽略 `XhsTaskProducer` 的 4 小时节流

抖音 discovery 需要 `[sources.douyin].enabled = true`。Cookie 解析顺序是：先读 `cookie_env` 指向的环境变量（默认 `OPENBILICLAW_DOUYIN_COOKIE`，适合调试覆盖），再读浏览器扩展同步的 `data/douyin_cookie.json`。初始化画像的 `init --yes-douyin` 不受这个配置影响，仍走浏览器扩展任务桥。知乎 discovery 需要 `[sources.zhihu].enabled = true`，并依赖已登录知乎的浏览器扩展；`discover --source zhihu` 会读取 `[sources.zhihu].source_modes`，不会使用 `--strategy`。Reddit discovery 需要 `[sources.reddit].enabled = true`；默认 `backend="rdt"`，优先使用 rdt-cli 登录态命令后端，不使用 CDP/临时浏览器；rdt / opencli 不可用时自动复用 OpenBiliClaw 插件所在浏览器的 Reddit 登录态，也可在配置页显式切到 `extension`。

`search` 子来源走浏览器插件 DOM-first 链路：CLI 入队 `dy_tasks(type="search")`，扩展后台 tab 先打开抖音首页，再在已登录页面里模拟搜索框输入 / 提交，候选以 `dy-plugin-search` 进入 discovery；fetch tap 兼容 `/general/search/single/`、`/search/item/` 和新版 `/general/search/stream/` chunked JSON。`hot` 子来源同样走插件：后端取 hot board 的 `sentence_id`，并把可用的 `group_id` 作为 `seed_aweme_id` 透传给扩展；扩展从首页点击热榜 / 热点入口和目标热词，靠页面自身加载与被动响应监听回传 `dy_hot`，不足时用已登录页面的 related API bridge 按 seed 拉相关视频，候选以 `dy-plugin-hot-related` 进入 discovery；小批量 hot 请求会展开一个小窗口并优先执行带 seed 的 hot item，在累计达到 `--limit` 后提前结束，避免串行 DOM 点击和页面加载拖到 `task_timeout`。`feed` 子来源会入队 `dy_tasks(type="feed")`，扩展在首页推荐流滚动触发加载，候选以 `dy-plugin-feed` 进入 discovery。三条链路都不主动跳 `/search/...`、`/hot/...` 快捷 URL；插件任务空 / 超时 / 失败时默认返回 0 条，direct-cookie fallback 只保留给显式诊断路径。search 若真实响应为 `search_nil_info.search_nil_item="hit_shark"` 且没有 `data/aweme_list`，属于抖音反爬空结果，CLI 会显示 0 条。

需要调试抖音 discovery 子来源时，优先使用独立命令 `openbiliclaw discover-douyin`。它和 `discover --source douyin` 共用同一个 `DouyinDiscoveryService`，但可以显式指定关键词、是否写缓存和是否跳过 LLM 评估：

```bash
# 调试 search + feed，直接看源接口召回，不写 content_cache
$ openbiliclaw discover-douyin \
  --keyword 猫咪,机械键盘 \
  --source search,feed \
  --limit 20 \
  --no-cache \
  --no-evaluate
```

`discover-douyin` 的 `--source` 只接受 `search` / `hot` / `feed`；不传时默认三者都跑。`--keyword` 不传时从 Soul 画像兴趣生成搜索词；`hot` 会自动取 hot board 热词，不需要手动传关键词；`feed` 直接从抖音首页推荐流召回，不需要关键词。

xiaohongshu 渠道并不直接抓取内容，而是调用 `XhsTaskProducer.produce_if_due()` 将 Soul 画像改写成关键词写入 `xhs_tasks` 表，由浏览器扩展的后台调度器在隐藏 Tab 中抓取。若返回 `throttled` 可加 `--force` 重试；若返回 `no_profile` 需先执行 `openbiliclaw init`。

### `openbiliclaw discover-zhihu`

通过浏览器插件执行知乎搜索 discovery，适合真实端到端测试已登录知乎浏览器路径。CLI 会入队 `zhihu_tasks(type="search")`，唤醒已安装插件，等待扩展在真实 `zhihu.com` 登录态里拉取 `zhihu_search` 候选，再把候选转换为统一 `DiscoveredContent` 并写入 `discovery_candidates(pending_eval)`。这条命令不会写 memory，也不会触发画像初始化；正式手动补池优先使用 `discover --source zhihu`，它会按配置的 `source_modes` 跑完整 producer 并接入统一 evaluator。

```bash
$ openbiliclaw discover-zhihu "AI 工程化" "数据库" --limit 10 --wait-seconds 240
知乎搜索发现
  知乎搜索 20 条候选
  已写入待评估候选池：20 条
```

选项：

- 位置参数 `keywords`：一个或多个知乎搜索关键词；也可以用逗号分隔。
- `--limit, -n`：每个关键词最多回传的搜索候选数，默认 `20`。
- `--wait-seconds, -w`：等待插件任务完成的最长时间，默认 `180`。
- `--no-enqueue`：只看插件搜索结果，不写入 `discovery_candidates`。

如果返回 `login_required`，先在安装了 OpenBiliClaw 插件的 Chrome 里正常登录知乎；这条链路不使用 CDP，也不需要另开调试浏览器。

同一插件任务桥还提供四个非搜索 smoke 命令：

```bash
openbiliclaw discover-zhihu-hot --limit 10 --wait-seconds 240
openbiliclaw discover-zhihu-feed --limit 10 --wait-seconds 240
openbiliclaw discover-zhihu-creator https://www.zhihu.com/people/<slug> --limit 10 --wait-seconds 240
openbiliclaw discover-zhihu-related https://www.zhihu.com/question/<id> --limit 10 --wait-seconds 240
```

它们分别入队 `zhihu_tasks(type="hot"|"feed"|"creator"|"related")`，回写 `zhihu_hot` / `zhihu_feed` / `zhihu_creator` / `zhihu_related` 候选，source strategy 对应 `zhihu-hot` / `zhihu-feed` / `zhihu-creator` / `zhihu-related`。

### `openbiliclaw fetch-reddit`

单独触发 Reddit 事件 / 搜索 smoke，用于验证 Reddit 后端、登录态和归一化是否联通。默认 `--backend rdt`，`rdt-cli` 已随后端默认安装；已连接插件会把 `reddit_session` 自动同步到 rdt-cli credential store，插件不可用时才需要在本机已登录 Reddit 的浏览器环境里运行 `rdt login`。`--mode search|hot|subreddit|related` 优先通过 rdt-cli 读取候选并转换为低权重 view 事件用于终端预览；命令后端不可用、未登录或显式 `--backend extension` 时会改走插件任务桥。`--mode bootstrap` 会自动使用插件后端，入队 `reddit_tasks(type="bootstrap_events")` 并拉 saved / upvoted / subscribed。默认不会写 memory，也不会触发画像初始化或增量画像更新；需要真实落库时必须显式传 `--write-memory`，需要写入后重建画像时传 `--rebuild-profile`。`bootstrap` 只支持 extension 后端，因为它必须运行在已登录浏览器同源页面内。

```bash
$ openbiliclaw fetch-reddit "open source ai" --limit 10 --wait-seconds 180
Reddit 数据拉取
  Reddit 搜索 10 条 / 统一事件 10 条

$ openbiliclaw fetch-reddit --mode bootstrap --wait-seconds 180
Reddit 事件拉取
  收藏(saved) 12 条 / 点赞(upvoted) 31 条 / 订阅 subreddit 18 个
  写入 memory 未写入 memory
  画像生成 未触发画像生成
```

### `openbiliclaw discover-reddit*`

Reddit discovery smoke 命令会把 rdt-cli（默认安装）、OpenCLI 或插件后端返回的候选转换为 `DiscoveredContent(source_platform="reddit")` 并写入 `discovery_candidates(pending_eval)`；rdt / opencli 不可用或未登录时会自动 fallback 到插件任务。它们只验证取数和入池，不写 memory、不重建画像、不直接写 `content_cache`。正式补池优先使用 `openbiliclaw discover --source reddit`，它会按配置页保存的 `source_modes`、后端和来源比例进入 runtime producer。

```bash
openbiliclaw discover-reddit "open source ai" --limit 10
openbiliclaw discover-reddit-hot --subreddit all --limit 10
openbiliclaw discover-reddit-subreddit LocalLLaMA --limit 10
openbiliclaw discover-reddit-related https://www.reddit.com/r/LocalLLaMA/comments/<id>/<slug>/ --limit 10
```

`discover-reddit` 默认走 search；`discover-reddit-hot` 默认 `r/all`，rdt 路径实际调用 `rdt all --json`；`discover-reddit-subreddit` 需要一个或多个 subreddit 名，rdt 路径实际调用 `rdt sub <name> --json`；`discover-reddit-related` 需要一个或多个 Reddit 内容 URL，rdt 路径会抽取 `/comments/<id>/` 后调用 `rdt read <id> --json`。命令默认 `--backend rdt`，优先使用插件同步的 rdt credential；插件不可用时可手动运行 `rdt login`。需要强制插件登录态链路时加 `--backend extension --wait-seconds 180`。若 rdt 路径不可用或未登录，CLI 会自动 fallback 到插件；若插件路径返回 `login_required`，请在安装了 OpenBiliClaw 插件的浏览器里正常登录 Reddit。

### `openbiliclaw search-douyin`

通过浏览器插件执行抖音搜索 smoke，适合排查真实登录浏览器 DOM-first 路径能否召回视频候选。

```bash
$ openbiliclaw search-douyin -k 猫 --max-items-per-keyword 10 -w 180
抖音搜索发现
  抖音搜索 10 条候选
  1. 盘点全网那些叛逆的猫咪... 迷惑菌呀
     https://www.douyin.com/video/7219607743328537915
```

行为边界：

- CLI 入队 `dy_tasks(type="search")`，唤醒扩展 dispatcher，等待 `dy_tasks.result_json`。
- 扩展会在已登录抖音浏览器会话的后台 tab 先打开首页，再模拟真实搜索框输入和提交；MAIN-world fetch tap 只被动收集页面自己发出的搜索响应，content script 同时解析已渲染 DOM，再把 `dy_search` 候选回传。
- 默认等待窗口为 `180s`；如果调试机上搜索页首开很慢，可显式加 `--wait-seconds 240`。
- 结果只作为搜索 discovery 候选保存在任务结果中；后端不会把它转换成 memory event，也不会重建画像。独立 `search-douyin` smoke 不写 `content_cache`；正式 `discover-douyin --source search` / `discover --source douyin` 会把同一插件搜索候选纳入 discovery 结果，并在 cache 模式下按 `dy-plugin-search` 写入 `content_cache`。
- 如果返回 0 条，优先检查是否有多个加载扩展的 Chrome 实例抢任务、当前浏览器是否登录抖音、页面搜索入口是否可见，以及 debug 中 `ui_triggered / api_items_harvested / dom_items_harvested`。若 direct / 页面响应的 `search_nil_info.search_nil_item` 为 `hit_shark`，说明当前 Cookie / 会话被抖音搜索风控空 200 拦截。

如果画像尚未初始化，会提示先执行：

```bash
openbiliclaw init
```

### `openbiliclaw chat`

进入持续对话模式，复用 `SocraticDialogue` 的多轮历史。输入 `exit`、`quit` 或空行可结束。聊天内容仅在得到真实回复后以受控方式积累到长期理解候选中，不会因为一句话立刻改写画像。单轮 LLM 失败会打印安全、可操作的错因（不显示上游异常原文），REPL 继续接受下一轮输入。

```bash
$ openbiliclaw chat
苏格拉底式对话
你：我最近总在刷讲结构的视频。
阿花：我听见你在说，你现在在意的可能不只是内容本身，而是想把事情看得更透一点。
你：exit
阿花：对话结束。
```

如果画像尚未初始化，会提示先执行：

```bash
openbiliclaw init
```

### `openbiliclaw start`

启动本地后端 API 服务，默认监听 `127.0.0.1:8420`，供浏览器插件或本地调试调用。

启动前会先做两件事：

1. 检查 `data/openbiliclaw.db` 是否完整；如果检测到损坏，会拒绝启动并提示先执行 `openbiliclaw db-repair`
2. 在数据库健康且距离上次冷备超过 24 小时时，自动生成一份冷备到 `data/backups/`

```bash
$ openbiliclaw start
启动 OpenBiliClaw
API 服务
  正在启动本地后端，默认监听 127.0.0.1:8420。
```

如果数据库已损坏：

```bash
$ openbiliclaw start
数据库损坏
检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。
```

### `openbiliclaw db-repair`

显式检查并修复本地 SQLite 数据库。命令遵循”先检查、先备份、后修复”的顺序：

1. 运行完整性检查
2. 若数据库正在被进程占用则拒绝继续
3. 备份 `openbiliclaw.db` 与可选的 `openbiliclaw.db-wal`
4. 尝试恢复到新的 repaired 副本
5. 验证 repaired 副本通过后，再切换正式库

```bash
$ openbiliclaw db-repair
数据库已恢复并完成切换。
备份文件: data/backups/openbiliclaw-20260315-020000.db
恢复副本: data/openbiliclaw.repaired.db
```

如果数据库本来就是健康的，命令会直接退出并提示无需修复；如果仍被运行中服务占用，会返回非零退出码并列出占用进程。

### Stub 命令的输出约定

当前仍是 stub 的命令会统一使用”开发中”占位态输出，避免与真实错误混淆，并会附带建议的下一步命令。
