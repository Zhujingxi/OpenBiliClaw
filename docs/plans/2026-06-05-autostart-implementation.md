# 开机自启动 — 实现计划 (Implementation Plan)

**Created:** 2026-06-05
**Spec:** [`docs/specs/autostart.md`](../specs/autostart.md)（CONVERGED，5 轮对抗 review）
**Status:** 待执行 — **CONVERGED**（4 轮对抗；独立 fresh-eyes 冷读判 **EXECUTE**，0 blocker / 0 major）
**Reviews:** 轮 1（coverage + buildability + Codex，~26 findings：2 atomic-commit 雷 + 2 真 bug + 半数 status producer 缺失）+ 轮 2（fix-verify + fresh-eyes：ollama `/v1` 探测 bug + E2b save-throws 回滚 + 数 minor）+ 轮 3（自审：D1↔E1 `ollama_unavailable` detail 矛盾、F1 dep 漏 E2 拆分等 consistency nit）+ 轮 4（独立 fresh-eyes 终审：四个前轮修复全部验证在位且对得上真实代码，仅 3 条 cosmetic 备注，判 **EXECUTE**）全部 incorporated
**Scope:** 后端 Python + 浏览器插件；纯新增，不改现有行为（除 `start` 新增 preflight/self-heal、`save_config` 新增 keyword 参数，默认值保持旧行为）

> 把 spec 的 9 个 Requirement 拆成 **依赖有序、每个 = 一个原子 commit** 的任务。最高风险是 config provenance / anti-clobber（spec 里错过两次），故排在最前且测试最重。每个任务自带测试落进专属文件，最后一个任务补齐验收矩阵并跑 gate 命令。

## 0. 总览：依赖顺序与并行度

```
A. config 基座 + provenance ──┬─> D. start 集成 ──> (E 依赖 C/B)
   (Req 1, 6.3)               │
B1. ollama_supervisor ────────┘
   (Req 4 模块, M2)
B2. autostart guards ─────────┐
   (Req 6.1/6.2)              ├─> E. API ──> F. 插件 UI
C. autostart managers ────────┘     (Req 5,6)     (Req 7)
   (Req 2,3)                  └─> G. CLI (Req 8)
H. 测试矩阵补齐 (Req 9) 贯穿；I. 文档同步收尾 (CLAUDE.md 强制)
```

- **可并行**：**A1 是唯一根**（所有任务都要 `cfg.autostart` 形状 + `config.py` shared）。A1 落地后，A2 / B1 / B2 / C 可并行起；但 A2、C1 都改 `config.py`/新建包，宜串行避免冲突。
- **关键路径**：A1 → A2 → E → F（provenance → API → UI）。
- **粗估**：A≈1.5d（重测试）、B1≈0.5d、C≈3–4d（三平台 + 真实登录手测缓冲）、D≈0.5d、E≈2d（E2 拆 2 commit）、F≈0.5d、G≈0.5d、H≈1d、I≈0.5d。合计 ~10 人日（三平台手测是排期风险）。

## 1. 任务分解

### Phase A — 配置基座 + provenance（最高风险，先做）

#### A1. `[autostart]` 配置 dataclass + 解析 + example
- **目标**：Req 1 的配置面。
- **文件**：`src/openbiliclaw/config.py`、`config.example.toml`
- **步骤**：
  1. `config.py`：新增 `@dataclass AutostartConfig`（`enabled: bool = False`、`manage_ollama: bool = True`），挂到 `Config` 根（`:424` 区，仿 `scheduler` 字段）。
  2. `_build_config()`（`:540`）：`autostart_raw = raw.get("autostart", {})`，两字段过 `_coerce_bool(autostart_raw.get(...), default=...)`（`:749`），构造进 `Config(...)`（`:746` 区）。
  3. `config.example.toml`：`[scheduler]` 块之后加 `[autostart]`（spec Config Schema 原文：`enabled=false`、`manage_ollama=true` + 注释）。
