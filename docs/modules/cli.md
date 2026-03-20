# CLI 命令参考

> 所有已实现的 `openbiliclaw` CLI 命令。
>
> 当前 CLI 已统一使用 Rich 输出：
> - 页面标题采用统一标题面板
> - 状态反馈统一为成功 / 警告 / 失败 / 开发中几类状态块
> - 推荐列表使用卡片式展示
> - 用户画像使用分区块展示

## 全局选项

```bash
openbiliclaw [--log-level DEBUG|INFO|WARNING|ERROR] <命令>
```

## 命令一览

| 命令 | 说明 | 状态 |
|------|------|------|
| `config-show` | 显示当前配置和可用 Provider | ✅ |
| `health-check` | 检查 LLM Provider 可用性 | ✅ |
| `auth login` | 设置并验证 B 站 Cookie | ✅ |
| `auth status` | 查看认证状态 | ✅ |
| `browser status` | 检查 agent-browser 安装 | ✅ |
| `browser open <url>` | 通过浏览器打开页面 | ✅ |
| `browser content <url>` | 获取页面文本内容 | ✅ |
| `start` | 启动本地 API 服务 | ✅ |
| `db-repair` | 检查、备份并修复本地 SQLite 数据库 | ✅ |
| `serve-api` | 启动容器友好的 API 服务 | ✅ |
| `init` | 首次初始化 | ✅ |
| `recommend` | 查看推荐 | ✅ |
| `feedback <id> <like\|dislike\|comment>` | 对推荐提交反馈 | ✅ |
| `profile` | 查看用户画像 | ✅ |
| `discover` | 手动触发发现 | ✅ |
| `chat` | 苏格拉底式对话 | ✅ |

## 详细说明

### `openbiliclaw config-show`

显示当前加载的配置、已注册的 LLM Provider 和最终生效的默认 Provider。

```bash
$ openbiliclaw config-show
当前配置概览
配置项
Provider 概览
```

### `openbiliclaw health-check`

逐个检查已注册 Provider 的连通性。

```bash
$ openbiliclaw health-check
Provider 健康检查
  openai (default): 可用
  deepseek: 可用
  ollama: 不可用
    原因: connection refused
```

### `openbiliclaw auth login`

交互式或非交互式设置 B 站 Cookie。验证通过后才保存。

```bash
# 交互式
$ openbiliclaw auth login
请输入 B 站 Cookie: SESSDATA=abc; bili_jct=xyz
登录成功
  用户名: alice
  UID: 10086

# 非交互式
$ openbiliclaw auth login --cookie "SESSDATA=abc; bili_jct=xyz"
```

### `openbiliclaw auth status`

检查当前保存的 Cookie 是否有效。

```bash
$ openbiliclaw auth status
认证概览
认证信息
  状态: 已认证
  用户名: alice
  UID: 10086
```

### `openbiliclaw browser status`

检查 agent-browser 是否已安装。

```bash
$ openbiliclaw browser status
浏览器集成状态
浏览器信息
  状态: 已安装
  可执行文件: /usr/local/bin/agent-browser
```

### `openbiliclaw browser open <url>`

通过 agent-browser 打开指定页面。

```bash
$ openbiliclaw browser open https://www.bilibili.com
浏览器已打开
目标地址
  URL: https://www.bilibili.com
```

### `openbiliclaw browser content <url>`

获取指定页面的可见文本内容。

```bash
$ openbiliclaw browser content https://example.com
页面内容
╭─ 页面内容 ─╮
│ Example Domain ... │
╰──────────────╯
```

### `openbiliclaw start`

启动本地 API 服务。默认监听 `127.0.0.1:8420`，也支持显式传入 host/port。

```bash
$ openbiliclaw start

$ openbiliclaw start --host 0.0.0.0 --port 9000
```

适合本地直接运行或调试场景。

### `openbiliclaw serve-api`

启动更适合 Docker / 脚本调用的 API 服务入口。默认监听 `0.0.0.0:8420`。

