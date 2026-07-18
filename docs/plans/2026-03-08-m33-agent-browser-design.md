# M3.3 agent-browser 集成设计

**目标**

完成 `docs/v0.1-todolist.md` 中 `3.3 agent-browser 集成` 的 P1 部分：检测 `agent-browser` 是否安装，提供准确安装引导，完成 `navigate()` 与 `get_page_content()` 的基础封装，并补一个可见的 CLI 入口用于手动验证浏览器集成。

**外部事实校验**

- 官方项目为 `vercel-labs/agent-browser`
- 推荐安装方式为：
  - `npm install -g agent-browser`
  - `agent-browser install`
- 当前 CLI 的核心能力包括 `open`、`snapshot`、`click`、`get text`、`screenshot`、`close`
- 当前仓库里 `browser.py` 对 `open --text` 的假设与官方 CLI 形态不一致，需要修正

**核心决策**

- 保留 `BilibiliBrowser` 作为浏览器自动化薄封装，但改成对齐官方 CLI 的会话型调用
- `get_page_content()` 不再依赖假定的 `open --text`，而是使用 `open + snapshot` 组合提取文本
- 增加 `browser` CLI 命令组，作为手动联调入口

**范围**

- 修改 `src/openbiliclaw/bilibili/browser.py`
- 修改 `src/openbiliclaw/cli.py`
- 视情况更新 `src/openbiliclaw/bilibili/__init__.py`
- 新增/扩展 browser 和 CLI 测试

**不在范围内**

- 不实现评论区抓取等复杂 DOM 操作
- 不做登录态 cookie 注入
- 不做点击、表单、选择器驱动的高级交互
- 不把真实浏览器测试纳入默认主门禁

**封装结构**

- `BilibiliBrowser`
  - `is_available`：检测实际可执行文件
  - `get_install_hint()`：返回准确安装步骤
  - `navigate(url)`：调用 `agent-browser open <url>`
  - `get_page_content(url)`：执行 `open` 后获取 `snapshot -i --json` 并提取结构化可见文本
  - `close()`：关闭会话
- CLI
  - `openbiliclaw browser status`
  - `openbiliclaw browser open <url>`
  - `openbiliclaw browser content <url>`

**错误处理**

- 未安装：输出安装命令，不把原始堆栈暴露给用户
- 浏览器 CLI 非零退出码：返回简短错误
- snapshot 解析失败：返回“无法提取页面内容”
- 页面内容提取只做基础文本聚合，不追求完整 DOM 语义

**验收标准**

- 未安装 `agent-browser` 时，用户能看到准确安装提示
- 已安装环境下，`navigate()` 能打开 B 站页面
- `get_page_content()` 能拿到基础页面文本
- CLI 可以直接用于手动验证浏览器集成是否可用
