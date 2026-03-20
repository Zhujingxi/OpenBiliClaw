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

### `[scheduler]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 是否启用定时发现 |
| `discovery_cron` | string | `"0 */4 * * *"` | 发现任务 cron 表达式 |
| `pool_target_count` | int | `300` | discovery pool 期望保有的可换候选数量；允许范围 `1..300`。运行时会持续补货直到接近该目标，给 popup 连续“换一批”和自动续页留出更充足余量 |
| `account_sync_interval_hours` | int | `6` | 账户侧长期信号同步间隔；运行时会低频拉取 history / favorites / following |

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
