# 浏览器端 Delight / Interest Probe E2E Design

## Background

当前仓库里与浏览器插件相关的“端到端”测试主要还是两类：

1. `extension/tests/*.test.ts` 下的 node 级测试
2. 若干手动联调文档

这对 popup 纯 helper、background 纯逻辑和 build 回归已经足够，但对下面这条真实链路覆盖还不够：

`本地后端状态 -> runtime-stream/WebSocket -> 真 Chrome 扩展页面 -> 用户点击反馈 -> 后端状态回写`

用户本次明确要求的是：

- 使用 **真实 Playwright / Chromium**
- 加载 **真实 unpacked extension**
- 接到 **真实本地后端**
- 重点完成 **惊喜推荐（delight）** 和 **兴趣探针（interest probe）** 的浏览器端到端测试
- 只覆盖扩展 side panel / popup 真实流程，不把操作系统原生通知点击纳入首版自动化

## Goal

新增一套可重复运行的浏览器 E2E，用真实浏览器验证以下两条关键扩展链路：

1. `interest probe -> 喜欢`
2. `delight -> 不感兴趣`

并让这套测试具备以下特征：

- 不依赖人工先手动启动 `openbiliclaw start`
- 不依赖操作系统原生通知 UI
- 不依赖真实 LLM 聊天分支
- 在测试中使用真实后端进程，而不是前端 stub
- 具备可控 seed / reset / trigger 机制，避免把稳定性押在 runtime 的自然轮询和隐式状态上

## Non-Goals

- 不把 `多聊聊 / 聊一聊` 分支放进首版浏览器 E2E
- 不自动化验证系统原生通知是否真的弹出、是否真的被点击
- 不覆盖推荐 tab 其他已有反馈链路
- 不把整套 Python 全量测试、extension 全量测试都改造成 Playwright
- 不为此重写 popup 架构或 runtime-stream 协议

## Constraints

### 1. Probe 与 Delight 的后端触发能力不对称

`interest-probes` 已有 `POST /api/interest-probes/trigger` 手动触发入口，但它要求后端已有 active speculation。

`delight` 目前只有：

- `GET /api/delight/pending`
- `POST /api/delight/sent`
- `POST /api/delight/respond`

没有对称的“强制发布到 runtime-stream”入口。

### 2. 真实浏览器测试需要可控数据起点

如果完全依赖自然 refresh loop 去产出 probe / delight：

- 运行时间长
- 失败定位困难
- delight 尤其缺少 deterministic trigger

因此首版必须补最小的测试专用 seed / trigger 机制。

### 3. 当前基线存在两条与本任务无关的旧 UI 布局断言失败

`extension/tests/popup-layout.test.ts` 仍保留对旧版推荐卡片 grid 布局的断言，而当前 popup 已经是纵向 `flex` 布局。这两条失败与本次浏览器 E2E 无直接关系，不应阻塞本任务本身。

## Approaches Considered

### 方案 A：完全黑盒，等待现有 runtime 自然产出 probe / delight

做法：

- 启真后端
- 启真扩展
- Playwright 等待后端自然推送 probe / delight

优点：

- 看起来最“真实”
- 代码改动最少

缺点：

- delight 没有对称 trigger，几乎不可控
- 等待时间长
- 状态不稳定，容易受池子库存、refresh 节奏、历史运行状态影响

### 方案 B：真实后端 + 测试专用 seed / trigger（推荐）

做法：

- 仍然启动真后端和真扩展
- 只在 `OPENBILICLAW_E2E=1` 时暴露极小的测试专用接口
- 通过接口 reset 后端状态、seed probe / delight 数据、强制触发 push
- Playwright 只负责浏览器端断言和用户操作

优点：

- 仍是“真后端 + 真浏览器 + 真扩展”
- 可重复、稳定、运行快
- 失败定位集中，便于维护

缺点：

- 需要增加一小块测试专用 API 面

### 方案 C：真实后端 + 直接写 SQLite 种子

做法：

- Playwright 或外部 helper 直接写数据库
- 再依赖 runtime 读取这些状态

优点：

- 不必新增太多 API

缺点：

- 与 schema 强耦合
- 每次数据结构调整都要跟着改测试
- 不如 API seed 清晰

## Chosen Approach

采用 **方案 B：真实后端 + 测试专用 seed / trigger**。

核心原则是：

- **浏览器侧必须是真**
- **后端进程必须是真**
- **测试驱动的数据入口可以是受控的**

这样既保住了端到端价值，也避免了把稳定性押在自然调度和历史残留状态上。

## Design

### 1. 后端测试模式

在 `src/openbiliclaw/api/app.py` 中，仅当 `OPENBILICLAW_E2E=1` 时注册一组测试专用接口：

- `POST /api/e2e/state/reset`
- `POST /api/e2e/probe/seed`
- `POST /api/e2e/delight/seed`
- `POST /api/e2e/delight/trigger`

这些接口不面向生产，只服务 Playwright 测试。

#### `state/reset`

职责：

- 清理测试注入的 probe / delight / cognition 更新 / 未读消息状态
- 恢复一个确定性的空白起点

目标是确保每条 E2E 在独立状态下运行，不被前一条测试污染。

#### `probe/seed`

职责：

- 往 speculator 注入一个 active speculation
- 包含 `domain / reason / specifics / question` 等测试需要的最小字段