- **测试**（`tests/test_config.py`）：缺省 `enabled is False`/`manage_ollama is True`（经 `Config()` / `_build_config({})`）；`OPENBILICLAW_AUTOSTART_ENABLED="false"` 经 env 强转为 `False`。**`save_config`→`load_config` 往返测试放 A2**——A1 还没渲染块，渲染 + 往返同落（仿 `[soul.preference]` 的 `test_config.py:194`）。
- **依赖**：无。
- **commit**：`feat(config): add [autostart] config section (enabled, manage_ollama)`

#### A2. provenance：anti-clobber 渲染（**riskiest，重测试**）
- **目标**：Req 6.3 / B1。`[autostart]` 落盘永不被陈旧快照 clobber；apply 是唯一权威写者。
- **文件**：`src/openbiliclaw/config.py`
- **步骤**：
  1. 新增 `_read_on_disk_autostart(path) -> dict`（仿 `_read_on_disk_auth` `:1344`，读 raw `[autostart]` 表，缺失返回 `{}`）。
  2. `_render_config_toml(...)`（`:1476`）签名加 `on_disk_autostart: dict | None = None, autostart_authoritative: bool = False`（**与现有 `on_disk_auth` + `consult_local` 并列**——这是个已有 4 路 provenance 的 renderer，不是「加一个 flag」那么轻）；`[autostart]` 渲染块插进那个大 `lines=[...]` 字面量内（`[scheduler]` 块 `:1579` 之后、列表 `:1651` 闭合之前，**不是** append 到 `return` 后）：
     - `enabled`：`authoritative=True` → `_toml_bool(config.autostart.enabled)`；否则从 `on_disk_autostart["enabled"]` 经 `_coerce_bool` 渲染，缺键则省略（落默认）。
     - `manage_ollama`：**始终** `_toml_bool(config.autostart.manage_ollama)`（apply 从不改它）。
  3. `save_config(config, config_path=None, *, autostart_authoritative: bool = False)`（`:1455`，**保留既有 `config_path` 位置参，新参 keyword-only**——否则断现有定位调用方）：**每次都** `on_disk_autostart = _read_on_disk_autostart(path) if path.exists() else None`（像 `on_disk_auth` `:1464`，不只 `False` 时），透传给 `_render_config_toml`。
- **测试**（`tests/test_config.py`）：
  - **往返**：`save_config(cfg, autostart_authoritative=True)`→`load_config` 读回非默认值无损（A1 挪来的往返测试落这）；
  - 默认 `save_config(cfg)`（`False`）**保留磁盘 `[autostart]`** 即便 `cfg.autostart.enabled` 与磁盘不符（模拟陈旧快照）；
  - `manage_ollama` 两种 flag 下都从内存渲染；
  - 往返不破坏其他 section；
  - `_render_config_toml(Config())` 定位调用（`test_config.py:200`）与 **所有现有 `save_config(...)` 调用方**仍按旧行为（新参 keyword-only + 默认值）。
- **依赖**：A1。
- **commit**：`feat(config): preserve [autostart] on-disk unless authoritative write (anti-clobber)`
- **⚠ 注意**：**别实现成「永远 from disk」**——会死锁 apply（spec §3 / 修订 4）。renderer 默认 from disk、`authoritative=True` 才 from memory。

### Phase B — 共享 helper

