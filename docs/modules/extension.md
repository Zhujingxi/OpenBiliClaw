# 浏览器插件模块

## 模块范围

`extension/` 是 Chrome 插件子项目，负责：

- 在 B 站 / 小红书等支持的站点采集行为事件（平台无关内核 + 平台适配器）
- 通过 background service worker 缓冲并上报到本地后端
- 在 side panel 中展示连接状态、推荐结果、画像和聊天入口

当前里程碑进度：

| 子模块 | 状态 | 说明 |
|------|------|------|
| 8.1 行为采集 | ✅ | `collector.ts` + `service-worker.ts` 已接通真实事件链 |
| 8.2 后端 API | ✅ | Python 侧 `/api/events`、`/api/health`、`/api/recommendations` 已可联调 |
| 8.3 Side Panel | ✅ | 已切到 side panel 主入口，继续复用 `popup/` 页面承载推荐 / 画像 / 聊天三 tab |
| 持续补货与通知 | ✅ | 运行状态已接入 popup，service worker 会拉取高置信通知并回写发送状态 |
| B 站 Cookie 自动同步 | ✅ | service worker 会读取 `SESSDATA` / `bili_jct` / `DedeUserID` 三件套并推送到本地后端；后端暂未启动时切到 1 分钟重试，成功后恢复 60 分钟兜底刷新；后端 runtime-stream 也可发 `bilibili_cookie_sync_requested` 让扩展立刻回传 |
| 认知变化提醒 | ✅ | service worker 会提示关键认知变化，画像 tab 会显示“阿B 最近新记住了什么” |
| 认知变化历史分页 | ✅ | 画像 tab 的认知卡片支持展开详情，并可下拉或点击按钮继续查看更早的变化记录 |
| 认知卡片上下文澄清 | ✅ | 画像 tab 的认知卡片默认态现在固定展示“结论 + 上下文 + 状态提示”，用户可直接看出这是对哪条内容/哪轮聊天/哪组聚合信号形成的判断，以及这张卡片是否还能展开 |
| 画像多层认知展示 | ✅ | 画像 tab 现已把“你怎么处理信息 / 你在内容里长期在找什么 / 这阵子更像在经历什么”单独拆开，不再只显示一段画像 prose 加兴趣 chips |
| 多源行为采集（MVP） | ✅ | content script 拆成「平台无关 kernel + 平台适配器」，新增小红书适配器。manifest 覆盖 `*.xiaohongshu.com`，事件携带 `source_platform` 字段；MVP 仅采 snapshot / click / scroll / search，like/collect 延后 |
| xhs token 嗅探（MAIN world） | ✅ | `src/main/xhs-token-sniffer.ts` 以 `world: "MAIN"`、`run_at: "document_start"` 注入 xhs 页面，劫持 `window.fetch` / `XMLHttpRequest` 扫描 xhs 自家 API 响应里的 `(note_id, xsec_token)` 对子，通过 `postMessage` 桥接到 isolated world 再 `/api/sources/xhs/tokens` 回填——解决搜索页永不带 token 导致点击命中 300031 登录墙的问题 |
| xhs 初始化画像任务 | ✅ | 后端可派发 `bootstrap_profile` 任务；插件先打开小红书 `/explore`，滚动任务会以前台 tab 点击页面“我”入口进入 profile，再从 profile 页 state / DOM 解析收藏、点赞和小红书页面内显式浏览记录信号；显式启用 `max_scroll_rounds` 时会有限滚动，并用 `status="partial"` 分批回传给 `/api/sources/xhs/task-result` |

## 目录结构

