# Docker 部署指南

[← 返回 README](../README.md)

> 🔒 **局域网访问安全（可选密码门禁）**：容器把后端暴露在 `8420`，同网段设备都能访问。需要为局域网 / 远程设备加登录密码时（本机与浏览器扩展仍免登录），设置环境变量 `OPENBILICLAW_API_AUTH_ENABLED=true` + `OPENBILICLAW_API_AUTH_PASSWORD=…`（或进容器跑 `openbiliclaw set-password`）。若前面再套同机反向代理，记得配 `[api.auth].trusted_proxies` 或让代理自行鉴权。详见 [`docs/modules/api-auth.md`](modules/api-auth.md)。

## 前置条件

- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) V2（`docker compose` 命令）
- 一个 LLM API Key（OpenAI / Claude / Gemini / DeepSeek / OpenRouter）—— **Embedding 用 compose 自带的 Ollama 不再需要单独申请**

## LiteLLM 基础设施密钥与 provider 配置

Compose 现在同时启动固定版本 tag 的 LiteLLM proxy 和它的独立 PostgreSQL。为避免默认密码，启动前必须在当前目录的 `.env` 生成两个仅供本机基础设施使用的值：

```bash
umask 077
printf 'LITELLM_POSTGRES_PASSWORD=%s\nLITELLM_MASTER_KEY=sk-%s\n' \
  "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > .env
```

不要提交 `.env`。如果已有该文件，请只补缺失的 `LITELLM_POSTGRES_PASSWORD` / `LITELLM_MASTER_KEY`，不要覆盖其他环境变量。一行 installer 与 `agent_bootstrap.py --mode docker` 会在跨进程锁内生成缺失值、保留已有非空值，并用同目录 `0600` 临时文件、`fsync` 和原子替换更新；`.env` 或锁文件为符号链接时会拒绝继续。

容器健康后打开 `http://127.0.0.1:4000/ui`，把实际 provider API key 只录入 LiteLLM 管理面，并创建精确 model group：

- `obc-interactive`：交互任务；
- `obc-analysis`：分析任务；
- `obc-embedding`：embedding 模式。

不要把 provider key 写进 Compose 或 OpenBiliClaw 配置。LiteLLM 独占 provider routing/fallback、网络重试、限流和缓存。当前 vNext typed AI 还没有接入 legacy 业务 runtime；`/setup/` 的现有模型表单和 `openbiliclaw init` 在切换任务完成前仍配置并使用 legacy route，两套配置此时并存。

Admin 端口在两份 Compose 中都固定绑定宿主机 `127.0.0.1`，不会向局域网暴露。确需远程管理时，必须显式把 `127.0.0.1:${LITELLM_PORT:-4000}:4000` 改成受控监听地址，并先配置防火墙、TLS 和访问控制；不要直接裸露到公网。

## vNext 后台 worker

源码与预构建 Compose 现在都启动独立 `openbiliclaw-worker`。它先执行 Alembic migration，再以最多 4 个 thread worker 运行四个有锁、可恢复的任务：`source_sync`、`profile_projection`、`feed_replenishment`、`cleanup`。backend/API 与 worker 显式使用同一个 `OPENBILICLAW_DATABASE_URL=sqlite:////app/runtime/data/vnext/openbiliclaw.db` 和共享 volume；Huey transport/result 位于独立的 `data/vnext/huey.db`。即使 Huey result 中有值，任务状态、dispatch marker、运行中取消与单调 progress 仍只以应用库 `job_runs` 为准。queue handoff 失败会由重复 schedule 或 worker restart reconcile，不会永久丢失 pending job。

worker 固定构造 Bilibili、小红书、抖音、YouTube、X、知乎与 Reddit connector，不加载动态插件。`source_enabled` 默认全部关闭，因此启动不会调用平台。Bilibili、抖音、X 等 direct/CLI client 只在首次真实调用时读取 `source_accounts` 并解密；需要此路径时，把与写入密文相同的 `OPENBILICLAW_SECRET_KEY` 放进本地 `.env`。当前 installer 尚未自动生成或轮换该值；缺失/错误 secret 或账户 Cookie 会让对应 job 以不含凭据内容的明确配置错误失败。小红书、知乎、YouTube bootstrap 与 production-default Reddit 使用 generic browser queue，但扩展 dispatcher 要到后续切换任务才会消费它们。

