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
| `setup-embedding` | 配置本地 Ollama 作为 embedding 兜底服务（可选） | ✅ |
| `recommend` | 查看推荐 | ✅ |
| `feedback <id> <like\|dislike\|comment>` | 对推荐提交反馈 | ✅ |
| `profile` | 查看用户画像 | ✅ |
| `discover` | 手动触发发现 | ✅ |
| `chat` | 苏格拉底式对话 | ✅ |
| `delight` | 手动查看当前惊喜推荐候选 | ✅ |
| `probe` | 手动查看并确认猜测兴趣方向 | ✅ |

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

启动前会先做两件事：

1. 检查 `data/openbiliclaw.db` 是否完整；如果检测到损坏，会拒绝启动并提示先执行 `openbiliclaw db-repair`
2. 在数据库健康且距离上次冷备超过 24 小时时，自动生成一份冷备到 `data/backups/`

如果数据库已损坏：

```bash
$ openbiliclaw start
数据库损坏
检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。
```

当前 `start` 不只是提供静态接口，还会顺手启动候选池运行时：

- 监听插件上报的强信号行为
- 在阈值满足时自动刷新推荐候选
- 定时做榜单/探索补货
- 为插件 popup 和 service worker 提供 `/api/runtime-status` 与通知接口

启动后除了现有候选池刷新 loop，还会常驻一个低频账户同步 loop：
- 定期检查观看历史
- 定期检查收藏夹变化
- 定期检查关注 UP 主变化

这些账户侧长期信号会统一转成事件，再进入现有偏好/画像更新链。

当前 `start` 会启动这些接口：

- `GET /api/health`
- `POST /api/events`
- `GET /api/recommendations`

### `openbiliclaw serve-api`

启动更适合 Docker / 脚本调用的 API 服务入口。默认监听 `0.0.0.0:8420`。

```bash
$ openbiliclaw serve-api

$ openbiliclaw serve-api --host 0.0.0.0 --port 8420
```

推荐容器内使用该命令作为启动入口。

### `openbiliclaw delight`

手动查看当前可推送的惊喜推荐候选。

```bash
$ openbiliclaw delight
惊喜推荐
【意外契合】阿B 觉得这条你会意外喜欢
  标题: ...
  惊喜分: 0.72
  理由: ...
```

行为说明：

- 先补一次 delight backlog，再从当前池子里取一条“文案已就绪”的候选
- 运行时与 CLI 共用同一套 delight 阈值口径：默认 `0.70`
- 如果当前只有分数、还没生成 `reason/hook`，CLI 不会把它当成可展示候选

### `openbiliclaw probe`

手动列出当前最值得确认的猜测兴趣方向，并支持确认 / 否认 / 多聊聊。

```bash
$ openbiliclaw probe
猜测兴趣方向
1. 城市空间叙事
2. 复杂系统
```

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
3. 拉取 B 站历史 / 收藏 / 关注
4. best-effort 等待插件导入小红书初始化信号
5. 写入事件层并分析偏好
6. 生成初始画像
7. 按阶段自动补首轮内容池

```bash
$ openbiliclaw init
初始化 OpenBiliClaw
1/4 拉取数据
  浏览历史 300 条 / 收藏 128 个 / 关注 43 人
  小红书 收藏 20 个 / 点赞 20 个 / 浏览记录 0 个
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
  小红书事件: 40
  画像状态: 已生成
  发现内容数: 94
```

小红书导入依赖浏览器插件在用户已登录的小红书网页里执行 `bootstrap_profile` 任务。后端只入队任务并短暂等待结果，不直接登录或爬取小红书。插件会先定位当前用户 profile，再读取 profile state 里的收藏 / 赞过分组；这里的“浏览记录”指小红书网页自己明确暴露的浏览记录/足迹 state，不是读取 Chrome 浏览器历史，也不会把普通推荐流当成浏览记录。如果后端任务显式设置 `max_scroll_rounds`，插件会按任务 payload 中的 `scroll_wait_ms` 和 `max_stagnant_scroll_rounds` 做有限滚动和停滞判断。如果插件未连接、未登录或页面没有暴露对应 scope，`init` 会继续使用已有 B 站数据完成初始化。

如果当前终端是交互式，且缺少 provider API Key 或 B 站 Cookie，`init` 会直接进入用户友好的引导（v0.3.5+）：