```text
extension/
├── manifest.json
├── package.json
├── popup/
│   ├── popup.html
│   ├── popup.js
│   └── popup-helpers.js
├── src/
│   ├── background/
│   │   ├── buffer.ts
│   │   ├── cookie-sync.ts     # B 站 Cookie 自动同步到 localhost 后端
│   │   └── service-worker.ts
│   ├── content/
│   │   ├── kernel.ts          # 平台无关的 DOM 观察 + 事件派发
│   │   ├── bilibili.ts        # B 站 entry point，挂载 bilibiliAdapter
│   │   ├── xiaohongshu.ts     # 小红书 entry point，挂载 xiaohongshuAdapter
│   │   └── xhs/
│   │       ├── bootstrap.ts   # 初始化画像任务的 state / DOM 解析 helper
│   │       ├── passive.ts     # 小红书被动 URL / note metadata 采集
│   │       └── task-executor.ts # 后台任务在页面内的执行入口
│   ├── main/
│   │   └── xhs-token-sniffer.ts  # MAIN-world fetch/XHR sniffer，捞 xsec_token
│   └── shared/
│       ├── behavior.ts        # createBehaviorEvent / DOM snapshot kernel
│       ├── types.ts           # BehaviorEvent + PlatformAdapter 接口
│       └── platforms/
│           ├── bilibili.ts    # bvid 提取、卡片选择器、动作关键字
│           └── xiaohongshu.ts # note_id 提取、卡片选择器
└── tests/
    ├── collector-helpers.test.ts
    ├── dist-module-specifiers.test.ts
    ├── manifest-assets.test.ts
    ├── popup-helpers.test.ts
    └── service-worker-buffer.test.ts
```

## 当前能力

### `collector.ts`

负责内容脚本侧采集：

- 点击与搜索
- 视频 `view` / `pause` / `seek`
- 页面快照 `snapshot`
- 滚动 `scroll`
- 卡片停留 `hover`
- 评论 / 点赞 / 投币 / 收藏意图事件

同时支持 B 站 SPA 导航感知，在 URL 变化时重新发送快照并重绑视频监听。

### `service-worker.ts`

负责后台缓冲与上报：

- 接收内容脚本事件
- 高频事件去重
- 强信号行为优先 flush
- `chrome.alarms` 周期性批量发送
- 发送失败时把事件回填到缓冲区
- flush 成功后检查一次待发通知
- 缓冲为空时也会周期轮询高置信通知
- 每次 service worker 冷启动都会启动 B 站 Cookie 同步；如果 localhost 后端暂时不可用，会通过 `chrome.alarms` 以 1 分钟间隔重试，成功同步后恢复为 60 分钟刷新
- 以 `client=background` 连接 `/api/runtime-stream` 后，如果后端发现本地没有 B 站 Cookie，会收到 `bilibili_cookie_sync_requested` 事件并立即执行一次 Cookie POST
- Cookie 监听器幂等注册，避免 onInstalled / onStartup / 冷启动重复挂载导致同一次 B 站登录触发多次 POST
- 点击扩展图标时优先打开 side panel
- 通知和认知提醒也会优先把用户带回插件 side panel 上下文
- 在推荐通知之外，认知变化通知会打开带 `?tab=profile` 的插件页面，直接落到画像视图
- 惊喜推荐通知现在会打开带 `?tab=recommend&delight=<bvid>` 的插件页面，落到对应的首屏惊喜卡，而不是只把人丢回通用推荐页

### 小红书任务桥

`src/background/xhs-task-dispatcher.ts` 会轮询后端 `/api/sources/xhs/next-task`。当收到 `bootstrap_profile` 时，它会先打开 `https://www.xiaohongshu.com/explore`；默认用非激活 tab，若任务显式启用了 `max_scroll_rounds > 0` 则打开前台 tab，方便页面自己处理 profile 点击和后续滚动。dispatcher 会向 content script 发送：

```json
{
  "task_id": "...",
  "type": "bootstrap_profile",
  "scopes": ["saved", "liked", "xhs_history"],
  "max_items_per_scope": 20,
  "max_scroll_rounds": 0,
  "scroll_wait_ms": 1200,
  "max_stagnant_scroll_rounds": 5
}
```

