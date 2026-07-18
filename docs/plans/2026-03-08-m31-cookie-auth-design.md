# M3.1 Cookie 认证设计

**目标**

完成 `docs/v0.1-todolist.md` 中 `3.1 Cookie 认证` 的 P0 部分：支持交互式录入 B 站 cookie、调用导航接口校验登录态、持久化本地 cookie，并在 CLI 提供 `auth login` 与 `auth status`。

**核心决策**

- 保留 `bilibili/auth.py` 作为认证中心，但把它从简单文件读写扩展为真正的 `AuthManager`
- `BilibiliAPIClient` 只负责请求和响应解析，不负责磁盘持久化
- `auth login` 默认使用交互式粘贴 cookie，同时支持 `--cookie` 参数
- 只有在导航接口验证成功后才持久化 cookie

**范围**

- 修改 `src/openbiliclaw/bilibili/auth.py`
- 修改 `src/openbiliclaw/bilibili/api.py`
- 修改 `src/openbiliclaw/cli.py`
- 必要时更新 `src/openbiliclaw/bilibili/__init__.py`
- 新增/扩展认证、API、CLI 测试

**不在范围内**

- 不实现二维码登录
- 不实现加密存储
- 不做真实网络集成测试作为默认门禁
- 不提前实现后续 `init` 或历史拉取流程

**认证结构**

- `AuthManager`
  - `set_cookie(cookie: str)`：清洗 cookie 并持久化
  - `load_cookie()`：读取本地 cookie
  - `validate_cookie()`：通过导航 API 校验登录态
  - `get_status()`：返回结构化认证状态，包括是否已保存、是否有效、昵称、UID、失败原因
- `BilibiliAPIClient`
  - 新增 `get_nav_info()`：请求 B 站导航接口并解析登录信息
- CLI
  - `openbiliclaw auth login`
  - `openbiliclaw auth status`

**命令行为**

- `auth login`
  - 默认提示用户粘贴完整 cookie
  - 若提供 `--cookie`，则跳过交互输入
  - 校验成功后才写入 `data/bilibili_cookie.json`
  - 输出昵称、UID 和成功提示
- `auth status`
  - 无本地 cookie 时显示未配置
  - 有本地 cookie 时再次验证登录态
  - 输出当前状态、昵称、UID、cookie 文件路径或失败原因

**错误处理**

- 空 cookie、导航接口返回未登录、网络超时、HTTP 错误，都转换为明确的认证错误
- `auth login` 失败时不保存 cookie
- `auth status` 失败时只报告状态，不自动清除本地 cookie

**验收标准**

- 用户执行 `openbiliclaw auth login` 后，可通过交互式输入完成 cookie 设置
- 验证成功时显示昵称和 UID，并把 cookie 保存到本地
- `openbiliclaw auth status` 能显示已认证 / 未认证状态和失败原因
- 重启后仍能从本地文件加载 cookie
