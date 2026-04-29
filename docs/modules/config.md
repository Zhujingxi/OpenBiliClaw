# 配置参考

> `config.toml` 所有配置段落详解。

## 快速开始

```bash
cp config.example.toml config.toml
# 编辑 config.toml，填入 LLM API Key
```

## 配置段落

### `[general]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `language` | string | `"zh"` | Agent 输出语言（`zh` / `en`） |
| `data_dir` | string | `"data"` | 数据目录（记忆、Cookie、数据库） |

### `[llm]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `default_provider` | string | `"openai"` | 默认 Provider：`openai` / `claude` / `gemini` / `deepseek` / `ollama` / `openrouter` |

### `[llm.openai]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | OpenAI API Key（default_provider=openai 时必填） |
| `model` | string | `"gpt-4o"` | 模型名称 |
| `base_url` | string | `""` | 留空使用官方 API，可设置兼容 API 地址 |

### `[llm.claude]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | Anthropic API Key（default_provider=claude 时必填） |
| `model` | string | `"claude-sonnet-4-20250514"` | 模型名称 |

### `[llm.gemini]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | Gemini API Key（default_provider=gemini 时，若未填写则回退读取 `GOOGLE_API_KEY` / `GEMINI_API_KEY`） |
| `model` | string | `"gemini-2.5-flash"` | Gemini 模型名称 |

> Gemini provider 按官方 quickstart 走 `google-genai` SDK 的 Gemini Developer API，不是 Vertex AI。

### `[llm.deepseek]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | DeepSeek API Key |
| `model` | string | `"deepseek-chat"` | 模型名称 |
| `base_url` | string | `"https://api.deepseek.com"` | API 地址 |

### `[llm.ollama]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `model` | string | `"llama3"` | 本地模型名称 |
| `base_url` | string | `"http://localhost:11434"` | Ollama 服务地址 |

> Ollama 不需要 API Key，适合本地开发测试。

### `[llm.openrouter]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | OpenRouter API Key（default_provider=openrouter 时必填） |
| `model` | string | `"openai/gpt-4o-mini"` | OpenRouter 模型名称 |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | OpenRouter API 地址 |
| `http_referer` | string | `""` | 可选的 `HTTP-Referer` 请求头 |
| `x_title` | string | `"OpenBiliClaw"` | 可选的 `X-Title` 请求头 |

> `http_referer` 和 `x_title` 都是可选项；留空时不会阻止请求发送。

### `[llm.embedding]`

Embedding 服务用于多个语义任务：discovery 内容兴趣预过滤、recommendation 跨主题去重、PoolCurator 反馈相似度判定、interest probe 主题归类。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `provider` | string | `""` | 留空 = 跟随 `[llm].default_provider`；填 `"ollama"` 启用本地 Ollama 作 embedding 兜底 |
| `model` | string | `"gemini-embedding-001"` | embedding 模型名；按 provider 自动填合理默认：`gemini → gemini-embedding-001` / `openai → text-embedding-3-small` / `ollama → bge-m3` |
| `similarity_threshold` | float | `0.82` | 余弦相似度阈值，超过即视为"同主题" |

#### 启用本地 Ollama embedding（v0.3.0+，**v0.3.3 起真实生效**）

> ⚠️ **如果你装的是 v0.3.0~v0.3.2**：`setup-embedding` 当时虽然写了 `[llm.embedding] provider="ollama"`，但 LLM 注册表静默回退到 default provider，embedding 实际仍走 Gemini。
> **升级到 v0.3.3+ 重启 backend** 即可生效，不需要改配置；想"零悬念"的话可以再跑一次 `openbiliclaw setup-embedding`，向导会顺手补上 `[llm.ollama] base_url`。

不想再多一份 embedding API Key、或要支持离线，可以用 Ollama + bge-m3 跑本地 embedding：

```bash
# 1. 装 Ollama（一次性）
# Mac
brew install ollama && ollama serve &
# Windows: 从 https://ollama.com/download 下载安装包
# Linux
curl -fsSL https://ollama.com/install.sh | sh && ollama serve &

# 2. 跑向导自动拉模型 + 写配置
openbiliclaw setup-embedding
```

