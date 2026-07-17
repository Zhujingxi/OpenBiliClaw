# Docker 部署（vNext）

[← README](../README.md)

Docker 是推荐安装方式。Compose 运行一次性 `migrate`、`api`、独立 `worker`、
LiteLLM Proxy 和 LiteLLM PostgreSQL。OpenBiliClaw 应用数据仍是 SQLite；
PostgreSQL 只属于 LiteLLM Admin 与其配置。

## 快速开始

源码构建：

```bash
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw
MODE=docker bash scripts/install.sh
```

预构建镜像：

```bash
mkdir -p ~/openbiliclaw/litellm
cd ~/openbiliclaw
curl -fsSLO https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docker-compose.prebuilt.yml
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/litellm/config.yaml -o litellm/config.yaml
python3 /path/to/OpenBiliClaw/scripts/agent_bootstrap.py --project-dir "$PWD" --mode docker
```

The prebuilt bootstrap command requires the repository script to be available. For
the simplest supported path, use the source checkout command above; it still runs all
services in containers.

## 私密环境

安装器在 `.env` 中生成并幂等复用：

- `LITELLM_POSTGRES_PASSWORD`
- `LITELLM_MASTER_KEY`
- `OPENBILICLAW_SECRET_KEY`
- `OPENBILICLAW_ACCESS_TOKEN`

文件以 mode `0600` 原子写入并被 Git 忽略。不要把 provider key 写进该文件或
Compose；provider credentials 只进入 LiteLLM Admin。`OPENBILICLAW_SECRET_KEY`
加密来源账户，`OPENBILICLAW_ACCESS_TOKEN` 保护 `/api/v1` 业务接口。

## 数据与 queue 一致性

API 与 worker 都挂载：

```text
openbiliclaw_data:/app/runtime/data
```

并使用完全相同的路径：

```text
OPENBILICLAW_DATABASE_URL=sqlite:////app/runtime/data/vnext/openbiliclaw.db
OPENBILICLAW_HUEY_PATH=/app/runtime/data/vnext/huey.db
```

应用 SQLite 和 Huey SQLite 是两个文件。Huey result store 是 transport 状态；
用户可见 job 状态以应用库 `job_runs` 为准。不要给 API 与 worker 配不同 volume
或 host path，否则任务会排入 worker 看不到的 queue。

## Migration 所有权

`migrate` 是 Compose 中唯一执行 `openbiliclaw db migrate` 的服务。`api` 与
`worker` 都以 `service_completed_successfully` 等待它，并在自身 startup 只读检查
应用库 revision 是否等于 Alembic head，不再竞争 SQLite DDL。migration 失败时两个
长期服务都不会启动；重复 `docker compose up` 可安全重跑幂等 migration。Source / uv
安装仍由 installer 在启动 API/worker 之前完成同一 migration。

`SKIP_START=1 MODE=docker bash scripts/install.sh` 仍会执行
`docker compose run --rm migrate`，只是不启动长期服务。因此 `*_runtime_prepared`
明确表示数据库已经迁移，而不是只生成了 `.env`。

## LiteLLM

Admin 只绑定 `127.0.0.1:${LITELLM_PORT:-4000}`。打开
`http://127.0.0.1:4000/ui`，配置 provider credentials 与 routing，并建立：

- `obc-interactive`
- `obc-analysis`
- `obc-embedding`

LiteLLM 负责 provider、fallback、cooldown、网络 retry、rate limit、budget 和
cache。OpenBiliClaw 不提供重复的 provider editor。

## 健康检查

```bash
docker compose ps
curl -fsS http://127.0.0.1:8420/api/v1/system/readiness
curl -fsS http://127.0.0.1:4000/health/readiness
docker compose logs worker
```

API public readiness 只表示进程可服务。Installer 还使用生成的 bearer secret
请求受保护 settings 端点，确保 access control 与数据库依赖实际可用；它不会
把 token 输出到终端。Docker installer 同时解析 `docker compose ps --all --format
json`，要求 `migrate` 已成功退出且 API、worker 都是 `running/healthy`；worker
healthcheck 不只检查文件存在，而是验证容器 PID 1 是正式 worker、应用 schema 位于
Alembic head、Huey SQLite 完整且能取得可回滚的写事务。`restarting`、`exited` 或
`unhealthy` worker 都会使安装非零失败，不能被 API readiness 掩盖。受保护 API probe
完成后 installer 会再次读取相同 Compose 状态，probe 期间转为 crash-loop 也会失败。
Queue writable probe 会在 `BEGIN IMMEDIATE` 中执行真实 `CREATE`/`INSERT` 后 rollback，
不留下 durable artifact，并在 SQLite connection 前后确认 pathname 仍指向 held inode。Source installer
在 migration 后启动 API/worker、等待 queue
文件就绪，再运行 `openbiliclaw doctor` 检查应用数据库、access token 和 LiteLLM
配置；任一步失败都会停止这两个新进程并非零退出。

## 来源与 onboarding

七个 connector 在 composition 时显式注册，不是动态 plugin。来源默认关闭；
通过 `/api/v1/sources` 写入账号配置，通过 `/api/v1/onboarding` 启动 bootstrap。
浏览器辅助任务统一使用 `/api/v1/source-tasks/claim` 与 completion endpoint。

现有 static Web/extension 仍被挂载，但其 vNext API client wiring 在 Task 22；
在此之前用 OpenAPI/API tests 验证，不要使用旧 UI 配置来源或 AI。

## 运维

```bash
docker compose up -d --build
docker compose pull
docker compose logs -f api worker
docker compose down
```

`down` 不删除 named volumes。只有明确需要销毁 vNext 数据时才使用 `down -v`；
旧数据文件保持原样作为手工 archive，不做自动导入。
