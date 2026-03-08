# 贡献指南

感谢你有兴趣为 OpenBiliClaw 做贡献！

## 开发环境搭建

```bash
# 克隆项目
git clone https://github.com/OpenBiliClaw/OpenBiliClaw.git
cd OpenBiliClaw

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装开发依赖
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

## Skill 开发

参见 `skills/` 目录下的内置 Skill 示例，了解如何创建自定义 Skill。

## 文档更新清单

完成功能开发后，合入前请检查以下文档是否需要更新：

- [ ] `docs/modules/<模块>.md` — 更新"已实现功能"和"公开 API"
- [ ] `docs/changelog.md` — 追加变更记录
- [ ] `docs/modules/cli.md` — 如新增/修改了 CLI 命令
- [ ] `docs/modules/config.md` — 如新增了配置项
- [ ] `docs/architecture.md` — 如涉及跨模块交互变化
- [ ] `docs/index.md` — 如新增模块文档或状态变化

详见 [AGENTS.md](../AGENTS.md) 中的"文档更新要求"段落。
