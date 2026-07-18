# 开机自启动 SPEC

**Created:** 2026-06-04
**Updated:** 2026-06-05
**Ambiguity score:** 0.08 (gate: <= 0.20)
**Requirements:** 9 locked
**Status:** CONVERGED（5 轮对抗，fresh-eyes 冷读判 **SHIP**）
**Reviews:** gpt-5.5 (6) + Codex (10) + 轮 3 (1B/2M/4m) + 轮 4 (provenance 1B/3min) + 轮 5（provenance CORRECT&IMPLEMENTABLE + fresh-eyes **SHIP**，0 blocker），全部 incorporated

## Goal

为 OpenBiliClaw 后端建立一套跨平台、默认关闭、用户可在设置页一键开关的“开机自启动”机制：

- 用户开启后，操作系统在登录时自动拉起后端服务（等价于 `openbiliclaw start`），无需手动开终端。
- 开关默认 **关闭**；只有用户显式打开并保存后才注册系统自启动项。
- 关闭开关时干净地移除系统自启动项，不残留 plist / 注册表项 / desktop 文件。
- 当 LLM 或 embedding 任一方配置为 **Ollama** 时，自启动起来的后端会“顺带”确保本机 `ollama serve` 已运行，不需要用户单独维护 Ollama 的自启动。
- 覆盖 macOS（LaunchAgent）、Windows（计划任务 / 注册表 Run）、Linux 桌面（XDG autostart）三类桌面安装场景；Docker / 只读包 / 非交互环境明确出界，只上报 `unsupported`。

核心原则：**自启动只负责“登录时把后端拉起来”这一件事；进程保活、崩溃重启、自动更新都不在本特性范围内。** Ollama 通过“后端启动时按需拉起”的依赖引导方式顺带自启动，而不是注册独立的系统自启动项。

## Background

当前进程生命周期（已核对源码）：

- `openbiliclaw start`（`src/openbiliclaw/cli.py` `start` 命令）最终调用 `_run_api_server()` → `uvicorn.run(api_app, host, port)`，这是一个 **前台阻塞** 进程，没有 daemon 化、没有 PID 文件、没有 fork。用户要让它常驻，今天只能手动 `&` / `nohup` / `tmux`，或用 Docker `restart: unless-stopped`。
- `serve-api`（`cli.py` `serve_api` 命令）是容器入口，`docker-compose.yml` 用它 + `restart: unless-stopped` 托管，属于容器编排，不在本特性内。
- 仓库目前 **没有任何** systemd unit / launchd plist / Windows 启动项集成；OS 级常驻完全依赖外部 supervisor。
- 后端启动后由 `ContinuousRefreshController.run_forever()` 拉起 9 个 asyncio 后台 loop（discovery / soul / 各源 producer / proactive push 等）。这些都在 `start` 之后自动运行，本特性无需改动。

Ollama 集成现状（已核对源码，本特性直接复用）：

- LLM provider：`src/openbiliclaw/llm/ollama_provider.py`，默认 `http://localhost:11434/v1`。
- Embedding 独立于 LLM：由 `cfg.llm.embedding.provider` 决定，`v0.3.32+` 拥有独立 `api_key` / `base_url`，**不再** 跟随 `[llm].default_provider`。
- 健康探测：`cli.py:1108 _ollama_is_running()` → `GET /api/version`（`trust_env=False`，避免被本机 HTTP_PROXY 劫持误判）。
- 后台拉起：`cli.py:1251 _ollama_start_serve_background()` 已实现“若未运行则 detached 启动 `ollama serve` 并轮询 15s 直到 `/api/version` 健康”，Windows 用 `DETACHED_PROCESS`，POSIX 用 `start_new_session=True`。目前仅在 `init` 向导（`cli.py` 约 1597 / 1725 行）被调用，`start` 路径 **没有** 调用它——所以今天 ollama 用户跑 `openbiliclaw start` 时若 ollama 没开，会一路 connection-refused（与记忆中 embedding “掉线”/refused 现象一致）。
- 安装兜底：`cli.py:1178 _ollama_install_if_missing()`（brew / winget / curl|sh），本特性不调用，仅在文档中提示。

配置系统现状（已核对源码，本特性直接复用）：

- 根配置为 `@dataclass Config`（`src/openbiliclaw/config.py:424`），各 section 是嵌套 dataclass（如 `SchedulerConfig` `config.py:188`）。
- 读：`_build_config(raw)` 从 dict 构造；写：`save_config()`（`config.py:1455`）→ `_render_config_toml()`（`config.py:1476`，手写 TOML 字符串，非 tomli_w）。`[scheduler]` 块在 `:1579` 附近渲染，可作为新增 `[autostart]` 块的模板。
- API：`GET/PUT /api/config`（`api/app.py` 约 5298 / 5318）热加载配置；`PUT` 通过 `api/runtime_context.py` 重建可热插拔组件。
- 本机特权操作先例：`auth_admin`（`api/app.py:659`）用 `gate.is_trusted_local(request)`（`:678`）限定“仅本机可改密码开关”，并落 `save_config` + 热应用。**自启动注册是同一类“仅本机、带 OS 副作用、可失败、需回报状态”的操作，照这个先例做，而不是塞进普通 `PUT /api/config`。**

## Chosen Approach

采用 **单一自启动项 + 依赖引导 Ollama** 模型。

### 1. 后端开机自启动（每平台一种机制）

注册时把一条“稳定启动命令”冻结进 OS 自启动项；登录时由 OS 拉起。启动命令统一解析为：

```
argv        = [sys.executable, "-m", "openbiliclaw.cli", "start"]
                # Windows 用 pythonw.exe（由 sys.executable 推导）以免弹控制台窗口
working_dir = <配置根目录>   # 解析 config.toml 所在目录
env         = { OPENBILICLAW_PROJECT_ROOT: <配置根>, PATH: <注入 ollama 所在目录> }
```

- 选 `python -m openbiliclaw.cli` 而非 `which openbiliclaw`：登录会话（尤其 macOS LaunchAgent / Windows 非登录 shell）的 PATH 可能不含 console-script shim，绝对 python + 模块自洽。`which("openbiliclaw")` 仅用于 status 展示。
- `OPENBILICLAW_PROJECT_ROOT` 必须写进 env，保证无论 cwd 如何都能找到用户的 `config.toml`。
- PATH 注入：注册时若 `shutil.which("ollama")` 命中，把其所在目录并入 entry 的 PATH，保证登录会话里后端能找到 ollama。
- **env 注入渠道按平台落地**：macOS 写进 plist `EnvironmentVariables`；Linux 写进 `.desktop` 的 `Exec=env KEY=VAL …`；Windows 由生成的 **pythonw 启动脚本**（`.pyw`：`os.environ[...]=…; os.chdir(root); os.execv(...)`）设 env+cwd（Run 值与 `schtasks /tr` 都注不进 env，见下表与 Windows 说明）。
- **只注入 `OPENBILICLAW_PROJECT_ROOT` + PATH(ollama)，绝不把秘密写进 OS 自启动项**。但配置加载会吃所有 `OPENBILICLAW_*` env override（`config.py:514`）——若用户当前靠 env 提供 API key / auth / provider，自启动的登录会话不会带上它们，配置会悄悄漂移甚至破功。故见下文 `env_managed` 守卫：enable 前检出此类 override 即拒绝。

