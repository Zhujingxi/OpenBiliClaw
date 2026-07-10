# Issue #96：手机版二维码低延迟打开设计

## 背景

浏览器插件在当前后端地址为 `127.0.0.1` 或 `localhost` 时，需要向后端获取电脑的局域网 IP，再生成手机可访问的 `/m/` 二维码。现有 `renderMobileQrPanel()` 为此同步请求 `GET /api/health`，而该 readiness 端点还会执行 embedding 探活；冷启动或模型服务异常时，二维码面板会等待到 2 秒超时，之后还可能退回手机不可访问的 loopback URL。

后端已经提供 `GET /api/qr-info`。该端点只返回缓存的 `lan_ip`，不会读取画像或执行 embedding probe；桌面 Web 已经使用它。Issue #96 的剩余缺口是浏览器插件仍未迁移到这个轻量端点。

## 目标

- 插件从 loopback 后端构建手机二维码时只请求 `GET /api/qr-info`，不再通过二维码链路触发 `GET /api/health`。
- 保留当前 endpoint 解析、局域网 IP 替换、二维码本地生成和失败降级行为。
- 不改变桌面 Web、后端响应结构、认证策略、权限声明或二维码 UI。
- 用自动化测试锁定请求端点，防止后续回退到 readiness API。

## 非目标

- 不新增后端 API；`/api/qr-info` 已经存在。
- 不引入插件启动预取或持久化 LAN IP 缓存。
- 不让后端拼装完整移动端 URL；host、port 与当前移动 Web 的 HTTP URL 行为保持不变。
- 不调整 `/api/health` 的 readiness 语义或 embedding probe。

## 方案比较

### A. 插件直接迁移到 `/api/qr-info`（采用）

仅替换 `renderMobileQrPanel()` 的请求路径和相关注释，沿用 2 秒超时与原有 fallback。改动局部，复用现成 API，并与桌面 Web 保持一致。

### B. 插件启动时预取并缓存 LAN IP

首次打开面板可以直接命中内存缓存，但会给 popup 启动增加请求，还需定义 endpoint 变更、网络切换和缓存失效策略。对一个本就轻量的本地请求收益有限。

### C. 后端返回完整移动端 URL

能把 URL 拼装集中到后端，但后端无法可靠获知插件当前配置的协议、host 和端口，尤其不适合自定义 LAN 或远程 endpoint，会引入错误耦合。

## 组件与数据流

用户点击插件顶部“手机版”按钮后：

1. 插件立即显示现有二维码 overlay。
2. `renderMobileQrPanel()` 读取当前后端 endpoint。
3. endpoint host 若不是 loopback，直接按现有配置生成 `/m/` URL，不发 LAN IP 请求。
4. endpoint host 若是 loopback，按现有本地后端地址构造方式请求 `GET /api/qr-info`，超时仍为 2 秒。
5. 响应包含非 loopback `lan_ip` 时，仅替换 host，保留当前 port，再由 `getMobileQrViewState()` 生成 URL、提示和本地 SVG。
6. 请求失败、超时、非 2xx、JSON 无有效 `lan_ip` 或返回 loopback 时，继续使用原 endpoint，并展示现有局域网 IP 警告。

该链路不会访问 `/api/health`，因此不会等待 embedding readiness probe。

## 错误处理与兼容性

- 保留现有 `try/catch` 静默降级，二维码入口不会因轻量端点暂时不可用而打不开。
- 保留当前 2 秒上限，兼容尚未提供 `/api/qr-info` 的旧后端；旧后端返回 404 时走原 fallback。
- `/api/qr-info` 已在 degraded mode 与 API auth 白名单内，配置错误或启用局域网密码门禁时仍可生成入口。
- 不新增 host permission；请求仍发往用户已经配置并授权的后端 origin。

## 测试

- 在扩展测试中增加二维码请求端点契约：`renderMobileQrPanel()` 的 loopback 分支必须引用 `/api/qr-info`，且不得引用 `/api/health`。
- 保留并运行现有 `popup-mobile-qr` 测试，验证 endpoint URL、loopback 警告和本地 SVG 生成逻辑不变。
- 运行扩展完整测试与类型检查，覆盖与当前 staged 扩展认证改动的组合兼容性。
- 运行后端 `/api/qr-info` 定向测试，确认端点仍只返回 `lan_ip` 且不触发 embedding probe。

## 文档范围

- 更新 `docs/modules/extension.md`，明确插件二维码读取 `/api/qr-info.lan_ip`。
- 修正 `docs/modules/config.md` 中仍引用 `/api/health.lan_ip` 的旧描述。
- 在 `docs/changelog.md` 当前版本块记录 Issue #96 的用户可感知延迟修复。
- 架构图已经把二维码链路描述为 `/api/qr-info`，本次不改变模块边界或数据流，因此无需重绘。

## 验收标准

- 点击插件“手机版”时，二维码链路不会请求 `/api/health`。
- loopback 配置下，插件通过 `/api/qr-info` 获取有效 LAN IP 并生成 `http://<lan-ip>:<configured-port>/m/`。
- `/api/qr-info` 失败时仍生成原 endpoint 的二维码并显示警告，不抛出未处理异常。
- 扩展测试、类型检查和后端定向测试通过。
- 模块文档与 changelog 和代码行为一致。
