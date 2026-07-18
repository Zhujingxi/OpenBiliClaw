# 小红书安全发现（XHS Safe Discovery）Design

## Background

当前 `XiaohongshuAdapter` 继承自 `WebSourceAdapter`，使用无头浏览器抓取小红书搜索结果页。这条链路有两个根本问题：

1. **封号风险高**。无头浏览器的指纹、行为模式、IP 都容易被小红书风控识别，用户自己的 xhs 账号有被限流或封禁的实际风险。
2. **发现效果脆**。搜索页结构更新频繁，浏览器模拟容易被识别为爬虫并直接拒绝返回结果。

调研了两个现有开源项目：

- **XHS-Downloader**（GPL-3.0，10.8k star）：Python 后端只做"按已知 URL 取笔记详情"，发现（搜索、账号 feed、滚动加载）完全交给用户真实浏览器里的油猴脚本。作者有意把高风险操作移出后端。
- **Spider_XHS**（MIT 徽章 + README 附加禁商业化条款，5.3k star）：后端直接逆向 `x-s / x-t` 签名，真正暴露 `search_note` / `get_homefeed_recommend` 等风控敏感接口。功能全但**封号风险实质很高**，作者自己建议配合住宅代理池使用。

## Goal

为 OpenBiliClaw 提供可持续的小红书内容发现能力，**将用户主账号封禁风险降到接近零**，同时保留"被动 + 主动"两种发现节奏。

具体目标：

- 后端不直接访问小红书的搜索 / 推荐 / feed 接口
- 发现动作全部在用户真实浏览器里完成（真实 session、真实 IP、真实指纹）
- 后端通过一个独立 sidecar 服务做"已知 URL → 完整元数据"的 enrichment
- sidecar 作为独立 Python 进程隔离运行，不污染主项目的依赖与许可证

## Non-Goals

- **不做自动滚动加载**。自动滚动是小红书风控的重点识别信号，本轮明确放弃，只抓首屏可见内容。
- **不做后端主动爬虫**。任何形式的后端签名逆向、IP 伪装、代理池集成都超出本轮范围。
- **不集成 Spider_XHS**。其许可证状态模糊（MIT 徽章与 README 附加条款冲突），且运行风险与本轮安全目标相反。
- **不改造 `BilibiliAdapter`**。本轮只处理 xhs。
- **不做 `InterestTag.source` 字段**。该工作（原 Task 28）与本轮独立，推后单独一轮。
- **不保留老的浏览器版 `XiaohongshuAdapter`** 作为 fallback。替换即删除，避免两份逻辑并存。

## Chosen Approach

采用"**三层发现 + sidecar 详情富化**"架构：

```
┌────────────────────────── 用户真实浏览器 ──────────────────────────┐
│                                                                     │
│  [1. 被动采集]  content script 监听用户自然浏览 xhs 页面，           │
│                  抓取可见笔记 URL                                   │
│                                                                     │
│  [2. 主动首屏]  扩展后台打开 xhs 搜索页（background tab），          │
│                  渲染完成后抓首屏 10~20 条 URL，关闭 tab             │
│                  关键词由后端按 Soul 兴趣下发                        │
│                                                                     │
│  [3. 创作者订阅] 扩展每日后台打开订阅博主主页，                      │
│                  抓首屏最新 10 条 URL，关闭 tab                      │
│                                                                     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ URL 回传
                               ▼
                    ┌──────────────────────┐
                    │  OpenBiliClaw 后端     │
                    │  XiaohongshuAdapter    │── enrichment queue
                    └──────────┬──────────┘
                               │ HTTP POST /xhs/detail
                               ▼
                    ┌──────────────────────┐
                    │  sidecar 容器           │
                    │  XHS-Downloader (GPL)   │
                    │  Python 3.12 + FastAPI  │
                    └──────────────────────┘
```

### 为什么是这个形态

- **用户真浏览器 = 最自然的反风控**：真 cookie、真 IP、真指纹、真浏览行为。xhs 看到的就是一个普通登录用户做了少量搜索 / 点开了自己订阅的博主主页。
- **放弃自动滚动 = 去掉风控最敏感的那一块**：滚动频率、滚动加速度、滚动停留时间都是风控评分项。只看首屏相当于"用户打开页面瞄了一眼就走"。
- **sidecar 单独容器 = GPL 隔离**：XHS-Downloader 是 GPL-3.0，作为独立进程跑在单独容器里只通过 HTTP 通信不构成衍生作品（业界普遍接受的聚合边界），主项目许可证可自由选择。
- **sidecar 只暴露 `/detail` = 攻击面最小**：单笔记详情是低频公开页面抓取，和用户自己点开那条笔记没区别。

