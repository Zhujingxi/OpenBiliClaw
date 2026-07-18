# OpenClaw Adapter Design

## Background

当前仓库已经有完整的用户学习与推荐主链：

- `memory/` 负责事件、偏好、画像与运行时状态持久化
- `soul/` 负责偏好分析、画像构建、反馈学习与对话学习
- `discovery/` 负责候选发现与相关性评估
- `recommendation/` 负责推荐排序、表达生成与反馈记录
- `runtime/` 已经负责账户增量同步、事件触发刷新和候选池补货

因此，OpenClaw 的接入目标不应该是重写这条主链，也不应该让 OpenClaw 直接依赖内部 engine 组合细节，而是增加一层稳定的 integration adapter。

## Goal

在不影响当前项目结构和现有模块职责的前提下，为 OpenBiliClaw 增加一个可被 OpenClaw 调用的 adapter 层，并把核心能力暴露为一组稳定 skill。

## Non-Goals

- 不重构现有 `memory/`、`soul/`、`discovery/`、`recommendation/`、`runtime/`
- 不把现有推荐与学习主链改造成 OpenClaw-first 架构
- 不让 OpenClaw 直接访问 SQLite、JSON 状态文件或内部模型
- 本轮不实现 HTTP/MCP 传输层
- 本轮不引入新的推荐策略或新的学习算法

## Chosen Approach

采用三层结构：

1. **领域主链保持不动**
   继续由现有 runtime 和 engine 持有系统状态与业务逻辑。
2. **新增 OpenClaw adapter**
   在 `src/openbiliclaw/integrations/openclaw/` 下封装依赖装配、输入输出结构、业务操作和异常翻译。
3. **对外暴露 OpenClaw skill**
   skill 只是 adapter 的外层协议映射，不承载核心业务逻辑。

这个方案的关键点是：

- `skill` 负责“给 OpenClaw 调用”
- `adapter` 负责“把 OpenBiliClaw 现有能力整理成稳定接口”
- 状态与学习闭环仍然留在 OpenBiliClaw 内核

## Directory Layout

新增目录与文件如下：

