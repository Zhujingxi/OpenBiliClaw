# Spec: 小红书自发布内容过滤

**日期**: 2026-05-27
**状态**: Spec complete
**问题**: 开启小红书信息源后，推荐内容中会出现用户自己发布的笔记

---

## 问题分析

### 现象

用户在小红书登录状态下浏览时，扩展采集到的笔记元数据中可能包含用户自己发布的内容。这些内容进入 `content_cache` 推荐池后，会被当作推荐候选项推送给用户。

### 根因

系统已有 `_is_self_authored_note()` 过滤机制（v0.3.48+），但存在 **3 个漏洞**：

#### 漏洞 1: `_cache_xhs_notes` 仅按 nickname 匹配，`_purge_self_authored_pool_items` 也仅按 nickname

`_is_self_authored_note()` (`app.py:3923-3938`) 支持 `user_id` 和 `nickname` 双匹配，但 `_purge_self_authored_pool_items()` (`app.py:3940-3973`) 的 SQL 只按 `up_name`（即 nickname）过滤。如果用户改了昵称，旧昵称的笔记无法被清理。

此外，`content_cache` 表中 `author_name` 列虽存在，但 purge 逻辑没有使用它，也没有存储 `author_id`。

#### 漏洞 2: 搜索任务结果中裸 URL 的 notes 元数据可能缺少 author 字段

`xhs_task_result()` (`app.py:4181-4269`) 中：
- `notes` 路径有过滤（`_cache_xhs_notes` + `_is_self_authored_note`）✓
- 但扩展采集搜索结果页时，note 的 `author` / `author_id` 字段可能为空（搜索结果卡片上不一定展示完整作者信息），导致 `_is_self_authored_note` 返回 `false`

#### 漏洞 3: self_info 未持久化时的首次启动窗口

如果扩展升级后首次报告 self_info 之前，已有一批 notes 通过 `_cache_xhs_notes` 进入池中（`self_info=None` 且 `_load_xhs_self_info()` 返回空 dict），这些 notes 不会被过滤。虽然 startup purge（`app.py:5297-5310`）会在下次启动时清理，但在同一次运行中的窗口期内，自发布内容可能已被推荐。

---

## 修复方案

### 方案 A: 推荐出口守卫（Serve-time filter）— **推荐**

在推荐候选项从 `content_cache` 进入推荐流程的最后一步，增加 self_info 交叉检查。

**优点**:
- 单一守卫点，覆盖所有入口路径（passive、task-result、bootstrap）
- 不需要修改每条入口的过滤逻辑
- 即使入口过滤遗漏，出口也能兜底

**实现位置**: `get_pool_candidates()` SQL 中增加条件。不要只在 `RecommendationEngine._load_pool_candidates()` 返回后过滤，否则 `count_pool_candidates()` / `count_pool_readiness()` 等“可推荐数量”口径仍会把自发布内容算作可换库存，继续造成 UI count 与实际 serve 结果漂移。

**具体做法**:

1. 给 serving/readiness 相关 DB 方法增加显式的 `xhs_self_nickname` 参数，默认空字符串以保持现有调用兼容。不要让 `Database` 直接读取 `discovery_runtime_state`；runtime state 属于 `memory_manager` / `RuntimeContext` 边界。

2. 在 `get_pool_candidates()` 的 WHERE 子句中，对 `source_platform = 'xiaohongshu'` 的行增加排除条件。注意 nickname 为空时必须 no-op，不能把空作者的 XHS 行误杀：
   ```sql
   AND (
     ? = ''
     OR COALESCE(source_platform, '') != 'xiaohongshu'
     OR (
       LOWER(COALESCE(up_name, '')) != LOWER(?)
       AND LOWER(COALESCE(author_name, '')) != LOWER(?)
     )
   )
   ```
   其中三个 `?` 都是持久化的 `xhs_self_info.nickname`。

3. 同步应用到 `count_pool_candidates()` 与 `count_pool_readiness()` 的 available/pending/raw 读取口径。否则推荐出口已挡住，但“还有 N 条可换”仍可能包含自发布内容。

4. 同时在 `_purge_self_authored_pool_items` 中增加 `author_name` 列的匹配（目前只查 `up_name`）。

**局限**: 依赖 nickname 匹配；如果 note 的 author 字段为空且 DB 中 `up_name` / `author_name` 也为空，出口 SQL 仍无法识别。这个缺口需要 P1/P2 继续降低概率。

### 方案 B: 入口层加固 + 延迟 purge

加固每条入口路径的过滤，并在 self_info 首次到达时触发一次 purge。

**实现**:
1. `_persist_xhs_self_info` 中，当 `self_info` 从空变为非空或内容变化时，立即对已入池的 xiaohongshu 行执行 `_purge_self_authored_pool_items`
2. `_purge_self_authored_pool_items` 增加 `author_name` 列匹配
3. 在扩展侧确保搜索结果 notes 尽量携带 author 字段

**缺点**: 入口多、路径分散，仍可能遗漏新增入口。

### 推荐: A + B 结合

- **方案 A** 作为兜底守卫（必做）
- **方案 B** 的第 1、2 点作为纵深防御（推荐做）