#### B1. 抽 `runtime/ollama_supervisor.py` + `ollama_required` + loopback
- **目标**：Req 4 模块面 + M2（迁移不裸搬）。
- **文件**：新建 `src/openbiliclaw/runtime/ollama_supervisor.py`；改 `src/openbiliclaw/cli.py`
- **步骤**：
  1. 迁移 `_ollama_is_running` / `_ollama_start_serve_background`（`cli.py:1108/1251`）到新模块；二者内部互调一起搬。**带走模块级依赖**：函数体用了 `cli.py` 的模块全局 `console`（`:67`，失败 `console.print` `:1287`）与 `os`（`os.name` `:1267`）——新模块自建 `console = Console()`（或换 `logging.warning`）并 `import os`；`shutil`/`subprocess`/`time` 已是函数内 local import，随函数走。
  2. 新增 `ollama_required(cfg) = registry._ollama_is_chat_capable(cfg) ∨ str(cfg.llm.embedding.provider).strip().lower()=="ollama" ∨ str(cfg.llm.embedding.fallback_provider).strip().lower()=="ollama"`（复用 `registry.py:493`，**不重写**；`.strip().lower()` 与既有 registry 判定一致，防空白）。
  3. 新增 `effective_ollama_endpoint(cfg) -> str` 与 `is_loopback(url) -> bool`：从 chat（`registry.py:465`）/ embedding（`:225`）配置解析生效 endpoint。**关键：返回 daemon 根 `scheme://host:port`，剥掉被规范化追加的 `/v1` 后缀（`registry.py:482-483`）与尾斜杠**——否则 `_ollama_is_running` 的 `{host}/api/version` 会变成 `/v1/api/version` → 404 → 误判「没运行」。`is_loopback` 按 host 判（`localhost`/`127.0.0.1`/`::1`）；`serve` 还需 `host:port == 127.0.0.1:11434`（`ollama serve` 只绑默认端口，自定义端口只探测）。
  4. `cli.py`：**保留向后兼容 re-export** `from openbiliclaw.runtime.ollama_supervisor import _ollama_is_running, _ollama_start_serve_background`；更新 `init` 调用点（`cli.py:1597/1666/1725`，注意 `:1666` 同行还用未迁移的 `_ollama_has_model`，留在 cli 即可）。
- **测试**（`tests/test_ollama_supervisor.py`）：`ollama_required` 对 chat/`fallback_provider`/per-module/embedding/embedding-fallback 全覆盖；loopback 判定（localhost/127.0.0.1 vs 远端）；**`effective_ollama_endpoint` 剥 `/v1`+尾斜杠**——`base_url=http://localhost:11434/v1` 仍解析出根 `http://localhost:11434` 且探测路径为 `/api/version`（防 404 回归，mock 别只 mock `_ollama_is_running` 否则盖不住此 bug）；`cli._ollama_is_running` re-export 仍可解析（`test_cli.py:3842/3864` 不破）。
- **依赖**：A1。无环依赖（`registry` 不 import cli/runtime；`runtime/__init__` 现仅 import `account_sync`/`events`，不碰 cli）。**本模块保持 import-light**（只 `httpx`/`os`/`shutil`/`subprocess`/`time`/`registry`），别 import cli/manager，免 cli↔runtime re-export 成环。
- **commit**：`refactor(runtime): extract ollama supervisor from cli, add ollama_required + loopback`

#### B2. autostart guards：`active_env_managed_inputs` + shadow 检测
- **目标**：Req 6.1 / 6.2 的可复用守卫（API + CLI + self-heal 共用）。
- **文件**：新建 `src/openbiliclaw/runtime/autostart/guards.py`（顺带建包目录）
- **步骤**：
  1. `active_env_managed_inputs(cfg) -> list[str]`：枚举当前生效的「会被登录会话丢掉的外部 env 配置」——`os.environ` 中除 `OPENBILICLAW_PROJECT_ROOT` 外的 `OPENBILICLAW_*`（含 `API_AUTH_*`）、`GOOGLE_API_KEY`/`GEMINI_API_KEY`（`registry.py:440`）、配置的 `cfg.sources.douyin.cookie_env`（默认值 `config.py:265`，在 `douyin_auth.py:86` 处 `os.environ.get`）指向的变量名。返回命中的 key 名列表（供 `detail` 文案）。
  2. `autostart_shadowed(intended: bool) -> bool`：写后 reload `load_config()` 的 effective，比对 `autostart.enabled != intended`（`config.local.toml` 钉住即 True）。
- **测试**（`tests/test_autostart.py`）：分别 set `OPENBILICLAW_LLM_*` / `GOOGLE_API_KEY` / douyin cookie_env，断言命中；`config.local.toml` 钉 enabled 时 shadow=True。
- **依赖**：A1。
- **commit**：`feat(autostart): add env-managed + config.local shadow guards`

### Phase C — 跨平台自启动管理器

