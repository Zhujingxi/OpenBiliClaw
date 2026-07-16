# 自动更新链路加固 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-16-auto-update-hardening-spec.md`](./2026-07-16-auto-update-hardening-spec.md)
> **Status:** r1
> **Execution order:** Task 1 → 2 → 3 → 4 → 5 → 6(Wave A:1-3 守卫与网络,可先发布;Wave B:4-5 安全与并发;Task 6 展示面+文档门)
> **Tech:** `.venv/bin/python`(系统 python 无包);测试 `.venv/bin/python -m pytest tests/test_runtime_updater.py -q`;lint/format ruff;类型 `.venv/bin/python -m mypy src/`。扩展改动:`cd extension && npm run typecheck && npm test`。

**Invariants that MUST hold — re-read before each task:**

- 守卫校验 git 实际使用的 URL(`ls-remote --get-url` + `get-url --all` 全值),绝不改写用户 git 配置。
- 仅 `ssh.github.com[:443]` 等价于 `github.com`;镜像包装继续拒绝。
- 永不 `verify=False` 重试(对应旧锁定测试是本计划唯一允许改写的既有测试,改写处注明故意变更)。
- 每个失败出口:稳定 reason + 非空真实 cause;子进程返回码必检。
- apply 进程唯一锁跨热重载存活。
- tag 通道校验在任何 git 变更之前。
- direct/system 模式子进程网络行为与现状逐字节一致;仅 custom 补显式代理。
- 修复指引路径加引号。
- 除声明的 TLS 测试外,既有测试零修改通过。
- 提交纪律:每 Task 一个 conventional commit,只 add 本 Task 触碰的文件,不碰共享区其他文件。

### Task 1: 守卫读 effective URL + 443-SSH 等价(C+H)

**Files:** modify `src/openbiliclaw/runtime/updater.py`(守卫 URL 读取、`_canonicalize_remote_url`);test `tests/test_runtime_updater.py`。

**Steps:**
- [ ] 失败测试:多值 `remote.origin.url`(config --get rc=2、get-url --all 两条官方 URL)→ 守卫放行;两条中一条不可信 → `untrusted_remote`;`url.insteadOf` 把官方改写为不可信主机(`ls-remote --get-url` 返回改写后 URL)→ 拒绝;`ssh://git@ssh.github.com:443/whiteguo233/OpenBiliClaw.git` 与 scp 形 `git@ssh.github.com:...` → 放行;镜像包装 → 仍拒绝。
- [ ] 确认 FAIL → 最小实现 → PASS → 全文件回归 + ruff + mypy。

**Acceptance:** 上述五类场景断言全绿;既有 canonicalization 测试(`test_runtime_updater.py:1127` 一带)零修改通过。复现:`.venv/bin/python -m pytest tests/test_runtime_updater.py -q -k "canonical or remote or origin"`。

### Task 2: Atom 传输兜底 + 失败真实分类(A+B+N4)

**Files:** modify `src/openbiliclaw/runtime/updater.py`;test `tests/test_runtime_updater.py`。

**Steps:**
- [ ] 失败测试:传输异常(httpx.ConnectError)后 Atom 成功 → 检查恢复(现状:直接 github_unreachable);`str(exc)` 为空(ReadTimeout(''))→ 日志与 `last_error` 含 `ReadTimeout`;畸形 JSON → 稳定 reason 非 500;git 可执行缺失/超时 → `last_error` 非空且 reason 不为 `branch_diverged`;`uv.lock` checkout 返回码非零 → 真实分类;merge 因锁文件失败(stderr 含 `index.lock`)→ 不归为 divergence。
- [ ] 确认 FAIL → 实现(异常边界逐处分类,提取公共 `_describe_exc(exc)` 帮助函数)→ PASS → 回归 + ruff + mypy。

**Acceptance:** 空消息出现率 =0(测试断言日志文本);六个误分类场景 reason 各自稳定。复现:`-k "atom or classify or describe or misclassif"`。

### Task 3: custom 代理贯通 git/uv/pip(D scoped)

**Files:** modify `src/openbiliclaw/runtime/updater.py`(子进程构造);test `tests/test_runtime_updater.py`。

**Steps:**
- [ ] 失败测试:mode=custom 时捕获 git 命令含 `-c http.proxy=<url>`、uv/pip 环境含 `HTTP_PROXY/HTTPS_PROXY`;mode=direct 与 mode=system 时命令与环境**与现状逐字节一致**(锁定不变量)。
- [ ] 确认 FAIL → 实现(从 `openbiliclaw.network` 读当前模式/代理,构造处统一注入)→ PASS → 回归 + ruff + mypy。