### 自带 Ollama embedding sidecar（bge-m3 已烤进镜像,离线开箱即用）

`docker-compose.yml` 有一个 `ollama` 服务,对外暴露 `http://ollama:11434`,用 Docker 网络和后端互通。**bge-m3(~1.1GB)已在构建时烤进镜像 `openbiliclaw-ollama`**:容器启动时其 entrypoint 把烤好的模型播种进存储再 serve,**零网络拉取、离线可用**,对国内网络尤其友好。named volume `openbiliclaw_ollama` 持久化,重建容器不丢。

- 预构建路径(`docker-compose.prebuilt.yml`):直接拉 GHCR 上的 `openbiliclaw-ollama:<version>` 镜像。
- 源码构建路径(`docker-compose.yml`):`ollama` 服务用 `docker/ollama-bundled.Dockerfile` 本地构建(构建时联网拉一次 bge-m3 烤进镜像;之后运行离线)。
- 万一烤好的种子缺失/损坏,healthcheck 会**明确报 unhealthy**(不静默降级);设 `OPENBILICLAW_OLLAMA_ALLOW_PULL=1` 可显式允许运行时联网补拉。

后端容器首次启动时会在原生 `[models.embedding]` 下加入一条稳定的
`ollama` Provider 记录，并把共享 model 设为 `bge-m3`、endpoint 指向
`http://ollama:11434/v1`。这是生成新 `config.toml` 时的 template seed：
本地记录位于第 1 项，模板里已有的远端 Provider 保持相对顺序排在其后，
Chat route 不改。磁盘上已存在的 `config.toml` 整体不覆盖。

不需要这个 sidecar？删掉 `docker-compose.yml` 里 `ollama` 服务块和后端的 `OPENBILICLAW_SEED_OLLAMA_DEFAULTS` 环境变量即可。

### 平台支持（v0.3.4+）

镜像基于 `python:3.11-slim`（多架构 manifest），同一份 `docker-compose.yml` 可以在以下平台直接跑：

| 平台 | 架构 | 备注 |
|------|------|------|
| macOS Intel | linux/amd64 | Docker Desktop |
| macOS Apple Silicon (M1/M2/M3) | linux/arm64 | Docker Desktop，自动选 arm64 |
| Linux x86_64 | linux/amd64 | 直接 Docker Engine |
| Linux ARM (Raspberry Pi 4/5) | linux/arm64 | 直接 Docker Engine |
| Windows | linux/amd64 (默认) | Docker Desktop（默认 WSL2 backend）|