### 组件责任划分

| 组件 | 职责 | 运行位置 |
|---|---|---|
| 扩展 xhs content script | 被动抓可见 URL（方案 1） | 用户浏览器 |
| 扩展 xhs search executor | 接后端任务，开搜索 tab，抓首屏（方案 2） | 用户浏览器 |
| 扩展 xhs creator watcher | 每日订阅任务，抓博主首屏（方案 3） | 用户浏览器 |
| 后端 `XiaohongshuAdapter` | 接扩展回传的 URL，调 sidecar `/detail` 富化 | 后端进程 |
| 后端订阅存储 | 关键词队列 + 创作者订阅列表 | SQLite |
| sidecar 容器 | XHS-Downloader FastAPI，暴露 `/detail` | 独立容器 |

### 扩展 tab 可见性

方案 2 和方案 3 打开 xhs tab 时使用 `chrome.tabs.create({ active: false })`（后台 tab）。原因：

- 不打断用户当前工作
- xhs 页面在后台 tab 仍正常渲染，content script 能抓到首屏 DOM
- 体验上等同于"扩展悄悄地替用户看了一眼"

## Scope Boundary

### 本轮做

- sidecar 容器（Dockerfile + 薄包装 FastAPI + compose 配置）
- 替换后端 `XiaohongshuAdapter` 为 HTTP→sidecar 版
- 订阅表 schema + 后端 API（扩展拉取任务 / 上报 URL）
- 扩展 xhs 被动 URL 采集逻辑
- 扩展 xhs 主动搜索任务执行（无滚动）
- 扩展 xhs 创作者订阅任务执行（无滚动）
- 文档 + 测试 + changelog

### 本轮不做

- 后端主动搜索接口
- 自动滚动（任何位置）
- Spider_XHS 集成
- 住宅代理池 / 小号模式
- RSSHub 集成
- `InterestTag.source` 字段
- UI 层面的订阅管理（仅保证后端 API 可用，扩展侧先提供配置入口即可）

## Risks And Trade-Offs

### xhs 页面结构变更
小红书可能改变搜索结果 / 博主主页的 DOM 结构，扩展 selector 会失效。需要写成防御性选择器（多候选），并在失败时静默降级。

### 后台 tab 被浏览器节流
Chrome 在后台 tab 会降低 timer 精度、延迟渲染。需要在 content script 里等待实际渲染完成后再抓 DOM，不能用固定 timeout。

### sidecar cookie 失效
XHS-Downloader 的 `/detail` 无需登录也能抓公开笔记。但如果后续要抓需登录才可见的内容，cookie 管理会成为痛点。本轮暂按"无 cookie"运行，后续按需加。

### GPL 合规边界
sidecar 容器是 GPL-3.0 衍生作品，`sidecar/xhs-downloader/` 目录要带 LICENSE 副本和出处声明。主项目 `pyproject.toml` 不通过 `uv add` 直接依赖 XHS-Downloader。

### 并发与节流
扩展主动任务（方案 2/3）需要限速：
- 同一时间只执行一个任务（一个后台 tab）
- 任务之间至少间隔 30~60s
- 每日总量上限（例如 20 次搜索 + 10 个博主）

防止即使是"首屏无滚动"，高频也会被风控盯上。

## Acceptance

- 主后端启动无需浏览器依赖即可处理 xhs 内容
- `docker compose up` 同时启动主后端和 sidecar，健康检查通过
- 扩展在用户浏览 xhs 时能采集到笔记 URL 并回传后端
- 扩展能按后端下发的关键词后台打开搜索页并回传首屏 URL
- 扩展能按订阅的博主主页每日回传最新 URL
- 整个流程不访问小红书的搜索 / feed 接口
- 删除旧 `XiaohongshuAdapter(WebSourceAdapter)` 继承路径后测试仍全绿
- 文档更新覆盖 `docs/modules/sources.md` 和 `docs/changelog.md`