或手动改 `config.toml`：

```toml
[llm.embedding]
provider = "ollama"
model = "bge-m3"
```

CPU 即可跑（~100-200ms/次），跨 Mac / Win / Linux 一致。

### `[bilibili]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `auth_method` | string | `"cookie"` | 认证方式：`cookie` / `qrcode` / `none` |
| `cookie` | string | `""` | 浏览器 Cookie（推荐通过 `auth login` 命令设置） |

### `[bilibili.browser]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `executable` | string | `""` | agent-browser 路径（留空使用全局安装） |
| `headed` | bool | `false` | 是否显示浏览器窗口（调试用） |

> 运行时行为：
> 如果 `bilibili.cookie` 留空，CLI 命令和本地 API 服务会自动回退到 `auth login` 保存的 `data/bilibili_cookie.json`。
> 只有在你想显式覆盖本地登录态时，才需要把 cookie 直接写进 `config.toml`。

### `[sources.browser]`

多源内容适配器（小红书、知乎、V2EX 等非 B 站源）使用的浏览器配置。与 `bilibili.browser` 独立 —— 后者控制 B 站登录 / 扫码用的 agent-browser CLI。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `cdp_url` | string | `""` | 预启动 Chrome 的 CDP 端点，例如 `"http://localhost:9222"`。设置后优先走 Playwright `connect_over_cdp` 复用你手动登录的会话；留空则回退到 agent-browser（无登录态） |
| `headed` | bool | `false` | agent-browser 回退路径是否显示窗口 |

> **推荐用 CDP 模式。** 小红书等站点对匿名请求限流严格，只有复用真实登录态才稳定工作。
>
> 启动步骤：
> 1. 安装 Playwright：`pip install 'openbiliclaw[browser]'`
> 2. 启一个独立 profile 的 Chrome：
>    ```bash
>    open -na "Google Chrome" --args \
>      --remote-debugging-port=9222 \
>      --user-data-dir="$HOME/.openbiliclaw-chrome"
>    ```
> 3. 在这个 Chrome 里手动登录目标站点（小红书等），profile 会记住，后续复用
> 4. 在 `config.toml` 里填 `cdp_url = "http://localhost:9222"`
>
> `127.0.0.1` 与 `localhost` 并非总是等价：macOS 上 Chrome 常只绑定 IPv6 `::1:9222`，而 Python urllib 默认走 IPv4。用 `localhost` 最稳妥（`getaddrinfo` 会同时尝试两边）。

### `[sources.xiaohongshu]`

小红书专用配置。详情富化通过 GPL 隔离的 xhs-downloader sidecar 容器完成（`POST /xhs/detail`），主后端不导入任何 xhs 代码。内容发现交给扩展在真实登录态的浏览器中完成，后端不做主动爬取。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `sidecar_url` | string | `""` | xhs-downloader sidecar 的 HTTP 地址。留空禁用小红书源。Docker compose 自动注入 `OPENBILICLAW_XHS_SIDECAR_URL` 环境变量 |
| `daily_search_budget` | int | `30` | 每天后端允许入队的 Soul 驱动搜索任务数上限。由 `XhsTaskProducer`（`runtime/xhs_producer.py`）在持续刷新循环里使用，搭配内部 4h 最小间隔避免反复抢配额 |
| `daily_creator_budget` | int | `10` | 每天每位订阅创作者的抓取任务上限 |
| `task_interval_seconds` | int | `45` | 扩展分发器两次任务之间的最小间隔（秒） |

> **安全设计要点：** 后端从不直接调用小红书搜索 / Feed API。所有"主动发现"（关键词搜索、创作者主页浏览）都在用户自己的浏览器中以后台标签页形式执行，由扩展代理完成。被动发现则利用用户正常浏览时已经加载的卡片 URL，零额外请求。