`docker compose build` 会自动按主机架构选择正确的 base image 层。如果你要为发布构建跨架构镜像，用 buildx：

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t openbiliclaw-backend:v0.3.4 .
```

## 多源登录前置：装了扩展的浏览器要登录每一个想用的源

OpenBiliClaw 不爬登录态——它复用**你**当前浏览器的登录会话来跨平台抓你能看到的内容。Docker 部署后，仍然需要在装了扩展的同一个浏览器里登录每个目标源：

- **B 站**：浏览器里登录 https://www.bilibili.com 即可。v0.3.12+ 扩展会自动把 Cookie 推到容器里的 `/api/bilibili/cookie`，免 F12
- **小红书**：必须在浏览器里登录 https://www.xiaohongshu.com。后端不直接抓小红书，所有发现/详情都通过扩展以你的登录态执行——大部分任务(search / creator 抓取)在隐藏 tab 里跑;但 v0.3.22+ 起 `init` 期间的 **bootstrap_profile 滚动任务会临时打开一个前台 tab**(后台 tab 在小红书上无法触发瀑布流懒加载),会抢一次焦点 10-30 秒,完成后自动关闭。**不登录 = 完全没有小红书内容**
- **抖音**：如果要启用 `init --yes-douyin`、`fetch-douyin` 或 `discover --source douyin`，必须在装了扩展的宿主机浏览器里登录 https://www.douyin.com。后端不直接抓抖音；初始化只接收扩展回传的发布 / 收藏 / 点赞 / 关注信号。search / hot / feed discovery 走登录浏览器插件 DOM-first 链路：后台 tab 先打开抖音首页，再模拟真实 DOM 操作触发加载，并被动收集页面响应 / 渲染结果；Cookie 可用环境变量覆盖或由扩展同步到容器 volume 的 `data/douyin_cookie.json`。不登录或触发风控时会返回 0 条并让 init 继续。
- **YouTube**：如果要启用 `init --yes-youtube` 或 `fetch-youtube`，必须在装了扩展的宿主机浏览器里登录 https://www.youtube.com。后端不直接抓 YouTube；初始化只接收扩展回传的观看历史 / 订阅 / 点赞信号。不登录、页面布局变化或任务仍在后台跑时会返回 0 条并让 init 继续。
- **X**：如果要启用 X 初始化或 discovery，必须在宿主机浏览器里登录 https://x.com；扩展同步 `auth_token` + `ct0` 到容器 volume，后端用默认安装的 `twitter-cli` 做只读服务端重放。
- **知乎**：如果要启用知乎初始化或 discovery，必须在装了扩展的宿主机浏览器里登录 https://www.zhihu.com；事件、初始化和 search / hot / feed / creator / related discovery 都走插件任务。
- **Reddit**：如果要启用 Reddit 初始化或 discovery，必须在装了扩展的宿主机浏览器里登录 https://www.reddit.com，插件读取 saved / upvoted / subscribed，并把 `reddit_session` 同步到容器 volume 内的 rdt-cli credential store。日常 discovery 默认使用容器内随 OpenBiliClaw 安装的 `rdt-cli`；插件不可用时可在容器里手动运行 `rdt login`，未登录或命令后端不可用时会自动 fallback 到宿主机浏览器插件任务。
- **CDP 说明**：小红书、抖音、YouTube、知乎和 Reddit 插件 fallback 都走 Chrome 插件任务链路，不需要额外启动 CDP 调试 Chrome。`[sources.browser].cdp_url` 只保留给通用 Web / 自定义网页源的浏览器抓取场景。

详见 [配置参考 / sources.browser 段](modules/config.md#sourcesbrowser)。

## 快速开始

三种方式按省事程度排序。**无论选哪种，启动后端后都建议打开图形化引导页 `http://127.0.0.1:8420/setup/` 完成 AI 配置与前置检查**——它和桌面安装包是同一套首启向导：先选 Chat 连接类型、按需选 preset，创建或编辑第一条稳定-ID Chat 记录，同时保留已有 fallback、Embedding route 与共享 settings；完整路由管理使用设置页或 `openbiliclaw models ...`。随后选择初始化来源（B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit）并校验前置条件。

> ⚠️ **容器内「开始初始化」按钮不可用**：Docker 运行时后端会拒绝网页发起的图形化初始化（`unsupported_runtime`），向导页会直接给出替代命令。在 `/setup/` 完成配置和前置检查后，初始化本身在宿主机执行：
>
> ```bash
> docker exec -it openbiliclaw-backend openbiliclaw init
> ```
>
> 方式 C 的一行安装脚本会自动跑这一步，无需手动执行。

### 方式 A：预构建镜像（最快，无需克隆源码）

GHCR 上有随后端版本发布的多架构镜像（linux/amd64 + linux/arm64），下载一个 compose 文件即可启动：

```bash
mkdir -p ~/openbiliclaw && cd ~/openbiliclaw
mkdir -p litellm
curl -fsSLO https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docker-compose.prebuilt.yml
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/litellm/config.yaml -o litellm/config.yaml
umask 077
printf 'LITELLM_POSTGRES_PASSWORD=%s\nLITELLM_MASTER_KEY=sk-%s\n' \
  "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > .env
docker compose -f docker-compose.prebuilt.yml up -d
```

预构建路径必须同时保留 `docker-compose.prebuilt.yml` 与 `litellm/config.yaml`；后者与源码 Compose 使用完全相同的 retry/cache/logging policy。先在 `http://127.0.0.1:4000/ui` 配好三个 vNext 稳定别名；然后打开 `http://127.0.0.1:8420/setup/` 完成当前 legacy AI 配置与前置检查，再运行 `docker exec -it openbiliclaw-backend openbiliclaw init` 完成初始化。想固定版本，把 compose 文件里的 `latest` 换成具体版本号（如 `0.3.152`）。