| 平台 | 机制（`mechanism`） | 落点 | 注册 / 反注册 |
|------|--------|------|------|
| macOS | `launchd` LaunchAgent | `~/Library/LaunchAgents/com.openbiliclaw.daemon.plist` | **仅写 / 删 plist 文件**（`RunAtLoad=true`）；登录时由 launchd 自动加载。v1 **不** `launchctl bootstrap/bootout`——避免立刻拉起第二个实例（端口冲突）或停掉当前进程 |
| Windows | pythonw 启动脚本 + HKCU `Run`（主）；`schtasks /create /xml` 导入（备选） | `HKCU\…\Run` 值 `OpenBiliClaw` = `"<pythonw.exe 绝对路径>" "<data_dir>/autostart/openbiliclaw-autostart.pyw"` | 写 / 删 注册表值 + `.pyw` 启动脚本 |
| Linux 桌面 | `xdg_autostart` | `~/.config/autostart/openbiliclaw.desktop` | 写 / 删 `.desktop` 文件；`Exec=env OPENBILICLAW_PROJECT_ROOT=… PATH=… <python> -m openbiliclaw.cli start` |

- macOS plist：`RunAtLoad=true`，`WorkingDirectory`、`EnvironmentVariables`、`StandardOutPath`/`StandardErrorPath` 指向日志目录；**v1 `KeepAlive=false`**（只“登录时拉起”，不保活）。`registered` = plist 文件存在（不依赖 `launchctl print` 的加载态）；注册即「下次登录生效」，当前进程不受影响。「立即应用」是另一个独立 opt-in，v1 不做。
- Windows：`schtasks /create` **没有** 工作目录参数（默认从 `System32` 起），Run 值与 `schtasks /tr` 也注不进 env——故 cwd 与 env 一律交给生成的 **pythonw 启动脚本** `<data_dir>/autostart/openbiliclaw-autostart.pyw`（Python 内 `os.environ` 设 `OPENBILICLAW_PROJECT_ROOT`/`PATH` → `os.chdir(配置根)` → `os.execv` 进 `-m openbiliclaw.cli start`），Run 值用 **绝对 `pythonw.exe` 直接跑该 `.pyw`**——`pythonw` 全程无控制台窗口（不经 `.cmd`，没有 cmd 闪窗；这正是放弃 `.cmd` wrapper 的原因）。`registered` = **Run 值存在 _且_ 指向的 `.pyw` 存在**；Run 值在但脚本缺失（清了 `data_dir` / `data_dir` 搬家致路径过期）= **drift（`registered=false`）**，由 `start` 自愈重写。`.pyw` 在 register 时按当前 `data_dir` 生成；`data_dir` 移动需重新 enable（已知限制）。需要原生带 `<WorkingDirectory>` 的计划任务时走 `schtasks /create /xml` 或 PowerShell `Register-ScheduledTask`（备选）。
- Linux：XDG `.desktop` 只在 **图形登录** 生效；无头服务器场景在文档里指向 `systemd --user` + `loginctl enable-linger`，v1 不实现，status 仍报 `supported=true, mechanism="xdg_autostart"`。`.desktop` 最小字段：`Type=Application` / `Name=OpenBiliClaw` / `Exec=env KEY=VAL … <python> -m openbiliclaw.cli start` / `X-GNOME-Autostart-enabled=true` / `Hidden=false`。
- 三平台都要 **幂等**：重复 enable 不产生重复项；disable 未注册时是 no-op。

### 2. Ollama 顺带自启动（依赖引导，而非独立自启动项）

在 `openbiliclaw start` 启动序列里加一道 **preflight**：

- 判定 `ollama_required(cfg)` = **`registry._ollama_is_chat_capable(cfg)`**（`registry.py:493`，已覆盖 `[llm.ollama].model`、`default_provider==ollama`、`fallback_provider==ollama`、以及 soul/discovery/recommendation/evaluation 四个 per-module override）**或** `cfg.llm.embedding.provider.strip().lower() == "ollama"`。**不要只看 `default_provider`**——那会漏掉 fallback 与 per-module 用 ollama 的已支持配置（chat 实际打到 ollama，preflight 却不拉起）。实现时把 `_ollama_is_chat_capable` 提升为可复用（去下划线或加薄封装），`ollama_required = chat_capable ∨ embedding==ollama`。
- 若需要且 `cfg.autostart.manage_ollama` 为真（默认真）：调用既有 `_ollama_is_running()`；未运行则 `_ollama_start_serve_background()` 拉起并轮询健康。失败只告警、不阻断后端启动（后端自带重试 / 降级）。

这条 preflight 对 **手动** `openbiliclaw start` 同样生效（直接缓解 ollama 没开时的 refused 体验），自启动场景因此自然“顺带”把 ollama 拉起。

为此需把 `_ollama_is_running()` / `_ollama_start_serve_background()` 及 `ollama_required()` 判定从 `cli.py` 抽到共享模块（建议 `src/openbiliclaw/runtime/ollama_supervisor.py`），供 `cli.start` 与 preflight 复用，避免 import `cli`。**迁移不能裸搬**：`_ollama_start_serve_background` 仍被 `init` 向导调用（`cli.py:1597/1725`）、`_ollama_is_running` 于 `cli.py:1666`，且 `tests/test_cli.py:3842/3864` 以 `cli._ollama_*` 引用——故 `cli.py` 必须保留 `from …ollama_supervisor import _ollama_is_running, _ollama_start_serve_background` 的 **向后兼容 re-export** 并更新 init 调用点；`tests/test_cli.py` 列入测试更新范围。（无环依赖：`registry` 不 import `cli`/`runtime`，`runtime/__init__` 不 import `cli`。）

**不** 为 Ollama 注册独立 OS 自启动项（理由见 Approach Trade-offs）。

### 3. 设置开关与本机特权 API

照 `auth_status` + `auth_admin` 这对先例：**读端可远程、写端仅本机**——而非把开关塞进普通配置字段。

- `GET /api/autostart-status`（读端，可远程）：登记进 `_is_public()`，镜像 `auth_status`（`auth.py:379`）——**不对远程返回 403**，而是回报 `supported / enabled / registered / can_manage / platform / mechanism / manage_ollama / ollama_required / reason / detail`。`can_manage = is_trusted_local && !env_managed && !shadowed && supported`：仅它为真时插件才允许切换，远程用户看只读态。`enabled` 是 **配置意图**，`registered` 是 **OS 实态**（每次现探 plist/注册表/desktop 文件是否存在），二者 **可以不一致**：`enabled=true & registered=false` 即 drift（手删 OS 项 / 上次注册失败），status 如实回报、不互相覆盖——drift 正是 `start` 自愈要消解的对象。
- `POST /api/autostart/apply`（写端，仅本机）：`{enabled: true|false}`，同样登记进 `_is_public()`，由 handler 自判 `gate.is_trusted_local(request)`，非本机 `403 local_only`（照 `auth_admin` `app.py:678`；否则开密码后中间件先拦成 401/CSRF，进不到 handler）。
- **写入按 `auth_admin` 的事务序**（`app.py:744-758`）：取 `_CONFIG_SAVE_LOCK` → 快照 config → 写 `config.toml`（开=`enabled=true`/关=`false`）→ **reload 合并后的 effective config 校验改动真生效**（`config.local.toml` 后置合并会盖过，见 Req 6 shadow 守卫）→ 再做 OS 注册/反注册 → 任一步失败则 **回滚 OS 步骤 + 恢复 config 快照**，回报稳定 `reason`，绝不留「配置说开了但 OS 没注册 / 反之」的半成品。
- 配置里镜像 `cfg.autostart.enabled` 只为 **展示 + `start` 自愈**：`[autostart]` 在 `ConfigResponse` 只读透出，**不进 `ConfigUpdateIn`**。但「不进 ConfigUpdateIn」不足以防 clobber：`update_config` 锁外读 `cfg=load_config()`（`app.py:5345`）、锁内 `save_config(cfg)`（`:5708`）整文件重渲染——一旦加 `[autostart]` 渲染块，陈旧 PUT 快照会把刚被 apply 改的值覆盖回去（config 说开但 OS 已反注册 = drift）。**注意 auth 的真实机制不是「永远从磁盘渲染」**：`_api_auth_lines`（`config.py:1391-1399`）默认渲染 **内存值**（`else: mem_line`），仅对 **被 override 层（env / `config.local.toml`）shadow 的字段** 回退 on-disk（避免把层值固化成 literal）；`auth_admin` 能改写是因为它 **锁内 `_load()` 重读**（`app.py:699`）后改内存值走 `mem_line` 落盘——renderer **没有调用方身份**，不能靠「apply 是权威写者」分支，更不能「永远 from disk」（那会死锁 apply：apply 写内存 true 但 renderer 渲染 on-disk false → reload-verify 失败 → 永远 enable 不了）。**故 autostart 用显式开关**：`_render_config_toml(..., on_disk_autostart=<落盘时读>, autostart_authoritative: bool=False)`——默认 `False`（PUT / set-password / 启动 secret-gen / cookie sync）→ `[autostart]` 从 `on_disk_autostart` 渲染，**内存 cfg 再陈旧也不 clobber**；`POST /api/autostart/apply` 与 CLI `enable/disable` 传 `True` → 从内存渲染目标值。再叠加 apply **锁内 `_load()` 重读 + reload-verify**（照 `auth_admin`），既不死锁也彻底关掉陈旧并发写的 race（比 auth 默认姿态更稳）。层级 shadow（`config.local` 钉值）由 reload-verify 命中 → `409 shadowed`。