**Acceptance:** 三模式矩阵测试全绿;direct/system 零变更断言在。复现:`-k proxy`。

### Task 4: TLS 不降级 + staged 算脏 + tag 通道校验(N1+N2+N3)

**Files:** modify `src/openbiliclaw/runtime/updater.py`、`src/openbiliclaw/api/models.py`(如需);test `tests/test_runtime_updater.py`。

**Steps:**
- [ ] 失败测试:TLS 错误 → 一次失败即 `tls_verification_failed` + 指引文本,无 verify=False 二次请求(改写旧锁定测试,注释注明 spec 不变量 3 故意变更);staged 修改/新增(`A  src/x.py`)→ `dirty_worktree`(uv.lock 豁免链保持);未跟踪文件 → 不脏;`request_apply("extension-v0.3.171")`/畸形/未放行 prerelease tag → 校验拒绝且无任何 git 子进程调用。
- [ ] 确认 FAIL → 实现 → PASS → 回归 + ruff + mypy。

**Acceptance:** verify=False 在 updater.py 中 grep 计数 =0;staged/untracked 矩阵与通道拒绝测试全绿。复现:`-k "tls or staged or channel or apply_tag"`。

### Task 5: 进程唯一 apply 锁 + 间隔校验(N5+N7)

**Files:** modify `src/openbiliclaw/runtime/updater.py`、`src/openbiliclaw/config.py`(+ 保存路径校验所在处);test `tests/test_runtime_updater.py`(+ config 对应测试文件)。

**Steps:**
- [ ] 失败测试:阻塞的假 apply 进行中,重建 service(模拟热重载)后第二个 `request_apply` → `already_applying`;间隔 0/-1/字符串经 TOML 加载被钳制 ≥1h,经 `PUT /api/config` 保存被拒绝(pitfall #7:保存时拒绝)。
- [ ] 确认 FAIL → 实现(模块级 asyncio.Lock/标志,不新增线程)→ PASS → 回归 + ruff + mypy。

**Acceptance:** 热重载双 apply 测试绿;config 校验双路径(load 钳制/save 拒绝)测试绿。复现:`-k "already_applying or interval"`。

### Task 6: 指引引号 + 展示面 + 文档门(N9+N6 部分)

**Files:** modify `src/openbiliclaw/runtime/updater.py`(指引字符串)、`src/openbiliclaw/web/desktop/assets/js/app.js`(error 态优先 `last_error`)、`extension/popup/`(非 git 形态禁用 auto-apply 控件);docs `docs/modules/runtime.md`、`docs/modules/config.md`(如间隔语义变化)、`docs/changelog.md`;test 各对应测试 + `cd extension && npm test`。

**Steps:**
- [ ] 失败测试:含空格 Windows/POSIX 路径的三类指引字符串带引号可执行;桌面卡片 error 态渲染含 `last_error` 细节;扩展 popup 对 frozen/docker/unsupported 禁用 auto-apply 控件。
- [ ] 实现 → PASS → 文档三处更新(移动/CLI 排除声明写入 runtime.md)→ 全量回归:`.venv/bin/python -m pytest tests/test_runtime_updater.py tests/test_desktop_web_update_status.py -q` + `cd extension && npm run typecheck && npm test` + ruff + mypy。

**Acceptance:** 预合并清单勾选(模块 doc/changelog/排除声明);全部测试绿。复现:`git diff --stat` 逐文件核对。

## Verification after merge

- 用户笔记本(多值 origin 的 git 安装):升级到含本修复的版本后,设置页「立即检查」→ 状态不再 blocked、能走完 apply;owner:white。
- 一轮本地 E2E:临时 git 仓库模拟(多值 origin / 443-SSH origin / staged 文件 / 假 tag)跑守卫矩阵——由验收方(Claude 会话)在合并前执行。
- 回滚触发:任何形态出现"此前可更新、现被新守卫拦截"的误伤报告 → revert 对应 Task 提交,重开该分支裁决。

## Explicitly out of scope

- 移动 Web 更新面板、CLI update 命令(N6-full,文档声明排除)。
- prerelease 版本排序(N8,已知限制)。
- direct/system 模式子进程代理行为变更(裁决否决)。
- 自动改写用户 git 配置(任何形式的 auto set-url)。
