# OpenBiliClaw install instructions (for AI agents)

> **Important:** If you're an AI coding agent reading this file, use
> `curl -fsSL <url> -o /tmp/agent-install.md` via Bash to fetch it —
> **do NOT use the WebFetch tool.** WebFetch hands the document to an
> internal summariser model which can silently drop exact commands.

## The only thing you actually need to do

Pick the command for the user's shell — **detect the platform from the user's environment, do not let them choose if you can tell**:

**macOS / Linux / WSL2 (Bash):**

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

**Native Windows (PowerShell, no Docker / WSL2 required, since v0.3.4):**

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

> The leading `[Net.ServicePointManager]...Tls12` line is **required on PowerShell 5.1** (the default that ships with Windows 10/11). PS 5.1 defaults to TLS 1.0/1.1, but GitHub.com only accepts TLS 1.2+. Without the prefix, `iwr` fails with "underlying connection was closed" and the user blames the installer. Users on PowerShell 7+ can omit the prefix. Compatible from v0.3.9 forward — the script itself also re-applies the same setting once it starts running, so any subsequent HTTPS calls (git, pip, uv) inside the script are also covered.

Either command:

1. Clones the OpenBiliClaw repo (default `~/OpenBiliClaw` on Unix, `%USERPROFILE%\OpenBiliClaw` on Windows; override with the `INSTALL_DIR` env var)
2. Auto-detects any existing OpenBiliClaw install under the standard candidate paths (`~/workspace/OpenBiliClaw`, `~/OpenBiliClaw`, `~/projects/OpenBiliClaw`, `~/code/OpenBiliClaw` — same set on both platforms, rooted at `$HOME` / `%USERPROFILE%`) and **reuses** its LLM API keys and Bilibili cookie so the user never has to retype them
3. Installs Python dependencies (`uv sync` preferred, `pip install -e .` fallback)
4. Starts the backend and runs a health check against `/api/health`
5. Prints a self-contained **status block** at the very end of stdout:

```
================================================================
 OpenBiliClaw install complete / partial (credentials missing)
================================================================
Status:      complete | running_with_missing_secrets | needs_secrets | error
Checkout:    <absolute path to the repo>
Reused from: <path>                 (only present when reuse happened)
Health URL:  http://host:port/api/health
Missing:     (none)  |  llm.<provider>.api_key, bilibili.cookie, ...

Next action (required — credentials are missing):
  1. Ask the user for: <exactly the missing items>
  2. Run this command with the values: <exact python3 command>
     (init will run automatically once credentials are filled in;
      do NOT add --skip-init)
  3. Curl the Health URL to confirm.
  4. Report the final state.

 — or —

Next action (init has been run automatically):
  - Verify the backend is healthy: curl -sS <Health URL>
  - See recommendations:    cd <dir> && uv run openbiliclaw recommend
  - View the soul profile:  cd <dir> && uv run openbiliclaw profile
  - Re-run init manually if needed: cd <dir> && uv run openbiliclaw init
================================================================
```

**Follow that block literally.** That's the entire contract.

## Handling missing credentials

When `Missing` is non-empty, you (the AI agent) need to walk the user through
**three things in order**: pick an LLM, decide on embedding, get a Bilibili
cookie. Don't dump all questions at once — most users hit "I don't know what
that means" if you do. Ask one block at a time, **explain what each thing
does in plain language**, and offer the easy path first.

### Step 1 — Pick an LLM service

