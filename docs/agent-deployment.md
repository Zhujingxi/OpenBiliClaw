# Agent 一键部署指南

[← 返回 README](../README.md)

本文是 OpenBiliClaw 安装器的长版操作契约，供 AI 编码智能体和人类维护者使用。目标不是只把后端进程拉起，而是完成原生模型路由配置、凭据补齐、健康检查和首次初始化，最终收到 ``init_complete``。

## 核心约束

- 模型配置的唯一写入权威是 ``[models]``：Chat 使用有序连接，Embedding 使用有序 Provider 和一份共享 settings。
- 新自动化使用 ``--connection-type``；只有该连接类型支持 preset 时才加 ``--preset``。
- 多个 Embedding 端点通过可重复的 ``--embedding-endpoint TYPE[:PRESET]=BASE_URL`` 表达；所有端点共享模型、维度、阈值和多模态开关。
- API Key 和手动 Cookie 由 ``--interactive-confirm`` 的安全提示采集。提示会关闭终端回显，不把秘密写进进程参数或 shell 历史。
- B 站 Cookie 首选浏览器扩展同步；手动粘贴只作为安全交互提示中的 fallback。
- 安装器不会替用户决定小红书、抖音或 YouTube 的初始化授权；未明确同意时使用对应的 ``--no-*``。

## 1. 前置条件与代码目录

需要 Python 3.11+（或 ``uv``）和 Git。Docker 是可选运行方式。

如果当前目录已经包含 ``pyproject.toml`` 与 ``config.example.toml``，直接使用；否则只在空目录中克隆仓库。不要删除未知的非空目录。

如需复用旧安装，优先使用用户明确给出的路径；其次检查常见工作区，最后在家目录有限深度查找：

```bash
find ~ -maxdepth 4 -type f -name "config.toml" -path "*OpenBiliClaw*" 2>/dev/null
```

旧安装只有在包含可用的原生模型凭据或 ``data/bilibili_cookie.json`` 时才适合作为复用源。复用后的 Cookie 仍要以 live validation 结果为准。

## 2. 首选安全安装入口

人类在 TTY 中运行：

```bash
python3 scripts/agent_bootstrap.py \
  --project-dir . \
  --mode auto \
  --interactive-confirm \
  --wait-for-extension-cookie
```

如果已有安装，再加非秘密参数：

```bash
python3 scripts/agent_bootstrap.py \
  --project-dir . \
  --mode auto \
  --reuse-from /ABSOLUTE/PATH/TO/EXISTING/OpenBiliClaw \
  --interactive-confirm \
  --wait-for-extension-cookie
```

``install.sh`` 和 ``install.ps1`` 在人类路径中会添加同样的安全交互与扩展等待参数。AI 智能体应运行脚本、转述非秘密状态，并让用户本人在 no-echo 提示中输入秘密；不要要求用户把凭据拼进可复制命令。

## 3. 原生 Chat 路由

顶层连接类型为：

| Connection type | 说明 | Preset |
|---|---|---|
| ``openai_compatible`` | OpenAI 协议，包括 DeepSeek、OpenAI、OpenRouter 与自建网关 | ``deepseek``、``openai``、``openrouter``、``custom`` |
| ``anthropic_compatible`` | Anthropic 协议与兼容网关 | ``anthropic``、``custom`` |
| ``gemini_api`` | Gemini 原生 API | 无 |
| ``ollama`` | 本地 Ollama | 无 |
| ``codex_oauth`` | Codex OAuth | 无 |

非秘密选择可以显式保留，例如：

```bash
python3 scripts/agent_bootstrap.py \
  --project-dir . \
  --connection-type openai_compatible \
  --preset deepseek \
  --llm-model deepseek-v4-flash \
  --interactive-confirm
```

安全提示会补齐该记录所需的 credential。第一条 Chat 记录是 primary，后续记录依次是 fallback；角色只由数组顺序决定。

部署后使用统一 CLI 检查或编辑：

```bash
openbiliclaw models list
openbiliclaw models add --kind chat
openbiliclaw models edit <STABLE_ID>
openbiliclaw models move <STABLE_ID> --position <1-10>
openbiliclaw models remove <STABLE_ID>
openbiliclaw models probe <STABLE_ID>
```

## 4. 原生 Embedding 路由

Embedding 可关闭，也可配置 1–10 个有序 Provider。下例只在命令中保留非秘密选择：

```bash
python3 scripts/agent_bootstrap.py \
  --project-dir . \
  --embedding-model bge-m3 \
  --embedding-endpoint ollama=http://127.0.0.1:11434/v1 \
  --interactive-confirm
```

远程端点同样使用 ``--embedding-endpoint`` 表达，所需 credential 由安全提示采集。不要为每个 Provider 配一套不同的模型空间；``[models.embedding.settings]`` 中的模型、输出维度、相似度阈值和多模态开关对整条路由共享。

后续用同一组模型命令管理：