`src/content/xhs/task-executor.ts` 会调用 `bootstrap.ts` 解析小红书页面已经渲染出的 state。若当前页不是个人主页，executor 会只从可信入口找当前登录用户的 profile URL：优先使用小红书导航栏“我”的链接，其次使用 `__INITIAL_STATE__.user.loggedIn=true` 时的 `userInfo.userId`。滚动任务找到导航栏“我”时，会先把 `next_url_clicked=true` 的中间结果回传，然后在页面内触发 anchor click；background 收到后不会直接 `tabs.update(profileUrl)`，而是等待同一 tab 自己导航完成并再次执行任务，SPA 没有发出完整 load 事件时会短暂 fallback 到同 tab 重发。到达 profile 后，executor 会继续等待小红书 React 页面出现 profile state、收藏/赞过 tab 文案或 note 卡片，避免浏览器 load complete 早于页面内容渲染时误判为空。只有找不到可点击入口、但能从 state 推出 profile URL 时，background 才会在同一 tab 直接导航到 profile 页。

到 profile 页后，executor 读取 `__INITIAL_STATE__.user.notes` 分组：`[0]` 为发布，`[1]` 为收藏，`[2]` 为赞过；如果收藏 / 赞过分组尚未加载，会尝试点击对应 profile tab 等待页面自己补齐 state，再退回到已渲染 DOM 卡片解析。state 解析兼容小红书 profile noteCard 结构（`noteCard.displayTitle`、`noteCard.user.nickName`、`noteCard.cover.urlDefault`），滚动后每轮也会把 state 和 DOM 结果合并，避免只看当前可见 DOM 时漏掉已加载但被虚拟列表移出的卡片。默认任务不滚动；如果后端任务显式传入 `max_scroll_rounds > 0`，executor 会优先探测小红书实际 feed / waterfall / masonry 滚动容器，并排除 `clientHeight` 过小、`overflow-y` 不是 `auto/scroll/overlay`、以及 `channel-list` / sidebar 这类非内容侧栏；如果没有可用内容容器，会回退到窗口级小步 `wheel` / `scrollBy`，贴近用户手动前台滚动。任务会运行到达到 `max_items_per_scope`、达到滚动轮数上限，或连续五轮没有新增卡片。每个 scope 的首批和后续新增卡片会先以 `status="partial"` 回传，partial 批次也会按该 scope 剩余名额裁剪，background 等后端确认后再继续，最后用 `status="ok"` 完成任务。

后端可以按任务控制滚动节奏，不需要改插件常量：

| payload 字段 | 默认值 | 插件端裁剪 | 说明 |
|---|---:|---:|---|
| `scroll_wait_ms` | `1200` | `500..5000` | 每轮滚动后等待小红书瀑布流加载的时间 |
| `max_stagnant_scroll_rounds` | `5` | `1..10` | 连续多少轮没有新增卡片后停止 |

dispatcher 会把这两个字段透传给 content script；如果 `scroll_wait_ms` 拉长，background 也会同步放宽任务 timeout，最多 6 分钟。

滚动任务的 debug 会带 `scroll_candidates` 和 `tab_load_results[scope].scroll_metrics`：前者列出页面上排名靠前的滚动候选、`overflow-y`、note 数和评分；后者按每轮记录实际滚动目标、`scroll_top / scroll_height / client_height`、滚动前后位置、新增卡片数和累计卡片数。真实联调时可用它区分“页面到底了”“滚错容器了”和“页面没有暴露更深的滚动节点”。

这条链路仍不直接调用小红书 API、不读取 cookie、不接触 Chrome 浏览器历史。这里的 `xhs_history` 指“小红书网页自己明确暴露的浏览记录 / 足迹 state”，不会把普通 `/explore` 推荐流当成浏览记录；如果小红书网页没有暴露稳定入口，就返回 0 条并让初始化继续。

### `popup/`

`popup/` 目录当前承载 side panel 页面，已具备：

