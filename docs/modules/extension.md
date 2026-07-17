# 浏览器扩展

`extension/` 是 Chrome/Chromium 与 Firefox 的 vNext 薄客户端。它不拥有画像、推荐、
provider 路由或平台写入逻辑，只负责登录态页面中的被动证据采集、声明式浏览器来源任务和
popup 产品界面。

## 权威数据流

```text
content adapters (Bilibili / Xiaohongshu / Douyin / YouTube / X / Zhihu / Reddit)
  ├─ passive behavior ─► ActivityEvent normalization ─► POST /api/v1/events
  └─ browser operation ◄─ generic claim loop ◄──────── GET /api/v1/source-tasks/claim
                         └─ typed result/failure ───────► POST /api/v1/source-tasks/{id}/complete

popup ─► generated API client ─► device-key exchange ─► finite bearer
                              └─► authenticated fetch-SSE / JSON APIs
```

来源别名只允许出现在各平台 adapter 内；transport 使用 `bilibili`、`xiaohongshu`、
`douyin`、`youtube`、`twitter`、`zhihu`、`reddit`。任务 operation 与 manifest 声明一致，
不支持的 operation 不模拟。claim payload/result 先经过生成类型与运行时校验，credential-shaped
字段不会回传。失败回写只携带闭合 code 和经校验的异常类型，不携带页面错误文本。

## Popup 范围

Popup 使用 `extension/popup/api-client.js`，覆盖来源状态/配置、bootstrap、证据画像、feed、
feedback、chat/history、favorites/watch-later、完整 nested settings、LiteLLM alias health 与
Admin navigation。认证只使用 device key 换取的有限期 bearer；不使用 loopback bypass。

Provider editor、ordered routes、native platform save、saved sync、delight/通知、self-update、
desktop、Soul/awareness/insight/probe 控件已从 active markup、manifest 与 service worker graph 移除。

## 生成、检查与构建

在仓库根目录生成/校验共享 client：

```bash
node openapi/generate-client.mjs --write
node openapi/generate-client.mjs --check
```

在 `extension/` 运行：

```bash
npm run typecheck
npm test
npm run build
npm run build:firefox
```

生成物分别位于 `extension/dist/`、`extension/dist-firefox/`；popup 是随包复制的原生
HTML/CSS/ES module，不引入第二套 API schema。