注册只影响 **下次登录**，不会启停当前进程（当前后端通常已在跑，插件正与它通信）。status 文案需说明这点。

`start` 自愈（reconcile）：自愈 **当且仅当 `cfg.autostart.enabled is True` 且 OS 项缺失** 时注册——`enabled=true` 只可能由先前成功的 apply/CLI-enable 写入，故这天然是「补注册」而非「首次开通」，**无需额外「曾注册过」标记**；补注册前仍过 `active_env_managed_inputs(cfg)`，非空则跳过 + warn（不注册一个登录会话拿不到这些 env 的自启动项，否则正中 `env_managed` 要防的破功）。shadow 在自愈处自然消解（`start` 读的已是 effective config，被 `config.local` 钉 false 时 `enabled` 本就为 false，自愈不触发）。Windows 下「Run 值在但 `.pyw` 缺失」也算 OS 项缺失，自愈重写脚本；失败只 warn。

## Requirements

1. **配置项 `[autostart]`**
   - Current: 无 autostart 配置。
   - Target: 新增顶层 `@dataclass AutostartConfig`（`config.py`，挂到 `Config` 根，参照 `SchedulerConfig`），字段 `enabled: bool = False`、`manage_ollama: bool = True`；`_build_config()` 解析时两字段都过 `_coerce_bool(..., default=…)`（`config.py:749`）——env override 进来是字符串（`config.py:521,536`），不强转会把 `"false"` 当真值；`_render_config_toml()` 渲染（参照 `:1579 [scheduler]` 块）；同步 `config.example.toml`、`docs/modules/config.md`。API schema：`[autostart]` **只进 `ConfigResponse`（只读展示），不进 `ConfigUpdateIn`**（写走 apply 端点，见 Req 6）。
   - Acceptance: 缺省 / 全新配置下 `cfg.autostart.enabled is False`、`manage_ollama is True`；`OPENBILICLAW_AUTOSTART_ENABLED=false`（字符串）被正确强转为 `False`；`save_config` 后重新 `load_config` 能无损读回 `[autostart]`；TOML 往返不破坏其他 section。

2. **跨平台自启动管理器**
   - Current: 无任何 OS 自启动集成。
   - Target: 新增 `src/openbiliclaw/runtime/autostart/` 包：`base.py`（`AutostartManager` 协议 + `LaunchSpec`）、`macos.py` / `windows.py` / `linux.py` 三实现、`command.py`（解析稳定启动命令）、`__init__.py`（`get_manager()` 工厂按 `sys.platform` 选实现，外加 `register()/unregister()/status()/is_supported()`）。
   - Acceptance: 每平台 `register()` 后对应落点存在且内容指向解析出的启动命令；`unregister()` 后落点被删除；重复 `register()` 不产生重复项（幂等）；`unregister()` 未注册时返回成功 no-op。

3. **稳定启动命令解析**
   - Current: 无。
   - Target: `command.py` 输出 `LaunchSpec(argv, working_dir, env)`：`argv=[sys.executable, "-m", "openbiliclaw.cli", "start"]`；`working_dir`=配置根；`env` 含 `OPENBILICLAW_PROJECT_ROOT` 与（若 ollama 可达）注入 ollama 目录后的 `PATH`。Windows `pythonw.exe` 由 `Path(sys.executable).with_name("pythonw.exe")` 推导，不存在则回退 `sys.executable`（接受一次闪窗），**绝不因缺 pythonw 注册失败**；`.pyw` 由 Run 值的 `pythonw` 执行，故脚本内 `os.execv` 目标即 `sys.executable`（此时为 pythonw）+ `-m openbiliclaw.cli start`。
   - Acceptance: 在 venv / 全局安装下解析出的 python 路径可执行且能加载 `openbiliclaw.cli`；entry 不依赖登录会话 PATH 里存在 `openbiliclaw` shim；Windows entry 用 `pythonw` 不弹控制台。

4. **Ollama preflight（顺带自启动）**
   - Current: `start` 不确保 ollama；`_ollama_*` 助手仅在 `init` 向导被调用，且锁在 `cli.py`。
   - Target: 抽 `runtime/ollama_supervisor.py`（迁移 `_ollama_is_running` / `_ollama_start_serve_background`，新增 `ollama_required(cfg) = _ollama_is_chat_capable(cfg) ∨ embedding.provider==ollama ∨ embedding.fallback_provider==ollama`——embedding 侧的 fallback 也能路由到 ollama（`registry.py:150,157`），**复用** 既有检测而非重写）；`openbiliclaw start` 在绑 API 前执行 preflight：`ollama_required(cfg) and cfg.autostart.manage_ollama` 为真时，未运行则后台拉起 `ollama serve`，最多等既有 15s 窗口。
   - **只管 loopback 的 ollama**：`_ollama_is_running` / `_ollama_start_serve_background` 硬编码 `localhost:11434`（`cli.py:1108`），但 chat（`registry.py:465`）与 embedding（`registry.py:225`）都可配自定义 `base_url`。preflight 必须先从配置解析 **生效的 ollama endpoint**，仅当它指向本机 loopback 时才探测 + `ollama serve`；远端 ollama 交给用户自管，preflight 跳过（可选：把远端可达性写进 `detail`，但绝不 `ollama serve`）。
   - Acceptance: 当 ollama 被用作 chat（`default_provider` / `fallback_provider` / 任一 per-module override）**或** embedding（`provider` 或 `fallback_provider`）且 `manage_ollama=true` 且 **endpoint 为 loopback** 时，`start` 在 ollama 未运行时尝试拉起；ollama 已运行 / endpoint 为远端 / `manage_ollama=false` / 完全没用到 ollama 时不拉起；ollama 未安装 / 拉起失败时 `start` 仍继续（仅告警），不崩。

