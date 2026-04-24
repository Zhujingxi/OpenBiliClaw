# 📖 OpenBiliClaw 文档导航

> 本页面是项目文档的一站式入口。

## 项目概览

- [项目规格说明书 (SPEC)](spec.md) — 完整的项目设计与规划
- [v0.1 开发任务清单](v0.1-todolist.md) — 当前版本的开发主线
- [架构设计](architecture.md) — 系统架构与模块关系
- [记忆系统设计](memory-design.md) — 多层网状记忆架构详解
- [变更日志](changelog.md) — 各里程碑交付记录
- [GitHub Releases](https://github.com/whiteguo233/OpenBiliClaw/releases) — 从 `extension-v*` 下载插件、从 `backend-v*` 下载后端桌面包
- [手动端到端联调](manual-e2e.md) — CLI、插件与 SQLite 的真实联调步骤
- [OpenClaw 接入最短指南](openclaw-quickstart.md) — Docker 优先、本地兜底的安装、初始化、skill 发现与 CLI bridge 自检
- [Agent 机器契约 (短)](agent-install.md) — 给 AI 智能体 WebFetch 的短契约,配合 README 的短粘贴语句
- [Agent 部署详细说明](agent-deployment.md) — 给人看的详细版本 + 所有 JSON 事件/错误码/排查表
- [Docker 部署指南](docker-deployment.md) — 手动 Docker / docker compose 部署步骤

## 模块文档

| 模块 | 文档 | 对应代码 | 状态 |
|------|------|----------|------|
| LLM 多模型支持 | [modules/llm.md](modules/llm.md) | `src/openbiliclaw/llm/` | ✅ M2 完成 |
| B 站接入层 | [modules/bilibili.md](modules/bilibili.md) | `src/openbiliclaw/bilibili/` | ✅ M3 完成 |
| 记忆系统 | [modules/memory.md](modules/memory.md) | `src/openbiliclaw/memory/` | ✅ 完成 |
| 灵魂引擎 | [modules/soul.md](modules/soul.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 内容发现引擎 | [modules/discovery.md](modules/discovery.md) | `src/openbiliclaw/discovery/` | ✅ 完成 |
| 推荐引擎 | [modules/recommendation.md](modules/recommendation.md) | `src/openbiliclaw/recommendation/` | ✅ 完成 |
| 灵魂管线架构 | [modules/soul-pipeline-architecture.md](modules/soul-pipeline-architecture.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 浏览器插件 | [modules/extension.md](modules/extension.md) | `extension/` | 🔄 M8 进行中（popup 已支持推荐/画像/聊天/通知，并完成亮色 UI 刷新） |
| CLI 命令参考 | [modules/cli.md](modules/cli.md) | `src/openbiliclaw/cli.py` | ✅ 持续更新 |
| 配置参考 | [modules/config.md](modules/config.md) | `config.example.toml` | ✅ 持续更新 |
| 集成适配层 | [modules/integrations.md](modules/integrations.md) | `src/openbiliclaw/integrations/` | ✅ OpenClaw adapter 已接入 |

## 开发指南

- [贡献指南](contributing.md) — 环境搭建、代码规范、文档更新要求
- [AGENTS.md](../AGENTS.md) — AI 代理开发规则（含文档更新强制要求）
