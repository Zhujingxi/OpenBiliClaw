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
