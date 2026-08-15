# Safari Web Extension 构建与分发

OpenBiliClaw 提供 Safari（macOS）Web Extension 支持。与 Chrome / Firefox 直接加载
`manifest.json` 不同，Safari 要求把扩展资源包进一个 Xcode 工程，再由 Xcode 编译、签名并在
Safari 里启用。因此 Safari 产物分两步：先 `build:safari` 产出自包含的 `dist-safari/`，再用
Apple 的 `safari-web-extension-converter` 转成 Xcode 工程。

## 前置条件

- macOS，安装 Xcode（含命令行工具）；`xcrun --find safari-web-extension-converter` 能找到即算就绪
- Node.js + npm（与 Chrome/Firefox 构建一致，复用 `extension/package-lock.json`）
- 最低 Safari 18（macOS Sequoia 起；MV3 `background.service_worker`、`alarms`、`scripting` 都需要它）

## 构建 + 转换

```bash
cd extension
npm ci
npm run build:safari                 # 产出 dist-safari/（manifest.json + background/content/main/popup/icons）
npm run convert:safari               # 调 safari-web-extension-converter 生成 Xcode 工程（默认 safari-project/）
```

`convert:safari` 常用参数：

```bash
node scripts/convert-safari.mjs --no-build                # 跳过 build，仅转换现有 dist-safari/
node scripts/convert-safari.mjs --project-location <path> # 指定 Xcode 工程输出目录
node scripts/convert-safari.mjs --bundle-identifier <id>  # 覆盖默认 bundle id
```

转换后打开 `safari-project/OpenBiliClaw.xcodeproj`：

1. 选择 `OpenBiliClaw (macOS)` target，在 Signing & Capabilities 里设置你的开发者 Team
2. 本地调试：Safari → 开发 → 「允许未签名的扩展」勾选后，Xcode 直接 Run 即可在 Safari 启用
3. 正式分发：用 Developer ID + 公证（notarization）签名，或走 App Store 提审

命令行验证（无签名）：

```bash
cd safari-project/OpenBiliClaw
xcodebuild -project OpenBiliClaw.xcodeproj -scheme "OpenBiliClaw (macOS)" \
  -configuration Debug -destination 'platform=macOS' \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO build
```

## 与 Chrome / Firefox 的差异矩阵

| 能力 | Chrome / Edge | Firefox | Safari |
|------|---------------|---------|--------|
| 主 UI | side panel（`side_panel`） | sidebar（`sidebar_action`） | 工具栏 popup（`action.default_popup`） |
| 后台 | `background.service_worker` | `background.scripts` | `background.service_worker` |
| 定时 / 轮询 | `chrome.alarms` | `chrome.alarms` | `chrome.alarms`（Safari 18+） |
| 注入脚本 | `chrome.scripting` | `chrome.scripting` | `chrome.scripting`（Safari 18+） |
| OS 通知 | `chrome.notifications` | `chrome.notifications` | ❌ 不支持，静默降级 |
| MAIN-world 内容脚本（`world:"MAIN"`） | ✅ | ✅ | ❌ 不支持，退化为隔离世界 |
| `sidePanel` / `side_panel` | ✅ | ❌（用 sidebar_action） | ❌（用 action popup） |

`manifest.safari.json` 因此去掉了 `side_panel`、`sidePanel`、`notifications` 权限与所有
`world` 字段，其余权限（`alarms` / `cookies` / `scripting` / `storage`）与 host permission
边界和 Chrome/Firefox 完全一致（无 `<all_urls>`，仅声明各平台站点 + `127.0.0.1` /
`localhost`）。版本号在 `build:safari` 时从 `manifest.json` 注入，保持单一来源。

## 已知限制（Safari 端）

- **没有侧边栏**：Safari 不支持 side panel，点击工具栏图标打开 popup（`action.default_popup`）。
  需要「新开标签页打开面板」的路径（如通知点击）会走 `openExtensionUi` 的 tab 兜底。