### `[scheduler]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 是否启用定时发现 |
| `discovery_cron` | string | `"0 */4 * * *"` | 发现任务 cron 表达式 |
| `pool_target_count` | int | `600` | discovery pool 的硬上限，同时作为期望保有的可换候选数量；允许范围 `1..600`。pool < 目标时会持续补货；pool ≥ 目标时任何 refresh（含 `force_refresh`）都直接返回 `pool_at_cap` 不再 discover；pool > 目标时会先按 `relevance_score` / 时间 / `explore` 优先顺序把溢出部分降为 `suppressed` |
| `account_sync_interval_hours` | int | `6` | 账户侧长期信号同步间隔；运行时会低频拉取 history / favorites / following |
| `speculation_interval_minutes` | int | `10` | 猜测兴趣推测的运行间隔（分钟） |
| `speculation_ttl_days` | int | `3` | 猜测兴趣的默认存活天数 |
| `speculation_cooldown_days` | int | `7` | 猜测兴趣被否定后的冷却天数 |
| `speculation_confirmation_threshold` | int | `3` | 需要多少次正向信号确认猜测兴趣 |
| `speculation_max_active` | int | `5` | 最多同时活跃的猜测兴趣数 |
| `speculation_max_primary_interests` | int | `15` | 主要兴趣域的最大数量 |
| `speculation_max_secondary_interests` | int | `60` | 次要兴趣域的最大数量 |

> 运行时护栏：
> 即使 `pool_target_count` 设得较高，单次 refresh 里的单轮 discover 补货请求也会封顶在 `60`，避免一次性把全部缺口都打满。

### `[storage]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `db_path` | string | `"data/openbiliclaw.db"` | SQLite 数据库路径 |

### `[logging]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `level` | string | `"INFO"` | 控制台日志级别 |
| `file_level` | string | `"DEBUG"` | 文件日志级别 |
| `directory` | string | `"logs"` | 日志目录 |
| `filename` | string | `"openbiliclaw.log"` | 日志文件名 |
| `max_file_size_mb` | int | `1024` | 单个日志文件上限（MB），超过即轮转；`0` 禁用轮转 |
| `backup_count` | int | `1` | 保留的历史日志份数；设为 `1` 时总占用封顶 `max_file_size_mb * 2` MB |

启动时如果现有日志文件已经超过 `max_file_size_mb`，会被重命名为 `<filename>.1`（覆盖旧的 `.1`）并重新开始写入——这样意外堆积的大日志不会在下次启动时继续增长。运行时到达上限则由 `RotatingFileHandler` 正常轮转：`app.log` → `app.log.1` → `app.log.2` → …，超出 `backup_count` 的旧份自动丢弃。

## 环境变量

| 变量 | 说明 |
|------|------|
| `OPENBILICLAW_BILIBILI_COOKIE` | 集成测试用 B 站 Cookie |
| `GOOGLE_API_KEY` | Gemini 官方推荐 API Key 环境变量，优先级高于 `GEMINI_API_KEY` |
| `GEMINI_API_KEY` | Gemini 官方兼容环境变量，`default_provider=gemini` 时可替代 `llm.gemini.api_key` |
| `OPENBILICLAW_PROXY_HOST` | Docker 运行时可选宿主机代理地址，默认 `host.docker.internal` |
| `OPENBILICLAW_PROXY_PORT` | Docker 运行时可选宿主机代理端口，默认 `7897` |
| `OPENBILICLAW_PROXY_TIMEOUT` | Docker 运行时代理探测超时（秒），默认 `1.0` |

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

[llm]
default_provider = "openai"

[llm.openai]
api_key = "sk-..."
model = "gpt-4o"

[bilibili]
auth_method = "cookie"
cookie = ""
```

建议：

- Docker 模式下的首选入口是 `docker exec -it openbiliclaw-backend openbiliclaw init`
- 如果缺少 provider API Key 或 B 站 Cookie，`init` 会直接在终端里引导并写回 Docker volume
- provider 和 API Key 会写入 `/app/runtime/config.toml`
- B 站 cookie 会写入 `/app/runtime/data/bilibili_cookie.json`
- 首轮 `init` 和后续 `discover` 可能持续几分钟，因为它们会真实访问 B 站和当前 LLM provider
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
