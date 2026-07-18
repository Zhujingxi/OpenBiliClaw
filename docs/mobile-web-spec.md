# Mobile Web — Spec & Plan

## 目标

在同局域网内通过手机浏览器访问 OpenBiliClaw，查看推荐、画像、对话并编辑模型路由，体验对齐浏览器插件。

## 决策记录

| 决策点 | 方案 |
|--------|------|
| 技术栈 | Vanilla JS + ES Modules，模块化组件，无构建步骤 |
| 样式 | 复用插件 CSS 设计令牌（CSS Variables），针对移动端重写布局 |
| 路由 | SPA hash routing（`#/recommend`、`#/profile`、`#/chat`） |
| 文件位置 | `src/openbiliclaw/web/` — 随 pip install 分发 |
| 静态服务 | FastAPI `StaticFiles` mount at `/m/` |
| 入口 URL | `http://<局域网IP>:8420/m/` |
| 鉴权 | 可选密码门禁：`[api.auth].enabled`；本机免登录，局域网 / 远程设备需密码（详见 [`docs/plans/2026-05-30-web-password-auth-design.md`](plans/2026-05-30-web-password-auth-design.md)）。仍需 `start --host 0.0.0.0` 才能被手机访问 |
| 安全边界 | 默认面向可信局域网；开启 `[api.auth]` 后局域网 / 远程访问需密码。LAN HTTP 仍为明文，介意嗅探请上 HTTPS（反代），不建议直接暴露公网 |
| PWA | 提供 manifest.json + iOS Web Clip 元数据，支持添加到主屏幕（暂不做 service worker / 离线缓存） |
| 行为采集 | 不做（无 bilibili 页面上下文） |
| 源管理/爬取 | 不做 |
| 设置页 | 顶部 overlay 提供 Saved Sync 与 Models 两个独立 section；Models 复用三端权威模型 API 与共享 reducer |

## 功能范围

### 包含

1. **推荐页**（默认 Tab）
   - 插件同款紧凑头部：`For You / 这几条，你大概会点开` + 首屏「换一批」
   - 推荐列表（封面、标题、UP 主、推荐理由）
   - 来源标识（Bilibili / Xiaohongshu / Douyin / YouTube / Web）
   - 点击跳转原始内容链接（`content_url` 优先，B 站 `bvid` fallback）
   - 移动端点击优先拉起目标平台 App（`js/app-launch.js`：B站 / 小红书 / 抖音 / YouTube / X / 知乎 URL scheme 深链，小红书携带 `xsec_token`）。回落策略保证 `/m/` 当前页永不被跳走：1.6s 内页面未被切走视为拉起失败，优先 `window.open` 新标签页打开网页；弹窗被拦（用户手势已过期，iOS Safari 必拦）则显示页内提示条「打开网页版」按钮由用户新手势打开。系统弹窗（iOS「在 App 中打开?」确认 / 打不开提示）挂起时 `blur` 暂停回落计时、关闭后 `focus` 续 0.9s 再回落，避免拉起成功却误跳当前页。桌面端及无法解析深链的地址（b23.tv / xhslink 短链、Reddit 等）保持新标签页打开网页
   - 点击直达上报（best-effort，不追踪观看时长）
   - "换一批" 按钮（reshuffle）
   - 接近列表底部自动 append 下一批，底部 "加载更多" 保留为手动兜底
   - 推荐池状态显示（当前可换、最近补进、现在在忙）
   - Delight 惊喜推荐 banner（队列浏览 ‹/›），动作与插件对齐为「看看 / 喜欢 / 不感兴趣 / 聊一聊」

2. **画像页**
   - 人格素描段落
   - Core 层：核心特质、需求、MBTI（含可信度）
   - Values 层：价值观
   - Interest 层：兴趣领域树（喜欢/不喜欢）
   - Role 层：生活阶段
   - Surface 层：认知风格、内容口味中文标签、使用场景（含模式）、探索开放度
   - Speculate 层：推测性兴趣（确认/拒绝交互）
   - 认知更新历史（分页加载，保留上下文与来源标签）
   - 活跃洞察 & 意识笔记