```bash
openbiliclaw models add --kind embedding
openbiliclaw models edit <STABLE_ID>
openbiliclaw models move <STABLE_ID> --position <1-10>
openbiliclaw models remove <STABLE_ID>
openbiliclaw models probe <STABLE_ID>
```

## 5. Cookie 与缺失凭据恢复

推荐 Cookie 路径：

1. 用户登录 bilibili.com。
2. 安装或打开 OpenBiliClaw 浏览器扩展。
3. 让扩展把 Cookie 同步到后端。
4. 重新运行原命令并保留 ``--interactive-confirm --wait-for-extension-cookie``。

如复用 Cookie 的 live validation 失败，先让用户重新登录。需要手动 fallback 时，仍然只运行安全交互命令：

```bash
python3 scripts/agent_bootstrap.py \
  --project-dir . \
  --mode auto \
  --interactive-confirm \
  --wait-for-extension-cookie
```

在向导中选择手动 Cookie，再在关闭终端回显的提示中粘贴。API Key 的恢复方式相同。秘密不应出现在命令、聊天转述、日志或终端历史里。

安装器打印的恢复命令会保留原始 ``--mode``，并从状态摘要带回已经校验过的
``connection_type`` 与 ``preset``，
并从当前原生配置保留模型、Base URL、完整 Chat fallback 顺序和完整
Embedding Provider 顺序。``--interactive-confirm`` 只询问仍缺失的 secret
或尚未决定的来源授权，不要求重填无关选择，也不会把多 Provider route
压成单一 legacy alias。

## 6. 初始化选择

在首次 ``init`` 前确认：

- Embedding 路由是启用还是关闭；
- B 站收藏与关注导入上限；
- 小红书、抖音、YouTube 是否获用户明确授权。

非秘密选择可以复用：

```bash
python3 scripts/agent_bootstrap.py \
  --project-dir . \
  --interactive-confirm \
  --embedding-model bge-m3 \
  --embedding-endpoint ollama=http://127.0.0.1:11434/v1 \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

不要为正常首装添加 ``--skip-init``。凭据、服务探测与隐私选择全部就绪后，bootstrap 会自动执行 ``openbiliclaw init``。

## 7. 状态事件

脚本输出人类日志和以 ``BOOTSTRAP_STATUS:`` 开头的 JSON。常见事件：

```json
{"status":"ok","message":"repo_ready","details":{}}
{"status":"ok","message":"secrets_reused","details":{"reused":[],"source":"..."}}
{"status":"ok","message":"config_summary","details":{"missing":[]}}
{"status":"ok","message":"mode_selected","details":{"mode":"local"}}
{"status":"complete","message":"backend_healthy","details":{"health_url":"...","missing":[]}}
{"status":"progress","message":"init_progress","details":{"phase":"1/4","elapsed_seconds":0.3}}
{"status":"complete","message":"init_complete","details":{"health_url":"..."}}
```

终态含义：

- ``complete``：后端与 init 已完成。
- ``needs_decisions``：先补齐 Embedding 与来源授权选择。
- ``running_with_missing_secrets`` / ``needs_secrets``：按第 5 节使用安全提示恢复。
- ``service_check_failed``：init 尚未运行；修复精确 Chat 或 Embedding 记录并重新探测。
- ``error``：根据 ``details.step`` 处理。

始终使用状态里的 ``details.health_url``，不要硬编码端口。初始化期间应实时转述 ``init_progress``，不要静默等待最终事件。

## 8. 服务检查与排障

如果 Chat 检查失败：

```bash
openbiliclaw models list
openbiliclaw models probe <CHAT_STABLE_ID>
```

检查 connection type、preset、Base URL、模型、credential 状态、配额和本地运行时。Embedding 检查失败时，检查共享 settings、Provider 顺序与端点，再探测精确 stable ID。探测不使用 fallback。

| 症状 / step | 处理 |
|---|---|
| ``clone`` | 检查 Git 与目标空目录 |
| ``config`` | 确认仓库含配置样例并检查原生模型字段 |
| ``reuse`` | 重新定位旧安装或取消复用 |
| ``install`` | 检查 Python 3.11+ / ``uv`` |
| ``docker_up`` | 检查 Compose；经用户同意后可改 local 模式 |
| ``health_check_failed`` | 查看本地 bootstrap 日志或 Compose 日志 |
| ``service_check_failed`` | 用 ``models probe`` 修复精确记录后重跑 |
| ``reused_cookie_stale`` | 重新登录并让扩展同步，或使用安全手动提示 |

## 9. 完成报告

向用户报告：

1. 使用的 local / Docker 模式；
2. 是否复用了旧安装以及复用了哪些种类的凭据（绝不回显值）；
3. 状态事件提供的健康 URL；
4. 原生 Chat / Embedding 路由的 stable ID 与顺序；
5. ``init_complete`` 是否已出现，或下一项非秘密操作。

相关文档：

- [Agent 安装精简契约](agent-install.md)
- [Docker 部署](docker-deployment.md)
- [OpenClaw 快速开始](openclaw-quickstart.md)
