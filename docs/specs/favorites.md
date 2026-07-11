# 收藏夹 (Favorites) — Feature Spec

## 1. 概述

独立的"收藏"功能，让用户在任意推荐 surface 上通过星星按钮把视频**长期收藏**，并在专门的收藏页浏览、移除。

收藏与「[稍后再看](watch-later.md)」是两个**互相独立**的本地集合：

| 概念 | 语义 | 图标 | 典型用途 |
|------|------|------|----------|
| 稍后再看 (watch_later) | 临时队列，看完即移除 | 时钟 SVG | "马上要看的" |
| 收藏 (favorites) | 永久留存，长期回顾 | 星星 SVG | "想长期保存的好内容" |

一条内容可以同时在两者中、只在其一、或都不在。数据先存入本地 SQLite，不影响 soul profile 或推荐评分；用户手动同步，或明确开启默认关闭的自动同步后，才会写入对应平台收藏 / Bookmark / Saved / 播放列表。平台失败不回滚本地记录。

> 下文 legacy `favorites` 表与 `/api/favorites` 只描述 B 站兼容入口。新图形界面统一使用 canonical `saved_items/saved_memberships/native_save_states` 与 `/api/saved/favorite*`。

## 2. 数据层

### 2.1 表结构

```sql
CREATE TABLE IF NOT EXISTS favorites (
    bvid     TEXT PRIMARY KEY,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    note     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_favorites_added
    ON favorites(added_at DESC);
```

自动 migration：`_ensure_favorites_table()` 在 DB 初始化时检查并创建，与 `watch_later` 表结构对称但完全独立。

### 2.2 DB 方法

| 方法 | 签名 | 说明 |
|------|------|------|
| `add_to_favorites` | `(bvid: str, note: str = "") -> bool` | UPSERT，重复保存更新 `added_at` |
| `remove_from_favorites` | `(bvid: str) -> bool` | 删除 |
| `is_in_favorites` | `(bvid: str) -> bool` | 查询 |
| `count_favorites` | `() -> int` | 总数 |
| `list_favorites` | `(limit=50, offset=0) -> list[dict]` | 分页列表，JOIN content_cache 拿标题/封面/平台 |

## 3. API

| 端点 | 方法 | 请求体 | 响应 |
|------|------|--------|------|
| `/api/favorites` | POST | `{bvid: str, note?: str}` | `FavoriteStateResponse` |
| `/api/favorites/{bvid}` | DELETE | — | `FavoriteStateResponse` |
| `/api/favorites/{bvid}` | GET | — | `FavoriteStateResponse` |
| `/api/favorites` | GET | `?limit=50&offset=0` | `FavoriteListResponse` |

列表端点对分页参数做 `Query(limit ge=1 le=200, offset ge=0)` 校验，非法值返回 422。

**FavoriteStateResponse**: `{saved: bool, total: int}`

**FavoriteListResponse**: `{items: FavoriteItem[], total: int}`

**FavoriteItem**: `{bvid, title, up_name, cover_url, content_url, source_platform, added_at}`

## 4. 前端 — 收藏入口 + 浏览页

三端均提供星星收藏入口**和**已收藏内容浏览页。

### 4.1 通用交互规范

- **收藏按钮**：星星 SVG，点击 toggle；选中态通过 `aria-pressed=true` 与金色填充表达
- **乐观 UI**：点击后立即切换图标，请求失败时回退
- **防重复提交**：请求期间禁用当前动作按钮；状态 / 错误通过 `aria-live` 或 `role=alert` 发布
- **canonical 状态**：卡片用 `GET /api/saved/favorite/status?item_key=...` 水合；平台路由完全留在后端
- **手动同步**：列表显示真实 target 和五档状态，提供单项重试及「同步未同步内容（N）」批量确认；结果按平台显示成功/总数
- **durable UI**：task 非终态时持续后台轮询并支持 visibility resume，超过前台窗口显示「仍在后台同步」而不是提前汇总；列表刷新失败保留最后成功快照和总数，提供内联重试并按 `item_key/action` 恢复焦点
- **本地删除**：只调用 `/api/saved/favorite/remove`，不反向取消平台记录

### 4.2 各 Surface 实现

| Surface | 收藏入口（★ 星星图标） | 浏览页入口 | 列表 API |
|---------|-----------|-----------|----------|
| 插件 popup | 推荐卡和 delight banner 的星星 SVG toggle | tab bar「收藏」tab（`viewFavorites` + `favoritesList`） | `saveItem/fetchSavedItems/syncSavedItems("favorite")` |
| 移动端 Web | 推荐卡**封面右上角 chip**（★ SVG）；惊喜 tray 紧凑图标 | 底部导航「收藏」tab（`initFavoritesView`） | platform-neutral saved helpers |
| 桌面端 Web | 推荐卡 / 惊喜横幅底部反馈行内联 SVG 图标 | 侧边栏「我的收藏」(`favoritesBtn` + `favoritesPage`) | `/api/saved/favorite*` |

> 图标约定：**收藏 = 星星、稍后再看 = 时钟**，统一用与「点赞/点踩」同款的 SVG line-icon。插件端与桌面端两者内联在动作行；移动端推荐卡用封面右上角玻璃态 chip（小屏省空间）。选中态由 `aria-pressed` + CSS 驱动：星星填充金色 `#e8a33d`，时钟变 accent 色。

浏览页对每条内容支持点击打开原链接、单条「移除」，空态有引导文案；插件 popup 收藏列表会展示固定 16:9 头图缩略图，并复用 `/api/image-proxy` 加载封面；桌面端导航项带数量徽章。

## 5. 与稍后再看的关系

收藏页与稍后再看页复用同一套「已存内容列表」组件（移动端 `views/saved.js`、桌面端 `renderSavedList`），仅数据源、图标、文案不同，确保两套浏览体验一致而后端集合独立。

## 6. 不做的事情（当前 scope out）

| 特性 | 原因 |
|------|------|
| 多收藏夹 / 分类 | 当前单一默认收藏集，`favorites` 表可后续加 `folder` 列扩展 |
| Note 编辑 UI | 数据层已支持，UI 推迟 |
| 搜索 / 筛选 / 排序 | 列表量级小，默认按时间倒序 |
| 删除本地时自动删除平台记录 | 避免意外的外部账号删除操作 |