3. **对话页**
   - 消息历史
   - 文本输入 & 发送
   - AI 思考中状态
   - 与插件共享 `session=popup&scope=chat` 的主聊天历史
   - 聊天回复完成后刷新画像摘要与活动流
   - 底部固定两行输入框，优先保留聊天上下文浏览空间
   - 消息收件箱 overlay（兴趣探测 + 避雷探针 + 惊喜推荐通知；兴趣探测动作对齐插件为「喜欢 / 不喜欢 / 多聊聊」，避雷探针动作为「确实不喜欢 / 不是 / 多聊聊」，惊喜推荐动作对齐插件为「看看 / 喜欢 / 不感兴趣 / 聊一聊」；探针非聊天动作按归一化后的 `type + domain` 键记录独立的 in-flight 状态，关闭再打开 overlay 或其它重渲染仍从该状态恢复整卡禁用、`is-processing` 与 `aria-busy=true`，避免重复提交；只有服务端接受结算或返回终态 no-op 后才写入 terminal handled key 并移除卡片，传输/服务端失败则清除 pending、保留卡片并恢复全部动作供重试；空态提示保持 X 关闭入口可用）

4. **通用**
   - 底部 Tab 导航栏（推荐/画像/对话）
   - 顶部状态栏（连接状态、消息提醒角标）
   - WebSocket 实时更新（池变化、delight、画像更新）
   - 下拉刷新手势（推荐页）
   - PWA manifest（添加到主屏幕，不做 service worker 离线缓存）

5. **设置页**
   - 顶部设置 overlay 保留 Saved Sync，并新增独立 Models section；两者分别保存，模型草稿不会进入通用 `/api/config` payload
   - Models 按 Chat / Embedding / Runtime 三个 tab 组织；Chat 与 Embedding 均先显示最多 10 项的稳定-ID 有序列表，再进入详情
   - 详情页通过 Back 回列表且保留草稿和选中 ID；移动排序以 44px 触摸区的「上移 / 下移」为主，按设计不提供拖拽；编辑器文案中文优先（「未命名连接」等），Chat / Embedding / Runtime / Primary / Fallback 等技术术语保留英文
   - 连接类型完全由 descriptor 分组、搜索、preset 与字段定义驱动；credentials 使用 `keep / set / env / clear` 显式动作
   - Embedding 的 model、维度、相似度阈值和多模态开关是 route 共享设置；Runtime 编辑 Chat concurrency 与 timeout
   - 保存使用 revisioned `PUT /api/model-config`，失败保留草稿，`409` 与字段错误就地展示；精确 probe 绑定 revision、route kind、稳定 ID 与 draft fingerprint
   - snapshot 与 descriptor 分别维护 readiness 与 latest-request gate；Saved Sync 先收到 `config_reloaded` 仍会在首次进入 Models 补载 descriptor，descriptor 失败可由显式按钮或重新进入 Models 单独重试；旧 snapshot 被较新 reload 取代后，即使 reload 失败或仍未结束，完整加载一旦 settled-not-ready 就显示重试并保持锁定，迟到的胜出 reload 或后续重试可自动恢复；两项都 ready 前锁定编辑器。锁定/inert 与 `aria-busy` 分开：首次/在途加载和重试为 busy，settled-not-ready/error 仍锁定但 busy=false，ready 后解锁且 busy=false；实时状态与重试入口位于 inert 边界之外，销毁后的 readiness 通知不再写 DOM
   - model / preset / credential / Embedding 共享设置变更同步刷新列表、probe health 与错误投影；单纯 Back 导航保留仍有效的服务端字段错误
   - 保存与 exact probe 前统一校验 Runtime concurrency 为 1–16 的整数、timeout 为至少 10 秒的整数、每个 Chat `num_ctx`/Embedding 输出维度为非负整数、相似度阈值为 0–1 的有限数值；空输入保持为空，非法值保留草稿、字段/摘要提示且不序列化请求，显式 `0` 仍是后三项的合法下界
   - 数值错误随每次草稿 mutation 与权威 hydration 重新推导；Pydantic `422 detail` 不回显 input/msg，只映射安全路径、固定消息和当前稳定 ID；prototype-like ID/字段既只作为 own property 存储，读取时也逐级检查 own property，因此 `constructor` 连接的真实 `name` 错误可显示，缺失项不会回落到对象原型
   - 关闭设置或从 Models 切到 Saved Sync 时仅在模型草稿 dirty 时确认；关闭、列表/详情返回都恢复合理焦点，shell 重绘替换 opener 后按稳定 ID 聚焦当前 live 设置按钮
   - 移动端不提供一键本地 Ollama，避免把窄屏编辑器变成隐式覆盖入口