- `src/openbiliclaw/integrations/__init__.py`
- `src/openbiliclaw/integrations/openclaw/__init__.py`
- `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- `src/openbiliclaw/integrations/openclaw/errors.py`
- `src/openbiliclaw/integrations/openclaw/schemas.py`
- `src/openbiliclaw/integrations/openclaw/operations.py`
- `src/openbiliclaw/integrations/openclaw/skill.py`

测试：

- `tests/test_openclaw_adapter.py`
- `tests/test_openclaw_skill.py`

文档：

- `docs/modules/integrations.md`
- `docs/architecture.md`
- `docs/index.md`
- `docs/changelog.md`

## Bootstrap Strategy

`bootstrap.py` 负责构建一份可复用的 adapter services 容器，统一初始化并缓存以下依赖：

- `Config`
- `Database`
- `MemoryManager`
- `SoulEngine`
- `ContentDiscoveryEngine`
- `RecommendationEngine`
- `ContinuousRefreshController`
- `AccountSyncService`

装配方式尽量复用 `src/openbiliclaw/api/app.py` 当前的依赖组合逻辑，避免在 integration 层重新发明另一套初始化流程。

建议在 `bootstrap.py` 中提供：

- `OpenClawAdapterServices`
- `build_openclaw_adapter_services()`
- `build_openclaw_adapter()`

这样无论后续是 OpenClaw skill、CLI、还是别的集成入口，都可以复用同一套装配结果。

## Adapter Surface

第一阶段只暴露 5 个稳定 operation：

1. `sync_account()`
   触发一次账户侧增量同步，复用 `AccountSyncService.sync_now()`
2. `get_profile()`
   返回裁剪后的用户画像与偏好摘要
3. `recommend(limit=5, refresh_if_needed=True)`
   按需触发 refresh，并返回适合给 OpenClaw 消费的推荐列表
4. `submit_feedback(recommendation_id, feedback_type, note="")`
   持久化反馈，记录即时认知变化，并触发反馈后的学习/刷新检查
5. `get_runtime_status()`
   返回轻量运行时状态摘要

这些 operation 只返回 integration DTO，不直接暴露 `SoulProfile`、`Recommendation`、数据库 row 或 memory layer 原始结构。

## Skill Exposure

`skill.py` 负责把 operation 映射为 OpenClaw skill。

为避免当前仓库对未知的 OpenClaw SDK 产生硬依赖，本轮 skill 层采用“协议中立”的描述方式：

- 提供 `OpenClawSkillDescriptor`
- descriptor 中包含 `name`、`description`、`input_schema`、`handler`
- 提供 `build_openclaw_skills(adapter)` 返回 skill descriptor 列表

建议第一阶段 skill 名称：

- `openbiliclaw_sync_account`
- `openbiliclaw_get_profile`
- `openbiliclaw_recommend`
- `openbiliclaw_submit_feedback`
- `openbiliclaw_get_runtime_status`

如果后续确认了 OpenClaw 官方 SDK 的注册方式，只需要在 `skill.py` 最外层追加 SDK 映射，不需要修改 `operations.py`。

## Error Model

新增 integration 层异常，隔离内部错误语义：

- `OpenClawAdapterError`
- `AdapterInitializationError`
- `AdapterValidationError`
- `AdapterOperationError`

错误传播规则：

1. 内核模块保留原有异常
2. `operations.py` 捕获并翻译成 adapter 异常
3. `skill.py` 再把 adapter 异常转换成 OpenClaw 可消费的错误结果

这样可以避免 OpenClaw 反向依赖内部模块实现细节。

## Data Mapping Rules

`schemas.py` 负责定义 DTO，并承担以下约束：

- 输入结构只接受 OpenClaw 需要的字段
- 输出结构只暴露稳定、可序列化的字段
- adapter 内部完成模型裁剪、字段重命名和默认值处理

推荐返回中建议只保留：

- `recommendation_id`
- `bvid`
- `title`
- `up_name`
- `cover_url`
- `reason`
- `topic_label`
- `confidence`

画像返回中建议只保留：

- `initialized`
- `personality_portrait`
- `core_traits`
- `deep_needs`
- `top_interests`

## Testing Strategy

测试分两层：

### Adapter Tests

`tests/test_openclaw_adapter.py` 覆盖：

- operation 输入校验
- 调用正确的 runtime/engine
- 输出 DTO 裁剪
- 异常翻译
- 反馈后触发学习/刷新逻辑

这层测试优先使用 fake service 和 mock，不依赖真实 OpenClaw。

### Skill Tests

`tests/test_openclaw_skill.py` 覆盖：

- skill descriptor 名称与描述
- 输入 schema 绑定
- handler 是否调用正确 operation
- 成功/失败时的返回结构

## Documentation Impact

实现完成后至少同步更新：

- `docs/modules/integrations.md`
- `docs/architecture.md`
- `docs/index.md`
- `docs/changelog.md`

其中：

- `docs/modules/integrations.md` 记录 adapter 和 skill 的公开接口
- `docs/architecture.md` 增加 OpenClaw adapter 作为对外集成层
- `docs/index.md` 增加新模块文档导航
- `docs/changelog.md` 记录本次集成交付

## Acceptance

交付完成后应满足：

- 新增 `integrations/openclaw`，但现有核心模块职责不变
- OpenClaw 可通过 skill 调用账户同步、读取画像、拉取推荐、提交反馈和读取运行时状态
- adapter 不直接暴露内部模型或数据库结构
- 新增单元测试覆盖 adapter 和 skill 的成功路径与错误路径
- 文档清晰说明 integration 边界与公开 API

## Assumption

当前设计假设 OpenClaw 可以消费“Python 侧注册的 skill/handler”或等价描述结构。若后续确认 OpenClaw SDK 的具体注册 API 与本假设不同，调整范围应被限制在 `src/openbiliclaw/integrations/openclaw/skill.py`。
