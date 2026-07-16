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

For an explicit Docker install on macOS / Linux / WSL2, set `MODE=docker`
on the same one-liner:

```bash
MODE=docker curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

> ⚠️ **Detect, don't guess.** On a native Windows host (no WSL),
> `bash` may resolve to `C:\Windows\System32\bash.exe`, which is the
> Microsoft WSL launcher — not Git Bash. If WSL isn't actually
> installed, the launcher fails with `execvpe(/bin/bash) failed: No
> such file or directory`. AI agent: if you see this on Windows, do
> NOT silently fall back to `bash`. Instead either:
> - run the **PowerShell** one-liner below (preferred — install.ps1
>   is the supported native-Windows path), **or**
> - explicitly invoke Git Bash:
>   `& "C:\Program Files\Git\bin\bash.exe" -c "curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash"`

**Native Windows (PowerShell, no Docker / WSL2 required, since v0.3.4):**

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

For an explicit Docker Desktop install on native Windows:

```powershell
$env:MODE="docker"; [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

> The leading `[Net.ServicePointManager]...Tls12` line is **required on PowerShell 5.1** (the default that ships with Windows 10/11). PS 5.1 defaults to TLS 1.0/1.1, but GitHub.com only accepts TLS 1.2+. Without the prefix, `iwr` fails with "underlying connection was closed" and the user blames the installer. Users on PowerShell 7+ can omit the prefix. Compatible from v0.3.9 forward — the script itself also re-applies the same setting once it starts running, so any subsequent HTTPS calls (git, pip, uv) inside the script are also covered.
> v0.3.71+ also sets `NO_PROXY/no_proxy=localhost,127.0.0.1,::1` in `install.sh`, `install.ps1`, and `agent_bootstrap.py` before local health checks. This keeps corporate/VPN proxies from intercepting `http://127.0.0.1:<port>/api/v1/system/readiness` on native Windows.

Either command:

1. Clones the OpenBiliClaw repo (default `~/OpenBiliClaw` on Unix, `%USERPROFILE%\OpenBiliClaw` on Windows; override with the `INSTALL_DIR` env var)
   - Desktop installers use this same directory for `config.toml` / `data/` / `logs/`. If the desktop package created the directory first, the one-line installer clones source files into it without touching existing user data.
2. Auto-detects any existing OpenBiliClaw install under the standard candidate paths (`~/workspace/OpenBiliClaw`, `~/OpenBiliClaw`, `~/projects/OpenBiliClaw`, `~/code/OpenBiliClaw` — same set on both platforms, rooted at `$HOME` / `%USERPROFILE%`). It first constructs the connection type/preset routes selected by flags or the wizard, then overlays only compatible credentials plus the Bilibili cookie; explicit credentials/cookie still win
3. In a human terminal, opens the full installer wizard **before dependency install or backend start**: human one-line installer asks Chat connection type first, then a preset only when supported, descriptor-specific fields, the ordered embedding route, Bilibili init limits, XHS / Douyin / YouTube opt-ins, and Bilibili cookie source
4. Installs Python dependencies for local mode, or generates missing LiteLLM/PostgreSQL infrastructure secrets under a cross-process lock using a same-directory mode-`0600` temporary file, file/directory `fsync`, and atomic replacement before Docker Compose starts when `MODE=docker`; existing non-empty values and unrelated `.env` entries are preserved, while `.env`/lock symlinks are rejected. X/Twitter discovery's `twitter-cli` and Reddit discovery's `rdt-cli` packages are part of the default dependency set, so AI one-line installs do not need an extra flag for either one
5. Starts the vNext API with `openbiliclaw serve` and checks `/api/v1/system/readiness`. Docker mode creates PostgreSQL, LiteLLM, source-encryption, and bearer-access secrets. Static Web/extension API wiring lands in Task 22.
   - **Optional LAN password gate**: exposing `0.0.0.0` makes the UI reachable by any device on the network. To require a login for LAN/remote devices (the local machine and the browser extension stay password-free), run `openbiliclaw set-password` (or answer "yes" to the init prompt), or set `OPENBILICLAW_API_AUTH_ENABLED=true` + `OPENBILICLAW_API_AUTH_PASSWORD=…` for unattended/Docker installs. See [`docs/modules/api-auth.md`](modules/api-auth.md). Behind a same-host reverse proxy, also set `[api.auth].trusted_proxies` or have the proxy enforce auth.
6. Probes the exact stable primary Chat connection with fallback disabled and every ordered Embedding provider against the shared settings. One failed Embedding probe does not stop later exact probes; after all IDs are attempted, any failure blocks init with the fixed secret-safe `status=service_check_failed`
7. Automatically runs init after credentials, confirmations, and AI service checks are complete, then prints a self-contained **status block** at the very end of stdout:

```
================================================================
 OpenBiliClaw install complete / partial (credentials missing)
================================================================
Status:      complete | running_with_missing_secrets | needs_secrets | needs_decisions | service_check_failed | error
Checkout:    <absolute path to the repo>
Reused from: <path>                 (only present when reuse happened)
Health URL:  http://127.0.0.1:8420/api/v1/system/readiness
Missing:     (none)  |  models.chat.connections.<id>.credential, bilibili.cookie, ...

Next action (required — credentials are missing):
  1. Ask the user for: <exactly the missing items>
  2. Run this command with the values: <exact python3 command>
     (init will run automatically once credentials are filled in;
      do NOT add --skip-init)
  3. Curl the Health URL to confirm.
  4. Report the final state.

 — or —

Next action (AI service check failed):
  1. Fix the exact primary Chat connection or ordered Embedding provider shown in the status block.
  2. Re-run the printed bootstrap command without --skip-init.
     The bootstrap repeats the checks and only then runs init.

 — or —

Next action (init has been run automatically):
  - Verify the backend is healthy: curl -sS <Health URL>
  - Open Mobile Web: click the phone icon in the extension header and scan the QR code; if the backend address is loopback, the extension calls `GET /api/qr-info` and reads the `lan_ip` response field to show the LAN URL automatically
  - See recommendations:    cd <dir> && uv run openbiliclaw recommend
  - View the soul profile:  cd <dir> && uv run openbiliclaw profile
  - Re-run init manually if needed: cd <dir> && uv run openbiliclaw init
================================================================
```

**Follow that block literally.** That's the entire contract.

`init_complete` is the normal success target. `needs_secrets`,
`running_with_missing_secrets`, and `needs_decisions` are intermediate
states: continue the printed bootstrap command after asking the user,
or wait for the browser extension to sync the Bilibili cookie, until
bootstrap emits `init_complete` or a concrete blocker.
`service_check_failed` means credentials and init choices were present,
but the exact primary Chat connection or an ordered Embedding provider
failed its pre-init probe. Do not run `openbiliclaw init` manually around
this; use `openbiliclaw models list`, fix the indicated stable record, and
re-run bootstrap so the same exact gate can pass.

If the block says `Status: needs_decisions`, credentials are present
but init has deliberately not run. Ask the listed init choices, then
re-run the printed `agent_bootstrap.py` command with explicit
`--embedding-*`, `--yes-xhs` / `--no-xhs`, and
`--yes-douyin` / `--no-douyin`, and
`--yes-youtube` / `--no-youtube`.

### After init succeeds — relay the per-source signal counts

When `openbiliclaw init` finishes, the CLI prints a "初始化摘要" key-
value table with explicit B 站 + 小红书 + 抖音 + YouTube breakdowns. The
agent **must summarise these numbers in the user-facing reply** so
the user knows what fed their soul profile. Render approximately:

> 「初始化已完成 ✅
>   - 📺 B 站:观看历史 N 条 / 收藏 M 条 / 关注 K 个 UP → **入库 X 条事件**
>   - 📕 小红书:收藏 P 条 / 点赞 Q 条 / 浏览记录 R 条 → **入库 Y 条事件**
>   - 🎵 抖音:发布 A 条 / 收藏 B 条 / 点赞 C 条 / 关注 D 人 → **入库 E 条事件**
>   - 🌐 YouTube:观看历史 H 条 / 订阅 S 个 / 点赞 L 条 → **入库 T 条事件**
>   - 📊 画像建模总事件:Z 条
>   - 🔍 首轮发现内容池:D 条
> 现在可以打开扩展 popup 看推荐了。」

When 小红书 totals are 0 / 0 / 0 the CLI also prints a "ℹ️  小红书
0 条信号入库" hint with likely causes — pass that note through to
the user verbatim, don't drop it.

## HARD RULES (read first, follow every time)

These are non-negotiable agent-side rules. Violating any of them
breaks the user experience in ways our backend cannot recover from.
**If you summarise the doc and skip these, the install fails.**

### Rule 1 — Never prompt for B 站 Cookie without leading with the extension

This rule exists because real users got the prompt
> 「请把 B 站 Cookie 发给我。从浏览器登录后的 B 站页面复制 Cookie，至少要包含 SESSDATA=...」

with **no mention** of the extension. Their AI agent had paraphrased
Step 4 into the F12 path because "give me a cookie" is the obvious-
seeming question. It is **wrong**. Since v0.3.12 the extension auto-
syncs the cookie via `chrome.cookies.onChanged` →
`POST /api/bilibili/cookie`; F12-extraction is the legacy fallback,
and 95% of users who pick F12 are doing it because the agent didn't
tell them the extension exists.

**Concrete contract**:
- The first sentence of the cookie step must reference the browser
  extension and the Releases page URL.
- F12-extraction must appear *after* the extension option, marked as
  「不想装扩展时的兜底」, not before.
- See Step 4 below for the exact prescribed prompt — render it
  approximately verbatim, do not "summarise" it down to one option.

### Rule 2 — Run `agent_bootstrap.py` exactly as the status block prints

The status block at the end of `install.sh` / `install.ps1` prints a
literal `python3 scripts/agent_bootstrap.py ...` command with the
right flags. Do not re-derive that command from memory; the flag set
evolves and stale flags silently no-op.

If a user's environment has `HTTP_PROXY` / `HTTPS_PROXY` set, keep the
printed command as-is. `agent_bootstrap.py` will extend `NO_PROXY` and
`no_proxy` for localhost before starting the backend and polling health.

### Rule 3 — One question at a time, with a clear default

Don't dump all four credential questions at once. Each question must
have a default that reads as "ok if you don't care, just pick this
one"; most users will accept it. The previous "tell me what an
embedding is" framing was the failure mode.

### Rule 4 — Reused credentials must be confirmed, not silently skipped

When `install.sh` / `install.ps1` reuses a previous install's secrets,
the status block prints a `Reused from: <path>` line **and**
`agent_bootstrap.py`'s JSON output lists each reused field under
`reused`. You can also detect cookie reuse by inspecting whether
`bilibili.cookie` is in the bootstrap summary's `reused` list, or
whether `data/bilibili_cookie.json` already exists in the install dir.
Route selection happens before the credential overlay, so a fresh DeepSeek
template can reuse an OpenAI credential when OpenAI was selected for the new
install. A credential borrowed from one legacy Provider table is consumed at
most once across Chat and Embedding; separate native route records retain their
own stable-ID identity even if their credential values happen to match. Explicit
Raw secret flags remain compatibility inputs for controlled automation, but
they expose values in process argv and shell history. Human setup and recovery
must prefer `--interactive-confirm`; its API Key and manual Cookie prompts
disable terminal echo. Explicit compatibility values still override reused
values.

Recovery is selective. The installer carries the selected runtime `mode` and
the validated current `connection_type` and `preset` forward as non-secret
flags, while bootstrap
reuses the current model, Base URL, ordered Chat fallbacks, complete Embedding
route, source decisions, and import limits. It prompts only for a missing
credential or an unresolved privacy choice; it does not rerun unrelated
questions or collapse a multi-Provider Embedding route to one legacy alias.

When `--embedding-api-key` is supplied without `--embedding-provider` or
`--embedding-endpoint`, bootstrap updates every existing ordered Embedding
provider whose descriptor accepts credentials. Stable IDs, names, types,
presets, endpoints, shared settings, and order are preserved; credentialless
providers such as Ollama remain unchanged. If the route has no
credential-capable provider, bootstrap reports an error before route writes or
credential reuse; neither the configuration nor the Bilibili cookie file is
rewritten.

**You must surface the reuse to the user, not skip the corresponding
question silently.** Specifically for `bilibili.cookie`:

- B 站 cookies expire (typically every few weeks; faster if the user
  signs out / changes IP / triggers risk control).
- A reused cookie was set during the **previous** install, possibly
  days or weeks ago. The user has no reason to know whether it's
  still valid.
- Init may run to completion against a stale cookie because the
  history-fetch path swallows the auth failure and just returns 0
  items — leading to a hollow soul profile and the "为什么我的画像
  里没东西" support ticket.

**Live validation (v0.3.162+)**: once the backend passes its health
check, bootstrap consumes the backend's own live cookie probe
(`GET /api/init-status` → `prerequisites.bilibili_check`) for any run
that reused a cookie:

- Probe says **failed** → the status block reports
  `bilibili.cookie (stale — reused cookie failed live validation)`
  under `Missing:`, the final `Status:` is downgraded from `complete`
  to `needs_secrets`, and auto-init is **not** run (it could only fail
  with `empty_history`). `install.sh` / `install.ps1` print an explicit
  "cookie 已失效，请重新登录后由扩展同步" block instead of the generic
  disclaimer. When you see this state, go straight to the re-login
  options below (default A) — the cookie is confirmed dead, do not
  offer "先用旧的" (option B).
- Probe says **ok** → the reused cookie is confirmed live; you may
  soften the confirmation below, but still surface the reuse.
- Probe unreachable / indeterminate (backend not healthy, `checking`)
  → no downgrade; the generic "NOT validated" disclaimer applies and
  the full contract below is mandatory.

**Concrete contract for reused cookies**:

> When you see `bilibili.cookie` in the reused set, render this to
> the user before continuing to the next question:
>
> ```
> 我注意到安装器从之前的目录复用了一份 B 站 Cookie。
> 这份 Cookie 可能已经过期(B 站 Cookie 几周内就会失效)。
>
> 你想怎么办?
>   A. 装一下浏览器扩展(推荐): 装好后扩展会立刻把最新 cookie
>      推到后端,覆盖那条旧的。即使旧 cookie 还有效也是净赚——
>      过期 / 续签都会自动同步,以后再装就不用管了。
>      下载: https://github.com/whiteguo233/OpenBiliClaw/releases
>   B. 先用旧的: 我先继续 init,如果中途看到
>      "Cannot fetch history without authentication" 或者
>      画像数据明显偏少,就是 cookie 过期了,到时再装扩展。
>   C. 现在就手动贴一份新的(F12): 适合你正好在 B 站登录页
>      手边能直接拿,且不想装扩展的情况。
> ```
>
> Wait for an explicit answer (A/B/C) before continuing. Default is A.

The same rule pattern applies to reused LLM API keys, but those are
less likely to silently expire — a one-line "我用了 v0.3.x 那次留下
的 DeepSeek key,有问题告诉我" mention is enough.

---

## Handling missing credentials

When `Missing` is non-empty, or the final status is
`needs_decisions`, you (the AI agent) walk the user through **six
questions, in order**: pick a Chat connection type (and preset when the
type offers one), configure the ordered Embedding route, get a
B 站 cookie, ask whether Xiaohongshu likes/favorites may be used, then
ask whether Douyin post/favorite/like/follow signals may be used, then
ask whether YouTube history/subscriptions/likes may be used.
Each question must have a clear default — most users will accept it.

`agent_bootstrap.py` is non-interactive by default; the one-line human
installers pass `--interactive-confirm` to open the terminal wizard. If
credentials are already present but you did not pass an explicit embedding choice
and explicit source choices (`--yes-xhs` / `--no-xhs` plus
`--yes-douyin` / `--no-douyin` plus
`--yes-youtube` / `--no-youtube`), it returns
`status=needs_decisions` and **does not run init**. Ask the missing
questions, then re-run bootstrap with those flags.

On a recovery rerun, `--interactive-confirm` reads the current native route and
the supplied non-secret flags first. The original runtime mode and already
settled connection, preset,
Embedding-route, limit, and source values are retained without being asked
again; only fields still listed as missing are prompted.

When an older non-interactive command omits an Embedding choice, the local
bootstrap compatibility path may create one native `ollama` provider with
shared model `bge-m3`; explicit disable remains `--embedding-provider ""`.
Docker instead seeds the same native provider at
`http://ollama:11434/v1`. Any already configured native ordered route is
preserved; this compatibility default never follows the selected Chat preset.

### Step 1 — Choose the Chat connection type, then its preset

Tell the user, in plain Chinese (or the conversation's language):

> 「OpenBiliClaw 需要一个语言模型来理解你的兴趣、写推荐文案。先选连接方式；如果这种连接方式支持多个服务，再选 preset。」

Contract marker: human one-line installer asks Chat connection type first.

Present **five top-level connection types**. API-compatible families stay
grouped by protocol; OAuth logins remain separate types.

| # | Connection type | Meaning | Credential |
|---|---|---|---|
| 1 | `openai_compatible` ★ default | OpenAI Chat Completions-compatible API | API key + optional endpoint |
| 2 | `anthropic_compatible` | Anthropic Messages-compatible API | API key + optional endpoint |
| 3 | `gemini_api` | Google native Gemini API | API key |
| 4 | `ollama` | Local Ollama runtime | no key |
| 5 | `codex_oauth` | Imported Codex login | OAuth reference only |

DeepSeek / OpenAI / OpenRouter are presets, not top-level providers.
Likewise, Anthropic official and a custom Anthropic Messages gateway share
one connection type. This keeps the top-level list stable as more vendors
and relays are added.

**OpenAI-compatible presets**:

| Preset | Default model | Endpoint behavior |
|---|---|---|
| `deepseek` ★ default | `deepseek-v4-flash` | official DeepSeek endpoint |
| `openai` | `gpt-5-nano` | official OpenAI endpoint |
| `openrouter` | `openai/gpt-5-nano` | official OpenRouter endpoint |
| `custom` | `gpt-5-nano` | user must provide `--llm-base-url` |

**Anthropic-compatible presets**:

| Preset | Default model | Endpoint behavior |
|---|---|---|
| `anthropic` ★ default | `claude-sonnet-4-6` | official Anthropic endpoint |
| `custom` | user supplied | user must provide `--llm-base-url` |

The wizard only asks for a preset after a type that defines presets is
selected. `gemini_api`, `ollama`, and `codex_oauth` do not receive an
irrelevant preset question. It then asks only fields allowed by the selected
descriptor: model, custom Base URL when required, and credential when
required. Credentials are reused only when both connection type and preset
still match; switching either one clears the incompatible credential.

The first Chat record and every fallback are the same record type. List order
is priority: item 1 is primary and items 2–10 are fallbacks. Bootstrap edits
the stable primary ID in place and preserves the ordered fallback records.
For later route edits use:

```bash
openbiliclaw models list
openbiliclaw models add --kind chat
openbiliclaw models edit <STABLE_ID>
openbiliclaw models move <STABLE_ID> --position <1-10>
openbiliclaw models remove <STABLE_ID>
openbiliclaw models probe <STABLE_ID>
```

### Step 2 — Configure the selected Chat connection

Canonical setup uses `--connection-type` and, only where supported,
`--preset`. These examples keep only non-secret choices in argv;
`--interactive-confirm` collects credentials with terminal echo disabled:

```bash
# Default DeepSeek preset
python3 scripts/agent_bootstrap.py \
  --connection-type openai_compatible --preset deepseek \
  --llm-model deepseek-v4-flash --interactive-confirm ...

# OpenAI official preset
python3 scripts/agent_bootstrap.py \
  --connection-type openai_compatible --preset openai \
  --llm-model gpt-5-nano --interactive-confirm ...

# Custom OpenAI-compatible relay
python3 scripts/agent_bootstrap.py \
  --connection-type openai_compatible --preset custom \
  --llm-base-url https://relay.example/v1 \
  --llm-model relay-model --interactive-confirm ...

# Anthropic official preset
python3 scripts/agent_bootstrap.py \
  --connection-type anthropic_compatible --preset anthropic \
  --llm-model claude-sonnet-4-6 --interactive-confirm ...

# Native APIs, local runtime, and OAuth do not take --preset
python3 scripts/agent_bootstrap.py --connection-type gemini_api --interactive-confirm ...
python3 scripts/agent_bootstrap.py --connection-type ollama --llm-model qwen2.5:7b ...
python3 scripts/agent_bootstrap.py --connection-type codex_oauth --llm-model gpt-5-nano ...
```

`--provider` remains a deprecated non-interactive compatibility alias. It maps
exactly to a connection type plus preset (`deepseek`, `openai`, `openrouter`,
`claude`, `gemini`, or `ollama`) and never creates a legacy
`[llm]` writer. New scripts must use the canonical flags above.

Bootstrap converts every accepted choice into the same native descriptor-backed
records used by `ModelConfigService`; runtime construction then uses only the
ordered Chat and Embedding factories. The deprecated flags are input aliases,
not a legacy provider registry, class, or alternate configuration authority.

Codex OAuth is canonical/human-wizard only: select `codex_oauth` directly;
there is no deprecated `--provider codex` alias and no raw token flag.

`--llm-preset` is also a deprecated shortcut for older OpenAI-compatible
relay names. Prefer `--connection-type openai_compatible --preset custom`
with explicit Base URL and model for new automation.

Before init, bootstrap probes the exact stable primary Chat connection with
fallback disabled. A failure is reported as a fixed safe error and blocks
init; raw provider exception text and secrets are not emitted. Use
`openbiliclaw models list` to find the stable ID, then
`openbiliclaw models probe <STABLE_ID>` for a deliberate exact check.

### Step 3 — Configure the ordered Embedding route

Embedding can be disabled or contain 1–10 ordered providers. All providers
must use the same shared model settings: `model`, output dimensionality,
similarity threshold, and multimodal flag. Provider records only hold their
connection type/preset, endpoint, and credential. Reordering compatible
providers therefore changes failover priority without creating a second
vector space.

Repeat `--embedding-endpoint TYPE[:PRESET]=BASE_URL` to configure multiple
providers; one `--embedding-model` applies to the complete route:

```bash
python3 scripts/agent_bootstrap.py \
  --embedding-model bge-m3 \
  --embedding-endpoint ollama=http://127.0.0.1:11434/v1 \
  --embedding-endpoint ollama=http://127.0.0.1:11435/v1 ...
```

Bootstrap preserves positional stable IDs when editing this list and never
carries a credential across a connection-type or preset change. The deprecated
single `--embedding-provider` alias edits only the first provider and preserves
the remaining ordered fallbacks and their credentials; when it creates a first
provider, `embedding-main` or its first deterministic numeric suffix is allocated
against both Chat and Embedding IDs. For provider-specific credentials or later
add/remove/reorder work, use the native editor:

```bash
openbiliclaw models add --kind embedding
openbiliclaw models edit <STABLE_ID>
openbiliclaw models move <STABLE_ID> --position <1-10>
openbiliclaw models remove <STABLE_ID>
openbiliclaw models probe <STABLE_ID>
```

The pre-init gate probes every configured ordered Embedding provider exactly
against the shared settings, continuing through the complete list after a
secret-safe fixed failure. It does not silently pass because a different
provider works. Docker's default `ollama` + `bge-m3` record points to
`http://ollama:11434/v1`. A user-supplied ordered route is preserved.

### Step 4 — B 站 Cookie

> 🚨 **Hard Rule 1 applies here.** Read it again before writing your
> reply. The single most common install regression is an agent that
> paraphrases this step down to "请把 B 站 Cookie 发给我" with no
> mention of the extension. Don't be that agent.

**Render the prompt below approximately verbatim.** It is fine to
translate to the conversation's language, but **all five lettered
points (A.1 + B.1–B.4) must appear**, and **A. must come first**.

```
==== render this to the user (exact text, do not summarise) ============
「OpenBiliClaw 需要你的 B 站登录态（Cookie）来拉你的观看历史 + 调 B 站 API。
 Cookie 只存在你本机，不会上传任何地方。

 两种方式（任选其一，强烈推荐 A）：

 A. 装浏览器扩展（推荐，零配置）
    下载: https://github.com/whiteguo233/OpenBiliClaw/releases
    装好后，确保你已登录 B 站（如果没登就去登）。扩展会在几秒内
    自动把 Cookie 推到本地后端，之后 Cookie 过期/续签也会自动同步。
    选这个就什么都不用贴给我，等我从 /api/runtime-status 看到
    bilibili_cookie_synced 即可。

 B. 手动贴 Cookie（不想装扩展时的兜底）
    1. 用 Chrome / Edge / Firefox 登录 https://www.bilibili.com
    2. 按 F12 → Network 标签 → 刷新 → 点任意 bilibili.com 请求
    3. Headers 区域找到 cookie: 这一行，右键复制整行 value
    4. 把那一长串（含 SESSDATA / bili_jct / DedeUserID）粘给我」
==== end of prescribed text =============================================
```

**Background (don't render this part to the user, this is just for
your understanding):** since v0.3.12 the extension auto-syncs the
B 站 cookie to the backend on install — `chrome.cookies.onChanged` →
`POST /api/bilibili/cookie` → backend validates against B 站 nav and
persists. The F12 dance is genuinely a fallback path now: most users
hit it only because their AI agent forgot to mention option A.

**If user picks A**: choose extension sync instead of the manual Cookie prompt. The
v0.3.20+ install.sh status block will explicitly print
`OpenBiliClaw backend ready — waiting for browser extension to sync
B站 Cookie` in **green** when this is the only thing missing — this is
the success state, not a failure. (Earlier versions printed yellow
`partial / credentials still missing` here, which routinely scared
users into thinking the install crashed.) Tell the user:

> 「我已经把后端跑起来了。现在请你装扩展（链接 ↑），登录 B 站，
>   等几秒——扩展会自动把 Cookie 推过来。然后我帮你跑 `openbiliclaw init`
>   完成画像生成 + 首轮发现（2-5 分钟）。」

Then poll `GET /api/runtime-status` (or watch for the
`bilibili_cookie_synced` event on `ws://127.0.0.1:8420/api/runtime-stream`)
to detect when the cookie has arrived. If the original bootstrap wait
timed out, re-run the printed `agent_bootstrap.py` command so service
checks and source choices still gate init:

```bash
python3 scripts/agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie
```

**If user picks B**: rerun with `--interactive-confirm`, choose the
manual Cookie option, and let the user paste it into the no-echo prompt. Keep
the explicit non-secret Embedding and source flags from the user's answers;
bootstrap auto-runs init once everything is present.

### Step 5 — Bilibili init signal limits

Before any non-interactive auto-init, confirm:

> 「B 站初始化默认导入最近 500 条观看历史、最多 500 条收藏、最多 100 个关注 UP。
> 历史保持 500；收藏和关注要改上限吗？收藏直接回车就是 500，关注直接回车就是 100，填 0 就跳过对应信号。」

Map answers to:

| 项 | 用户回答 | 命令行参数 |
|---|---|---|
| 收藏上限 | 回车 / 不确定 | 省略或 `--bilibili-favorite-limit 500` |
| 收藏上限 | 数字 N | `--bilibili-favorite-limit N` |
| 关注上限 | 回车 / 不确定 | 省略或 `--bilibili-follow-limit 100` |
| 关注上限 | 数字 N | `--bilibili-follow-limit N` |

Human-run `install.sh` / `install.ps1` pass `--interactive-confirm`, so
`agent_bootstrap.py` will ask these two numbers directly and pass them into
`openbiliclaw init`.

### Step 6 — Source data opt-in

Before any non-interactive auto-init, ask:

> 「要把你的小红书收藏 / 点赞也混进初始画像吗？这能让跨平台口味更准，
> 但会让扩展打开小红书页面抓取这些信号。默认不启用；你明确说要用我才开。」

Then ask separately:

> 「要把你的抖音发布 / 收藏 / 点赞 / 关注也混进初始画像吗？这会让抖音口味进入画像，
> 但会让扩展打开抖音页面执行拉取；扩展也会把 douyin.com Cookie 同步给后续 discovery，search / hot / feed discovery 会复用登录浏览器，从抖音首页开始模拟 DOM 操作触发加载，并被动收集页面响应 / 渲染结果。
> 默认不启用；你明确说要用我才开。」

Then ask separately:

> 「要把你的 YouTube 观看历史 / 订阅 / 点赞也混进初始画像吗？这会让长视频口味进入画像，
> 但会让扩展打开 YouTube 页面执行拉取。默认不启用；你明确说要用我才开。」

Map each answer to exactly one bootstrap flag:

| 源 | 用户回答 | 命令行参数 |
|---|---|---|
| 小红书 | 明确同意 | `--yes-xhs` |
| 小红书 | 拒绝 / 没有小红书 / 不确定 / 没回答 | `--no-xhs` |
| 抖音 | 明确同意 | `--yes-douyin` |
| 抖音 | 拒绝 / 没有抖音 / 不确定 / 没回答 | `--no-douyin` |
| YouTube | 明确同意 | `--yes-youtube` |
| YouTube | 拒绝 / 没有 YouTube / 不确定 / 没回答 | `--no-youtube` |

Do not omit these flags. Omitting any source means the agent never asked; bootstrap
will pause with `status=needs_decisions` instead of running init.

### Putting it all together — example commands

The shape of the command depends on what the user picked at each step.
Match each example to the user's actual answers — don't copy-paste blindly.

**默认推荐路径** (DeepSeek + 选项 1 本地 Ollama embedding + 扩展 Cookie)：

```bash
python3 scripts/agent_bootstrap.py \
  --connection-type openai_compatible \
  --preset deepseek \
  --interactive-confirm \
  --embedding-model bge-m3 \
  --embedding-endpoint ollama=http://127.0.0.1:11434/v1 \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

Pass the ordered Embedding endpoint explicitly because the user actively picked it —
this records their choice and survives a future primary-LLM swap. The
bootstrap auto-installs Ollama and pulls `bge-m3` in the same run.
Cookie comes via the extension after the backend is up; don't ask the
user to F12 if you can lead them to the extension first.

**质量优先路径** (Gemini 主 + 选项 2 Gemini embedding + 扩展 Cookie)：

```bash
python3 scripts/agent_bootstrap.py \
  --connection-type gemini_api \
  --interactive-confirm \
  --embedding-model gemini-embedding-001 \
  --embedding-endpoint gemini_api=https://generativelanguage.googleapis.com \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

Chat and Embedding credentials are explicit and independently owned even
when they contain the same value. Bootstrap never borrows a secret from a
different record implicitly.

**完全离线路径** (Ollama 主 + 选项 1 Ollama embedding + 扩展 Cookie)：

```bash
python3 scripts/agent_bootstrap.py \
  --connection-type ollama \
  --interactive-confirm \
  --llm-model llama3 \
  --embedding-model bge-m3 \
  --embedding-endpoint ollama=http://127.0.0.1:11434/v1 \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

**"暂不启用 embedding" 路径** (选项 3)：

```bash
python3 scripts/agent_bootstrap.py \
  --connection-type openai_compatible \
  --preset deepseek \
  --interactive-confirm \
  --embedding-provider "" \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

When no `--embedding-*` flag is passed, bootstrap leaves embedding as a
pending decision. When `--embedding-provider ""` is passed, bootstrap
records the explicit choice to leave embedding disabled. It does not
follow or auto-rewrite based on the primary LLM.

**自建网关路径** (Advanced — only when user explicitly mentions a gateway)：

```bash
python3 scripts/agent_bootstrap.py \
  --connection-type openai_compatible \
  --preset custom \
  --llm-base-url http://localhost:8000/v1 \
  --interactive-confirm \
  --llm-model meta-llama/Llama-3.1-70B-Instruct \
  --embedding-model bge-m3 \
  --embedding-endpoint ollama=http://127.0.0.1:11434/v1 \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

Embedding explicitly pinned to local Ollama because most self-hosted
gateways (vLLM, LMStudio) don't expose `/v1/embeddings`; relying on
the runtime fallback would still work but adds a startup warning.

> ⚠️ **Do NOT pass `--skip-init`** here. The point of running the
> bootstrap with credentials is to reach a usable state. When all
> credentials are present, `--skip-init` is absent, and all init
> decisions are explicit (`--embedding-*` plus source flags:
> `--yes-xhs` / `--no-xhs`, `--yes-douyin` / `--no-douyin`,
> and `--yes-youtube` / `--no-youtube`),
> `agent_bootstrap.py` first probes the exact stable primary Chat connection
> with fallback disabled and every configured ordered Embedding provider
> against the one shared settings object. Only if all checks pass does it run
> `openbiliclaw init`: it pulls the user's Bilibili history, generates
> the soul profile, and runs the first content discovery pass. Without
> explicit decisions, bootstrap returns `status=needs_decisions` and
> waits for you to ask the user.
>
> If bootstrap emits `status=service_check_failed`, init has deliberately
> **not** run. Surface the failing service and error to the user, help
> them fix the indicated stable connection/credential/endpoint/model issue,
> then re-run
> the same bootstrap command without `--skip-init`.

After running, **always**:

1. Curl the `Health URL` from the status block to confirm `200 OK`.
2. Report the final state to the user, including:
   - "✅ 后端已启动，监听 http://127.0.0.1:8420"
   - "✅ 配置已写入"
   - "✅ 初始化已完成 —— 已拉取你的 B 站历史，按你的同意混入小红书 / 抖音 / YouTube 信号，生成画像并跑了首轮内容发现"
   - "👉 下一步：装浏览器扩展（链接）来看推荐"

**`init` takes 2-5 minutes on first run** (real LLM calls + real
Bilibili / optional Xiaohongshu / optional Douyin / optional YouTube fetches). Tell the user upfront so they don't think it's
hung. The bootstrap streams init's stdout so progress is visible, and
also emits `BOOTSTRAP_STATUS` events with `status=progress` and
`message=init_progress` for key milestones (`1/4`, `2/4`, `3/4`,
`4/4`, discovery refill progress). AI agents must relay those progress
events to the user instead of staying silent until `init_complete`.

### Init 期间会问用户:B 站上限与小红书 / 抖音 / YouTube 数据是否加入

`openbiliclaw init` 在拉 B 站数据前会确认 B 站收藏 / 关注初始化上限：
默认收藏最多 500 条、关注 UP 最多 100 人；用户直接回车即接受默认，输入
自定义数字会透传到 `--bilibili-favorite-limit` / `--bilibili-follow-limit`，
输入 `0` 可跳过对应信号。

`openbiliclaw init` 在拉 B 站数据**之前**会弹一个交互式问题:是否把
小红书的收藏 / 点赞混进画像。三种状态:

- **交互式终端 + 没有任何 flag**:打印小红书接入说明 + 准备清单
  (装扩展、登录小红书、浏览器开着),用户回 Y/N。回 Y 后再确认
  "准备好了吗",回车继续
- **`openbiliclaw init --no-xhs`**:跳过提问 + 跳过 enqueue,只用
  B 站数据建画像。给"我有 B 站没小红书"的用户一个干净 opt-out
- **`openbiliclaw init --yes-xhs`**:跳过提问直接启用,适合脚本化
- **`OPENBILICLAW_NO_XHS=1` 环境变量**:同 `--no-xhs`,用于永久跳过
- **直接调用 `openbiliclaw init` 的非交互式终端(管道 / CI)**:
  CLI 本身不会弹提问,因此脚本必须传 `--yes-xhs` 或 `--no-xhs`
  才是可审计的行为。
- **通过 `agent_bootstrap.py` 自动 init**:bootstrap 会强制要求
  `--yes-xhs` / `--no-xhs` 二选一。没传就返回
  `status=needs_decisions`,不会运行 init。

随后 `init` 会单独问抖音发布 / 收藏 / 点赞 / 关注是否加入画像：

- **`openbiliclaw init --no-douyin`**:跳过提问 + 跳过 enqueue,只用
  B 站(+小红书,如启用)数据建画像。
- **`openbiliclaw init --yes-douyin`**:跳过提问直接启用,适合脚本化。
- **`OPENBILICLAW_NO_DOUYIN=1` 环境变量**:同 `--no-douyin`,用于永久跳过。
- **直接调用 `openbiliclaw init` 的非交互式终端(管道 / CI)**:
  CLI 默认跳过抖音；脚本化安装仍必须传 `--yes-douyin` 或
  `--no-douyin` 让行为可审计。
- **通过 `agent_bootstrap.py` 自动 init**:bootstrap 会强制要求
  `--yes-douyin` / `--no-douyin` 二选一。

最后 `init` 会单独问 YouTube 观看历史 / 订阅 / 点赞是否加入画像：

- **`openbiliclaw init --no-youtube`**:跳过提问 + 跳过 enqueue,只用
  B 站(+其他已启用源)数据建画像。
- **`openbiliclaw init --yes-youtube`**:跳过提问直接启用,适合脚本化。
- **`OPENBILICLAW_NO_YOUTUBE=1` 环境变量**:同 `--no-youtube`,用于永久跳过。
- **直接调用 `openbiliclaw init` 的非交互式终端(管道 / CI)**:
  CLI 默认跳过 YouTube；脚本化安装仍必须传 `--yes-youtube` 或
  `--no-youtube` 让行为可审计。
- **通过 `agent_bootstrap.py` 自动 init**:bootstrap 会强制要求
  `--yes-youtube` / `--no-youtube` 二选一。

**关键:接入会前台抢焦点**。`max_scroll_rounds=15`(v0.3.64+ CLI 默认,
v0.3.22 ~ v0.3.63 是 3)触发滚动模式,扩展会在用户浏览器里
`chrome.tabs.create({active: true})` 打开一个前台 tab(URL:
https://www.xiaohongshu.com/explore),自动跳到用户 profile 页向下滚动
加载收藏 / 点赞,完成后自动关闭。
执行时长视用户实际收藏量决定 — 收藏少的用户在连续 5 轮 stagnant
(滚不出新 note)后 executor 自动早退,不会跑满 15 轮;收藏多的用户
最多 15 轮才能拉满每 scope 300 条上限。
**这不是隐藏 tab**——背景 tab 在小红书上只渲染浅层 wrapper,触发不到
瀑布流懒加载,所以必须前台。告诉用户:
  - 装机过程中会被切走一次焦点,正常,完成后焦点还回来
  - 期间不要关那个 tab
  - 如果不想被抢焦点(比如在演示 / 录屏),让他设
    `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS=0` 改用浅层模式
    (只读初始 state,后台 tab,但只能拿 ~10-20 条)

抖音接入也会前台抢焦点。扩展会打开抖音页面，依次访问发布 /
收藏 / 点赞 / 关注 scope，结合 DOM 和 MAIN-world API harvester
分批回传结果。默认只在用户明确同意时启用；不登录或触发风控时
可能 0 条，但 init 会继续完成。

YouTube 接入同样会前台抢焦点。扩展会打开 YouTube 观看历史 /
订阅 / 点赞页面并从 DOM 读取条目。默认只在用户明确同意时启用；
不登录、页面语言/布局变化或任务仍在后台跑时可能 0 条，但 init
会继续完成。

AI agent 视角:**不要省略这些问题**。一句话安装走的是
`agent_bootstrap.py` 的非交互路径,不会有交互式 prompt 替你兜底。
用户明确同意才传 `--yes-xhs` / `--yes-douyin` / `--yes-youtube`;
其余情况传 `--no-xhs` / `--no-douyin` / `--no-youtube`。

## Optional: local Ollama as the embedding fallback

This is a **post-install opt-in**, not part of the install contract. Mention
it to the user only if they ask about offline operation, embedding-quota
errors, or a no-API-key setup. Steps:

1. User installs the official Ollama app: macOS / Windows from
   `https://ollama.com/download` (start the app so `localhost:11434` is live), Linux
   `curl -fsSL https://ollama.com/install.sh | sh && ollama serve &`.
2. User prepares the model with `ollama pull bge-m3`.
3. User runs `cd <INSTALL_DIR> && uv run openbiliclaw setup-embedding`.
   This command is configuration-only and writes native `[models.embedding]`;
   it does not install, start, download, probe, or access the network. Use
   `openbiliclaw models probe <id>` separately when a real connectivity check
   is required, then restart the backend if it is already running.

Do NOT run these steps for the user automatically — Ollama install is a
system-level package the user must consent to.

The native route is visible with `openbiliclaw models list`. It is independent
from Chat and can contain up to ten providers, but all of them must retain the
same shared Embedding model and settings.

## Hard rules

1. **Never edit `config.toml` by hand.** Every credential write goes through `scripts/agent_bootstrap.py`.
2. **Never hard-code `http://127.0.0.1:8420/api/v1/system/readiness`.** Always use the `Health URL` line from the status block — the port may be different if the user already has another instance running.
3. **Run init by default — DO NOT pass `--skip-init`.** Once all credentials are present, the user's expectation is "the app is ready to use." That means: history pulled, soul profile generated, first discovery pass done. `agent_bootstrap.py` does this automatically after the backend is healthy. Only pass `--skip-init` when the user explicitly says "don't pull my history yet" or you're doing a credentials-only patch on an already-initialized install.
4. **Never use WebFetch on this document.** WebFetch summarises markdown and can drop exact flags. Use Bash `curl -o` + Read instead.

## Deeper reference (for humans, not required)

- `docs/agent-deployment.md` — long-form troubleshooting with the full JSON event reference
- `docs/docker-deployment.md` — manual Docker setup
- `docs/openclaw-quickstart.md` — OpenClaw-specific integration after install
- `scripts/install.sh` — the installer itself (the command above)
- `scripts/agent_bootstrap.py` — the Python contract core invoked by install.sh
