# 贡献指南

感谢你有兴趣为 OpenBiliClaw 做贡献！

## 开发环境搭建

```bash
# 克隆项目
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw

# 推荐：使用 uv
uv sync

# 或使用 pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 代码规范

- 使用 **ruff** 进行代码格式化和 lint
- 使用 **mypy** 进行类型检查
- 遵循 PEP 8 命名规范
- 所有公开 API 需要 docstring

```bash
# 格式化
ruff format src/ tests/

# Lint
ruff check src/ tests/

# 类型检查
mypy src/
```

## 测试

```bash
# 运行所有测试
pytest

# 运行带覆盖率
pytest --cov=openbiliclaw
```

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new discovery strategy
fix: correct preference weight decay
docs: update memory design document
refactor: extract common LLM interface
test: add soul engine unit tests
```

## 浏览器插件开发

```bash
# 浏览器插件开发
cd extension
npm install
npm run build
npm test
```

## Skill 开发

Skill 定义为 `skills/<skill-name>/SKILL.md` 格式的 Markdown 文件。可参考 `skills/openbiliclaw-adapter/SKILL.md` 作为示例。

Skill 文件描述该 Skill 的能力边界、CLI bridge 命令列表，以及与主系统的集成工作流。参见 `skills/` 目录下的内置 Skill 示例，了解如何创建自定义 Skill。

## 文档更新清单

完成功能开发后，合入前请检查以下文档是否需要更新：

- [ ] `docs/modules/<模块>.md` — 更新"已实现功能"和"公开 API"
- [ ] `docs/changelog.md` — 追加变更记录
- [ ] `docs/modules/cli.md` — 如新增/修改了 CLI 命令
- [ ] `docs/modules/config.md` — 如新增了配置项
- [ ] `docs/architecture.md` — 如涉及跨模块交互变化
- [ ] `docs/index.md` — 如新增模块文档或状态变化

详见 [AGENTS.md](../AGENTS.md) 中的"文档更新要求"段落。

## 致谢

主干上的部分功能源自社区贡献者的实现，在此致谢：

- **探针「暂时忽略」搁置状态** — [@15515151](https://github.com/15515151) 在 [#82](https://github.com/whiteguo233/OpenBiliClaw/pull/82) 中提出并实现了中立/忽略态。主干实现（`83654613`）在其基础上改写为跨会话持久化的状态机，PR 因实现路径差异未直接合入，但方案与代码均来自该贡献。