### 不包含

- 行为采集（content script）
- Cookie 同步
- 源管理（XHS/抖音/YouTube）
- 通用配置全集（移动设置当前只覆盖 Saved Sync 与权威 Models）
- 观看时长追踪（离开移动端 Web 后无法可靠追踪）
- 离线缓存 / 后台推送型 PWA

## 技术方案

### 目录结构

```
src/openbiliclaw/web/
├── index.html          # SPA 入口
├── manifest.json       # PWA manifest
├── icon-192.png        # PWA 图标
├── icon-512.png
├── css/
│   └── app.css         # 全量样式（复用插件设计令牌）
├── js/
│   ├── app.js          # 入口：路由、Tab 切换、WebSocket
│   ├── api.js          # 后端 API 封装（同插件 popup-api.js）
│   ├── stream.js       # WebSocket 客户端（同插件 popup-stream.js）
│   ├── view-models.js  # 后端响应 → 移动端渲染字段适配
│   ├── app-launch.js   # 移动端深链拉起目标平台 App + 网页回落
│   ├── mobile-model-settings-controller.js # Models 资源 readiness、精确草稿渲染与保存校验
│   ├── saved-sync-runtime.js # 保存同步运行时：../shared/saved-sync-core.js 的薄适配器，注入 Date.now / 定时器 / 页面可见性 / activeElement
│   ├── views/
│   │   ├── recommend.js  # 推荐页渲染 & 交互
│   │   ├── profile.js    # 画像页渲染 & 交互
│   │   ├── chat.js       # 对话页渲染 & 交互
│   │   └── model-settings.js # Saved Sync / Models overlay 与顺序式列表→详情
│   └── components/
│       ├── tab-bar.js       # 底部导航
│       ├── status-bar.js    # 顶部状态栏
│       ├── card.js          # 推荐卡片
│       ├── delight.js       # 惊喜推荐 banner
│       ├── interest-tree.js # 兴趣树组件
│       ├── mbti.js          # MBTI 展示
│       ├── messages.js      # 消息收件箱 overlay
│       └── pull-refresh.js  # 下拉刷新
└── shared/
    ├── model-config-state.js # 桌面与移动 Web 共用的 DOM-free 模型草稿/竞态状态
    └── saved-sync-core.js    # 桌面与移动共用的保存同步核心（surface-neutral，ES module + globalThis.OBCSavedSyncCore）
```

### 后端改动

```python
# app.py — create_app() 内新增
from fastapi.staticfiles import StaticFiles
from pathlib import Path

web_dir = Path(__file__).resolve().parent.parent / "web"
if web_dir.is_dir():
    # Hash routing keeps client routes after "#", so StaticFiles only needs
    # to serve /m/ and asset files. /m/recommend is not a supported route.
    app.mount("/m", StaticFiles(directory=web_dir, html=True), name="mobile-web")
```

局域网访问约定：
- `openbiliclaw start` 默认仍绑定 `127.0.0.1`，只允许本机访问。
- 手机访问需要用户显式使用 `openbiliclaw start --host 0.0.0.0`。
- 默认无鉴权、面向可信局域网；可选 `[api.auth].enabled`（`openbiliclaw set-password`）为局域网 / 远程设备加密码门禁，本机免登录。LAN HTTP 仍为明文，介意嗅探请上 HTTPS（反代），不要直接暴露公网 / 公共 Wi-Fi / 未受信 VPN。

### 样式策略

从插件 popup.html 提取 CSS Variables 作为设计令牌：

```css
:root {
  --brand: #fb7299;
  --sky: #5aa9ff;
  --success: #22c55e;
  --danger: #ef4444;
  --surface: #ffffff;
  --surface-strong: #f8f9fa;
  --surface-soft: #f1f3f5;
  --text-main: #1a1a2e;
  --text-secondary: #6b7280;
  --text-muted: #9ca3af;
  --shadow-lg: 0 8px 32px rgba(0,0,0,.08);
  --shadow-sm: 0 2px 8px rgba(0,0,0,.04);
}
```

移动端适配：
- viewport meta: `width=device-width, initial-scale=1, viewport-fit=cover`
- 底部 Tab 栏固定 + safe-area-inset-bottom
- 卡片全宽布局（插件是固定宽度侧栏）
- 触摸友好的点击区域（最小 44px）
- 系统字体栈优先