5. **自启动状态 API（读端，可远程只读）**
   - Current: 无。
   - Target: 新增 `GET /api/autostart-status`，登记进 `_is_public()` 与 degraded 白名单；镜像 `auth_status`：返回 `supported / enabled / registered / can_manage / platform / mechanism / manage_ollama / ollama_required / reason / detail`，**远程不返回 403**，靠 `can_manage`（`is_trusted_local && !env_managed && !shadowed && supported`）告诉前端能否切换。
   - Acceptance: 全新时 `enabled=false`+`registered=false`；注册成功后两者皆 true；**drift 如实表达**——`enabled=true` 但 OS 项被外部删时返回 `enabled=true, registered=false`，不得用 `registered` 覆盖 `enabled`；远程调用 **不 403**、返回 `can_manage=false`；Docker / 不支持环境 `supported=false` 且带稳定 `reason`；响应不含 cookie / api_key 等秘密。

6. **自启动开关 API（写端，仅本机 + 事务 + 跨层守卫）**
   - Current: 无；`auth_admin` 已示范本机鉴权、shadow、env 三道守卫（`app.py:678/744-758`）。
   - Target: 新增 `POST /api/autostart/apply`（`{enabled: bool}`），登记进 `_is_public()`（`auth.py:285`）与 degraded 白名单；handler 自判 `gate.is_trusted_local`，非本机 `403 local_only`（不让中间件拦成 401/CSRF）。写入照 `auth_admin` 事务序：`_CONFIG_SAVE_LOCK` → 快照 → 写 config → **reload effective 校验** → OS 注册/反注册 → 任一步失败 **回滚 OS + 恢复快照**。三道前置/校验守卫：
     1. **env_managed**：enable 前若检出任何会被自启动登录会话丢掉的「外部 env 配置」——不止 `OPENBILICLAW_*`（除 `PROJECT_ROOT`），还含 provider 原生 cred（`GOOGLE_API_KEY` / `GEMINI_API_KEY`，`registry.py:440`）与配置的 `sources.douyin.cookie_env` 变量名（`douyin_auth.py:75`）——拒绝并 `409 env_managed`。用 helper `active_env_managed_inputs(cfg)` 枚举已知集合（见 Constraints）。
     2. **shadow**：`config.local.toml` 后置合并会盖过 `config.toml`（`config.py:1262-1267`）。写完 reload effective，若 `[autostart].enabled` 没真生效（被 local 钉住）→ 回滚 + `409 shadowed`（照 `auth_admin` `app.py:750-758`）；enable 被 local 钉 `false`、disable 被 local 钉 `true` 都按 `shadowed` 对称处理。
     3. **anti-clobber（provenance + 显式开关）**：`[autostart]` 不进 `ConfigUpdateIn`（只读透出 `ConfigResponse`）。因 `save_config`→`_render_config_toml` 整文件重渲染、PUT 锁外读 cfg（`app.py:5345`）锁内存盘（`:5708`），仅靠「不进 ConfigUpdateIn」挡不住陈旧快照 clobber：故 `_render_config_toml` 加 `on_disk_autostart`（落盘时读）+ `autostart_authoritative: bool=False`——非 apply 写者用默认 `False` 从 on-disk 渲染（不 clobber），apply/CLI enable/disable 传 `True` 从内存写目标值。**别照搬「永远 from disk」**（死锁 apply，见 §3）；apply 另锁内 `_load()` 重读 + reload-verify（照 `auth_admin` `app.py:699`）。实现细则：`save_config` **每次都读** `on_disk_autostart`（像 `on_disk_auth` `:1464`，不只在 `False` 时），renderer 仅在 `True` 时忽略它；`manage_ollama` **始终从内存渲染**（apply 从不改它，仅 `enabled` 受开关门控）；`_is_public`/degraded 都是硬编码分支，「登记」= 各加一个 path 条件，非注册表。
   - Acceptance: 开密码时非本机进 handler 返回 `403 local_only`、本机免密；开成功后 `GET /api/config` 与 `/api/autostart-status` 显示 `enabled=true`+`registered=true`；OS 注册失败 → 配置回滚到 `false`、`409 registration_failed`、不留半成品；`config.local.toml` 钉 `false` 时 enable 返回 `409 shadowed` 不注册；存在 `GOOGLE_API_KEY` / `OPENBILICLAW_*`（除 PROJECT_ROOT）/ 配置的 douyin cookie_env 时 enable 返回 `409 env_managed`；`PUT /api/config {autostart:…}` 不改 OS 注册态。

7. **设置页开关 UI**
   - Current: 插件设置页无自启动入口。
   - Target: 设置页新增“开机自启动”开关，缺省 **关**；打开设置读 `GET /api/autostart-status` 渲染，切换调 `POST /api/autostart/apply` 即时生效（照 `popup-auth-control.js` Pattern B）。**`can_manage=false`（远程 / env_managed / shadowed / 不支持）时开关禁用并按 `reason` 解释**（远程：“仅本机可改”；env_managed：“检测到环境变量配置，自启动登录会话带不上，请把这些值写进 config.toml”；shadowed：“被 config.local.toml 覆盖”）。文案讲清：仅影响下次登录拉起后端、不启停当前进程；ollama 用户会顺带拉起本机 Ollama；并附 **残留风险提示**——纯靠 shell 环境变量提供的配置（API key 等）自启动时可能缺失，建议落进 `config.toml`。
   - Acceptance: 全新安装开关显示关；本机 `can_manage=true` 时切换即写配置 + OS 注册/反注册；远程 / env_managed / shadowed 时开关禁用且 `reason` 文案正确；UI 展示残留风险提示。

8. **CLI 子命令**
   - Current: 无。
   - Target: 新增 `openbiliclaw autostart enable | disable | status`，复用同一注册 core（manager + `active_env_managed_inputs` + shadow 检查）；`config-show` 增显自启动状态行。**守卫自带、不蹭 API 锁**：CLI 是独立进程，拿不到 `_CONFIG_SAVE_LOCK`（`app.py:117` 的 `asyncio.Lock`），故照 `set-password`（`cli.py:3291-3318` 自查 env-managed + `config.local` shadow）自己做 env_managed / shadow，并享同一 `on_disk_autostart` provenance；CLI-vs-server 并发写 config.toml 是 **last-writer-wins**（与 `set-password` 现状一致）。
   - Acceptance: `autostart enable` 后 `status` 报已注册且 `enabled=true`；`disable` 后报未注册；检出 env-managed 输入 / `config.local` shadow 时 `enable` 拒绝并给清晰文案；不支持平台 `enable` 非零退出。

9. **测试覆盖**
   - Current: 无。
   - Target: 新增 Python 测试：三平台 manager（mock 文件系统 / `winreg` / `subprocess` / `launchctl`）、启动命令解析、幂等、drift/reconcile（含 Windows `.pyw` 缺失算 drift）、`ollama_required` 判定（chat / embedding / fallback）、preflight **loopback-only**（mock `_ollama_is_running` / `_ollama_start_serve_background`，远端 `base_url` 不拉起）、`[autostart]` 往返 + `_coerce_bool`、**`on_disk_autostart` provenance（PUT/set-password 不 clobber `[autostart]`）**、API 读端可远程 / 写端 403、env_managed（含 `GOOGLE_API_KEY` / douyin `cookie_env`）、shadow、self-heal 一致性、CLI 子命令 + `cli._ollama_*` re-export。集中放进专属 `tests/test_autostart.py` + `tests/test_ollama_supervisor.py`。
   - Acceptance: 专属文件 **整跑**（避免 `-k` 子串漏掉 preflight / loopback / supervisor 名）：
     - `pytest tests/test_autostart.py tests/test_ollama_supervisor.py -q`
     - 共享文件文件级 + 关键字：`pytest tests/test_api_app.py tests/test_config.py tests/test_cli.py -k "autostart or ollama" -q`