Tell the user, in plain Chinese (or the conversation's language):

> 「OpenBiliClaw 需要一个语言模型来理解你的兴趣、写推荐文案。你可以选：」

Then list options **in this order**, with this framing:

| 选项 | 适合谁 | 是否需要 API Key | 是否要钱 |
|---|---|---|---|
| 1. **本地 Ollama**（推荐新手 / 想白嫖 / 想离线用） | 不想花钱、不想申请 API Key、电脑跑得动小模型 | ❌ 不需要 | ❌ 完全免费 |
| 2. OpenAI 官方（GPT-4o 等） | 已有 OpenAI 账户和 Key | ✅ 需要 | 按 token 计费 |
| 3. Claude / Gemini / DeepSeek / OpenRouter（其他官方厂商） | 已有对应账户 | ✅ 需要 | 按 token 计费 |
| 4. **OpenAI 协议兼容的自建/第三方网关**（Azure / vLLM / LMStudio / OneAPI / Together / 自建反代…） | 自己有部署、或团队提供了内网 LLM 网关 | ✅ 通常需要 | 看部署 |

**Why Ollama first**: it's the only zero-friction option. Users without any
LLM API Key today can install Ollama in 30 seconds and have the project
working end-to-end. Cloud providers are kept as the explicit "I have a key
already" branch.

**Critical: option 2 ≠ option 4.** They both write to `[llm.openai]` in
config.toml because they share the OpenAI protocol, but the *user's* mental
model is different — option 2 means "I'll use OpenAI the company"; option 4
means "I have a self-hosted or third-party gateway that speaks the OpenAI
API." If the user picks option 4, **always ask for `--llm-base-url`**. If
the user picks option 2, leave `--llm-base-url` unset (defaults to
`https://api.openai.com/v1`).

### Step 2 — Configure the chosen LLM

Once they've picked, only ask the **fields that option actually needs**.

#### Option 1 (Ollama):

> 「Ollama 是个本地运行的开源工具。请确认：
>   - 你的电脑已经装了 Ollama（Mac: `brew install ollama` / Windows: 从 https://ollama.com/download 下载 / Linux: `curl -fsSL https://ollama.com/install.sh \| sh`）
>   - Ollama 服务在跑（终端里 `ollama serve &`，或 Mac 直接打开 Ollama.app）
>   - 拉了一个模型（建议 `ollama pull llama3` 或更小的 `ollama pull qwen2.5:3b`）」

Then run with `--provider ollama` (no `--llm-api-key`, no `--llm-base-url`
unless they need a non-default port).

#### Option 2 (OpenAI 官方):

> 「请给我你的 OpenAI API Key（以 sk- 开头）。可以从 https://platform.openai.com/api-keys 创建。」

Run with `--provider openai --llm-api-key <KEY>`. Don't ask for Base URL.

#### Option 3 (Claude / Gemini / DeepSeek / OpenRouter):

Substitute the right vendor name and Key URL:

- Claude: https://console.anthropic.com/ → Settings → API Keys
- Gemini: https://aistudio.google.com/apikey
- DeepSeek: https://platform.deepseek.com/api_keys
- OpenRouter: https://openrouter.ai/keys

Run with `--provider <name> --llm-api-key <KEY>`.

#### Option 4 (OpenAI 协议兼容自建网关):

Ask **all three**, with explanations:

> 「你的网关需要给我三件套：
>   - **Base URL**：网关的 `/v1` 端点（例：`http://localhost:8000/v1` 或 `https://your-gateway.example.com/v1`）。这是 OpenBiliClaw 实际去打的 HTTP 地址
>   - **API Key**：网关要不要鉴权？要的话给我 Key；不要的话填 `none` 或留空
>   - **模型名**：网关上具体部署的是哪个模型？（例：vLLM 上的 `meta-llama/Llama-3.1-70B`，Azure 上是你的 deployment 名）」

Run with `--provider openai --llm-base-url <URL> --llm-api-key <KEY> --llm-model <MODEL>`.

### Step 3 — Embedding（向量化服务，独立的一个问题）

**Embedding 是和聊天模型分开的**。它负责把文本变成向量，用于：「这条视频和你之前喜欢的那条是不是同一个主题」「换一批的时候去重」等。它调用频次很高，所以单独拎出来配。

Tell the user:

> 「OpenBiliClaw 还需要一个 embedding（向量化）服务。它做的事：把视频标题/简介变成向量，跨视频做相似度对比，决定换一批时哪些是『重复的』。**你可以三选一：**
>
>   1. **跟随你刚才选的 LLM**（最省事）—— 如果你刚才选了 Ollama，embedding 也用 Ollama；如果选了 OpenAI/Gemini，embedding 也用同一家
>   2. **本地 Ollama + bge-m3**（推荐：免费 + 离线 + 跨模型一致）—— 即使你聊天模型用 OpenAI/Claude，embedding 也单独走本地，省 quota
>   3. **跳过，先不配**（默认跟随 LLM）」

If the user picks option 2 (local Ollama embedding), use:
`--embedding-provider ollama --embedding-model bge-m3`. Tell them to run
`ollama pull bge-m3` (~568MB, CPU 即可) before next backend restart.

If option 1 or 3, leave embedding flags off entirely (don't pass empty
strings, just omit the flags).

### Step 4 — B 站 Cookie

Most users haven't done this before. **Don't just say "give me your
Bilibili cookie."** Walk them through it:

> 「OpenBiliClaw 需要你的 B 站登录态（Cookie），用来：拉你的观看历史 → 训练画像；以你的身份调 B 站 API 拿视频详情。**Cookie 只存在你本机，不会上传任何地方。**
>
> 怎么拿：
>   1. 用 Chrome / Edge / Firefox **登录** https://www.bilibili.com
>   2. 按 `F12` 打开开发者工具 → 切到 **Network（网络）** 标签
>   3. 刷新一下 B 站页面 → 在请求列表点任意一条 `bilibili.com` 的请求
>   4. 右侧 **Headers（请求头）** 区域，找到 `cookie:` 这一行，右键复制整行的 value
>   5. 把那一长串（包含 `SESSDATA=...; bili_jct=...; DedeUserID=...` 等）粘给我
>
> （或者更简单：装我们的 Chrome 扩展，它会自动用你的登录态，零配置。下载：https://github.com/whiteguo233/OpenBiliClaw/releases）」

Run with `--bilibili-cookie "<the full cookie string>"`.

### Putting it all together — example commands

**新手白嫖路径**（Ollama + 跟随 embedding + B 站 cookie）：

```bash
python3 scripts/agent_bootstrap.py \
  --provider ollama \
  --llm-model llama3 \
  --bilibili-cookie "SESSDATA=...; bili_jct=...; DedeUserID=..."
```

**自建网关 + Ollama embedding 兜底**（最常见的进阶路径）：

```bash
python3 scripts/agent_bootstrap.py \
  --provider openai \
  --llm-base-url http://localhost:8000/v1 \
  --llm-api-key sk-or-none \
  --llm-model meta-llama/Llama-3.1-70B-Instruct \
  --embedding-provider ollama \
  --embedding-model bge-m3 \
  --bilibili-cookie "SESSDATA=...; ..."
```

> ⚠️ **Do NOT pass `--skip-init`** here. The point of running the
> bootstrap with credentials is to reach a usable state. When all
> credentials are present and `--skip-init` is absent (the default),
> `agent_bootstrap.py` will automatically run `openbiliclaw init` after
> the backend is healthy: it pulls the user's Bilibili history,
> generates the soul profile, and runs the first content discovery
> pass. **Without init, the user has nothing to look at — they'd see an
> empty extension and would have to manually run `openbiliclaw init`
> themselves**, which defeats the point of one-line install.

After running, **always**:

1. Curl the `Health URL` from the status block to confirm `200 OK`.
2. Report the final state to the user, including:
   - "✅ 后端已启动，监听 http://127.0.0.1:8420"
   - "✅ 配置已写入"
   - "✅ 初始化已完成 —— 已拉取你的 B 站历史、生成画像、跑了首轮内容发现"
   - "👉 下一步：装浏览器扩展（链接）来看推荐"

**`init` takes 2-5 minutes on first run** (real LLM calls + real
Bilibili fetches). Tell the user upfront so they don't think it's
hung. The bootstrap streams init's stdout so progress is visible.

### Per-module overrides（高级，默认不要问）

`--module-override MODULE=PROVIDER:MODEL`（可重复）。模块：`soul` /
`discovery` / `recommendation` / `evaluation`。**只有当用户主动说「我想给画像
用更贵的模型，发现用便宜的」之类的话，才提这个**。否则跳过——大多数用户根本不
关心，多问一项就多一份让人迷惑的可能。

## Optional: local Ollama as the embedding fallback

This is a **post-install opt-in**, not part of the install contract. Mention
it to the user only if they ask about offline operation, embedding-quota
errors, or a no-API-key setup. Steps:

1. User installs Ollama: Mac `brew install ollama && ollama serve &`,
   Windows from `https://ollama.com/download`, Linux
   `curl -fsSL https://ollama.com/install.sh | sh && ollama serve &`.
2. User runs `cd <INSTALL_DIR> && uv run openbiliclaw setup-embedding`.
3. The wizard probes `localhost:11434`, pulls `bge-m3` if missing, and
   writes both `[llm.embedding]` and `[llm.ollama] base_url` to
   `config.toml`. Restart the backend after this.

Do NOT run these steps for the user automatically — Ollama install is a
system-level package the user must consent to.

> **Backend version note**: in v0.3.0–v0.3.2 the wizard wrote the
> embedding config but the LLM registry never registered Ollama
> unless `[llm.ollama]` was also populated, so embedding silently
> kept hitting the user's primary cloud provider. Fixed in **v0.3.3**:
> the registry auto-registers Ollama whenever `[llm.embedding]` asks
> for it. If a user reports "I configured ollama but embedding still
> hits Gemini/OpenAI", check their backend version and tell them to
> upgrade to v0.3.3+ and restart.

## Hard rules

1. **Never edit `config.toml` by hand.** Every credential write goes through `scripts/agent_bootstrap.py`.
2. **Never hard-code `http://127.0.0.1:8420/api/health`.** Always use the `Health URL` line from the status block — the port may be different if the user already has another instance running.
3. **Run init by default — DO NOT pass `--skip-init`.** Once all credentials are present, the user's expectation is "the app is ready to use." That means: history pulled, soul profile generated, first discovery pass done. `agent_bootstrap.py` does this automatically after the backend is healthy. Only pass `--skip-init` when the user explicitly says "don't pull my history yet" or you're doing a credentials-only patch on an already-initialized install.
4. **Never use WebFetch on this document.** WebFetch summarises markdown and can drop exact flags. Use Bash `curl -o` + Read instead.

## Deeper reference (for humans, not required)

- `docs/agent-deployment.md` — long-form troubleshooting with the full JSON event reference
- `docs/docker-deployment.md` — manual Docker setup
- `docs/openclaw-quickstart.md` — OpenClaw-specific integration after install
- `scripts/install.sh` — the installer itself (the command above)
- `scripts/agent_bootstrap.py` — the Python contract core invoked by install.sh
