# M3.2 核心 API 实现设计

**目标**

完成 `docs/v0.1-todolist.md` 中 `3.2 核心 API 实现` 的 P0 部分：补齐 Bilibili API Client 的历史、收藏夹、关注、搜索、视频详情、相关推荐、排行榜、评论等方法，并统一错误处理与轻量限流。

**核心决策**

- 继续使用单一 `BilibiliAPIClient` 作为 API-first 数据访问层
- 新增统一内部请求助手，收敛 HTTP 错误、B 站 `code != 0`、登录失效和风控提示
- 补一个最小请求间隔，降低连续抓取触发风控的概率
- 对下游真正会复用的数据结构补轻量 dataclass，避免把所有接口都暴露成裸 `dict`

**范围**

- 主要修改 `src/openbiliclaw/bilibili/api.py`
- 必要时更新 `src/openbiliclaw/bilibili/__init__.py`
- 扩展 `tests/test_bilibili_api.py`
- 可选新增 `tests/test_bilibili_api_integration.py` 作为集成测试骨架

**不在范围内**

- 不接入 browser 兜底
- 不实现复杂令牌桶或全局配额管理
- 不把所有 B 站响应都重建成完整领域模型
- 不做默认启用的真实网络集成测试

**API 结构**

- 保留并完善现有方法：
  - `get_user_history(max_items=200)`
  - `search(keyword, page=1, page_size=20, order="totalrank")`
  - `get_video_info(bvid)`
  - `get_related_videos(bvid)`
  - `get_ranking(rid=0)`
- 新增方法：
  - `get_favorite_folders()`
  - `get_all_favorites(max_folders=10, max_items_per_folder=50)`
  - `get_following(page=1, page_size=50)` 或等价的分页聚合能力
  - `get_video_comments(bvid, limit=20)`

**通用请求与错误处理**

- 增加统一请求助手，例如 `_get_json(...)`
- 统一流程：
  - 请求前执行限流等待
  - `raise_for_status()`
  - 解析 JSON
  - 校验 `code`
  - 返回 `data`
- 所有失败统一映射为 `BilibiliAPIError`
- 常见失败信息要显式区分：
  - 未登录 / cookie 过期
  - 请求过快 / 风控
  - HTTP 网络错误

**限流策略**

- client 内维护最小请求间隔
- 每次请求前等待 `min_interval`
- 默认只做单进程、单 client 级别的轻量限流

**分页与预算约束**

- `get_user_history()` 明确基于 `/x/web-interface/history/cursor` 的 cursor-based 翻页实现
- 不使用 page 参数模拟分页；持续使用接口返回的 cursor / offset 直到：
  - 拿到 `max_items`
  - 接口无更多数据
  - 或触发 API 错误
- `get_all_favorites()` 是高成本聚合接口，必须显式受预算控制
- 默认通过 `max_folders` 和 `max_items_per_folder` 限制请求规模，避免用户收藏夹过多时触发大量请求
- 调用结果若因预算截断，应通过返回值或文档让调用方知道是“受限聚合结果”，不能伪装成完整全量结果

**数据结构**

- 保留 `VideoInfo` 和 `NavInfo`
- 新增：
  - `FavoriteFolder`
  - `FavoriteFolderWithItems`
  - `FollowingUser`
  - `CommentInfo`
- 搜索、排行榜、相关推荐优先保持稳定 `dict` 列表返回，控制本轮复杂度

**验收标准**

- `get_user_history()` 能分页获取至少 200 条历史记录
- 能获取收藏夹列表并在预算范围内聚合每个收藏夹的内容
- 能获取关注列表、评论、排行榜、相关推荐
- 所有 API 方法有 mock 单元测试
- 提供可选的 `@pytest.mark.integration` 集成测试骨架
- 在 `pyproject.toml` 中注册 `integration` marker，避免 pytest 警告