之后 Playwright 仍然调用现有 `POST /api/interest-probes/trigger`，让真实 runtime-stream 路径发出 `interest.probe`。

#### `delight/seed`

职责：

- 插入一条满足 delight 阈值的待推候选
- 保证 `bvid / title / delight_reason / delight_hook / content_url / cover_url` 等 UI 需要字段完整

#### `delight/trigger`

职责：

- 复用 runtime controller 的真实推送路径，触发 `delight.candidate`

这使 delight 与 probe 一样具备 deterministic push 能力，而不是等待 runtime 自然轮询。

### 2. Playwright 测试结构

在 `extension/` 下新增 Playwright 工程：

- `extension/playwright.config.ts`
- `extension/tests/e2e/helpers.ts`
- `extension/tests/e2e/interest-probe.e2e.ts`
- `extension/tests/e2e/delight.e2e.ts`

并在 `extension/package.json` 中新增例如：

- `test:e2e`

#### 浏览器启动方式

使用 persistent Chromium context，并加载当前 `extension/` 目录为 unpacked extension。

测试需要拿到扩展 id，然后直接导航到：

- `chrome-extension://<id>/popup/popup.html?tab=profile`
- `chrome-extension://<id>/popup/popup.html?tab=recommend`

这样不依赖人工点工具栏图标，也不依赖 side panel API 的宿主窗口行为。

#### 后端启动方式

`test:e2e` 内部负责：

- build extension
- 启动本地后端进程
- 注入 `OPENBILICLAW_E2E=1`
- 使用隔离 data/config 目录

测试不要求开发者先手工执行 `openbiliclaw start`。

### 3. 首版测试用例

#### 用例一：Interest Probe Confirm Flow

链路：

1. `POST /api/e2e/state/reset`
2. `POST /api/e2e/probe/seed`
3. `POST /api/interest-probes/trigger`
4. 打开 `popup.html?tab=profile`
5. 断言 probe 卡片出现
6. 断言问题文案和 chips 正确
7. 点击 `喜欢`
8. 断言成功反馈出现并最终消失
9. 断言画像或 recent cognition 出现对应确认结果

这条用例验证：

- probe 事件真实穿过 runtime-stream
- popup profile 视图能真实接收并渲染
- 用户反馈能打到真实后端
- 后端状态更新能再次反映回 UI

#### 用例二：Delight Dislike Flow

链路：

1. `POST /api/e2e/state/reset`
2. `POST /api/e2e/delight/seed`
3. `POST /api/e2e/delight/trigger`
4. 打开 `popup.html?tab=recommend`
5. 断言消息 badge 增加
6. 打开消息面板
7. 断言 delight 卡片出现
8. 点击 `不感兴趣`
9. 断言卡片进入结果态后移除
10. 通过后端断言该 delight 已被标为 `purged_by_dislike` 或等价状态

这条用例验证：

- delight 事件真实穿过 runtime-stream
- popup 推荐页/消息面板能真实接收并渲染
- 用户的 dislike 反馈能打到真实后端
- 后端状态回写成功

### 4. 首版排除项

chat 分支暂不进入主 E2E 套件，原因是它会引入：

- 真实 dialogue engine
- API Key / provider 前提
- 更长的超时窗口
- 更高的波动性

如果后续需要，再单独补一条 smoke，不混进首版稳定套件。

## Failure Handling And Stability

### 1. 不使用硬编码睡眠

所有断言都基于 UI 结果等待：

- badge 出现
- card 出现
- result 文案出现
- 元素移除

避免使用长时间 `sleep()`。

### 2. 每条测试先 reset

E2E 入口统一先清状态，避免：

- 旧 probe 残留
- 未读消息累积
- 历史 cognition 影响断言

### 3. 保留调试产物

失败时保存：

- Playwright trace
- screenshot
- browser console
- backend stdout/stderr 日志

这样出问题时能区分是：

- push 没发生
- popup 没接到事件
- UI 没渲染
- API 回写失败

## Testing And Verification

实现完成后至少验证：

```bash
cd extension
npm run build
npm run test:e2e
```

如果 Python 测试环境可用，再补针对新增测试接口的后端测试，例如：

```bash
python -m pytest tests/test_api_app.py -q
```

## Documentation Impact

按仓库规则，落地后同步更新：

- `docs/modules/extension.md`
- `docs/changelog.md`
- 如命令或联调方式变化明显，再补 `docs/manual-e2e.md`

## Risks

### 测试专用 API 泄漏到生产

必须严格用环境变量守护，不允许默认启用。

### 扩展 id / 扩展页面导航在不同机器不稳定

需要把获取 extension id 的逻辑收进 Playwright helper，不在每个测试里手写。

### 后端状态 reset 不彻底

如果 reset 只清一半，测试会随机互相污染。首版设计里 reset 必须是第一等公民，而不是临时补丁。

### 现有旧布局断言继续失败

这两条失败不属于本任务根因，但最终如果继续跑 `npm test`，它们会继续显示红线。实现本任务时不应误把它们当成 E2E 新回归。

## Acceptance

- 能在真实 Playwright / Chromium 中加载本地扩展并运行测试
- interest probe confirm 流程稳定通过
- delight dislike 流程稳定通过
- 测试不依赖人工先启动后端
- 测试不依赖原生系统通知 UI
- 测试专用后端入口默认关闭，仅在 `OPENBILICLAW_E2E=1` 下启用
