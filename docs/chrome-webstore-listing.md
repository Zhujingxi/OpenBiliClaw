# Chrome Web Store 商店页文案与素材

> 用途：维护 Chrome Web Store Developer Dashboard 的商店详情页。
> 更新插件能力、安装路径、隐私政策、后端部署方式或截图时，同步更新本文件。

## 提交入口

- Chrome Web Store item: <https://chromewebstore.google.com/detail/openbiliclaw/cdfjfkdjjhdaccbldipkjhpibnfbiamg>
- Developer Dashboard: <https://chrome.google.com/webstore/devconsole/> -> `Store listing`
- 项目主页 / Website URL: <https://whiteguo233.github.io/OpenBiliClaw/>
- 支持 / Support URL: <https://github.com/whiteguo233/OpenBiliClaw/issues>
- 隐私政策: <https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/privacy.md>

## Short Description

```text
需本地后端的七平台内容发现 AI Agent：跨平台推荐、私有画像与可反馈侧边栏
```

## Detailed Description

将下面的纯文本完整复制到 Chrome Web Store 的 `Detailed description` 字段。

```text
OpenBiliClaw 是一个需要本地后端运行的、本地优先、私有、开源的个性化内容发现 Agent。它把你授权范围内的 B站、小红书、抖音、YouTube、X、知乎和 Reddit 内容信号汇合成跨平台推荐、可查看和纠正的个人画像，以及能继续反馈调教的浏览器侧边栏。数据默认保存在你的本机。

项目主页：
https://whiteguo233.github.io/OpenBiliClaw/

GitHub 源码 / Issue / Releases：
https://github.com/whiteguo233/OpenBiliClaw

安装和使用：
1. 安装这个浏览器插件。
2. 部署并启动本地后端。普通用户可从 GitHub Releases 下载 macOS .dmg / Windows .exe；需要源码部署或深度定制时，可按 README / AI 部署说明操作。
   Releases: https://github.com/whiteguo233/OpenBiliClaw/releases
   AI 部署说明: https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md
3. 后端启动后，在电脑上打开：
   http://127.0.0.1:8420/web
4. 在同一个浏览器登录你准备授权给 OpenBiliClaw 使用的平台；YouTube 等公开内容发现路径不一定需要登录。是否启用某个平台由你在设置页决定。
5. 打开 OpenBiliClaw 插件侧边栏，确认本地后端连接，按引导初始化画像，然后查看推荐、点喜欢 / 少来点 / 不感兴趣，或直接对话校准。

支持的平台：
- B站
- 小红书
- 抖音
- YouTube
- X（Twitter）
- 知乎
- Reddit

这个插件能做什么：
- 在支持的平台页面识别你授权范围内的内容与互动信号，或执行本地后端下发的来源任务。
- 把跨平台候选统一筛选，在侧边栏、PC Web 和移动 Web 中展示推荐及推荐理由。
- 展示可查看、可纠正的私有画像，并通过喜欢、少来点、不感兴趣和聊天反馈继续调整推荐。
- 在配置页分别展示“来源是否启用”和“接入状态”；“凭据已就绪”“状态待验证”“无需登录”含义不同，不会把仅保存在本地的令牌冒充成实时登录成功。

重要说明：
- 插件不是独立云服务；需要本地后端运行后才有完整体验。
- 默认连接 127.0.0.1 / localhost。连接局域网或远程后端时，需要你显式配置地址并授予对应站点权限；公网后端要求 HTTPS 和设备认证。
- 推荐、画像、反馈和运行数据默认保存在你的本机 SQLite 数据库里，不会发送到 OpenBiliClaw 开发者服务器。
- 你自行配置的 LLM / embedding 服务可能接收完成相应功能所需的内容；可以使用本机 Ollama，也可以使用你自己的 API Key。具体数据边界见隐私政策。
- 来源接入状态默认只读取本地后端保存的凭据、插件心跳和任务历史，不为了刷新配置页而访问外部平台，降低多余请求与封控风险。

隐私政策：
https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/privacy.md

英文说明：
https://github.com/whiteguo233/OpenBiliClaw/blob/main/README_EN.md
```

## 截图上传顺序

以下文件均为 1280×800，使用固定脱敏数据和当前真实 UI 生成。Developer Dashboard 中删除旧图后，按下面顺序上传：

1. `01-local-seven-platforms.png` — 本地私有的七平台内容 Agent
2. `02-three-surfaces.png` — 插件、PC、手机三端体验
3. `03-cross-platform-recommendations.png` — 跨平台推荐与反馈闭环
4. `04-trainable-profile.png` — 可查看、可纠正的私有画像
5. `05-truthful-login-local-data.png` — 诚实接入状态与本地数据

仓库路径：`docs/images/chrome-web-store/`。

需要重做截图时：

```bash
cd extension && npm run build && cd ..
PYTHONPATH=src .venv/bin/python scripts/capture_chrome_webstore_ui.py \
  --output-dir docs/images/chrome-web-store/source
.venv/bin/python scripts/build_chrome_webstore_assets.py
```

捕获脚本只连接临时 `127.0.0.1` 脱敏演示服务，并拦截所有非本机请求；不得用真实 `config.toml`、数据库、Cookie、账号名或画像文本生成商店素材。

## Metadata API bridge

`.github/workflows/update-chrome-webstore-listing.yml` 是独立的手动文案维护入口，默认 `mode=probe`，只交换短期 OAuth access token 并读取 v1.1 draft；它只输出字段名、文案长度和 SHA-256，不输出 token、secret 或 draft 原文。只有 probe 同时发现 `summary` / `description` 和足够的 listing identity 字段后，`mode=apply` 才可能继续；若当前 submission 正在审核，还必须显式启用 `replace_pending`，写入后必须精确回读一致，最后才允许 `publish`。

Chrome Web Store API v1.1 已弃用，官方只支持到 2026-10-15；而且其公开 `Item` resource 没有承诺商店文案字段，因此 probe 返回“不支持 writable listing metadata”是安全的预期停止结果，不得为绕过它而猜测 Dashboard 私有接口。该 bridge 不构建或上传 ZIP、不移动 release tag，也不上传截图；五张 PNG 仍需在 Developer Dashboard 手动替换。

本地只读探测命令（凭据必须来自环境变量）：

```bash
cd extension
npm run webstore:metadata -- \
  --listing ../docs/chrome-webstore-listing.md \
  --mode probe
```

## 提交前检查

- `Short description` 与 `Detailed description` 已粘贴，七个平台名称完整。
- 5 张截图已按上面的文件名顺序上传，尺寸均为 1280×800。
- `Website URL` 使用项目主页：`https://whiteguo233.github.io/OpenBiliClaw/`。
- `Support URL` 使用 GitHub Issues：`https://github.com/whiteguo233/OpenBiliClaw/issues`。
- `Privacy policy URL` 使用 `docs/privacy.md` 的 GitHub 链接。
- 后端默认端口、插件权限、安装方式或支持平台变化时，本文件和截图必须同步更新。
- Metadata workflow 的 probe 必须先成功，apply 才可撤审、写文案和重新提审；probe 失败时不得继续。
