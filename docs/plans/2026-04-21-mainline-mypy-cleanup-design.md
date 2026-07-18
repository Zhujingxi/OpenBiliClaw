# 主业务链 Mypy 清理 Design

## Background

当前 `mypy src/` 仍有大量存量错误。按主业务链范围缩小到
`runtime/api + recommendation/discovery/soul` 后，仍有 `47 errors / 10 files`。

这些错误不是 47 个彼此独立的小问题，而是少数几类宽类型契约在多层扩散：

1. **共享类型契约过宽**：`ToneProfile`、embedding service、JSON helper 等底层接口仍大量使用 `object`、宽字典或不完整返回类型。
2. **领域层直接消费宽类型**：`recommendation`、`discovery`、`soul` 中直接对 `object` 调 `.get()`、`.embed()`、`float()` 的写法较多。
3. **少量边缘签名不一致**：`runtime/api/dialogue` 中有一些函数签名与真实返回值不完全对齐。

如果直接逐文件补 `cast()` / `assert`，短期可以少量降错，但同一个类型问题会在多个模块来回出现，后续维护成本高。

## Goal

在不扩展到 `eval/`、第三方缺失 stub、浏览器适配器等非主链问题的前提下，清掉主业务链的类型错误，让以下范围内的 `mypy` 变干净：

- `src/openbiliclaw/runtime`
- `src/openbiliclaw/api`
- `src/openbiliclaw/recommendation`
- `src/openbiliclaw/discovery`
- `src/openbiliclaw/soul`

同时保持现有运行行为不变，避免把“类型清理”做成一轮行为重构。

## Non-Goals

- 不处理 `eval/` 目录里的类型错误
- 不处理 `claude_agent_sdk`、`playwright.async_api`、`aiohttp` 等外部 stub / 依赖缺失问题
- 不借机重构推荐、发现、灵魂链路的业务逻辑
- 不改 popup / extension 行为
- 不为了过 `mypy` 大量引入无意义的 `Any` 或全局 `# type: ignore`

## Approaches Considered

### 方案 A：局部错误逐点修补

做法：按 mypy 报错顺序逐行修，哪里红就补哪里。

优点：
- 立刻见到红线减少
- 不需要先整理共享契约

缺点：
- 同类问题会在多个模块反复修
- 容易堆出大量 `cast()` / `assert` / `type: ignore`
- 后期阅读成本高

### 方案 B：契约优先，再清主链实现（推荐）

做法：先收紧共享类型契约，再用统一契约回头清 `recommendation / discovery / soul / runtime / api`。

优点：
- 根因收敛，后续修改点更少
- 类型信息能顺着主链稳定传递
- 代码质量比“局部补丁”高

缺点：
- 第一批提交看起来不会立刻消掉最多错误
- 需要先梳理清楚共用类型边界

### 方案 C：先放宽 mypy 规则或局部忽略

做法：通过更宽的 config、模块级 `ignore_errors`、大面积 `type: ignore` 先压红。

优点：
- 最快把报告变短

缺点：
- 实际没有解决契约问题
- 未来继续清理时会失去真实信号

## Chosen Approach

采用 **方案 B：契约优先，再清主链实现**。

整体顺序分 3 批：

### 第一批：共享契约收紧

目标：把主链共用的底层类型源头先定准。

重点文件：

- `src/openbiliclaw/soul/tone.py`
- `src/openbiliclaw/llm/json_utils.py`
- `src/openbiliclaw/llm/embedding.py`
- `src/openbiliclaw/recommendation/delight.py`

这批主要处理：

- `TypedDict` 和返回类型不一致
- 过宽的 `object` / 宽字典输入
- embedding service 协议不完整

### 第二批：业务核心实现清理

目标：用统一契约修掉推荐、发现、灵魂链路里的主体错误。

重点文件：

- `src/openbiliclaw/recommendation/engine.py`
- `src/openbiliclaw/recommendation/curator.py`
- `src/openbiliclaw/discovery/engine.py`
- `src/openbiliclaw/discovery/strategies/explore.py`
- `src/openbiliclaw/soul/pipeline.py`
- `src/openbiliclaw/soul/layer_updaters.py`

这批主要处理：

- `object` 上的方法访问
- `float()` / `int()` 对宽类型的转换
- 列表/序列变型不匹配
- `ToneProfile`、embedding 协议在调用侧的落地

### 第三批：边缘与适配层收尾

目标：清掉主链边缘少量签名和返回类型问题，形成闭环。

重点文件：

- `src/openbiliclaw/api/app.py`
- `src/openbiliclaw/runtime/updater.py`
- `src/openbiliclaw/soul/dialogue.py`

这批主要处理：

- API handler 返回值与注解不一致
- runtime helper 返回 `Any`
- dialogue 工具分发路径的可空性

## Design Principles

### Fix at the Type Source

优先修“类型从哪里开始变宽”，而不是在每个使用点硬转。

### Avoid Fake Typing

除非确实无法表达，否则不使用：

- 模块级 `# type: ignore`
- 无解释的 `cast(Any, ...)`
- 只为了让 mypy 安静的大面积断言

### Preserve Behavior

本轮是类型清理，不改业务语义。必要的运行逻辑变动只允许发生在：

- 把原本隐式可空/宽类型显式化
- 把返回值注解调到与现状一致
- 把公共 helper 的输入输出定准

## Testing And Verification

每一批都做两类校验：

1. 类型校验
   - 先跑对应子目录的 `mypy`
   - 批次完成后再跑一次主链组合 `mypy`

2. 行为回归
   - 推荐/发现/运行时相关 pytest
   - 只跑与本批直接相关的测试集合，最后再跑一次主链总集合

建议最终验收命令：

```bash
uv run python -m mypy src/openbiliclaw/runtime src/openbiliclaw/api src/openbiliclaw/recommendation src/openbiliclaw/discovery src/openbiliclaw/soul
uv run python -m pytest tests/test_api_app.py tests/test_refresh_runtime.py tests/test_recommendation_engine.py tests/test_discovery_engine.py tests/test_tone_profile.py
uv run python -m ruff check src/openbiliclaw/runtime src/openbiliclaw/api src/openbiliclaw/recommendation src/openbiliclaw/discovery src/openbiliclaw/soul tests/
```

## Risks

### 类型修正触发行为分支变化

例如把可空值显式化后，某些之前“碰巧工作”的路径会暴露真实空值处理问题。需要靠现有测试兜住。

### 契约调整引发连锁修改

共享协议一旦收紧，会把上下游一起拉红。这是预期成本，不应回退为更宽类型。

### 清理范围滑出主链

一旦开始碰 `eval/`、外部缺失 stub、浏览器抓取适配器，这轮范围就会迅速失控。必须明确止损。

## Acceptance

- `mypy` 在主业务链范围内无错误：
  `runtime/api/recommendation/discovery/soul`
- 推荐、发现、运行时相关 pytest 绿
- 不新增全局 `ignore_errors` 或大面积 `type: ignore`
- 不改变推荐/发现/灵魂链路的用户可见行为