- 后端连接状态检查
- 从 `/api/recommendations` 拉取推荐列表
- 推荐 tab 现已改成“换一批”，会调用 `/api/recommendations/reshuffle` 直接从 discovery pool 秒级换出一批新推荐
- 推荐 tab 滚到底时会调用 `/api/recommendations/append` 继续往下续 10 条，不会把当前这一屏直接替换掉；首次渲染、切回推荐 tab 和追加完成后也会再检查一次底部距离，避免停在底部时没有新 scroll 事件导致续页卡住
- popup API 现在会统一规范化推荐项，追加出来的 `cover_url` 也会被收敛成可直接加载的 `https://` 地址
- `/api/recommendations/refresh` 仍保留为后台补货入口，用于继续往候选池里持续进货
- popup 推荐卡片现在不会再把空 `expression / topic_label` 补成固定占位文案；后端预生成没完成时，这两块会直接隐藏
- 亮色 side panel 视觉系统：顶部 hero + inline 状态徽标、胶囊 tab、统一卡片体系，整体更贴近 B 站内容产品气质
- 推荐 tab：展示视频封面、标题、UP 主、`topic_label`、朋友式推荐文案，并通过“打开视频”明确跳转到对应 B 站视频页
- 如果某条内容暂时没有可用封面，卡片会回退到占位态，不影响换片和反馈
- 推荐封面不再依赖原生 `loading="lazy"`，避免内部滚动容器续页时新卡片封面偶发空白
- 底部提示区已升级为更明显的状态横条，会按成功 / 提示 / 错误切换对比度和状态点，减少“反馈发出去了但看不见”的感觉
- 修复卡片误跳转：`喜欢` / `不喜欢` / `写一句` / 输入框 / 发送按钮不再冒泡触发视频打开
- `喜欢` / `不喜欢` / `写一句` 都会调用 `/api/feedback`
- 推荐卡片里的 `写一句 -> 发出去` 现在会在按钮本地显示 `发送中... / 已发出 / 可重试` 三态，卡片底部也会同步写明这句是否真的发出去了
- 页面会读取 `/api/runtime-status`，区分“未初始化 / 正在补货 / 推荐可用”三种状态；初始化刚完成但 `initialized` 标记尚未同步时，如果已有补货中或候选池信号，不再误提示用户重新执行 init
- popup 打开期间现在会建立 `/api/runtime-stream` websocket 连接，底部提示条和池子状态会跟着后端事件实时变化
- popup 底部提示区已升级成可展开动态卡：默认两行显示“现在在忙什么 / 最近一次关键变化”，点 `更多` 可以展开最近历史
- 新增 `/api/activity-feed` 聚合接口，popup 会把认知更新、反馈记下了、换一批和补货结果收成同一块动态面板
- “换一批 / 继续追加”现在优先直接消费 discovery pool 里预生成好的 `expression / topic_label`
- 如果某条候选的预生成文案还没补好，卡片会先只展示标题、封面和 UP 信息，不会再显示统一占位话题或默认推荐理由
- 后台补货继续异步进行，不会阻塞 popup 立刻换片
- pool 状态摘要现在会区分“正在补货”“这轮找到了内容但可换库存没变”“刚补进 N 条”，不再把 refresh 进行中和上一轮净新增为 0 混成同一句
- 推荐 tab 头部现已进一步压缩成双层内容型入口：第一层只保留 `For You`、标题和 `换一批`，第二层把池子状态收成三枚紧凑 chips，让第一张推荐卡更早进入首屏
- 推荐 tab 现在还会在头部下方展示独立的“惊喜推荐”首屏卡位：popup 启动时会主动读取 `/api/delight/pending`，runtime stream 收到新的 `delight.candidate` 也会立刻刷新这张卡
- 推荐 tab 会展示候选池摘要：
  - `当前可换`
  - `最近补进`
  - `现在在忙`
  - 三条状态仍然保留，但文案已收短成更适合 chips 的形式，例如 `还有 151 条可换 / 刚补进 6 条 / 这会儿先不补货`
  - refresh 还在跑时，状态 chip 会优先显示 `正在补货`，不再先落成 `这轮还没补进`
  - 点击 `换一批` 时，进行中的文案会直接进入“现在在忙” chip，而不是再额外挤出一条独立状态行