- **没有 OS 通知**：`chrome.notifications` 在 Safari 不存在，service worker 已加空值守卫；
  推荐 / 认知 / 惊喜候选仍全部展示在 popup 里（与 Chrome 当前「关闭系统 Toast、只走面板」的行为一致）。
- **MAIN-world 网络层强信号 tap 走 page-context 桥接（best-effort）**：
  `bili-interact-tap`、`xhs-token-sniffer`、`xhs-action-tap`、`dy-fetch-tap`、
  `x-graphql-tap`、`bgm-identity-bridge` 在 Chrome/Firefox 里依赖 `world:"MAIN"`
  注入去观察页面自身的 `fetch`/`XMLHttpRequest` 与页面全局变量。Safari 没有 MAIN-world，
  因此 `manifest.safari.json` 不再把这些脚本注册为 content script，改为由
  `content/safari-page-injector.js`（document_start）以 `<script src>` 注入页面上下文，
  这些 bundle 已列入 `web_accessible_resources`。隔离世界的既有 `window.postMessage`
  监听不变，因此 B 站 / 小红书 / 抖音 / X / Bangumi 的网络层确定性信号与登录态识别在
  Safari 上恢复生效。该桥接是 best-effort：页面 CSP 可能拦截 script 注入，异步加载也可能
  错过页面首个请求；抖音任务态下 `content/douyin.ts` 仍保留二次注入兜底。基于 DOM 的普通
  行为采集（`content/kernel.ts` + 平台适配器）不受影响，始终正常上报。
- **Cookie 同步已对 Safari 加固**：`cookie-sync.ts` 不再依赖 `cookies.getAll({domain})`
  的浏览器差异（Safari 可能只按精确域过滤而漏掉 `.bilibili.com` 这类子域会话 Cookie），
  改为读取全量可见 Cookie 后在 JS 内按「域或子域」规则过滤，并在 unfiltered `getAll({})`
  不可用时按站点逐域回退。`onChanged` 语义差异仍存在，但登录态心跳 / Cookie 同步的核心
  路径不再受域名过滤差异影响。

## 代码组织

- `extension/manifest.safari.json`：Safari 专用 manifest（单一版本来源仍为 `manifest.json`）
- `extension/scripts/build.mjs`：`TARGET=safari` 时产出 `dist-safari/`，esbuild target 为
  `safari18`，并通过 `banner` 注入 `browser → chrome` 兼容 shim（在已暴露 `chrome` 的
  环境里是 no-op；Chrome/Firefox 构建不带 banner，产物字节不变）
- `extension/scripts/convert-safari.mjs`：封装 `safari-web-extension-converter` 的转换步骤
- `extension/src/content/safari-page-injector.ts`：Safari 专用 page-context 桥接注入器，
  按 hostname 把 `main/*.js` tap bundle 以 `<script src>` 注入页面上下文
- `extension/src/background/service-worker.ts`：`chrome.notifications` / `chrome.alarms`
  注册点加空值守卫，缺失时优雅降级
- `extension/src/background/cookie-sync.ts`：Cookie 读取统一走「全量读取 + JS 域过滤」，
  避免 Safari `cookies.getAll({domain})` 的精确域差异；不可用时逐域回退

## 回归测试

`extension/tests/manifest-assets.test.ts` 与 `extension/tests/build-assets.test.ts` 分别钉死
Safari manifest 的形态（popup 而非 side panel、保留 alarms/scripting、无 `world`、host
permission 边界、page-context 桥接 content script + WAR 资源齐全）与 `build:safari` 脚本的
clean/typecheck 目标隔离；`build-assets.test.ts` 新增 `verifyBuildAssets({ target: "safari" })`
预检，确保 `dist-safari/` 的所有 manifest 脚本与 WAR 资产存在。
`extension/tests/safari-page-injector.test.ts` 钉死 hostname → 页面脚本映射；
`extension/tests/cookie-sync.test.ts` 覆盖 Safari 精确域过滤与 unfiltered `getAll({})`
回退路径。
