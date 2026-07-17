# Repository Guidelines

## 项目结构

权威后端位于 `src/openbiliclaw/`：

- `features/`：按 activity、profile、feed、library、chat、sources、system 拆分的领域与应用用例；
- `infrastructure/`：SQLAlchemy、Huey、LiteLLM/PydanticAI、来源 transport、加密等 adapter；
- `api/`：薄 FastAPI `/api/v1` feature routers 与 composition；
- `web/`：现有静态 Web；
- `extension/`：Chrome/Chromium 与 Firefox 扩展。

测试位于 `tests/`，vNext 合同测试位于 `tests/vnext/`。架构计划的唯一保留版本是 `docs/superpowers/plans/2026-07-17-backend-first-architecture-rebuild.md`。

## 构建与验证

```bash
uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run lint-imports
uv run pytest --cov=openbiliclaw
```

扩展修改还需运行：

```bash
cd extension
npm run api:check
npm run typecheck
npm test
npm run build
npm run build:firefox
```

运维 CLI 仅包含 `serve`、`worker`、`doctor`、`eval`、`db migrate`、`db backup`。产品工作流使用 Web、扩展或 `/api/v1`。

## 架构约束

- feature/domain 不依赖 FastAPI、SQLAlchemy、Huey 或 provider SDK。
- 平台条件只出现在对应 source package；connector 只返回规范化领域对象。
- AI feature 只通过 typed `TaskRunner` 或 embedding service 使用三个 LiteLLM alias。
- 可变产品设置存储在 vNext SQLite，并由设置 UI 与 `/api/v1/settings` 管理。
- 来源 credential 加密存储；provider credential 只在 LiteLLM。
- 不新增兼容 API、旧数据导入器、桌面应用、动态 source plugin 或平台账号写入。

## 编码与提交

Python 使用 4 空格、完整类型注解和简洁 docstring。Ruff complexity 上限为 12，MyPy 为 strict。测试命名 `test_<behavior>`。提交信息遵循 Conventional Commits。

不要提交 `.env`、Cookie、API key、device key、数据库或其他本地敏感数据。真实来源或模型调用必须显式标记和报告；默认测试使用 mock transport 或 PydanticAI test model。

## 文档要求

接口、模块边界、数据流、配置、CLI、依赖或外部集成变化必须同步：

- 相关 `docs/modules/*.md`；
- `docs/changelog.md`；
- 架构变化时同步 `docs/architecture.md`、`docs/spec.md`、README CN/EN 图；
- CLI 或设置变化时同步 `docs/modules/cli.md`、`docs/modules/config.md`；
- 安装变化时同步 `docs/installation.md`、`docs/agent-install.md`、`docs/docker-deployment.md` 和安装器输出；
- 旅程变化时同步 `docs/manual-e2e.md` 与 `docs/e2e/` runbook。

历史 v0.3 设计通过 Git 历史和 changelog 查阅，不在 active docs 中保留可执行旧命令或 endpoint。
