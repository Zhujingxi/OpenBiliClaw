# 手动端到端联调

> 用于验证 CLI 首跑、插件采集、持续补货和浏览器通知是否在真实环境中串通。

## 前置条件

1. 本地已配置有效的 LLM Provider 和 B 站 Cookie
2. 已在项目根目录创建本地 `config.toml`
3. Chrome 已允许加载 unpacked extension

## CLI 首跑

```bash
PYTHONPATH=src .venv/bin/openbiliclaw auth status
PYTHONPATH=src .venv/bin/openbiliclaw init
PYTHONPATH=src .venv/bin/openbiliclaw recommend
```

验收：

- `init` 输出历史条数、画像状态和发现内容数
- `recommend` 能输出朋友式推荐文案

## 启动本地后端

```bash
PYTHONPATH=src .venv/bin/openbiliclaw start
```

校验接口：

```bash
curl http://127.0.0.1:8420/api/health
curl http://127.0.0.1:8420/api/runtime-status
curl http://127.0.0.1:8420/api/recommendations
```

## 加载插件

```bash
cd extension && npm install && npm run build
```

1. 进入 Chrome 扩展管理页
2. 打开”开发者模式”
3. 加载 `extension/` 目录
4. 固定侧边栏图标

## 插件采集与持续补货

1. 打开 B 站首页、搜索页、视频页
2. 执行搜索、点击、播放、暂停、滚动等行为
3. 等待 1 到 2 个 flush 周期

校验：

```bash
sqlite3 data/openbiliclaw.db "select count(*) from events;"
sqlite3 data/openbiliclaw.db "select event_type, count(*) from events group by event_type order by event_type;"
curl http://127.0.0.1:8420/api/runtime-status
```

重点看：

- `events` 数量增长
- `runtime-status.pending_signal_events` 会先升高，再在自动刷新后归零
- `runtime-status.last_refresh_at` 发生变化

## 侧边栏验证

### 推荐 tab

- 能显示连接状态
- 未初始化时提示先跑 `openbiliclaw init`
- 有候选但正在补货时提示“正在根据你最近的新行为补货”
- 有推荐时能展示推荐卡片

### 我的画像 tab

- 能显示画像摘要、核心特质、深层需求和当前偏好

### 和阿B聊聊 tab

- 能发送消息并收到回复
- 聊天后数据库中应新增 `dialogue` 事件

## 推荐反馈

在侧边栏中分别测试：

- `喜欢`
- `不喜欢`
- `写一句`

校验：

```bash
sqlite3 data/openbiliclaw.db "select id,bvid,feedback_type,feedback_note,feedback_at from recommendations order by id desc limit 10;"
sqlite3 data/openbiliclaw.db "select id,event_type,title,metadata from events where event_type='feedback' order by id desc limit 10;"
```

## 浏览器通知

1. 让系统产生一条高置信、未展示、未通知过的推荐
2. 等待 `service worker` alarm 或新事件 flush 成功
3. 观察是否弹出浏览器通知
4. 点击通知，确认跳转到目标 B 站视频页

校验：

```bash
curl http://127.0.0.1:8420/api/notifications/pending
sqlite3 data/openbiliclaw.db "select bvid,notification_sent,notified_at from content_cache where notification_sent=1 order by notified_at desc limit 10;"
```

## 期望结果

- CLI 能完成首跑初始化
- 插件能持续上报行为
- 后端能自动补货候选池
- 侧边栏能读到运行状态与新推荐
- 高置信推荐会触发浏览器通知
- 反馈和聊天都会继续推动系统理解用户
