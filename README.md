<div align="center">

# 🦀 OpenBiliClaw

**你的 B 站专属 AI 朋友，比你更懂你想看什么**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

</div>

---

OpenBiliClaw 是一个开源的 Bilibili 个性化内容推荐 AI Agent。它不是一个冷冰冰的推荐算法，而是像一个真正了解你的朋友——理解你是什么样的人、为什么喜欢某些内容，然后主动在 B 站帮你发现你会喜欢但自己找不到的东西。

## ✨ 核心特性

- 🧠 **深度用户理解** — 五层网状记忆架构（事件→偏好→觉察→洞察→灵魂），从心理学角度理解你
- 🔍 **主动内容发现** — 多策略内容发现引擎，像资深 B 站用户一样帮你找好内容
- 💬 **有温度的推荐** — 不是"因为你看过类似视频"，而是像朋友一样解释为什么你会喜欢
- 🔄 **持续学习** — 苏格拉底式对话 + 行为分析，不断深化对你的理解
- 🔧 **Skill 系统** — 可扩展的技能架构，支持自定义发现策略
- 🔒 **隐私优先** — 所有数据和计算在本地运行

## 🏗️ 项目结构

```
OpenBiliClaw/
├── src/openbiliclaw/          # Python 后端核心
│   ├── agent/                 # Agent 编排和 Skill 系统
│   ├── soul/                  # 用户灵魂引擎 (深度画像)
│   ├── memory/                # 多层网状记忆系统
│   ├── discovery/             # 内容发现引擎
│   ├── recommendation/        # 推荐与表达引擎
│   ├── bilibili/              # B 站接入层 (API + Browser)
│   ├── llm/                   # 多模型 LLM 适配
│   └── storage/               # 数据存储层
├── extension/                 # Chrome 浏览器插件
├── skills/                    # 内置 Skill 定义
├── docs/                      # 项目文档
└── tests/                     # 测试
```

## 🚀 快速开始

> ⚠️ 项目处于早期开发阶段 (v0.1-dev)

### 安装

```bash
# 克隆项目
git clone https://github.com/OpenBiliClaw/OpenBiliClaw.git
cd OpenBiliClaw

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # macOS/Linux

# 安装依赖（开发模式）
pip install -e ".[dev]"
```

### 配置

```bash
# 复制配置模板
cp config.example.toml config.toml

# 编辑配置（设置 LLM API Key 等）
vim config.toml
```

### 运行

```bash
# 启动 Agent
openbiliclaw start

# 查看推荐
openbiliclaw recommend

# 查看用户画像
openbiliclaw profile
```

### Docker 一键启动后端

```bash
# 一键启动后端
docker compose up -d --build
```

默认行为：

- 后端对外监听 `8420`
- 配置、数据、日志全部存放在 Docker named volumes 中
- 健康检查地址为 `http://127.0.0.1:8420/api/health`
- 容器内对应路径分别是 `/app/runtime/config.toml`、`/app/runtime/data`、`/app/runtime/logs`

如果部署在远程服务器，也可以复用同一个 `docker-compose.yml`；是否加反向代理由部署方自行决定。

#### Docker 命令使用约定

建议区分两类命令：

- 生命周期管理：使用 `docker compose`
- 日常功能操作：优先使用 `docker exec`

原因：

- `docker compose` 默认依赖当前目录下的 `docker-compose.yml`
- `docker exec` 只依赖容器名，不受你当前所在目录影响

示例：

```bash
# 这类命令适合在项目目录执行
docker compose up -d
docker compose up -d --build
docker compose down

# 这类命令可以在任意目录执行
docker exec -it openbiliclaw-backend openbiliclaw auth login
docker exec -it openbiliclaw-backend openbiliclaw auth status
docker exec -it openbiliclaw-backend openbiliclaw init
```

如果你想在任意目录继续使用 `docker compose`，需要显式指定 compose 文件：

```bash
docker compose -f /Users/white/workspace/OpenBiliClaw/docker-compose.yml exec \
  openbiliclaw-backend openbiliclaw auth status
```

#### Docker 数据隔离说明

当前 Docker 部署默认与宿主机项目目录隔离：

- 不再读取宿主机根目录的 `config.toml`
- 不再把数据库、Cookie、画像、日志写回项目目录
- 所有运行时状态都保存在 Docker volumes 中

如果你需要彻底重置 Docker 内的状态：

```bash
docker compose down -v
docker compose up -d --build
```

这会清空容器内配置、数据库、Cookie 和日志。

如果你是从旧的 bind mount 版本迁移过来：

- 旧的宿主机 `config.toml`、`data/`、`logs/` 不会自动导入
- 需要按新的 volume 方案重新写入配置，或手动 `docker cp` 导入
- 旧宿主机文件会留在项目目录，但新容器不会继续使用它们

#### Docker 下的 Clash 代理行为

容器启动时会自动探测宿主机上的 Clash HTTP 代理：