### 关键边界

- `Database` 保持纯存储层，不直接读取 runtime state；由 `RecommendationEngine` / `ContinuousRefreshController` 从 `memory_manager.load_discovery_runtime_state()` 取 `xhs_self_info.nickname` 后传给 DB。
- “推荐出口”不只包含 `get_pool_candidates()`，还包含影响同一用户体验的 servable count/readiness。至少需要覆盖 `get_pool_candidates()`、`count_pool_candidates()`、`count_pool_readiness()`。
- `get_pool_candidates_needing_evaluation()`、`get_pool_candidates_needing_copy()`、`get_pool_candidates_needing_delight_score()` 属于后台整理链路。P0 可以用 purge 阻断已知自发布内容进入这些队列；如实现成本不高，也应传入同一 `xhs_self_nickname`，避免自发布内容继续消耗分类/文案/评分预算。
- `count_pool_candidates_by_source()` / `get_pool_distribution_counts()` 是 discovery planning 口径。P0 不要求必须加出口 guard，但即时 purge 后它们不应继续统计已识别的自发布内容；若后续发现 source quota 被污染，再把同一过滤参数扩展过去。

---

## 实现清单

### P0（必做）

1. **`get_pool_candidates()` 增加 self-author 排除**
   - 文件: `src/openbiliclaw/storage/database.py`
   - 在两个 SQL 分支（`max_per_topic_group <= 0` 和 `> 0`）的 WHERE 子句中，对 `source_platform = 'xiaohongshu'` 的行排除 `up_name` 或 `author_name` 匹配 self nickname 的行
   - `get_pool_candidates` 接收 `xhs_self_nickname` 参数；由推荐层传入，不在 DB 内部查询 runtime state
   - 同步处理 `count_pool_candidates`、`count_pool_readiness`
   - 尽量处理 `get_pool_candidates_needing_evaluation`、`get_pool_candidates_needing_copy`、`get_pool_candidates_needing_delight_score` 等后台整理查询，避免自发布内容继续消耗 LLM 预算

2. **`_purge_self_authored_pool_items()` 增加 author_name 匹配**
   - 文件: `src/openbiliclaw/api/app.py`
   - 当前只按 `up_name` 匹配，增加 `OR LOWER(COALESCE(author_name, '')) = LOWER(?)` 条件

3. **self_info 首次到达时触发即时 purge**
   - 文件: `src/openbiliclaw/api/app.py`
   - 在 `_persist_xhs_self_info` 中，当 `existing != self_info`（即首次或变更），调用 `_purge_self_authored_pool_items`
   - 避免仅依赖 startup purge，缩短窗口期

4. **推荐层 / runtime 层传递 self nickname**
   - 文件: `src/openbiliclaw/recommendation/engine.py`
   - 文件: `src/openbiliclaw/runtime/refresh.py`
   - `RecommendationEngine` 和 `ContinuousRefreshController` 从 `memory_manager` 读取 `xhs_self_info.nickname`，传给 DB 的候选读取与 count/readiness 方法
   - CLI 构建推荐引擎时同样传入 self-info provider，保持命令行推荐路径一致

### P1（推荐）

1. **扩展侧搜索结果采集增加 author 字段**
   - 文件: `extension/src/content/xhs/passive.ts`
   - 文件: `extension/src/content/xhs/task-executor.ts`
   - 确保搜索结果卡片的 author 信息被提取并随 notes 上报
   - 这是扩展侧改动，本 spec 仅提需求，具体实现另行跟踪

### P2（可选）

1. **content_cache 增加 `author_id` 列**
   - 当前 `cache_content()` 不存储 author_id，导致无法按 user_id 过滤
   - 若扩展能提供 author_id，可在 purge 和 pool query 中增加双重匹配
   - 涉及 schema migration，ROI 需评估

---

## 影响范围

- `src/openbiliclaw/storage/database.py` — `get_pool_candidates` 及衍生方法
- `src/openbiliclaw/api/app.py` — `_purge_self_authored_pool_items`、`_persist_xhs_self_info`
- `src/openbiliclaw/recommendation/engine.py` — 传递 self nickname 到 database 层
- `src/openbiliclaw/runtime/refresh.py` — runtime status/readiness count 传递 self nickname
- `src/openbiliclaw/cli.py` — CLI 推荐引擎构造传递 self-info provider
- `extension/src/content/xhs/passive.ts`、`extension/src/content/xhs/task-executor.ts` — P1 author 采集增强

## 测试要点

- 带有自己 nickname 的 xhs note 进入 content_cache 后，`get_pool_candidates` 不返回
- `count_pool_candidates` 与 `count_pool_readiness()["available"]` 不统计 self-authored xhs rows
- `_purge_self_authored_pool_items` 能同时清理 `up_name` 和 `author_name` 匹配的行
- self_info 首次到达时，已入池的自发布内容被立即 suppress
- nickname 为空时不误杀其他内容
- bilibili 内容不受影响（`source_platform != 'xiaohongshu'` 条件守卫）
- 后台整理查询不挑选已知 self-authored xhs rows（至少覆盖 copy/evaluation/delight 的一个回归用例）