## API Shape

`GET /api/autostart-status`

```json
{
  "supported": true,
  "enabled": false,
  "registered": false,
  "can_manage": true,
  "platform": "darwin",
  "mechanism": "launchd",
  "manage_ollama": true,
  "ollama_required": false,
  "reason": "none",
  "detail": "尚未开启开机自启动"
}
```

`POST /api/autostart/apply`（写端，仅本机：登记进 `_is_public()`，handler 自判 `is_trusted_local`）

```json
{ "enabled": true }
```

响应：`200 OK`，形状同 `GET /api/autostart-status`（反映应用后状态）。失败时 **config 与 OS 一并回滚到操作前**、`reason` 给稳定值：

| 场景 | HTTP | `enabled` | `registered` | `reason` |
|------|------|-----------|--------------|----------|
| 开启并注册成功 | `200` | `true` | `true` | `none` |
| 关闭并反注册成功 | `200` | `false` | `false` | `none` |
| 非本机来源（handler 判定，非中间件 401） | `403` | n/a | n/a | `local_only` |
| 当前环境不支持（Docker / 只读 / 未知平台） | `409` | `false` | `false` | `unsupported_docker_runtime` / `unsupported_platform` |
| 外部 env 配置接管（含 `GOOGLE_API_KEY` 等非 `OPENBILICLAW_*`） | `409` | 现状 | 现状 | `env_managed` |
| `config.local.toml` 钉住、写入不生效 | `409` | 现状 | 现状 | `shadowed` |
| OS 注册失败（权限 / 命令出错，config 已回滚） | `409` | `false` | `false` | `registration_failed` |

（degraded 模式下两端点仍可用——都在 degraded 白名单内，见 Constraints。）

`config.autostart` 经既有配置 API **只读** 透出（`ConfigResponse`，不在 `ConfigUpdateIn`；写仍走 `POST /api/autostart/apply`）：

```json
{ "autostart": { "enabled": false, "manage_ollama": true } }
```

## Status Values & Reasons

`supported`（bool）：当前安装/运行模式是否可注册自启动。Docker 运行时、只读包、未知平台为 `false`。

`can_manage`（bool）：仅 `is_trusted_local && !env_managed && !shadowed && supported` 时为真——前端据此决定开关可否切换；远程 / 受管态返回 `false`（只读），**不** 返回 403。

稳定 `reason` 取值（契约的一部分）：

- `none`
- `local_only`（写端：非本机调用 apply）
- `unsupported_platform`
- `unsupported_docker_runtime`
- `env_managed`（enable 被拒：检出会被自启动登录会话丢掉的外部 env 配置——`OPENBILICLAW_*`（除 `PROJECT_ROOT`）、provider 原生 cred `GOOGLE_API_KEY`/`GEMINI_API_KEY`、或配置的 `sources.douyin.cookie_env`——需先落进 `config.toml`）
- `shadowed`（enable 被拒：`config.local.toml` 钉住 `[autostart].enabled`，写 `config.toml` 不生效）
- `registration_failed`
- `unregister_failed`
- `ollama_unavailable`（preflight 想拉起但 ollama 未安装 / 拉起超时；仅出现在运行日志 / status detail，不阻断 `start`）

`mechanism` 取值：`launchd` / `schtasks` / `windows_run` / `xdg_autostart` / `none`。

## Settings UX

设置页“开机自启动”控件：

- 开关：`开机自启动`，缺省 **关**。打开设置页时读 `GET /api/autostart-status` 同步勾选态与提示。
- 切换即时生效：勾上调 `POST /api/autostart/apply {enabled:true}`，取消调 `{enabled:false}`；行内提示注册成功 / 失败原因（照 `popup-auth-control.js` 的 `hint` 渲染）。
- 文案必须讲清后果：开启后系统登录时会自动启动 OpenBiliClaw 后端；**不** 影响当前已运行的进程；若 LLM / embedding 用的是 Ollama，会在后端启动时顺带拉起本机 `ollama serve`。
- **`can_manage=false`** 时开关禁用并按 `reason` 解释：`unsupported_*`→“Docker 部署由容器编排托管，无需开机自启动”；`local_only`（远程）→“仅本机可改”；`env_managed`→“检测到环境变量配置，自启动登录会话带不上，请写进 config.toml”；`shadowed`→“被 config.local.toml 覆盖”。
- **残留风险提示**：纯靠 shell 环境变量提供的配置（API key 等）自启动登录会话可能缺失，建议落进 `config.toml`——即使 `env_managed` 守卫没逐一枚举到。
- 不做保活 / 崩溃重启 UI；不做开机延迟、登录项管理面板。v1 仅一个开关。

## Config Schema

`config.example.toml` 在 `[scheduler]` 块之后新增：

```toml
[autostart]
# 开机自启动：登录系统时自动拉起后端（等价 openbiliclaw start）。默认关闭。
# 开关也可在浏览器插件设置页一键切换；改这里需重新注册（openbiliclaw autostart enable）。
enabled = false
# 当 LLM 或 embedding 任一方用 Ollama 时，后端启动会顺带确保本机 ollama serve 已运行。
# 若你用 systemd / launchd 自行托管 Ollama，可设为 false 以免重复拉起。
manage_ollama = true
```

`AutostartConfig` dataclass（`config.py`，挂到 `Config` 根）：

```python
@dataclass
class AutostartConfig:
    """开机自启动配置。"""

    enabled: bool = False
    manage_ollama: bool = True
```

## Boundaries

**In scope:**

- macOS / Windows / Linux 桌面三平台“登录时拉起后端”的注册与反注册。
- 稳定启动命令解析（python -m 模块、项目根、env 注入）。
- `start` 的 Ollama preflight（按需拉起本机 `ollama serve`）。
- 自启动状态 API + 仅本机开关 API。
- 设置页单开关 + CLI `autostart` 子命令。
- `_ollama_*` 助手抽取到共享 `runtime/ollama_supervisor.py`。
- README / 模块文档 / changelog / 架构图同步。
- Python 测试。

**Out of scope:**

- 进程保活 / 崩溃自动重启（macOS `KeepAlive`、systemd `Restart=`）——v1 只“登录时拉起一次”，保活留作后续，单独配置门控。
- Linux 无头服务器 `systemd --user` + `loginctl enable-linger` 的自动配置——仅文档指引，不在 v1 实现。
- 为 Ollama 注册 **独立** 的 OS 自启动项（改走依赖引导）。
- Docker / Kubernetes 自启动——容器生命周期由编排器 `restart:` 策略负责。
- 卸载器 / 全局“开机自启动管理面板” / 多用户系统级（root）自启动。
- 自动更新、调度器开关、登录密码——各自有独立特性，不在本特性内联动。
- 修改 Ollama 官方安装器已注册的自启动项。

## Constraints