升级到最新版本：

```bash
docker compose -f docker-compose.prebuilt.yml pull
docker compose -f docker-compose.prebuilt.yml up -d
```

> 后端能识别自己跑在容器里（install mode `docker`）：设置页「版本与更新」会定期检查新版镜像并提示上面这两条命令，「立即检查」可用；容器内无法就地自更新，误点应用会以 `docker_install_mode` 明确拒绝。

### 方式 B：源码构建（想改代码 / 本地定制）

```bash
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw
umask 077
printf 'LITELLM_POSTGRES_PASSWORD=%s\nLITELLM_MASTER_KEY=sk-%s\n' \
  "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > .env
docker compose up -d --build
```

同样打开 `http://127.0.0.1:8420/setup/` 完成 AI 配置与前置检查，再运行 `docker exec -it openbiliclaw-backend openbiliclaw init` 完成初始化。更新：`git pull && docker compose up -d --build`（Dockerfile 已做依赖分层，依赖没变时重建只需数秒）。

### 方式 C：一行安装脚本 / AI agent 部署（终端向导 + 自动 init）

想在终端里一路问答式完成配置 + 自动 init，用一行安装脚本：

```bash
# macOS / Linux / WSL2
MODE=docker curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

```powershell
# Windows PowerShell + Docker Desktop
$env:MODE="docker"; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

安装脚本会克隆 / 更新仓库，然后调用 `agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie`。bootstrap 的 Docker 顺序是：

1. 在宿主机终端先选 Chat connection type，再按 descriptor 选择 preset / credential / model，然后配置 ordered Embedding route、B 站 Cookie 获取方式，以及小红书 / 抖音 / YouTube 初始化 opt-in；X / 知乎 / Reddit 请在 `/setup/` 引导页或后端设置页里开启。Contract marker: human Docker one-line installer asks the Chat connection type first.
2. 写入宿主机 `config.toml`，并在 `.env` 生成缺失的 LiteLLM/PostgreSQL 基础设施密钥（已有非空值保留）。
3. `docker compose up -d --build` 启动后端、LiteLLM/PostgreSQL 和 Ollama embedding sidecar。
4. 把确认后的 `config.toml` / Cookie 文件同步到容器 `/app/runtime`。
5. 等浏览器扩展把 B 站 Cookie 推到 `http://127.0.0.1:8420/api/bilibili/cookie`。
6. 在容器运行时精确探测 stable primary Chat（不走 fallback），并逐条探测所有 ordered Embedding providers 与共享 settings。
7. 检查通过后自动运行 `openbiliclaw init`。

缺 LLM Key、缺 Cookie、缺来源确认时，bootstrap 会停在明确的 `needs_secrets` / `needs_decisions` 状态并打印继续命令；这不是最终成功状态。凭据和选择齐全后，bootstrap 会先做真实服务检查。如果返回 `service_check_failed`，说明 init 尚未运行，先修 API key / base_url / model / Ollama 后再重跑同一条安装或 bootstrap 命令。

AI agent 一句话部署时，`agent_bootstrap.py` 会在 auto-init 期间额外输出 `BOOTSTRAP_STATUS status=progress message=init_progress` 事件。Agent 应把这些 `1/4`、`2/4`、`3/4`、`4/4` 和发现补货进度及时转述给用户，而不是等最终 `init_complete` 后才汇报。

> 💡 **AI agent 一句话部署**：把下面这句粘到 Claude Code / Codex CLI / Cursor / OpenClaw：
> ```
> 请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md 的说明帮我用 Docker Compose 部署 OpenBiliClaw 后端（务必用 Bash 的 curl 下载这个文档，不要用 WebFetch）
> ```
> 跨平台一致：Mac / Windows / Linux 上 AI 都按同一份文档执行。

### 启动后的通用说明

