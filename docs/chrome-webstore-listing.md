# Chrome Web Store listing source

This file is the current copy source for the extension listing. It does not publish or modify the live listing.

## Short Description

```text
需本地后端的七平台内容发现 Agent：证据画像、个性化 Feed、反馈和聊天
```

## Detailed Description

```text
OpenBiliClaw 是一个需要自托管本地后端的、本地优先、开源的个性化内容发现 Agent。它把你授权范围内的 Bilibili、小红书、抖音、YouTube、X、知乎和 Reddit 信号归一为可追溯证据，用于形成可查看、可编辑的画像和跨平台发现 Feed。产品数据默认保存在你的本机部署中。

主要功能：
- 在支持的平台页面采集被动活动证据，或执行后端 manifest 声明的只读浏览器来源任务。
- 在侧边栏、桌面 Web 和移动 Web 查看 Feed、推荐理由和来源。
- 通过喜欢、少来点、不感兴趣和聊天调整后续排序。
- 使用只保存在 OpenBiliClaw 中的收藏和稍后观看；不会写入平台账号。
- 查看来源能力、连接状态、嵌套设置和 LiteLLM alias 健康状态。

安装：
1. 按项目安装指南部署 Docker 后端。
2. 在 LiteLLM Admin 配置 obc-interactive、obc-analysis 和 obc-embedding。
3. 打开 http://127.0.0.1:8420/setup/ 连接来源并运行 bootstrap。
4. 安装扩展，配置本机后端和由部署者一次性交付的 device key。

隐私与安全：
- 数据发送到你配置的 OpenBiliClaw 后端，不发送到项目开发者运营的服务器。
- Provider credential、routing 和 budget 只在你管理的 LiteLLM 中配置。
- 扩展 device key 换取有限期 bearer；后端只保留 key digest。
- 扩展不含广告、分析、遥测或远程执行代码。
- 完整数据边界见项目隐私政策。

项目与安装文档：
https://github.com/whiteguo233/OpenBiliClaw
```

## Required listing links

- 项目主页 / Website URL: <https://whiteguo233.github.io/OpenBiliClaw/>
- 支持 / Support URL: <https://github.com/whiteguo233/OpenBiliClaw/issues>
- Privacy: <https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/privacy.md>

## Screenshot acceptance

Screenshots must use synthetic data and the current vNext UI. Show the seven-source Feed, profile evidence/editing, chat, local collections, truthful source status, and alias health. Do not show removed notifications, personality/probe cards, provider editors, platform-save controls, self-update, or desktop packaging.

Never capture `.env`, device keys, cookies, provider/source credentials, real account names, or real profile evidence. If existing images still show removed controls, replace them before the next store submission.

## Pre-publish check

- description matches the current manifest and capability matrix;
- backend URL and setup path are current;
- screenshots match the shipped popup;
- privacy policy matches actual permissions and data flow;
- Chrome and Firefox packages pass build, typecheck, tests, and lint;
- no external listing mutation is performed by repository verification.