```bash
$ docker exec -it openbiliclaw-backend openbiliclaw init
初始化前配置引导 · 选 LLM、配 Embedding、填 B 站 Cookie

OpenBiliClaw 需要一个语言模型来理解你的兴趣、写推荐文案。
请选一个 LLM 服务：

 #   名称                                  说明
 1   本地 Ollama                           免费 / 离线 / 无需 API Key（推荐新手）
 2   OpenAI 官方                           api.openai.com，需要 sk- 开头的 Key
 3   Claude 官方                           Anthropic console，按 token 付费
 4   Gemini 官方                           Google AI Studio 申请 Key，部分免费额度
 5   DeepSeek 官方                         OpenAI 兼容协议 + 国内可直连
 6   OpenRouter 聚合                       一个 Key 跑多家模型，按调用计费
 7   OpenAI 协议兼容（自建网关 / 第三方）   Azure / vLLM / LMStudio / OneAPI / 团队 LLM 网关。需自填 Base URL

Tip：如果你刚接触本项目、又不想花钱，直接选 1（Ollama）。

请输入序号或名称（默认 1=Ollama） [1]:

# (随后只问被选中那一项实际需要的字段——
#  例如选 1: 只问模型名；选 2/3/4/5/6: 只问 API Key + 模型名；
#  选 7: 问 Base URL + API Key + 模型名)

接下来配 Embedding
Embedding 是和聊天模型分开的：把视频标题/简介变成向量，
用于跨视频去重和相似度判定。频次很高，所以单独拎出来配。
  1) 跟随你刚才选的 LLM（最省事，默认）
  2) 本地 Ollama + bge-m3（推荐：免费 + 离线 + 省 Key）
  3) 自定义 OpenAI 兼容服务（vLLM / OneAPI / 自建网关）
  4) 指定其他 provider（claude / gemini / deepseek / openrouter）
  5) 跳过（暂不配置，等同于选 1）
请选择 embedding 方案 [1]:

最后是 Per-module 覆盖（高级，默认可跳过）
（高级，可跳过）是否为单个模块单独指定 provider/model？[y/N]:

初始化前认证引导 · 补齐 B 站认证
为什么需要 B 站 Cookie？
OpenBiliClaw 需要你的 B 站登录态来：
  • 拉你的观看历史（用来训练画像）
  • 以你的身份调 B 站 API 拿视频详情
Cookie 只存在你本机 data/bilibili_cookie.json，不会上传任何地方。

怎么获取：
  1. 用 Chrome / Edge / Firefox 登录 https://www.bilibili.com
  2. 按 F12 打开开发者工具 → 切到 Network（网络）标签
  3. 刷新一下 B 站页面 → 在请求列表点任意一条 bilibili.com 的请求
  4. 右侧 Headers（请求头）区域，找到 cookie: 这一行，右键复制整行的 value
  5. 把那一长串（包含 SESSDATA=...; bili_jct=...; DedeUserID=... 等）粘进来

请粘贴 B 站 Cookie:
```

引导完成后会继续当前初始化流程，不需要再单独执行 `auth login` 或手动改配置。

> **「OpenAI 官方」≠「OpenAI 协议兼容自建网关」**：v0.3.5 起向导把这俩拆成两个独立菜单项。选 2 时只问 API Key，base_url 走 `https://api.openai.com/v1`；选 7 时强制问 Base URL（指向你的 Azure / vLLM / LMStudio / OneAPI / 自建网关），写到 `[llm.openai]` 段。两者底层走的是同一个 OpenAI 协议家族，但用户视角分得很清楚。
>
> **Ollama 排第一**是有意为之：它是唯一不需要 API Key、不需要花钱的选项，对刚接触本项目的用户最友好。Tip 也明确告诉用户「不想花钱直接选 1」。

首次 `init` 的 discover 阶段可能持续几分钟，因为它会真实访问 B 站接口并调用当前 provider 进行候选打分与表达生成。
当前实现已经对首轮 discover 做了保守受控并发优化，但默认并发上限仍偏保守，优先减少 B 站和 LLM 限流风险。
首轮补货会按 `search + related_chain`、`trending`、`explore` 的顺序推进，并尽量把 fresh 候选池补到至少 `100` 条后再结束。
运行时后台则会继续以 `scheduler.pool_target_count` 为目标持续补货；当前默认目标是 `600`，到达后停止 discover，直到候选池掉回目标以下再继续补货。
运行中会直接打印每一阶段的策略名、当前池子进度和该轮请求上限，便于你判断首轮补货是在持续推进还是确实失败。

如果当前终端不是交互式，`init` 不会等待输入，而是直接报出明确错误；这适合服务器脚本和 CI 场景。

如果 discover 阶段失败，但历史和画像阶段成功，命令会提示“部分完成”，并建议稍后手动执行：

```bash
openbiliclaw discover
```

### `openbiliclaw setup-embedding`

重新进入 embedding 选择向导（v0.3.5 起 4 选 1）。`init` 阶段会自动问；只有当时跳过、或要切换方案时才需要主动跑：