- 新生成配置的 Embedding 第 1 项是稳定的 `ollama-docker` Provider，共享 model 为 `bge-m3`，endpoint 为 `http://ollama:11434/v1`；模板中远端项保留相对顺序。已有 `config.toml` 不参与 seed，因此不会被覆盖。
- **后端不再等 sidecar 拉完模型才启动**：`bge-m3` 首次下载（~568MB）期间后端已经可用，`/setup/` 的前置检查会显示 embedding 尚未就绪，拉取完成后自动通过。模型下载失败时 sidecar 守护进程仍在，重启 compose 会自动重试。
- B 站登录态推荐用浏览器扩展：扩展装在**宿主机浏览器**里，不在容器里。你登录 bilibili.com 后，扩展会把 Cookie 自动 POST 到 `127.0.0.1:8420` 的后端接口。
- 小红书 / 抖音 / YouTube / X / 知乎 / Reddit 都默认关闭，只有你在 `/setup/` 或设置页明确开启才会进入初始化和日常发现；启用时需在宿主机浏览器里装扩展并登录对应站点。镜像通过 pip 安装项目，X 的 `twitter-cli` 和 Reddit 的 `rdt-cli` 已内置。

健康状态随时可查：

```bash
docker compose ps          # 源码目录里；预构建方式加 -f docker-compose.prebuilt.yml
curl http://127.0.0.1:8420/api/health
curl http://127.0.0.1:4000/health/readiness
```

`docker compose ps` 应同时显示 backend、worker、LiteLLM、PostgreSQL 与 Ollama；worker 没有 HTTP health endpoint，可用 `docker compose logs worker` 检查 migration/consumer 启动。不要把 Huey 的 result storage 当成产品任务状态接口。

`/health/readiness` 只说明 proxy 可服务；三个 alias 的 deployment health 由 vNext `AIHealthService` 逐一检查 `/health?model=<alias>`，并只投影脱敏状态。注意 LiteLLM 的 model health 检查**可能真实调用 provider**，不要把它当成高频 liveness probe；当前 public API 尚未暴露该投影。任一 deployment 健康即 alias 可用，混合健康/失败会标为 `degraded`；transport、auth、未配置 alias、proxy server 和 provider unhealthy 分开分类。

**手动 fallback**：高级排查、CI 或重复初始化时，可以绕过安装脚本直接运行 bootstrap；如果只是想重跑 init，也可以进容器执行 init。

```bash
python3 scripts/agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie

docker exec -it openbiliclaw-backend openbiliclaw init
```

## 配置

一行安装脚本会先在宿主机生成 `config.toml`，再同步到 Docker volume 的 `/app/runtime/config.toml`。配置要改时，优先重跑同一条安装 / bootstrap 命令；高级排查时可以直接编辑容器内文件。

```bash
# 重新进入 Docker bootstrap 选择流程
python3 scripts/agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie

# 高级排查：直接编辑容器内配置
docker exec -it openbiliclaw-backend vi /app/runtime/config.toml
```

### 环境变量