```bash
$ openbiliclaw serve-api

$ openbiliclaw serve-api --host 0.0.0.0 --port 8420
```

推荐容器内使用该命令作为启动入口。

### `openbiliclaw profile`

展示当前灵魂画像。若画像尚未初始化，会明确提示后续执行 `openbiliclaw init`。

```bash
$ openbiliclaw profile
用户画像概览
人格描述
这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人……

核心特质
  理性、谨慎、自驱

价值观
  成长、真实

当前阶段
  稳定积累阶段

深层需求
  被理解、持续成长
```

### `openbiliclaw init`

首次运行编排命令。会顺序执行：

1. 检查运行时 LLM 配置
2. 检查 B 站认证
3. 拉取历史
4. 写入事件层并分析偏好
5. 生成初始画像
6. 按阶段自动补首轮内容池

```bash
$ openbiliclaw init
初始化 OpenBiliClaw
1/4 拉取历史
2/4 分析偏好
3/4 生成画像
4/4 发现内容
补货阶段 1/3: search + related_chain
当前池子 0/100，本轮请求上限 100
阶段完成: 当前池子 28/100，本轮发现 18 条
补货阶段 2/3: trending
当前池子 28/100，本轮请求上限 72
阶段完成: 当前池子 104/100，本轮发现 76 条
初始化完成
初始化摘要
  历史条数: 200
  画像状态: 已生成
  发现内容数: 94
```

如果当前终端是交互式，且缺少 provider API Key 或 B 站 Cookie，`init` 会直接进入引导：

```bash
$ docker exec -it openbiliclaw-backend openbiliclaw init
初始化前配置引导
请选择默认 LLM provider [gemini]:
请输入 gemini API Key:
初始化前认证引导
请输入 B 站 Cookie:
```

引导完成后会继续当前初始化流程，不需要再单独执行 `auth login` 或手动改配置。

首次 `init` 的 discover 阶段可能持续几分钟，因为它会真实访问 B 站接口并调用当前 provider 进行候选打分与表达生成。
当前实现已经对首轮 discover 做了保守受控并发优化，但默认并发上限仍偏保守，优先减少 B 站和 LLM 限流风险。
首轮补货会按 `search + related_chain`、`trending`、`explore` 的顺序推进，并尽量把 fresh 候选池补到至少 `100` 条后再结束。
运行时后台则会继续以 `scheduler.pool_target_count` 为目标持续补货；当前默认目标是 `300`。
运行中会直接打印每一阶段的策略名、当前池子进度和该轮请求上限，便于你判断首轮补货是在持续推进还是确实失败。

如果当前终端不是交互式，`init` 不会等待输入，而是直接报出明确错误；这适合服务器脚本和 CI 场景。

如果 discover 阶段失败，但历史和画像阶段成功，命令会提示“部分完成”，并建议稍后手动执行：

```bash
openbiliclaw discover
```

### `openbiliclaw recommend`

读取推荐缓存，生成朋友式推荐表达，并把已展示条目标记为 `presented=1`。

```bash
$ openbiliclaw recommend
本轮推荐
推荐 1
  标题: 讲透城市与建筑的空间叙事
  UP 主: 城市观察局
  话题标签: 你最近那股想把结构想透的劲头
  推荐理由: 这条会对上你最近那种想把结构想透的劲头，它不是快餐内容，而是会慢慢把结构给你铺开。
  BV号: BV1REC
```

如果当前还没有可推荐内容，会提示先执行：

```bash
openbiliclaw discover
```

### `openbiliclaw feedback <id> <like|dislike|comment>`

为一条已展示的推荐记录写入结构化反馈，可附带备注；`comment` 必须带 `--note`。

```bash
$ openbiliclaw feedback 7 dislike --note "太浅了"
反馈已记录
反馈详情
  推荐ID: 7
  反馈: dislike
  备注: 太浅了

$ openbiliclaw feedback 7 comment --note "方向对，但我想看更深一点。"
```