- 推荐卡片现已进一步改成更偏编辑式的内容流：封面、标题、推荐理由和操作区的层级被重新拉开，头部信息不会再和首张内容卡抢视觉主角
- 惊喜推荐卡会直接展示封面、hook、标题和惊喜理由，并提供 `看看 / 不感兴趣 / 聊一聊 / 稍后看` 四个动作
- `看看` 会打开对应内容并把这次点击保留成稳定的本地已处理态；`聊一聊` 会在卡内直接发送一条带上下文的聊天消息，不再强制把用户切去聊天 tab
- 画像 tab：调用 `/api/profile-summary` 展示轻量人格画像、核心特质、深层需求、更完整的近期兴趣关键词，以及单独的“最近明显会避开”分组
- 画像 tab 现在还会单独展示 `cognitive_style / motivational_drivers / current_phase` 三层认知摘要，让“这会儿的你”更像对用户的理解，而不是兴趣标签润色
- 画像 tab 会额外展示“阿B 最近新记住了什么”，让用户能看到最近几次高置信度认知变化
- 这块已经从单行列表升级为可展开认知卡片：默认只看一句总结，展开后可看“这对画像的影响 / 为什么这么判断 / 这次依据”
- 评论类认知卡片会带上对应内容标题，例如“阿B 刚记下了你对《某条视频》的评论”，不再缺少上下文
- 默认态现在固定显示：
  - 结论
  - `来自：《某条内容》` / `来自最近这轮聊天：…` / `基于最近主题：…` / `基于最近几条相关内容`
  - 以及 `展开 / 收起 / 仅结论` 这类显式状态提示，不再让用户猜能不能点开
- `/api/profile-summary` 现已支持 `limit / cursor` 分页参数，并返回 `has_more_cognition_updates / next_cognition_cursor`
- popup 首屏先展示 3 条认知卡片；滚动到画像列表底部时会自动续页，底部也保留“加载更多 / 重试加载”按钮作为兜底
- 推荐里提交 `dislike` 或 `说说原因` 后，这块会即时刷新，不再必须等到反馈批处理阈值满足
- 聊天或推荐反馈成功后，如果 side panel 已经看过画像摘要，popup 会强制重拉 `/api/profile-summary`，让“阿B 最近新记住了什么”尽快同步到当前视图
- 聊天 tab：调用 `/api/chat`，在 side panel 内和“阿B”进行轻量多轮对话；对话会记录为 `dialogue` 事件，并在高置信度重复出现时参与后续画像更新
- 推荐、画像和聊天文案共享后端的 `ToneProfile`，基础风格是“老B友”，但会根据画像和近期反馈在信息密度、温度和梗感上动态调整
- 推荐、画像、聊天三个 tab 已统一为同一套浅色卡片语言，推荐内容被提升为侧边栏首屏视觉重心

### 构建链路

- 运行时脚本不再直接把 `tsc` 的 ESM 产物交给 Chrome
- `scripts/build.mjs` 使用 `esbuild` 将 `collector.ts` 和 `service-worker.ts` bundle 为可直接加载的单文件
- `tsc --emitDeclarationOnly` 继续负责类型声明产物
- 新增构建回归测试，确保 content script 不会再次产出浏览器无法执行的 `import` 语句

## 本地开发

在 `extension/` 目录下：

```bash
npm install
npm test
npm run typecheck
npm run build
```

`npm test` 现在会覆盖：

- 页面识别 / BV 提取 / 动作识别
- 缓冲去重与强信号 flush
- B 站 Cookie 自动同步的重试闹钟和幂等监听器
- manifest 图标资源存在性
- `dist/` 运行时脚本可被 Chrome 直接加载

## Release 分发

插件现在走独立 release 通道：

- 发布 tag：`extension-vX.Y.Z`
- Release 资产：`openbiliclaw-extension-vX.Y.Z.zip`
- 下载入口：GitHub Releases 页面中查找最新的 `extension-v*` release

后端桌面包不再和插件共用同一个 release 语义；后端改由 `backend-v*` 通道单独发布。

## 手动联调

1. 在项目根目录启动后端：

