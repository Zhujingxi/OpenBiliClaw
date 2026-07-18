# Docker Backend Deployment Design

## Background

当前项目已经具备可运行的本地后端：

- FastAPI 应用入口位于 `src/openbiliclaw/api/app.py`
- CLI `openbiliclaw start` 会启动本地 API 服务
- 配置、数据和日志默认使用仓库根目录下的 `config.toml`、`data/`、`logs/`

但仓库还没有提供标准化的容器构建与部署方案，因此：

- 本地单机无法通过 `docker compose up -d` 一步启动
- 服务器部署缺少统一入口
- 当前 `start` 命令默认监听 `127.0.0.1:8420`，不适合容器内对外暴露

## Goal

为后端提供一套同时适用于本地单机和远程服务器的 Docker 部署方案，满足：

- 本地开发者可用 `docker compose up -d` 一键启动后端
- 服务器环境可复用同一套容器与 compose 配置
- 容器运行时可以持久化配置、数据库、记忆数据和日志
- 不破坏现有 CLI 使用方式

## Non-Goals

- 本轮不引入 Nginx / Caddy 反向代理模板
- 不把整套配置系统改造成“纯环境变量驱动”
- 不把浏览器插件一起容器化
- 不处理 HTTPS、域名、TLS 自动签发

## Chosen Approach

采用以下组合：

1. `Dockerfile`
2. `.dockerignore`
3. `docker-compose.yml`
4. 新增专用 API 启动命令 `openbiliclaw serve-api`
5. 让 `start` 命令支持 `--host` / `--port`

推荐入口设计：

- `openbiliclaw start`：保留现有“启动 API 服务”的语义，默认仍面向本机开发
- `openbiliclaw serve-api --host 0.0.0.0 --port 8420`：作为容器和脚本的显式启动入口

这样做的原因：

- 容器场景下需要显式绑定 `0.0.0.0`
- 本地直接运行时继续保持原有认知模型
- Docker 和非 Docker 场景共用同一套后端逻辑

## Deployment Shape

### Container Image

- 基于 Python 3.11 slim
- 安装项目运行依赖
- 工作目录统一为 `/app`
- 默认命令使用 `openbiliclaw serve-api --host 0.0.0.0 --port 8420`

### Compose Service

默认服务 `openbiliclaw-backend` 包含：

- 端口映射：`8420:8420`
- 只读挂载：`./config.toml:/app/config.toml:ro`
- 可写挂载：`./data:/app/data`
- 可写挂载：`./logs:/app/logs`
- 重启策略：`unless-stopped`

### Persistence

以下内容必须落宿主机：

- `config.toml`
- `data/`
- `logs/`

如果不挂载：

- SQLite 数据库、画像、Cookie、运行态文件会丢失
- 日志不便于排查
- 容器重建后状态无法延续

## Local And Server Compatibility

### Local Single-Host

推荐流程：

1. 复制 `config.example.toml` 为 `config.toml`
2. 根据需要填写 API Key / Cookie
3. 运行 `docker compose up -d`
4. 访问 `http://127.0.0.1:8420/api/health`

### Remote Server

同样使用 `docker compose up -d`，额外注意：

- 如需公网访问，可直接开放 `8420`
- 更推荐在外层自行加反向代理
- `config.toml` 中不要写开发机专用的本地路径

## Code Changes

### CLI

在 `src/openbiliclaw/cli.py` 中：

- 为 `_run_api_server()` 增加显式 host/port 支持
- `start` 命令支持 `--host`、`--port`
- 新增 `serve-api` 命令，默认 `0.0.0.0:8420`

### Tests

在 `tests/test_cli.py` 中增加：

- `start` 默认 host/port 行为
- `start --host/--port` 覆盖
- `serve-api` 默认绑定 `0.0.0.0:8420`

如有必要，在 Docker 相关测试里只验证文件存在和命令文本，不做真实 Docker 集成测试进入 `pytest` 主门禁。

## Documentation

本轮需要同步更新：

- `README.md`
- `docs/modules/cli.md`
- `docs/modules/config.md`
- `docs/changelog.md`

如部署说明需要单独落地，可在 README 中新增 Docker 小节，不额外创建新模块文档。

## Acceptance

- 仓库中存在可构建的 Docker 镜像定义
- 存在可直接运行的 `docker-compose.yml`
- 容器启动后后端监听 `0.0.0.0:8420`
- `GET /api/health` 可返回正常响应
- CLI 文档与 README 明确给出 Docker 使用方式
