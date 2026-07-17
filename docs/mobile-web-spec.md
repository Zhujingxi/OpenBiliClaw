# Mobile Web vNext

`/m` 是现有 Vanilla JS Web 的响应式入口，与 `/web` 使用同一 OpenAPI-generated client、
same-origin cookie + CSRF 和 authenticated fetch-SSE。没有独立后端、独立状态模型或构建框架。

## 保留旅程

- 密码登录与 session 状态
- onboarding/source bootstrap 与 job progress
- evidence profile 查看和显式编辑
- discovery feed、feedback、favorites、watch later
- chat stream 与 conversation history
- 来源配置、完整 nested settings、LiteLLM alias health/Admin navigation

移动端保留当前导航、设计 token 与响应式布局。长列表和流式内容使用单列布局，unsafe request
自动携带 CSRF header；GET/POST SSE 均由 fetch stream parser 处理，因此不依赖无法携带认证
header 的原生 `EventSource`。

## 不在范围内

Mobile Web 不采集平台页面行为，也不执行浏览器来源任务。Provider editor、native save/saved
sync、delight/通知、self-update、desktop 与 Soul-era 控件不再提供。离线/PWA cache 和 UI 框架
迁移也不属于本次后端优先重接。
