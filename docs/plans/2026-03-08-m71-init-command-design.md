# 7.1 `openbiliclaw init` 设计

## 背景

当前仓库已经具备：

- B 站 Cookie 认证
- 历史 API 拉取
- 偏好层分析
- 灵魂画像生成
- 内容发现与推荐闭环

但这些能力还没有通过一个“首次运行”命令串起来。`openbiliclaw init` 的目标是把“认证后首跑”收敛成一个一键流程。

## 目标

- 新增 `openbiliclaw init`
- 流程包含：
  1. 校验运行时配置和 B 站认证
  2. 拉取用户历史
  3. 写入事件层
  4. 分析偏好
  5. 生成初始画像
  6. 自动执行一次 discovery
- CLI 输出阶段性进度与结果摘要

## 方案选择

### 方案 A：CLI 直接编排首跑流程（采用）

优点：
- 范围最稳，适合 `7.1`
- 不引入新的 orchestration 类
- 充分复用已有 `SoulEngine`、`BilibiliAPIClient`、`ContentDiscoveryEngine`

缺点：
- `cli.py` 会多一些 helper

### 方案 B：新增 `InitEngine`

优点：
- 分层更漂亮

缺点：
- 当前阶段过度设计，CLI 以外暂时没有第二个调用方

### 方案 C：把 init 逻辑塞进 `SoulEngine`

优点：
- 表面上入口更少

缺点：
- `SoulEngine` 不应直接依赖 B 站拉取和 discovery 编排，职责会变乱

## 设计

### CLI 流程

`openbiliclaw init` 运行步骤：

1. `_require_runtime_config()`
2. 构建 `AuthManager`，确认当前 Cookie 已认证
3. 构建 `BilibiliAPIClient` 拉取历史
4. 将历史映射成事件并写入 `MemoryManager`
5. 调用 `SoulEngine.analyze_events()`
6. 调用 `SoulEngine.build_initial_profile()`
7. 构建 `ContentDiscoveryEngine` 并自动跑一次 discovery

### Helper

- `_build_bilibili_client()`
- `_build_discovery_engine()`
- `_history_item_to_event()`

其中 discovery engine 需要注册当前已实现的 4 个策略：

- `SearchStrategy`
- `TrendingStrategy`
- `RelatedChainStrategy`
- `ExploreStrategy`

### 历史映射

每条 history item 至少映射为：

- `event_type = "view"`
- `title`
- `url = "https://www.bilibili.com/video/<bvid>"`
- `metadata`:
  - `bvid`
  - `author`
  - `view_at`

同时给 `build_initial_profile()` 传入精简后的历史摘要数据。

### 输出

CLI 采用阶段性输出：

- `1/4 拉取历史`
- `2/4 分析偏好`
- `3/4 生成画像`
- `4/4 发现内容`

最终摘要至少包含：

- 历史条数
- 画像是否生成
- discovery 发现条数

### 错误边界

- 认证失败：立即退出
- 历史为空：提示无法生成画像并退出
- discovery 失败：前面阶段保留成功结果，整体提示部分完成

## 测试策略

- `tests/test_cli.py`
  - 认证失效
  - 历史为空
  - 全流程成功
  - discovery 失败但 init 前半段成功
- 不做真实网络测试进入主门禁，全部用 fake auth/api/soul/discovery 组件