#### C1. `base.py` + `command.py` + 工厂骨架
- **目标**：Req 2 协议 + Req 3 启动命令。
- **文件**：`runtime/autostart/base.py`、`command.py`、`__init__.py`
- **步骤**：
  1. `base.py`：`AutostartManager` 协议（`register(cfg)/unregister()/is_registered()->bool`、`mechanism: str`；`register` 收 `cfg` 以 `build_launch_spec(cfg)`，全 plan 统一 `register(cfg)`）、`@dataclass LaunchSpec(argv, working_dir, env)`、`@dataclass AutostartStatus(supported, registered, platform, mechanism, reason, detail)`。**`mechanism` 契约串钉死**：macOS=`launchd`、Windows=`windows_run`、Linux=`xdg_autostart`、不支持=`none`（spec Status Values）。各 manager register 前 `parent.mkdir(parents=True, exist_ok=True)`（落点父目录可能不存在）。**只写当前用户作用域，不以 root/管理员注册**（`~/Library`、HKCU、`~/.config`）。
  2. `command.py`：`build_launch_spec(cfg) -> LaunchSpec`——`argv=[sys.executable,"-m","openbiliclaw.cli","start"]`；`working_dir`=配置根；`env={OPENBILICLAW_PROJECT_ROOT, PATH(注入 shutil.which("ollama") 目录)}`。Windows `resolve_pythonw() = Path(sys.executable).with_name("pythonw.exe")`，不存在回退 `sys.executable`。
  3. `__init__.py`：`get_manager() -> AutostartManager | None` 按 `sys.platform` **lazy import** 平台实现（顶层不 import 三个 manager，免成环）；`is_supported()`（Docker 经 `docker_runtime.is_running_in_container()` `:176` → 否；未知平台 → 否）；`platform` 串由 `sys.platform` 映射；`register/unregister/status` 顶层封装，不支持时 `mechanism="none"`。
- **测试**：`build_launch_spec` 在 venv 解析出可执行 python + 加载 `openbiliclaw.cli`；pythonw 回退逻辑（mock `Path.exists`）；不支持平台 `is_supported()=False`、`mechanism="none"`。
- **依赖**：A1（`build_launch_spec` 读 cfg 找 ollama）。
- **commit**：`feat(autostart): add manager protocol, launch-command resolver, factory`

#### C2. macOS LaunchAgent
- **文件**：`runtime/autostart/macos.py`
- **步骤**：`mkdir` `~/Library/LaunchAgents/` 与 `StandardOut/ErrorPath` 日志目录（`cfg.logging.directory_path`）后，写/删 `~/Library/LaunchAgents/com.openbiliclaw.daemon.plist`（`RunAtLoad=true`、`KeepAlive=false`、`ProgramArguments`、`WorkingDirectory`、`EnvironmentVariables`、`StandardOut/ErrorPath`）。`mechanism="launchd"`。**不** `launchctl bootstrap/bootout`。`is_registered()` = plist 文件存在。幂等。
- **测试**（mock HOME/文件系统）：register 写出 plist 且含 argv/env；**父目录/日志目录不存在时 register 自建**；unregister 删除；重复 register 不重复；未注册 unregister no-op。
- **依赖**：C1。
- **commit**：`feat(autostart): macOS LaunchAgent manager (file-level, no bootstrap)`

#### C3. Windows pythonw `.pyw` + HKCU Run
- **文件**：`runtime/autostart/windows.py`
- **步骤**：`mkdir <data_dir>/autostart/` 后 register 生成 `openbiliclaw-autostart.pyw`（内 `os.environ[...]=…; os.chdir(root); os.execv(sys.executable, [sys.executable,"-m","openbiliclaw.cli","start"])`），写 HKCU `…\Run` 值 `OpenBiliClaw = "<pythonw 绝对路径>" "<.pyw 路径>"`（`winreg`）。`mechanism="windows_run"`。`is_registered()` = **Run 值存在 _且_ `.pyw` 存在**（缺脚本 → False=drift）。unregister 删值 + `.pyw`。
- **测试**（mock `winreg` + 文件系统）：register 写 Run 值 + `.pyw`、**父目录不存在时自建**；`.pyw` 缺失时 `is_registered()=False`；unregister 清两者；幂等。
- **依赖**：C1。
- **commit**：`feat(autostart): Windows HKCU Run + pythonw .pyw launcher`