- 默认探测地址：`host.docker.internal:7897`
- 探测成功：自动注入 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`
- 探测失败：直接按直连运行，不会阻塞容器启动
- 默认 LLM provider 仍保持你配置里的值，不会因为代理逻辑被改写

如果你的 Clash 端口不是 `7897`，可以在启动前覆盖：

```bash
export OPENBILICLAW_PROXY_PORT=7890
docker compose up -d --build
```

也可以同时覆盖主机名和探测超时：

```bash
export OPENBILICLAW_PROXY_HOST=host.docker.internal
export OPENBILICLAW_PROXY_PORT=7897
export OPENBILICLAW_PROXY_TIMEOUT=1.0
docker compose up -d --build
```

#### Docker 模式下如何配置

容器首次启动时会在 volume 中自动生成 `/app/runtime/config.toml`。

默认不需要你先手动编辑它。推荐直接执行交互式初始化：

```bash
docker exec -it openbiliclaw-backend openbiliclaw init
```

如果容器内缺少运行时配置或 B 站认证，`init` 会直接在终端里引导你完成：

- 选择默认 LLM provider
- 输入该 provider 的 API Key
- 输入 B 站 Cookie
- 验证通过后继续拉历史、生成画像，并按 `search + related_chain -> trending -> explore` 分阶段补首轮 discover 池
- 首轮网络拉取和 discover 可能持续几分钟，这不是卡死，而是在真实调用 B 站和 LLM
- 当前首轮 discover 已经做了保守受控并发优化，会并发拉少量 B 站请求并并发打分，但默认上限仍偏保守，优先避免 429
- 首轮会尽量把 fresh 候选池补到至少 `100` 条，再停下来，降低第一次 `recommend` 直接没内容的概率
- 运行中会直接打印 `补货阶段 1/3 ... 当前池子 0/100` 这类进度，方便判断还在工作还是已经卡住

这套引导会把运行时状态直接写入 Docker volumes：

- LLM 配置写入 `/app/runtime/config.toml`
- Cookie 写入 `/app/runtime/data/bilibili_cookie.json`
- 数据、画像和日志分别写入 `/app/runtime/data`、`/app/runtime/logs`

如果你要做服务器预置配置，也可以手动编辑容器内配置文件。最小示例如下：

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

说明：

- LLM 的 API Key 现在写在容器 volume 内的 `config.toml`
- `cookie` 可以留空，后续通过命令写入 `/app/runtime/data/bilibili_cookie.json`
- 数据和日志都不会落回宿主机项目目录
- 需要手动预置时，可用 `docker cp` 导出并回写 `/app/runtime/config.toml`

#### Docker 模式下如何认证和初始化

启动后先检查服务是否起来：

```bash
curl http://127.0.0.1:8420/api/health
```

首选流程只有一条命令：

```bash
docker exec -it openbiliclaw-backend openbiliclaw init
```

如果你要分步执行，也支持：

```bash
# 可先确认登录态
docker exec -it openbiliclaw-backend openbiliclaw auth status

# 单独登录，cookie 会持久化到 Docker volume
docker exec -it openbiliclaw-backend openbiliclaw auth login

# 非交互式写入 cookie，适合服务器脚本
docker exec openbiliclaw-backend \
  openbiliclaw auth login --cookie "SESSDATA=...; bili_jct=..."

# 首次拉取历史、生成画像并做一次 discover
docker exec -it openbiliclaw-backend openbiliclaw init

# 查看当前画像
docker exec -it openbiliclaw-backend openbiliclaw profile

# 查看推荐
docker exec -it openbiliclaw-backend openbiliclaw recommend
```

如果你以非交互方式运行 `init`，则仍需要提前准备好 `config.toml` 和 Cookie；非交互终端下不会进入问答引导。

如果你在 Docker 中使用 Clash，一般不需要手动给 `docker exec` 补 `HTTP_PROXY`。现在每次 `openbiliclaw` CLI 启动都会自动探测并按需注入代理。

如果想看日志：

```bash
docker compose logs -f openbiliclaw-backend
```

如果部署在远程服务器，命令不变；只需要把 `config.toml`、`data/`、`logs/` 放在服务器项目目录，并按需开放 `8420` 或挂自己的反向代理。

## 📖 文档

- [文档导航](docs/index.md) — 一站式文档入口
- [项目规格说明书 (SPEC)](docs/spec.md) — 完整的项目设计与规划
- [架构设计](docs/architecture.md) — 系统架构详解
- [记忆系统设计](docs/memory-design.md) — 多层网状记忆架构
- [变更日志](docs/changelog.md) — 各里程碑交付记录
- [LLM 模块](docs/modules/llm.md) · [B 站接入](docs/modules/bilibili.md) · [记忆系统](docs/modules/memory.md) · [灵魂引擎](docs/modules/soul.md) — 模块文档
- [CLI 参考](docs/modules/cli.md) · [配置参考](docs/modules/config.md) — 使用指南
- [开发指南](docs/contributing.md) — 如何参与贡献

## 🛠️ 技术栈

| 模块 | 技术 |
|------|------|
| 后端 | Python 3.11+ |
| 浏览器插件 | TypeScript + Chrome Extension (Manifest V3) |
| LLM | 多模型支持 (OpenAI / Claude / DeepSeek / 本地模型) |
| B 站交互 | bilibili-api-python + agent-browser |
| 存储 | SQLite + 向量索引 + JSON |
| Agent 框架 | 自研轻量框架 |

## 🤝 贡献

欢迎贡献！请查看 [开发指南](docs/contributing.md) 了解如何参与。

## 📄 License

[MIT](LICENSE)