```bash
$ openbiliclaw setup-embedding
配置本地 embedding · Ollama + bge-m3

  1) 跟随主 provider（默认）
  2) 本地 Ollama + bge-m3（推荐离线/省 Key）
  3) 自定义 OpenAI 兼容服务（vLLM / OneAPI / 自建网关）
  4) 指定其他 provider（claude / gemini / deepseek / openrouter）
  5) 跳过（暂不配置）
请选择 embedding 方案 [1]:
```

每个选项对应的写入路径：

| 选项 | 行为 | 写入字段 |
|---|---|---|
| 1 | 跟随主 provider，embedding 复用 chat provider | `[llm.embedding] provider=""` |
| 2 | 本地 Ollama，自动探测 + 拉取 `bge-m3` | `[llm.embedding] provider="ollama" model="bge-m3"` 并 seed `[llm.ollama] base_url` |
| 3 | 自填 base_url + api_key + model | `[llm.embedding] provider="openai" model="..."` + `[llm.openai] base_url/api_key`（用 openai 协议族指向你的网关） |
| 4 | 选另一个已知 provider 走 embedding | `[llm.embedding] provider="<target>" model="..."` + 对应 `[llm.<target>]` base_url/api_key |

选项 2 时向导会按顺序：

1. 探测 `localhost:11434/api/version`，确认 Ollama 服务在跑
2. 通过 `/api/tags` 检查 `bge-m3` 是否已 pull
3. 没拉就流式 `POST /api/pull`，进度直接打到终端
4. 把 `[llm.embedding] provider="ollama" model="bge-m3"` 写入 `config.toml`，并自动 seed `[llm.ollama] base_url="http://localhost:11434/v1"`（v0.3.3+ 修复：以前不写这一项 registry 不会注册 Ollama）

适合：

- embedding API Key 用完了
- 离线 / 没外网
- 不想再额外申请一份 embedding 服务密钥
- 跨平台一致体验（Mac/Win/Linux 同一 HTTP API）

CPU 即可跑，单次 embedding 约 100-200ms，配合后台 prewarmer 实际"换一批" 仍能稳在 600ms。

如果 Ollama 没安装：

```bash
检测不到 Ollama 服务（localhost:11434）。
  Mac:     brew install ollama && ollama serve
  Windows: 从 https://ollama.com/download 下载安装包
  装好后重新运行本命令即可启用。
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

每次反馈执行以下两个写入操作：

- 更新 `recommendations` 表中的 `feedback_type` / `feedback_note` / `feedback_at`
- 写入一条 `event_type="feedback"` 的事件，供后续记忆系统使用

### `openbiliclaw discover`

读取当前画像并触发一次内容发现。默认跑 Bilibili 的全部策略并将结果写入 `content_cache`，支持通过 `--source` 切换到 xiaohongshu 关键词生产流程，或通过 `--strategy` 限定只跑部分 Bilibili 策略。

```bash
# 默认：Bilibili 全策略
$ openbiliclaw discover
本次内容发现
发现摘要
  发现条数: 12
  缓存状态: 已写入 content_cache
  来源: bilibili
  策略: 全部

# 只跑 search + trending
$ openbiliclaw discover --strategy search,trending --limit 20

# 触发 xiaohongshu 关键词生产（由扩展在后台抓取）
$ openbiliclaw discover --source xiaohongshu
小红书关键词生产
生产摘要
  入队关键词数: 5
  尝试关键词数: 5
  今日预算: 30
  节流开关: 4 小时节流

# 忽略 4 小时节流
$ openbiliclaw discover --source xiaohongshu --force
```

选项：

- `--source, -s`：`bilibili`（默认）或 `xiaohongshu`
- `--strategy, -S`：仅对 Bilibili 生效，可多次传或逗号分隔，取值 `search` / `trending` / `explore` / `related_chain`
- `--limit, -n`：Bilibili 发现结果条数上限，默认 `30`
- `--force`：xiaohongshu 专用，忽略 `XhsTaskProducer` 的 4 小时节流

xiaohongshu 渠道并不直接抓取内容，而是调用 `XhsTaskProducer.produce_if_due()` 将 Soul 画像改写成关键词写入 `xhs_tasks` 表，由浏览器扩展的后台调度器在隐藏 Tab 中抓取。若返回 `throttled` 可加 `--force` 重试；若返回 `no_profile` 需先执行 `openbiliclaw init`。

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

显式检查并修复本地 SQLite 数据库。命令遵循”先检查、先备份、后修复”的顺序：

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

### Stub 命令的输出约定

当前仍是 stub 的命令会统一使用”开发中”占位态输出，避免与真实错误混淆，并会附带建议的下一步命令。