#### C4. Linux XDG autostart
- **文件**：`runtime/autostart/linux.py`
- **步骤**：`mkdir ~/.config/autostart/` 后写/删 `openbiliclaw.desktop`，最小字段 `Type=Application` / `Name=OpenBiliClaw` / `Exec=env KEY=VAL … <python> -m openbiliclaw.cli start` / `X-GNOME-Autostart-enabled=true` / `Hidden=false`。`mechanism="xdg_autostart"`。`is_registered()` = 文件存在。
- **测试**（mock HOME）：register 写 `.desktop` 且 `Exec` 注入 `OPENBILICLAW_PROJECT_ROOT`、**父目录不存在时自建**；unregister 删；幂等。
- **依赖**：C1。
- **commit**：`feat(autostart): Linux XDG autostart .desktop manager`

### Phase D — `start` 集成

#### D1. `start` preflight（ollama）+ self-heal（reconcile）
- **目标**：Req 4 wiring + M1。
- **文件**：`src/openbiliclaw/cli.py`（`start` 命令 `:3160-3196`，**在 `_maybe_create_runtime_database_backup()` + `_ensure_runtime_database_healthy()` 之后、`_run_api_server` 之前**插入——DB 健康失败时不应跑 preflight/register）
- **步骤**：
  1. **preflight**：`ep = effective_ollama_endpoint(cfg)`；`if ollama_required(cfg) and cfg.autostart.manage_ollama and is_loopback(ep)`：`_ollama_is_running(host=ep)`，未运行 **且 `ep` 是默认 `localhost:11434`** 才 `_ollama_start_serve_background()`（`ollama serve` 只绑默认端口；自定义 loopback 端口只探测不拉起）。失败仅告警 + 记 `ollama_unavailable` 到**运行日志**（不进 status detail，见 E1），不阻断。
  2. **self-heal**：`if cfg.autostart.enabled and not autostart.status().registered`：先 `active_env_managed_inputs(cfg)` 非空则跳过 + warn；否则 `autostart.register(cfg)`，失败仅 warn。（Windows `.pyw` 缺失也算未注册。）
- **测试**（`tests/test_autostart.py`，mock supervisor + manager）：远端 / 自定义 loopback 端口不 `serve`；`manage_ollama=false` 跳过；**DB 健康失败时不触发 preflight/register**；self-heal 在 env-managed 时跳过补注册；`enabled=true ∧ 未注册` 时补注册。
- **依赖**：A1、B1、B2、C。
- **commit**：`feat(cli): start preflight ensures loopback ollama + autostart self-heal`

### Phase E — API

#### E1. 状态读端 `GET /api/autostart-status`
- **目标**：Req 5。
- **文件**：`src/openbiliclaw/api/models.py`、`src/openbiliclaw/api/app.py`、`src/openbiliclaw/api/auth.py`
- **步骤**：
  1. `models.py`：`AutostartStatusOut`（spec API Shape 10 字段）；`AutostartConfigOut` 加进 **`ConfigResponse` 只读**（`:827`），**不进 `ConfigUpdateIn`**（`:843`）。
  2. **更新响应 builder**：`_config_to_response`（`app.py:5121`，逐字段显式构造 `ConfigResponse` `:5158`）的 `return ConfigResponse(...)` 加 `autostart=AutostartConfigOut(enabled=cfg.autostart.enabled, manage_ollama=cfg.autostart.manage_ollama)`——否则 schema 有默认值、`GET /api/config` 永远回默认（spec acceptance「apply 后 GET /api/config 显示 enabled=true」会 fail）。
  3. `app.py`：`GET /api/autostart-status`，**每字段都有 producer**：`supported`/`platform`/`mechanism`←`autostart.status()`（不支持→`mechanism="none"`）；`enabled`←cfg；`registered`←`manager.is_registered()`；`ollama_required`←`ollama_required(cfg)`；`manage_ollama`←cfg；`reason`：`!supported`→`unsupported_docker_runtime`/`unsupported_platform`，否则 `none`；`detail`←人读串（拼 `active_env_managed_inputs` 命中 key / 远端 ollama 提示 / drift 提示；`ollama_unavailable` 只是 start-side 运行日志，status 不在每次请求重探，不进 detail）；`can_manage = is_trusted_local && !env_managed && !shadowed && supported`；远程不 403。**响应只含固定字段集（无 cookie/api_key）**。
  4. `auth.py`：`_is_public()`（body `:269-288`，新条件加在末尾 `return` `:288` 前）加 `path == "/api/autostart-status"`；`app.py:837-844` degraded 白名单加该 path。