```bash
openbiliclaw start
```

2. 在 `extension/` 目录构建插件：

```bash
npm run build
```

3. 在 Chrome 的扩展管理页加载 `extension/` 目录
4. 打开 B 站首页、搜索页、视频页，执行点击、搜索、播放、暂停、滚动等行为
5. 观察后端 `/api/events` 写入效果，或直接查看 SQLite `events` 表

目前已通过真实联调确认：

- `collector` 能在首页和搜索页成功注入
- `service worker` 能启动并批量上报
- `/api/events` 能接收插件预检请求与事件批次
- SQLite `events` 表已能写入 `snapshot` 事件
- popup 能根据 `/api/health` 与 `/api/recommendations` 切换在线、空状态与推荐列表展示
- side panel 页面反馈按钮已能经 `/api/feedback` 写回推荐表和事件层
- side panel 现已支持 `推荐 / 我的画像 / 和阿B聊聊` 三个 tab，并已接通画像摘要与聊天接口
- side panel 聊天信号已进入后端学习链，但仍采用受控积累，不会因为单轮聊天立即重写画像
- side panel 推荐、画像和聊天回复现在共用“老B友”动态语气，不再固定成一套机械模板
- side panel 能根据 `/api/runtime-status` 切换“先初始化 / 正在补货 / 推荐可用”三态
- side panel 现在还能通过 websocket 看到“开始补候选 / 当前跑到哪个策略 / 刚补进几条新的 / 这批先换好了”这类实时运行状态
- service worker 现在会在高置信推荐出现时触发浏览器通知，并通过后端回写 `notification_sent`
- service worker 现在也会拉取认知变化通知；如果最近系统对用户形成了新的高置信理解，会发一条更克制的“阿B 又对你多看清了一点”提醒
- side panel 新版亮色布局已通过本地静态页面快照检查，推荐 / 画像 / 聊天三个视图结构渲染正常
- 小红书 `bootstrap_profile` 任务已通过单元测试覆盖：dispatcher 识别任务类型并能跟随 profile URL 二次执行，executor 可从 mock `__INITIAL_STATE__` 的 saved / liked / history 分组提取 scoped notes，并能用 `partial` 批次在滚动任务中持续回传新增结果

## 当前限制

- 行为按钮识别基于 DOM 文本、类名和 `aria-label`，不是服务端最终结果确认
- 采集范围优先覆盖首页、搜索页和视频页，未承诺所有 B 站模板完全一致
- side panel chat 会话只保留在当前打开周期内，不做本地持久化
- inline comment 采用轻量输入，不支持复杂反馈历史浏览
- side panel 视觉验证当前以静态快照 + extension 构建回归为主，仍建议结合真实后端做一次手动联调
- 浏览器通知当前只推送一条最高分未通知内容，不做通知中心或多条队列
- 惊喜推荐当前只维护一个首屏候选位，不做多条轮播或历史收件箱；`稍后看` 只在当前 popup 会话里隐藏，不做长期持久化
- 认知变化通知当前只提示最重要的一条，不支持用户确认/反驳，也不会在插件里维护完整通知历史
- 聚合型认知卡片如果后端暂时拿不到可信标题，会保守显示为“基于最近几条相关内容”，不会伪造具体视频名
- “换一批”依赖 discovery pool 当前已有候选；如果候选池本身供给不足，仍可能提示“池子里这会儿还没刷出新的”
- 自动续页同样依赖 discovery pool 当前已有候选；如果池子暂时不够，续页结果可能少于 10 条，甚至直接提示先等后台再补一点新的
- 池子摘要里的“最近在补”目前基于策略和候选标签做轻量聚合，属于方向提示，不是精确 taxonomy
- 小红书初始化导入是 best-effort：后端不登录、不爬取小红书，只等待插件在用户已登录浏览器里解析页面；收藏/点赞/浏览记录任一 scope 不暴露时，会跳过该 scope。普通推荐流不会被标成 `xhs_history`；受控滚动只在任务显式设置 `max_scroll_rounds` 时启用
