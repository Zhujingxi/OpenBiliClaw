# Repository Guidelines

## 项目结构与模块组织
主代码位于 `src/openbiliclaw/`：`core/` 提供生命周期与契约，`composition/` 负责唯一生产组合根，`content/` 与 `access/` 负责来源接入，`observations/`、`understanding/`、`recommendation/`、`application/`、`assistant/` 承载产品链路，`hosts/` 提供 API/CLI。Python 测试位于 `tests/`。Vue 3/Pinia/TypeScript 前端位于 `frontend/`，其中 `apps/web/` 是 responsive Web，`apps/extension/` 是浏览器扩展；`extension/` 仅保留声明式 manifest 与图标。

当前设计依据是 `docs/architecture.md`、`docs/spec.md` 与 `docs/modules/`。历史计划和已被 cutover 取代的文档不属于仓库当前规范。

## 构建、测试与开发命令
先创建虚拟环境并安装开发依赖：`pip install -e ".[dev]"`。常用检查命令如下：

```bash
ruff format src/ tests/ scripts/
ruff check src/ tests/ scripts/
mypy src/ tests/
pytest
pytest --cov=openbiliclaw --cov-branch
```

本地验证 CLI 使用 `openbiliclaw check`，启动服务使用 `openbiliclaw serve`；产品读写通过 `/v1` API 与 Vue 客户端完成。如修改配置相关逻辑，请同步验证 `openbiliclaw check --config PATH`。修改前端后运行 `frontend/package.json` 中的 format、lint、typecheck、test 与 build gates。

## 开发顺序与配置约定
按 `docs/architecture.md` 的依赖方向和 `docs/spec.md` 的产品契约开发，不得绕过 Application workflow 或 production composition 增加平行实现。配置样例使用 `config.example.toml`；本地调试时基于它生成 `config.toml`，并仅在本机保存凭据引用。模型由应用外部服务，通过 PydanticAI native provider 层接入；本应用不托管模型。

## 编码风格与命名约定
Python 统一使用 4 空格缩进、类型注解和清晰的模块边界；公开 API 与核心数据结构应补充简洁 docstring。格式化与 lint 由 Ruff 管理，静态类型检查使用 MyPy 严格模式。模块文件名使用小写下划线风格；测试函数采用 `test_<behavior>` 命名。前端只提交 TypeScript/Vue 源码，不提交手写 JavaScript。

## 测试要求
新增功能默认同时补充单元测试；涉及真实内容站点或模型服务的流程，优先拆成可 mock 的单元测试，并将真实调用保留为显式 opt-in 集成测试。仓库要求 aggregate branch coverage 不低于 90%，新增或保留模块也不得以 aggregate coverage 掩盖低覆盖。提交前至少运行 pytest、MyPy 与 Ruff；改动 frontend 时运行全部 frontend gates。

## 提交与 Pull Request 要求
提交信息遵循 Conventional Commits，例如 `feat: add bilibili auth status command`、`fix: validate missing api key`。PR 说明应包含：变更摘要、测试命令与结果、关联任务或文档入口；如改动 CLI 输出或插件页面，请附终端输出或截图。不要提交真实 `config.toml`、Cookie、API Key 或其他本地敏感数据。

## 文档更新要求（强制）
每次提交、合回 main 或发版，以及任何改动接口、模块边界、数据流、配置、CLI、依赖或对外集成的变更，均强制按范围同步模块文档、变更日志、架构图、CLI 与配置文档、安装器文档；权威逐项清单见 [CLAUDE.md「Documentation Requirements」](CLAUDE.md#documentation-requirements)。

AGENTS.md 面向可能不会自动加载 CLAUDE.md 的非 Claude agent，因此本义务在此独立生效：即使未自动读取 CLAUDE.md，也必须打开上述链接并遵循清单，缺少相应文档更新的分支不得合入。