可通过环境变量覆盖部分配置，在 `docker-compose.yml` 的 `environment` 中设置或启动时传入：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENBILICLAW_PROXY_HOST` | `host.docker.internal` | 代理主机地址 |
| `OPENBILICLAW_PROXY_PORT` | `7897` | 代理端口 |
| `OPENBILICLAW_PROXY_TIMEOUT` | `1.0` | 代理探测超时（秒） |

### 原生模型路由配置

安装脚本与 `/setup/` 都写原生 `[models]`。先选择 Chat connection type，
再仅对支持 preset 的类型选择 preset：

| Connection type | Preset / meaning |
|---|---|
| `openai_compatible` | `deepseek` / `openai` / `openrouter` / `custom` |
| `anthropic_compatible` | `anthropic` / `custom` |
| `gemini_api` | Google 原生 API，无 preset |
| `ollama` | 本地 runtime，无 preset |
| `codex_oauth` | 独立 OAuth 登录，无 preset |

DeepSeek、OpenAI 与 OpenRouter 都属于 OpenAI-compatible presets，不再
占据三个顶层 Provider 入口；Anthropic 官方与自定义 Messages 网关同理。
Chat 是 1–10 条同构记录的有序列表：第一条是 primary，其余按顺序是
fallback。每条记录有全局唯一稳定 ID；重排列表就是改变优先级。

Embedding 可关闭，或配置 1–10 个有序 Provider。所有 Provider 必须共享
同一个 model、输出维度、相似度阈值与多模态开关；endpoint 和 credential
属于各自记录。Docker 生成第一份配置时会 upsert 稳定 ID `ollama-docker`
到位置 1，并让模板中保留的远端记录维持原相对顺序；Chat route 不变。
如果 `config.toml` 已存在，bootstrap 不覆盖该文件，也不会再次 seed。

该 seed 与 `/setup/`、CLI、桌面和插件使用同一 descriptor-backed 原生 schema；
保存后的记录只经 ordered Chat / Embedding factories 构造
`RuntimeModelBundle`。Docker 没有独立的 legacy Provider registry 或模型配置
写入路径。

在容器内查看和编辑：

```bash
docker exec -it openbiliclaw-backend openbiliclaw models list
docker exec -it openbiliclaw-backend openbiliclaw models add --kind chat
docker exec -it openbiliclaw-backend openbiliclaw models add --kind embedding
docker exec -it openbiliclaw-backend openbiliclaw models move <STABLE_ID> --position <1-10>
docker exec -it openbiliclaw-backend openbiliclaw models probe <STABLE_ID>
```

终端 bootstrap 的新自动化参数是 `--connection-type`、条件式 `--preset`，
以及可重复的 `--embedding-endpoint TYPE[:PRESET]=BASE_URL`；全部
Embedding endpoint 共用一个 `--embedding-model`。`--provider` 只保留为
旧非交互脚本的 deprecated alias，新命令不要使用。

init 前置检查会精确探测 stable primary Chat（禁用 fallback），并逐一精确
探测全部 ordered Embedding providers 与共享 settings。任一失败都会返回
`service_check_failed`，不会用另一条成功连接掩盖错误。

## 日常命令

所有 CLI 命令通过 `docker exec` 在容器内执行：

```bash
# B 站认证登录
docker exec -it openbiliclaw-backend openbiliclaw auth login

# 可选：启用本地 Ollama 作为独立 embedding provider
docker exec -it openbiliclaw-backend openbiliclaw setup-embedding

# 手动触发内容发现
docker exec -it openbiliclaw-backend openbiliclaw discover

# 查看推荐
docker exec -it openbiliclaw-backend openbiliclaw recommend

# 查看用户画像
docker exec -it openbiliclaw-backend openbiliclaw profile
```

### 生命周期管理

```bash
# 启动（需要在项目目录）
docker compose up -d

# 停止
docker compose down

# 重新构建（代码更新后）
docker compose up -d --build

# 查看容器日志
docker compose logs -f openbiliclaw-backend
```

> **注意**：Docker 镜像在构建时打包代码，`git pull` 后必须加 `--build` 重新构建，否则容器内运行的仍是旧版代码。
> 如果发现画像内容缺失或功能不符合预期，首先尝试 `docker compose up -d --build` 重建镜像。

## 默认行为

- 后端对外监听 **`8420`** 端口
- 配置、数据、日志存放在 Docker named volumes 中：
  - `openbiliclaw_config` → `/app/runtime`（配置文件）
  - `openbiliclaw_data` → `/app/runtime/data`（SQLite 数据库等）
  - `openbiliclaw_logs` → `/app/runtime/logs`（日志文件）
- 健康检查地址：`http://127.0.0.1:8420/api/health`
- 容器设置为 `restart: unless-stopped`，异常退出后自动重启

## 数据与存储

Docker 部署默认与宿主机项目目录**完全隔离**，所有数据保存在 Docker named volumes 中。

### 查看日志

```bash
# 查看容器标准输出
docker compose logs -f

# 查看应用日志文件
docker exec -it openbiliclaw-backend cat /app/runtime/logs/openbiliclaw.log
```

### 备份数据

```bash
# 备份数据库
docker cp openbiliclaw-backend:/app/runtime/data ./backup-data

# 备份配置
docker cp openbiliclaw-backend:/app/runtime/config.toml ./config-backup.toml
```

### 彻底重置

删除所有 volumes 并重建，将清除所有数据（配置、画像、历史记录）：

```bash
docker compose down -v
docker compose up -d --build
```

## 网络与代理

### Clash 代理