- `autostart.enabled` 默认 `false`；配置缺省必须按关闭处理（后端、API、设置页一致）。
- **读写分权**（照 `auth_status`/`auth_admin`）：`GET /api/autostart-status` 与 `POST /api/autostart/apply` 都登记进 `_is_public()`（`auth.py:285`）绕过密码中间件；但 **status 读端可远程**（镜像 `auth_status`，远程返回 `can_manage=false` 而非 403），**apply 写端仅本机**（handler 自判 `is_trusted_local`，非本机 `403 local_only`，不让中间件拦成 401/CSRF）；局域网客户端不得改写登录项。
- 注册写入的启动命令必须是 **解析后的绝对路径**（`sys.executable` / `pythonw`），不得依赖登录会话 PATH 查找 `openbiliclaw`，避免 PATH 注入歧义。
- **env override 守卫**（`active_env_managed_inputs(cfg)` helper）：enable 前枚举会被自启动登录会话丢掉的外部 env 输入并拒绝 `env_managed`——含 (a) 除 `OPENBILICLAW_PROJECT_ROOT` 外的所有 `OPENBILICLAW_*`（含 `API_AUTH_*`），(b) provider 原生 cred `GOOGLE_API_KEY` / `GEMINI_API_KEY`（`registry.py:440`），(c) 配置的 `sources.douyin.cookie_env` 指向的变量名（`douyin_auth.py:75`）。秘密绝不写进 plist/注册表；仅 `OPENBILICLAW_PROJECT_ROOT` + PATH(ollama) 注入 OS 项。helper 是 **已知集合枚举**（无法穷举所有第三方 SDK env），故 UI 另给残留风险提示。
- 状态 / 配置响应不得泄露 cookie、api_key、含凭据的远端等秘密；OS 命令 stderr 只在本地 debug/warn 级日志，API 用稳定 `reason` 概括。
- 不以 root / 管理员身份注册系统级自启动；只写当前用户的 LaunchAgents / HKCU / `~/.config/autostart`。
- preflight 拉起 ollama 失败 **不得** 阻断 `openbiliclaw start`；后端保持现有重试 / 降级行为。
- 启用 / 关闭只影响下次登录的注册态，**不** 启停当前进程。
- 全平台幂等：重复 enable 不重复注册；disable 未注册为成功 no-op。
- **事务 + 回滚**（照 `auth_admin` `app.py:744-758`）：apply 在 `_CONFIG_SAVE_LOCK` 下「快照 config → 写 config → reload effective 校验 → OS 注册/反注册」，任一步失败回滚 OS + 恢复快照；不留「config 翻了但 OS 没动 / 反之」的半成品。
- **`config.local.toml` shadow 守卫**：写 config 后 reload 合并态校验 `[autostart].enabled` 真生效（`config.local.toml` 后置合并会盖过，`config.py:1262`），没生效则回滚 + `409 shadowed`。
- **anti-clobber**：`[autostart]` 不进 `ConfigUpdateIn`；且 `_render_config_toml` 用 `on_disk_autostart` + `autostart_authoritative` 开关——非 apply 写者从 on-disk 渲染（防陈旧快照 clobber），apply/CLI 传 `True` 从内存写目标值（renderer 无调用方身份，不能靠「apply 权威」分支，**更不能「永远 from disk」否则死锁 apply**）。apply 另锁内 `_load()` 重读 + reload-verify。
- **degraded 白名单**：`GET /api/autostart-status` 与 `POST /api/autostart/apply` 必须加入 degraded 模式白名单（`app.py:837`），否则核心组件未起来时被 503，用户无法查看 / 关闭自启动。
- Docker / 容器运行时下 `supported=false`、`reason="unsupported_docker_runtime"`，注册请求被拒；容器探测 **复用既有** `docker_runtime.is_running_in_container()`（`OPENBILICLAW_IN_CONTAINER` 环境变量、`/.dockerenv`、`/run/.containerenv`），不自造探测逻辑。

## Acceptance Criteria

- [ ] 全新 / 缺省配置下 `cfg.autostart.enabled is False`、`manage_ollama is True`，设置页开关显示关。
- [ ] `save_config` 后 `load_config` 能无损读回 `[autostart]`，且不破坏其它 section。
- [ ] macOS `register()` 写出 `~/Library/LaunchAgents/com.openbiliclaw.daemon.plist`（`RunAtLoad=true`、`ProgramArguments` 指向解析出的 python -m 命令、`EnvironmentVariables` 含 `OPENBILICLAW_PROJECT_ROOT`）；`unregister()` 删除该文件；注册 / 反注册都 **不** `launchctl bootstrap/bootout`，当前进程不受影响。
- [ ] Windows `register()` 生成 `.pyw` 启动脚本（Python 内设 cwd + `OPENBILICLAW_PROJECT_ROOT`/PATH 后 `os.execv` 进 start）并写 HKCU `Run` 值 = 绝对 `pythonw.exe` + 该 `.pyw`，**全程无控制台窗口**；`unregister()` 删注册表值 + `.pyw`；Run 值在但 `.pyw` 缺失时 status 报 `registered=false`（drift），`start` 自愈重写。
- [ ] Linux `register()` 写出 `~/.config/autostart/openbiliclaw.desktop`，`Exec` 经 `env` 注入 `OPENBILICLAW_PROJECT_ROOT`；`unregister()` 删除文件。
- [ ] 三平台重复 `register()` 不产生重复项；`unregister()` 未注册时成功 no-op。
- [ ] 解析出的启动命令在 venv 与全局安装下都能加载 `openbiliclaw.cli` 并启动后端。
- [ ] ollama 被用作 chat（`default_provider` / `fallback_provider` / per-module override 任一）或 embedding 且 `manage_ollama=true` 时，`openbiliclaw start` 在 ollama 未运行时尝试后台拉起 `ollama serve`。
- [ ] ollama 已在运行时 preflight 不重复拉起；ollama 未安装 / 拉起失败时 `start` 仍继续（仅告警）。
- [ ] `manage_ollama=false` 或 LLM/embedding 都非 ollama 时，preflight 完全跳过。
- [ ] `GET /api/autostart-status`：全新时 `enabled=false`+`registered=false`；注册后两者皆 true；`enabled=true` 但 OS 项被删时如实返回 `enabled=true`+`registered=false`（drift 不被 `registered` 覆盖）。
- [ ] 开了登录密码时：`POST /api/autostart/apply` 非本机进 handler 返回 `403 local_only`（不是 401/CSRF）、本机免密；`GET /api/autostart-status` 远程 **不 403**、返回 `can_manage=false`（只读）。
- [ ] enable 前检出 `OPENBILICLAW_*`（除 `PROJECT_ROOT`）、`GOOGLE_API_KEY`/`GEMINI_API_KEY`、或配置的 douyin `cookie_env` 任一 → `409 env_managed`，不写配置 / 不注册。
- [ ] `config.local.toml` 钉住 `[autostart].enabled=false` 时 enable → `409 shadowed`，OS 不注册、config 回滚。
- [ ] `POST /api/autostart/apply {enabled:true}` 注册成功后 `GET /api/config` 与 `/api/autostart-status` 均显示 `enabled=true` 且 `registered=true`。
- [ ] OS 注册失败时 config 被回滚到 `false`、返回 `409 registration_failed`；config 写成功但 reload/注册失败时不留半成品（OS 与 config 一并回滚）。
- [ ] `PUT /api/config {autostart:{enabled:true}}` 不改变 OS 注册态（autostart 不在 `ConfigUpdateIn`）。
- [ ] `PUT /api/config` / `set-password`（`autostart_authoritative=False`）任何写入都 **保留磁盘 `[autostart]`**，即便其内存 `cfg.autostart` 陈旧；并发 PUT 不回退 apply 刚写的值。
- [ ] `POST /apply {enabled:true}`（`autostart_authoritative=True`）把磁盘 `[autostart].enabled` 写成 `true` 并存活（apply 的值落盘、不被随后/并发的非权威写覆盖）。
- [ ] `start` 自愈检出 env-managed 输入时 **跳过补注册**（不注册会破功的自启动项），且只补注册、不首次注册。
- [ ] `_ollama_is_running` / `_ollama_start_serve_background` 迁出后 `cli.py` re-export 仍在，`tests/test_cli.py` 与 `init` 调用点不破。
- [ ] degraded 模式下 `GET /api/autostart-status` 与 `POST /api/autostart/apply` 不返回 503。
- [ ] 远端 ollama（非 loopback 自定义 `base_url`）时 preflight 不 `ollama serve`；`OPENBILICLAW_AUTOSTART_ENABLED=false` 字符串被 `_coerce_bool` 解析为 `False`。
- [ ] `POST /api/autostart/apply {enabled:false}` 后 OS 落点被移除，`enabled=false`。
- [ ] Docker 运行时 `supported=false`、`reason="unsupported_docker_runtime"`，注册请求被拒。
- [ ] `enabled=true` 但 OS 项缺失时，`openbiliclaw start` 会自愈补注册，失败仅告警。
- [ ] 设置页开关切换即时调用 apply 端点并行内回报成功 / 失败；`supported=false` 时开关禁用且有说明。
- [ ] `openbiliclaw autostart enable/disable/status` 行为与 API 一致；不支持平台 `enable` 非零退出并报错。
- [ ] README / README_EN 安装文档说明开机自启动开关与 Ollama 顺带自启动；`docs/modules` 与 `docs/changelog.md` 同步。
- [ ] 定向 Python 测试通过。