- **测试**（`tests/test_api_app.py`）：全新 `enabled=false`/`registered=false`；drift（`enabled=true,registered=false`）如实；远程 `can_manage=false` 非 403；degraded 下可访问；不支持环境 `supported=false` + 对应 `reason`；**apply 后 `GET /api/config` 反映 `enabled=true`**（验 builder）；**响应不含 secrets**。
- **依赖**：A、B1（`ollama_required`）、B2、C。
- **commit**：`feat(api): GET /api/autostart-status (remote-readable, can_manage)`

#### E2a. apply 路由 + 鉴权 + 前置守卫（不动 OS / config）
- **目标**：Req 6 入口与前置守卫，单独可审。
- **文件**：`src/openbiliclaw/api/app.py`、`auth.py`
- **步骤**：
  1. `_is_public()` + degraded 白名单各加 `/api/autostart/apply`。
  2. handler `POST /api/autostart/apply {enabled}`：非 `gate.is_trusted_local` → `403 local_only`。
  3. **不支持短路**（写前）：`not is_supported()` → `409 unsupported_docker_runtime`/`unsupported_platform`，`enabled=false,registered=false`。
  4. **env_managed**（仅 enable）：`active_env_managed_inputs(cfg)` 非空 → `409 env_managed`（命中 key 进 `detail`）。
  - 守卫命中即返回；都没命中时先回当前 status（事务在 E2b 接）。**E2a 单独落地时 happy-path apply 是 200-no-op（不写不注册），不可在 E2b 之前接 UI**（F1 依赖 E2a+E2b）；E2a 测试只断言 403/409 负路径，不伪装 success。
- **测试**（`tests/test_api_app.py`）：非本机→`403 local_only`；Docker→`409 unsupported_docker_runtime`；env→`409 env_managed`。
- **依赖**：E1、B2、C。
- **commit**：`feat(api): POST /api/autostart/apply route, auth, pre-write guards`

#### E2b. apply 事务（enable/disable 方向化 + 回滚）
- **目标**：Req 6 写入事务，照 `auth_admin` 但 OS 步骤按方向排序。
- **文件**：`src/openbiliclaw/api/app.py`
- **步骤**：`async with _CONFIG_SAVE_LOCK`（`app.py:117`）→ 快照 → `cfg=_load()` 锁内重读（照 `auth_admin` `app.py:698-758`）。**每个 `save_config` 都包 try/except**（照 `auth_admin` `_save` + `_rollback_cfg` `:744-749`）：
  - **enable**：set `enabled=true` → `save_config(cfg, autostart_authoritative=True)`（**throw → 恢复快照 + `503 unavailable`**）→ reload effective（shadow → 恢复快照 + `409 shadowed`）→ `manager.register()`（失败 → 恢复快照=`enabled=false` + `409 registration_failed`）。
  - **disable**：**先 `manager.unregister()`**（失败 → config 不动 + `409 unregister_failed`）→ set `enabled=false` + `save_config(..., autostart_authoritative=True)`（**throw → 重新 `register()` + 恢复快照 + `503 unavailable`**，否则 OS 没了但 config 还 enabled）→ reload（被 local 钉 `true` → **重新 `register()` + 恢复快照** + `409 shadowed`）。
  - **方向化避免崩溃残留**：若 enable 先 register 后写 config，或 disable 先写 config 后 unregister，中途崩溃会留「OS 注册着但 config 说关 / 反之」，而 self-heal 只修 `enabled=true ∧ 未注册`、修不了反向。故 enable 写后注册、disable 注销后写；任一步失败都把 **OS 与 config 一并回滚到操作前**（快照恢复 best-effort，同 `auth_admin` `_rollback_cfg`——双重失败=磁盘级灾难，与 PUT/auth 现状同等可接受）。
  - 成功返回最新 status。