### API 调用

移动端 JS 直接调用现有 `/api/*` endpoints，与插件完全相同：

| 页面 | 接口 |
|------|------|
| 推荐 | `GET /api/recommendations`, `POST /api/recommendations/reshuffle`, `POST /api/recommendations/append`, `POST /api/recommendation-click`, `GET /api/runtime-status` |
| Delight | `GET /api/delight/pending-batch`, `POST /api/delight/respond` |
| 画像（含认知更新历史分页） | `GET /api/profile-summary`（`limit` / `cursor` 分页，返回 `has_more_cognition_updates` / `next_cognition_cursor`；移动端不再调用 `/api/cognition-updates/*`） |
| 对话 | `POST /api/chat/turns`, `GET /api/chat/turns`, `GET /api/chat/turns/{id}`；主聊天使用 `session=popup&scope=chat` 与插件共享历史 |
| 消息 | `GET /api/notifications/pending`, `POST /api/notifications/sent` |
| 活动流 | `GET /api/activity-feed` |
| 兴趣探测 | `GET /api/interest-probes/pending`, `POST /api/interest-probes/respond` |
| 避雷探针 | `GET /api/avoidance-probes/pending`, `POST /api/avoidance-probes/respond` |
| Saved Sync | `GET /api/config`, `PUT /api/config`（仅 `saved_sync.auto_sync_enabled`） |
| Models | `GET/PUT /api/model-config`, `GET /api/model-connection-types`, `POST /api/model-config/probe` |
| 实时 | `WS /api/runtime-stream` |
| 健康 | `GET /api/health` |

移动端会在 `view-models.js` 中做最小字段适配：
- 推荐池状态读取 `/api/runtime-status` 的 `pool_available_count`、`last_replenished_count`、`recent_pool_topics`，再映射成推荐页三枚 chip 使用的 `pool_size`、`recent_replenish`、`current_topic`。
- 推荐页头部用 `getMobileRecommendationHeaderState()` 生成插件语义一致的标题、首屏「换一批」、三枚池状态 chip 和活动辅助行；移动端把池状态压成横向轻量 pill，并把 `xhs-extension-*` / `dy-plugin-*` / `yt-*` 等内部来源名显示为用户可读短标签；列表接近底部时用 `IntersectionObserver` 自动调用 `append`，同时保留底部「加载更多」作为手动兜底。
- 惊喜推荐沿用插件 compact banner 思路：左侧小缩略图、标签 / 标题 / 理由 / 来源围绕头图形成 featured card，推荐原因带轻量标记，翻页控件与「稍后看」关闭入口放在右上角，动作区保持「看看 / 喜欢 / 稍后再看 / 收藏 / 不感兴趣 / 聊一聊」；「聊一聊」会在当前卡片内展开 composer 和多轮气泡，不切换到对话 tab。结果提示与动作区独立渲染：`state="liked"` 同时显示「好，这类多来点。」和完整动作组，like 使用 `aria-pressed="true"` 且只禁用重复 like，其余动作继续可用；like 请求失败则恢复未选中状态。

Delight UI 投影矩阵：

| `state` | `show_status` | `show_actions` | `like_pressed` | `like_disabled` |
| --- | --- | --- | --- | --- |
| `pending` | 有响应文案时显示 | 是 | 否 | 否 |
| `liked` | 是 | 是 | 是 | 是 |
| `viewed` | 是 | 否 | 否 | 是 |
| `rejected` | 是 | 否 | 否 | 是 |
| `chatted / chatting` | 有响应文案时显示 | 是 | 否 | 否 |