## Ambiguity Report

| Dimension | Score | Min | Status | Notes |
|-----------|-------|-----|--------|-------|
| Goal Clarity | 0.92 | 0.75 | PASS | 范围锁定“登录时拉起后端 + 顺带 ollama”，默认关 |
| Boundary Clarity | 0.90 | 0.70 | PASS | 保活、Docker、systemd-linger、独立 ollama 自启动均明确出界 |
| Constraint Clarity | 0.91 | 0.65 | PASS | 读写分权、事务+回滚、shadow、env 枚举、degraded、绝对路径、幂等均锁定 |
| Acceptance Criteria | 0.93 | 0.70 | PASS | 配置、三平台 manager、preflight、API（含 drift/shadow/env/degraded/anti-clobber 双向）、UI、CLI、docs、tests 均有 pass/fail |
| **Ambiguity** | **0.08** | **<=0.20** | PASS | 5 轮对抗收敛；provenance 验证器判 CORRECT&IMPLEMENTABLE、fresh-eyes 判 SHIP |

文档内已决议的灰区（无需再问，可被 review 推翻）：

- Ollama 顺带自启动用 **依赖引导**（`start` preflight）而非独立 OS 项。
- v1 **只登录拉起、不保活**；Docker 出界。
- 开关走 **仅本机专用端点 + 立即生效**（auth 先例），配置里镜像 `[autostart].enabled` 供展示与自愈。
- Linux 主走 **XDG autostart**（桌面登录），systemd-linger 仅文档。
- macOS / Windows 注册为 **文件 / 注册表级**，登录时才生效，v1 不在当前会话 load/unload（review 修正）。
- `ollama_required` 复用 `_ollama_is_chat_capable`（含 fallback + per-module），不止看 `default_provider`（review 修正）。
- **读端可远程**（`auth_status`/`can_manage`）、**写端仅本机**（`auth_admin`/`is_trusted_local`）；两端都进 `_is_public` + degraded 白名单（Codex 修正）。
- enable 前 `env_managed` 守卫枚举 `OPENBILICLAW_*` + `GOOGLE/GEMINI_API_KEY` + douyin `cookie_env`；`config.local.toml` shadow + reload 校验；apply 走 `auth_admin` 式事务 + 回滚；`[autostart]` 不进 `ConfigUpdateIn`（Codex 修正）。
- `[autostart]` 落盘走 `on_disk_autostart` provenance（PUT/set-password 不 clobber），只有 apply 是权威写者；`start` 自愈复用 env_managed 守卫、只补不首注册（Round 3 修正）。
- Windows 改 pythonw `.pyw` 启动脚本（无 cmd 闪窗），`registered` = Run 值 + `.pyw` 俱在；`_ollama_*` 迁出 cli 保留 re-export（Round 3 修正）。
- anti-clobber 不是「永远 from disk」（会死锁 apply）——而是 `_render_config_toml(autostart_authoritative=False)` 默认 from disk、apply/CLI 传 `True` from memory；apply 锁内 `_load()` 重读（Round 4 修正）。

## Approach Trade-offs

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| 单一自启动项（后端）+ 依赖引导 Ollama | 一个登录项；复用既有 `_ollama_start_serve_background`；避免与 ollama 官方安装器自启动撞车；ollama 仅在后端需要时才起 | ollama 在后端未运行时不会被单独拉起（本特性也不需要） | **Chosen** |
| 为 Ollama 注册 **独立** OS 自启动项 | ollama 独立于后端常驻 | 平台差异翻倍；与 ollama 官方安装器自启动重复 / 双启动；起序竞争（后端可能早于 ollama 就绪） | Rejected |
| 自启动同时做保活 / 崩溃重启（macOS KeepAlive、systemd Restart=） | 进程更稳 | Linux XDG 无法保活，三平台语义不齐；扩大范围 | Future（单独门控） |
| Linux 用 `systemd --user` + linger | 无头服务器开机即起，可保活 | 需 `loginctl enable-linger`，对桌面用户过重；与“登录时拉起”语义不符 | 文档指引，v1 不实现 |
| 把开关塞进普通 `PUT /api/config` | 改动小 | OS 副作用 / 可失败 / 需本机鉴权，普通配置字段表达不了，且会被局域网客户端触达 | Rejected（改走 auth 先例） |
| Windows 用 HKCU Run + `.pyw` 启动脚本（主）vs 纯 schtasks | `.pyw` 统一解决 cwd+env 且 `pythonw` 跑无控制台窗口；Run 用纯 `winreg`，无外部依赖 | 多一个 `.pyw` 文件 | **Chosen**（schtasks `/create` 无工作目录参数、注不进 env，纯 schtasks 不达标；`/xml` 导入作备选） |

## Research Notes

- launchd / LaunchAgent: `man launchd.plist`；`~/Library/LaunchAgents/*.plist` 在用户登录时由 launchd 自动加载（`RunAtLoad` 即「登录拉起」），v1 因此只管文件、不主动 `launchctl bootstrap/bootout`（那会立刻起/停 job，与「不动当前进程」冲突）。
- Windows：`schtasks /create` **无工作目录参数**（默认 `System32`），`WorkingDirectory` 仅存在于 Task Scheduler XML/COM action——故主路径用「wrapper 脚本（设 cwd+env）+ HKCU Run」，wrapper 用 `pythonw` / `wscript //B` 隐藏窗口；需要原生计划任务时用 `schtasks /create /xml` 或 PowerShell `Register-ScheduledTask`。已据 Microsoft Learn 核实。
- XDG Autostart 规范：`~/.config/autostart/*.desktop`，`X-GNOME-Autostart-enabled`、`Hidden`；仅图形登录触发。
- systemd user services + lingering：`systemctl --user enable --now`，`loginctl enable-linger <user>`（无头开机即起的前置）。
- 复用源码：`cli.py:1108 _ollama_is_running`、`cli.py:1251 _ollama_start_serve_background`、`registry.py:493 _ollama_is_chat_capable` / `:150,157 embedding fallback` / `:225,465 ollama base_url`、`config.py:749 _coerce_bool` / `:1262 config.local 合并` / `:1455 save_config` / `:1476 _render_config_toml`、`api/app.py:659 auth_admin`（事务 + shadow 守卫 `:744-758`）+ `:678 is_trusted_local`、`api/auth.py:379 auth_status`（`can_manage` 先例）/ `:285 _is_public`、`api/app.py:837 degraded 白名单`、`api/models.py:827/843 ConfigUpdateIn/ConfigResponse`、`registry.py:440 GOOGLE/GEMINI_API_KEY` + `sources/douyin_auth.py:75 cookie_env`（`env_managed` 枚举）、`docker_runtime.py:176 is_running_in_container`、`runtime/updater.py:580 os.execv(sys.executable, …)`（既有“用 `sys.executable` 重启进程”的先例）。