- **测试**（`tests/test_api_app.py`）：enable→`enabled/registered=true`；disable→OS 移除 + `enabled=false`；`config.local` 钉→`409 shadowed`（且 config 回滚、OS 复位）；register 失败→config 回滚 `false` + `409 registration_failed`；unregister 失败→`409 unregister_failed`、config 不动；**disable 时 unregister 成功但 save 抛错 → 重新 register + config 回滚 + `503`**；并发 `PUT /api/config` 不回退 apply 值（provenance）。
- **依赖**：E2a、A2。
- **commit**：`feat(api): POST /api/autostart/apply transactional enable/disable with rollback`

### Phase F — 插件设置 UI

#### F1. 「开机自启动」开关
- **目标**：Req 7。
- **文件**：`extension/popup/popup.html`、`popup.js`、`popup-api.js`；建议平行新建 `popup-autostart-control.js`（仿 `popup-auth-control.js` 的 Pattern B，DOM-agnostic 可单测），`popup.js` wire 进来
- **步骤**：设置页加开关（缺省关）；打开读 `GET /api/autostart-status`；切换调 `POST /api/autostart/apply` 即时生效 + 行内 hint；`can_manage=false` 禁用并按 `reason` 出文案（远程/env_managed/shadowed/unsupported）。**必出的后果文案**（spec Settings UX）：仅影响下次登录拉起后端 / 不启停当前进程 / ollama 用户会顺带拉起本机 Ollama；外加残留风险提示（纯 shell env 配置自启动时可能缺失，建议落 config.toml）。`manage_ollama` v1 仅 config 文件可改（UI 不设入口，如展示标只读）。
- **测试**（`extension` `npm run test` + `npm run typecheck`）：渲染/禁用态逻辑（如有可测函数）；手测切换。
- **依赖**：E1、E2a、E2b。
- **commit**：`feat(extension): add boot-autostart toggle in settings`

### Phase G — CLI 子命令

#### G1. `autostart enable|disable|status` + `config-show`
- **目标**：Req 8。
- **文件**：`src/openbiliclaw/cli.py`
- **步骤**：新增 Typer 子命令组；复用 manager + `active_env_managed_inputs` + `autostart_shadowed` + **同 E2b 的 enable/disable OS 方向化排序**；**自带守卫**，照 `set-password`（`cli.py:3291-3318` 自查 env/shadow），不蹭 `_CONFIG_SAVE_LOCK`（进程内 `asyncio.Lock`，跨进程拿不到），`save_config(cfg, autostart_authoritative=True)`；`config-show` 增自启动状态行。`manage_ollama` 不提供 CLI setter（v1 config 文件改）。不支持平台 `enable` 非零退出。
- **测试**（`tests/test_cli.py`）：enable→status 报已注册；disable→未注册；env-managed/shadow 时拒绝；不支持平台非零退出。
- **依赖**：A、B1、B2、C。
- **commit**：`feat(cli): autostart enable/disable/status subcommands`

### Phase H — 测试矩阵补齐

#### H1. 补齐验收矩阵 + gate 命令
- **目标**：Req 9。各任务已落各自测试；本任务补缺口并确认 gate。**补的缺口**：provenance 正反向（含并发 PUT 不 clobber）、drift（含 Windows `.pyw` 缺失）、self-heal 一致性（env-managed 跳过、DB-health 失败不触发）、preflight loopback/默认端口、`cli._ollama_*` re-export、**status 响应不含 secrets**、**不以 root 注册**（mock euid，断言只写用户作用域）、disable「注销先于写」、`unregister_failed`、各平台落点父目录自建、`unsupported_*` reason、`GET /api/config` 反映 apply 后值。确认 gate：
  - `pytest tests/test_autostart.py tests/test_ollama_supervisor.py -q`
  - `pytest tests/test_api_app.py tests/test_config.py tests/test_cli.py -k "autostart or ollama" -q`
- **依赖**：A–G。
- **commit**：`test(autostart): complete acceptance matrix for boot-autostart`

### Phase I — 文档同步（CLAUDE.md 强制）

