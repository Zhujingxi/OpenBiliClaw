# 账户侧定时同步设计

## 目标
补上一条“账户侧低频同步”链路，让 OpenBiliClaw 在初始化之后仍能周期性吸收 B 站账户上的长期信号，而不是只依赖插件实时事件、推荐反馈和聊天。

第一版同步对象：
- 观看历史
- 收藏夹内容
- 关注的 UP 主

不纳入第一版：
- 点赞列表

## 现状与问题
当前主线里：
- `openbiliclaw init` 会调用 `get_user_history()` 拉一批历史，生成初始画像。
- 之后的画像更新主要依赖：
  - 插件上报的实时行为
  - 推荐反馈
  - 对话学习

这导致两个问题：
1. 用户没开插件时发生的站内行为不会自动进入画像更新链。
2. 收藏夹和关注这种长期偏好信号虽然 API 已实现，但还没进入主流程。

## 核心方案

### 1. 采用“event + 定时同步”的混合模式
- 高频即时：继续保留现有 event 驱动链路，用于响应当下行为。
- 低频补偿：新增账户侧同步 loop，用于补长期信号。

推荐频率：
- `start` 后补偿检查：若距离上次同步超过 24 小时，立即同步一次。
- 后台定时同步：每 6 小时检查一次，到点才真正拉取。

### 2. 同步链路只做“补事件”，不发明第二套画像系统
账户同步获取到的数据全部先转成事件，再复用已有分析链：
- 历史 -> `view`
- 收藏 -> `favorite`
- 关注 -> `follow`

统一流程：
1. 拉取账户数据
2. 增量过滤
3. 映射为事件
4. 写入事件层
5. 调用 `SoulEngine.analyze_events()`
6. 必要时让现有 refresh / cognition 机制继续工作

这样能保证：
- 偏好层、画像层、认知更新仍只有一套规则
- 新增同步不会绕开现有学习架构

### 3. 采用增量同步，不做全量重灌
需要新增一个运行状态文件：
- `data/memory/account_sync_state.json`

至少记录：
- `last_history_view_at`
- `last_history_bvid`
- `last_favorites_sync_at`
- `favorite_signature`
- `last_following_sync_at`
- `following_signature`
- `last_account_sync_at`
- `last_sync_error`

各对象的增量策略：

#### 观看历史
- 每次拉最近 100 到 200 条
- 只接收 `view_at` 晚于 `last_history_view_at` 的项
- 若时间相同，用 `bvid` 做兜底去重

#### 收藏夹
- 先拉收藏夹列表，再在预算内拉收藏夹内容
- 用 `folder_id + media_ids` 生成签名
- 签名不变则跳过
- 若变了，只为新增条目写 `favorite` 事件

#### 关注
- 拉当前关注列表
- 用 `mid` 集合生成签名
- 签名不变则跳过
- 若变了，只为新增关注写 `follow` 事件

### 4. 失败降级
同步链必须是低优先级后台任务，不能影响 API 服务本身。

规则：
- 单类失败：
  - 记录日志
  - 写 `last_sync_error`
  - 继续其它类同步
- 整轮失败：
  - 不影响后端服务
  - 下个周期继续重试
- Cookie 失效：
  - 记录失败原因
  - 运行状态可暴露给 popup，但不同步直接中断服务

### 5. 与运行时状态的关系
第一版建议在 `runtime-status` 中补最少两项：
- `last_account_sync_at`
- `last_account_sync_error`

这样 popup 或后续诊断入口可以知道：
- 最近有没有成功同步过账户侧长期信号
- 是否需要用户重新登录

## 验收标准
- `start` 启动后会创建账户同步 loop
- 到达同步时间时，会拉取 history / favorites / following
- 仅新增数据会被转成事件
- 同步结果会进入 `SoulEngine.analyze_events()`
- 局部失败不影响 API 服务
- `runtime-status` 能返回最近一次账户同步状态