## Docs to Sync（实现 PR 必须带）

- `docs/specs/autostart.md`（本文件）。
- `docs/modules/config.md`：新增 `[autostart]` section（`enabled` / `manage_ollama`）。
- `docs/modules/cli.md`：新增 `autostart enable|disable|status` 命令，`config-show` 增项。
- `docs/modules/runtime.md`（或新建 `docs/modules/autostart.md`）：自启动管理器 + `start` preflight + `ollama_supervisor`。
- `docs/modules/extension.md`：设置页开机自启动开关。
- `docs/changelog.md`：当前版本块加一条 bullet。
- `config.example.toml`：`[autostart]` 块。
- 架构同步（本特性新增 runtime/autostart 子模块 + 新依赖边 后端→ollama_supervisor）：`docs/architecture.md`、`docs/spec.md` §3 ASCII 图、`README.md` / `README_EN.md` 顶部架构图。
- 发布时：`README.md` / `README_EN.md` 📌 highlights callout（≤4 bullet，中英同步）。

## Interview Log

- 2026-06-04: 需求“增加开机自启动，配置页开关默认关，LLM/embedding 用 ollama 时顺带自启动”。决议：单一后端自启动项 + 依赖引导 Ollama（复用 `_ollama_start_serve_background`），v1 只登录拉起不保活，开关走仅本机专用端点（auth 先例），Linux 主走 XDG autostart。Docker / systemd-linger / 独立 ollama 自启动 / 保活均出界。
- 2026-06-04（review 修订 1）：外部 review（gpt-5.5）提 6 点，全部采纳并对源码核实——(1) `ollama_required` 改为复用 `_ollama_is_chat_capable`（补 `fallback_provider` + 四个 per-module override，`registry.py:493`）；(2) macOS 改 **文件级** 注册、不 `bootstrap/bootout`，消解与“不动当前进程”的冲突（`RunAtLoad` 登录时自加载）；(3) Windows 改 **wrapper + HKCU Run**（`schtasks /create` 无工作目录参数、注不进 env），`/xml` 作备选；(4) 锁定 `env_managed`：检出 `OPENBILICLAW_*`（除 `PROJECT_ROOT`）override 即拒绝 enable，秘密不入 OS 项（`config.py:514`）；(5) 厘清 drift 语义（`enabled` 意图 vs `registered` 实态可不一致，由 `start` 自愈）；(6) 自启动 API 必须进 `_is_public()`（`auth.py:285`）bypass，由 handler 自判 local-only，避免被密码中间件拦成 401。
- 2026-06-05（review 修订 2）：Codex（独立 review）提 10 点，全部采纳并核实源码——BLOCKER：(1) `env_managed` 拓宽到非 `OPENBILICLAW_*` 的 provider cred（`GOOGLE/GEMINI_API_KEY` `registry.py:440`）与 douyin `cookie_env`（`douyin_auth.py:75`），引入 `active_env_managed_inputs` helper + 残留风险提示；(2) 新增 `config.local.toml` shadow 守卫（reload effective 校验 + `409 shadowed`，照 `auth_admin` `app.py:750`）。MAJOR：(3) preflight 只管 loopback ollama（chat/embedding 可配自定义 `base_url`，`registry.py:225,465`）；(4) `ollama_required` 补 `embedding.fallback_provider`（`registry.py:150,157`）；(5) `GET /api/autostart-status` 改 **远程可读 + `can_manage`**（镜像 `auth_status` `auth.py:379`），不再对远程 403；(6) 两端点加入 degraded 白名单（`app.py:837`）；(7) apply 明确 `auth_admin` 式事务 + 跨失败回滚（`app.py:744-758`）；(8) `[autostart]` 只读、不进 `ConfigUpdateIn`，防 `PUT /api/config` 绕过 OS 注册（`models.py:827/843`）。MINOR：(9) `_build_config` 对 bool 字段用 `_coerce_bool`（`config.py:749`）。NIT：(10) `os.execv` 引用 `updater.py:579`→`:580`。
- 2026-06-05（review 修订 3）：第三轮对抗（引用审计 agent + 实现破坏 agent）。引用审计：30+ 处既有代码引用 **逐行核实无误**。实现破坏提 1 blocker + 2 major + 4 minor，全采纳——B1：`PUT /api/config` 锁外读 cfg、锁内整文件重渲染会 clobber 刚被 apply 改的 `[autostart]`（`app.py:5345/5708`）；M1：`start` 自愈绕过 `env_managed`——改为只补注册 + 复用 `active_env_managed_inputs`；M2：`_ollama_*` 迁出 `cli.py` 会断 `init`（`cli.py:1597/1725`）与 `tests/test_cli.py:3842`，补向后兼容 re-export；m1：CLI 拿不到 `_CONFIG_SAVE_LOCK`（跨进程），照 `set-password` 自带守卫、last-writer-wins；m2：`-k "autostart or ollama_required"` 会漏 preflight/supervisor 测试，改专属文件整跑；m3+m4：Windows 改 pythonw `.pyw` 启动脚本（无 cmd 闪窗），`registered` = Run 值 + `.pyw` 俱在、缺则 drift。
- 2026-06-05（review 修订 4）：第四轮对抗（holistic breaker）抓到 **修订 3 的 B1 修法本身是 blocker**——我把 auth provenance 模型搞反了：`_api_auth_lines`（`config.py:1391-1399`）默认渲染 **内存值**、仅对 override-层 shadow 字段回退 on-disk，renderer 无调用方身份。若「永远 from disk」会 **死锁 apply**（apply 写内存 true、渲染 on-disk false、reload-verify 永远失败）。改为显式 `_render_config_toml(autostart_authoritative=False)`：非 apply 写者 from disk（防 clobber）、apply/CLI 传 `True` from memory（写目标值），apply 另锁内 `_load()` 重读——既不死锁也关掉并发写 race。另收 3 minor：Windows `pythonw` 由 `Path(sys.executable).with_name` 推导 + 缺失回退、`.pyw` 内 `os.execv` 目标=pythonw；self-heal 重述为「`enabled=true ∧ OS 缺失` 即补注册」无需「曾注册」标记；disable 被 local 钉 true 也按 `shadowed` 对称。breaker 判：除此 blocker 外全部 CONVERGED（安全/枚举/回滚/边界/30+ 引用 solid）。
- 2026-06-05（review 修订 5 / **收敛**）：第五轮双 agent。provenance 验证器逐行核对 `autostart_authoritative` 设计 → **CORRECT & IMPLEMENTABLE**（12 个 `save_config` 调用方全默认 `False` 正确保留；env_managed 守卫序关掉 reload-verify 假阴；config 镜像非过度设计——纯 OS-state 会丢 drift/self-heal），仅 2 处零风险收紧（`save_config` 每次读 `on_disk_autostart`；`manage_ollama` 恒内存渲染）已并入。fresh-eyes 冷读全 spec → **SHIP**（9 Req 全对到 acceptance、三平台机制无误、scope 紧、引用准），仅 `_is_public`/degraded「登记」措辞 + Linux `.desktop` 最小字段两处措辞已收紧。**0 blocker / 0 major → 判定收敛**。最riskiest 区（config provenance/anti-clobber）已两轮验证正确，留作 code-review 重点。