#### I1. 模块文档
- `docs/modules/config.md`（`[autostart]` 段）、`docs/modules/cli.md`（`autostart` 命令 + `config-show`）、`docs/modules/runtime.md` 或新建 `docs/modules/autostart.md`（manager + preflight + `ollama_supervisor` + provenance）、`docs/modules/extension.md`（开关）。
- **commit**：`docs(modules): document autostart manager, CLI, config, extension toggle`

#### I2. changelog + 架构图
- `docs/changelog.md` 当前版本块加 bullet；架构同步（新增 `runtime/autostart` 子模块 + 依赖边 后端→`ollama_supervisor`）：`docs/architecture.md`、`docs/spec.md` §3 ASCII 图、`README.md`/`README_EN.md` 顶部架构图；发布时 README 中英 📌 highlights callout（≤4 bullet）。
- **commit**：`docs: changelog + architecture diagrams for boot-autostart`

## 2. 验收映射（任务 → spec Acceptance）

| Spec Req | 任务 |
|---|---|
| 1 配置 | A1, A2 |
| 2 manager | C1–C4 |
| 3 启动命令 | C1 |
| 4 preflight | B1, D1 |
| 5 status API | E1 |
| 6 apply API（含 env/shadow/anti-clobber/unsupported/方向化事务） | A2, B2, E2a, E2b |
| 7 UI | F1 |
| 8 CLI | G1 |
| 9 测试 | H1（+ 各任务内联测试） |

## 3. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| **config provenance / anti-clobber**（spec 错过 2 次） | 高 | A2 单列 + 正反向并发测试；code review 重点；**严禁「永远 from disk」** |
| Windows `.pyw` 真实启动 / `os.execv` 仅手测（CI 只能 mock `winreg`/`subprocess`） | 中 | mock 层测注册逻辑；手测一次真实登录拉起；pythonw 缺失回退不致失败 |
| `_ollama_*` 迁出断 `init`/`test_cli.py` | 中 | B1 保留 re-export，更新调用点，`test_cli.py` 纳入回归 |
| disable 崩溃残留（config 关但 OS 还注册着） | 中 | E2b 方向化：disable **先 unregister 后写 config**、enable 反之；测试覆盖中途失败 |
| CLI vs server 并发写 config.toml（`_CONFIG_SAVE_LOCK` 进程内、`save_config` 非原子 `write_text`） | 中 | provenance 防 autostart 段被 clobber；但真正同时写仍可能撕裂（**与 `set-password` 现状同，非本特性引入**）。建议服务停时跑 CLI 或经 API 改。config.toml 原子写（temp+replace）+ 跨进程文件锁列为 **future（出本 plan 范围）** |
| 远端 / 自定义端口 ollama 被误 `serve` | 低 | preflight loopback + 默认端口 gating + 测试 |

## 4. 完成定义 (Definition of Done)

- [ ] 两条 gate pytest 命令全绿（Req 9）。
- [ ] `mypy src/`（strict）、`ruff check src/ tests/`、`ruff format` 通过。
- [ ] `extension`：`npm run typecheck` + `npm run test` 通过。
- [ ] spec 全部 Acceptance 勾选项有对应通过测试。
- [ ] CLAUDE.md 文档同步清单全过（config/cli/runtime/extension 模块文档 + changelog + 架构图）。
- [ ] 三平台至少各做一次真实「登录后自动拉起」手测（或记录受限平台为已知限制）。
- [ ] 默认行为不变：未开启时无任何 OS 副作用；**所有现有 `save_config` 调用方**默认 `autostart_authoritative=False` 保留磁盘 `[autostart]`。

## 5. 约定与边界提醒

- 不在本特性内做：保活/崩溃重启、systemd-linger 自动化、独立 ollama 自启动项、Docker 自启动、卸载器（spec Boundaries）。
- **prompt-cache 约定**（CLAUDE.md）：本特性不新增 LLM prompt builder，无关。
- Conventional Commits；Python 3.11、4 空格、100 列、类型注解齐全。
- 提交顺序建议：A1→A2→B1→B2→C1→C2→C3→C4→D1→E1→E2a→E2b→F1→G1→H1→I1→I2（**17 commit**；**A1 先**，随后 A2/B/C 可并行；E 前需 A2+B2+C 就绪）。