### `openbiliclaw discover`

读取当前画像并执行一次真实内容发现，结果会写入 `content_cache`，并展示本次发现摘要与前几条预览。

```bash
$ openbiliclaw discover
本次内容发现
发现摘要
  发现条数: 12
  缓存状态: 已写入 content_cache

发现 1
  标题: 讲透城市空间与叙事结构
  UP 主: 城市观察局
  来源策略: search
  相关性分数: 0.83
```

如果画像尚未初始化，会提示先执行：

```bash
openbiliclaw init
```

### `openbiliclaw chat`

进入持续对话模式，复用 `SocraticDialogue` 的多轮历史。输入 `exit`、`quit` 或空行可结束。聊天内容会先记录为 `dialogue` 事件，并以受控方式积累到长期理解候选中，不会因为一句话立刻改写画像。

```bash
$ openbiliclaw chat
苏格拉底式对话
你：我最近总在刷讲结构的视频。
阿花：我听见你在说，你现在在意的可能不只是内容本身，而是想把事情看得更透一点。
你：exit
阿花：对话结束。
```

如果画像尚未初始化，会提示先执行：

```bash
openbiliclaw init
```

### `openbiliclaw start`

启动本地后端 API 服务，默认监听 `127.0.0.1:8420`，供浏览器插件或本地调试调用。

启动前会先做两件事：

1. 检查 `data/openbiliclaw.db` 是否完整；如果检测到损坏，会拒绝启动并提示先执行 `openbiliclaw db-repair`
2. 在数据库健康且距离上次冷备超过 24 小时时，自动生成一份冷备到 `data/backups/`

```bash
$ openbiliclaw start
启动 OpenBiliClaw
API 服务
  正在启动本地后端，默认监听 127.0.0.1:8420。
```

如果数据库已损坏：

```bash
$ openbiliclaw start
数据库损坏
检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。
```

### `openbiliclaw db-repair`

显式检查并修复本地 SQLite 数据库。命令遵循“先检查、先备份、后修复”的顺序：

1. 运行完整性检查
2. 若数据库正在被进程占用则拒绝继续
3. 备份 `openbiliclaw.db` 与可选的 `openbiliclaw.db-wal`
4. 尝试恢复到新的 repaired 副本
5. 验证 repaired 副本通过后，再切换正式库

```bash
$ openbiliclaw db-repair
数据库已恢复并完成切换。
备份文件: data/backups/openbiliclaw-20260315-020000.db
恢复副本: data/openbiliclaw.repaired.db
```

如果数据库本来就是健康的，命令会直接退出并提示无需修复；如果仍被运行中服务占用，会返回非零退出码并列出占用进程。

启动后除了现有候选池刷新 loop，还会常驻一个低频账户同步 loop：
- 定期检查观看历史
- 定期检查收藏夹变化
- 定期检查关注 UP 主变化

这些账户侧长期信号会统一转成事件，再进入现有偏好/画像更新链。

当前 `start` 不只是提供静态接口，还会顺手启动候选池运行时：

- 监听插件上报的强信号行为
- 在阈值满足时自动刷新推荐候选
- 定时做榜单/探索补货
- 为插件 popup 和 service worker 提供 `/api/runtime-status` 与通知接口

```bash
$ openbiliclaw start
启动 OpenBiliClaw
API 服务
  正在启动本地后端，默认监听 127.0.0.1:8420。
```

当前 `start` 会启动这些接口：

- `GET /api/health`
- `POST /api/events`
- `GET /api/recommendations`

### Stub 命令的输出约定

当前仍是 stub 的命令会统一使用“开发中”占位态输出，避免与真实错误混淆，并会附带建议的下一步命令。

- 更新 `recommendations` 表中的 `feedback_type` / `feedback_note` / `feedback_at`
- 写入一条 `event_type="feedback"` 的事件，供后续记忆系统使用