本地点击成功、刷新后 `pending-batch` 返回 liked，以及 `delight.liked` 实时事件都复用这份投影；`handled` 仅作为 `viewed / rejected` 的兼容终态，不参与 liked 动作区可见性。
- MBTI 维度兼容后端对象形态（如 `EI: { pole: "I", strength: 0.8 }`）和旧数组形态，统一映射为 `{ left, right, score }` 后再渲染。
- MBTI 会保留后端 `confidence` 显示为“可信度”；内容口味将 `long/slow` 等 raw 枚举映射为“长视频 / 慢节奏”等中文标签；使用场景会显示 `session_type` 为“模式”。
- 认知更新卡片会保留后端 `context_line` 与 `source_label`，即使前端已做过一次 normalize 后再次渲染，也不回退成泛化上下文。
- 对话 turn 兼容 `response` 和后端当前返回的 `reply` 字段，统一映射成聊天气泡使用的 `response`。
- 移动端主聊天与插件读取同一 `session=popup&scope=chat`；contextual delight/probe 聊天通过 `scope=delight/probe` 标识主题上下文。惊喜推荐内联聊天也复用 `session=popup&scope=delight`，按 `subject_id=bvid` hydrate 每条候选自己的 `turns` 历史，pending turn 通过 `/api/chat/turns/{turn_id}` 轮询恢复。
- 封面图会在渲染前归一化：B 站 `http` / protocol-relative 地址升级为 HTTPS，推荐、惊喜推荐和消息封面统一走本地 `/api/image-proxy`，加载失败时保留固定比例 fallback。推荐列表当前批次默认预热 12 张封面，前 12 张使用 eager 加载，追加批次会先等待封面预热/解码或短超时再插入卡片；封面 frame 使用粉蓝渐变骨架占位，真实图片 decode 完成后淡入，减少高速滑动过程中的白屏。

### 静态资源

- `/m/` 由 `StaticFiles` 服务移动 Web SPA。
- `/favicon.ico` 返回 `icon-192.png`，避免浏览器默认请求根路径 favicon 时产生 404。

### WebSocket

复用插件的 `runtime-stream` 协议，移动端关注的事件：
- `refresh.pool_updated` → 更新池子状态 / header，不替换当前推荐列表
- `delight.candidate` → 更新惊喜推荐
- `delight.liked` → 将匹配 bvid 的候选投影为 liked，保留状态与其它动作
- `profile_updated` → 刷新画像
- `interest.probe` → 弹出探测通知
- `activity.added` → 更新活动流
- `config_reloaded` → clean 模型草稿重新 hydration；dirty 草稿保留并显示远端更新提示

## 实施计划

### Phase 1: 后端 + 骨架（~1h）
1. `src/openbiliclaw/web/` 目录 + index.html 骨架
2. FastAPI StaticFiles mount
3. SPA hash router + Tab 切换
4. CSS 设计令牌 + 移动端基础布局
5. API 封装模块 (api.js)
6. WebSocket 客户端 (stream.js)

### Phase 2: 推荐页（~1.5h）
1. 推荐卡片组件
2. 推荐列表渲染 + 空状态
3. 池状态显示
4. 换一批 / 自动续页 / 加载更多兜底
5. Delight banner + 队列导航
6. 下拉刷新
7. 实时更新（WebSocket）

### Phase 3: 画像页（~1.5h）
1. 人格素描 + Core 层
2. MBTI 组件
3. 兴趣树组件
4. Values / Role / Surface 层
5. Speculate 层（确认/拒绝交互）
6. 认知更新历史（分页）
7. 活跃洞察 & 意识

### Phase 4: 对话页（~1h）
1. 消息历史渲染
2. 输入框 + 发送
3. AI 思考状态
4. 消息收件箱 overlay
5. 兴趣探测 / Delight 通知卡片

### Phase 5: 收尾（~0.5h）
1. PWA manifest + 图标
2. 局域网访问说明 / 安全提示
3. 连接状态指示
4. 顶部消息角标
5. 测试 & 调整

## 手机访问方式

```bash
# 启动（局域网可访问）
openbiliclaw start --host 0.0.0.0

# 手机浏览器打开
http://<电脑局域网IP>:8420/m/
```

打开 `/m/` 后可在 iOS Safari 通过「分享 → 添加到主屏幕」保存为桌面图标；Android Chrome / Chromium 浏览器可通过菜单里的「安装应用」或「添加到主屏幕」保存。局域网 HTTP 在部分 Android 浏览器上可能只生成快捷方式；完整 PWA 安装提示对 HTTPS 更稳定。

不想手敲地址时有两个扫码入口：插件 popup / side panel 顶部的「手机版」胶囊按钮（品牌色带文字，点开二维码浮层），以及桌面 Web（`/web`）顶栏的「手机版」入口（点开抽屉，二维码由自包含的 `desktop/assets/js/mobile-qr.js` 生成）；后端地址仍是 loopback 时，桌面抽屉会调用轻量端点 `GET /api/qr-info` 并读取响应中的 `lan_ip` 字段，插件入口也使用同一轻量端点生成局域网地址。