容器启动时自动探测宿主机 Clash 代理（默认 `host.docker.internal:7897`）。发现可用代理，或容器环境中已显式设置 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 时，启动器会在用户没有明确选择的前提下设置 `OPENBILICLAW_NETWORK_MODE=system`，让海外客户端继承这些变量；本机回环与 `host.docker.internal` 仍加入 `NO_PROXY`。如需强制忽略容器代理，可显式设置 `OPENBILICLAW_NETWORK_MODE=direct`。

自定义代理端口：

```bash
export OPENBILICLAW_PROXY_PORT=7890
docker compose up -d --build
```

自定义代理主机：

```bash
export OPENBILICLAW_PROXY_HOST=192.168.1.100
docker compose up -d --build
```

### 宿主机 Ollama 作为 Chat connection

容器访问宿主机 Ollama 时使用 `host.docker.internal`。先保证宿主机
Ollama 接受 Docker Desktop / Engine 的连接，再把它加入有序 Chat list：

```bash
docker exec -it openbiliclaw-backend openbiliclaw models add \
  --kind chat --id host-ollama-chat --name "Host Ollama" \
  --connection-type ollama --model llama3 \
  --base-url http://host.docker.internal:11434/v1
```

用 `models move host-ollama-chat --position N` 调整优先级；列表第一项
自然成为 primary，不需要单独的 primary/fallback 开关。

### 宿主机 Ollama 作为 Embedding provider

Compose 已默认提供 sidecar。只有明确要改用宿主机 Ollama 时才先在
宿主机准备相同共享模型，然后编辑原生 ordered route：

```bash
ollama pull bge-m3
docker exec -it openbiliclaw-backend openbiliclaw setup-embedding
docker exec -it openbiliclaw-backend openbiliclaw models list
```

`setup-embedding` 只负责配置，不安装、启动或下载模型。向导中填写
`http://host.docker.internal:11434/v1`，共享 model 保持 `bge-m3`。
需要第二个 endpoint 时，用 `openbiliclaw models add --kind embedding`
追加记录；新增 Provider 必须保持同一 model/维度/阈值/多模态设置。
真实检查必须显式运行 `openbiliclaw models probe <STABLE_ID>`。

## 常见问题

**Q: 容器启动后如何确认服务正常？**

```bash
curl http://127.0.0.1:8420/api/health
```

**Q: 如何更新到最新版本？**

预构建镜像方式：

```bash
docker compose -f docker-compose.prebuilt.yml pull
docker compose -f docker-compose.prebuilt.yml up -d
```

源码构建方式（依赖分层缓存，依赖没变时重建只需数秒）：

```bash
git pull
docker compose up -d --build
```

**Q: 启动时报 `container name "/openbiliclaw-backend" is already in use`？**

两个 compose 文件（源码构建的 `docker-compose.yml` 和预构建的 `docker-compose.prebuilt.yml`）管理的是同一组固定容器名。从一种方式切到另一种前，先在旧目录里 `docker compose down`（数据在 named volume 里，不会丢）；或直接移除残留容器后重试：

```bash
docker rm -f openbiliclaw-backend openbiliclaw-ollama
```

**Q: 端口 8420 被占用怎么办？**

修改 `docker-compose.yml` 中的端口映射：

```yaml
ports:
  - "9090:8420"  # 宿主机 9090 → 容器 8420
```

**Q: 数据库出现问题怎么修复？**

如果数据库出现问题，可以在容器内运行 `docker exec openbiliclaw-backend openbiliclaw db-repair` 进行检查和修复。

**Q: 后端启动了、健康检查也通过了，但插件里没有推荐？**

最常见原因是没有执行过 `init`。容器启动只运行 API 服务器，用户画像需要通过 init 命令生成：

```bash
docker exec -it openbiliclaw-backend openbiliclaw init
```

也可以检查 health endpoint 确认画像状态：

```bash
curl -s http://127.0.0.1:8420/api/health | python -m json.tool
# 看 "profile_ready" 字段：false 或缺失都表示还需要跑 init
```

v0.3.80+ 后端会在首次同步到行为数据后自动尝试生成画像，但手动 init 能获得更完整的初始画像（包含历史标题、作者等上下文信息）。
