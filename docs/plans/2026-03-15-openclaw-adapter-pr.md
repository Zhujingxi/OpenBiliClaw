# PR 描述：OpenClaw Workspace Skill Adapter

## 摘要

本次变更为 OpenBiliClaw 新增一层独立的 OpenClaw integration adapter，在不改动现有学习、发现、推荐主链职责的前提下，把核心能力暴露为可被 OpenClaw 调用的 skill/CLI 接口。

对外接入边界落在 `src/openbiliclaw/integrations/openclaw/`，并通过仓库根目录 `skills/openbiliclaw-adapter/SKILL.md` 提供真实 workspace skill。OpenClaw 侧通过 JSON CLI bridge 调用 adapter，而不是直接耦合内部 runtime 或数据库结构。

## 主要改动

- 新增 OpenClaw adapter bootstrap、DTO、operation、异常边界和协议中立 skill descriptor
- 新增 JSON CLI bridge，支持 `sync-account / get-profile / runtime-status / recommend / submit-feedback / doctor / emit-skill-descriptors`
- 新增仓库级 workspace skill pack：`skills/openbiliclaw-adapter/SKILL.md`
- 调整 OpenClaw `recommend` 默认行为为快路径，避免交互入口无条件触发重 refresh
- 为显式 refresh 增加超时/失败回退，异常时自动退回缓存推荐
- 补充 adapter / skill / CLI 测试
- 补充 OpenClaw 接入最短指南与集成层文档
- 顺手修复 `discovery/strategies/strategies.py` 中的 `mypy` 遗留类型问题

## 影响范围

- 新增模块：`src/openbiliclaw/integrations/openclaw/`
- 新增 skill pack：`skills/openbiliclaw-adapter/`
- 新增文档：`docs/openclaw-quickstart.md`
- 更新文档：`docs/modules/integrations.md`、`docs/architecture.md`、`docs/changelog.md`、`docs/index.md`
- 修复类型：`src/openbiliclaw/discovery/strategies/strategies.py`

## 测试

已执行：

```bash
uv run python -m pytest tests/test_openclaw_adapter.py tests/test_openclaw_skill.py tests/test_openclaw_cli.py -q
uv run ruff check src/openbiliclaw/integrations src/openbiliclaw/discovery/strategies/strategies.py tests/test_openclaw_adapter.py tests/test_openclaw_skill.py tests/test_openclaw_cli.py
uv run mypy src/
```

额外真实冒烟：

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli doctor
uv run python -m openbiliclaw.integrations.openclaw.cli get-profile
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 3
uv run python -m openbiliclaw.integrations.openclaw.cli emit-skill-descriptors
```

## 使用说明

- OpenClaw 安装与初始化说明见：`docs/openclaw-quickstart.md`
- OpenClaw workspace skill 入口见：`skills/openbiliclaw-adapter/SKILL.md`
- 默认推荐入口应优先使用快路径：

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 3
```

- 只有明确需要新鲜度检查时，才使用：

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 3 --refresh-if-needed
```
