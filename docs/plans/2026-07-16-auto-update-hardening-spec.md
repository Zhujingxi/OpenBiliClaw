# 自动更新链路加固 Spec — 审计 13 项发现的裁决与修复

**Created:** 2026-07-16(基于同日 Codex 全链路只读审计,发现清单见本文;裁决人:Claude 指挥会话)
**Scope:** `runtime/updater.py` 检查/守卫/应用三阶段、`api/models.py` apply 请求校验、`config.py` 调度间隔校验、桌面 web 与扩展 popup 的更新卡片展示。
**Out of scope(显式裁决为推迟)**:
- **N6-full**:移动 Web 更新面板与 CLI update 命令(功能批次,非缺陷;四端契约以「文档声明排除」满足,见 CLAUDE.md 第 5 条)。
- **N8**:prerelease 版本排序(rc1/rc2 比较相等)——prerelease 通道当前无真实用户,改版本比较器风险大于收益,记为已知限制。
- **D-direct-strip**:审计建议 direct 模式主动剥离子进程继承的代理环境变量——**否决**。现状 git/uv 继承环境是既有行为,大量装机依赖环境代理才能 fetch GitHub;剥离是无用户诉求的破坏性变更。direct/system 均保持继承不变,只给 custom 模式补显式代理。
- **N2-untracked**:审计建议未跟踪文件也算脏——**否决**。未跟踪文件(日志/数据/用户杂物)极常见,一刀切会把大量装机永久卡死;merge 冲突时 git 自己会拒绝,交由 N4 真实分类兜底。staged 变更算脏照做。

## Goal

当天真实用户故障:git 安装在多值 `remote.origin.url` 下被 `origin_remote_unusable` 卡死(检查已看到 0.3.172 但 apply 被拒);历史日志显示 CN 网络下检查窗口一半浪费(传输失败不走 Atom 兜底)、空错误消息不可诊断。目标:**修复后,该用户形态(多值 URL、ssh.github.com:443、CN 网络+custom 代理)全部无人工干预可完成自动更新**,且所有拒绝/失败都带真实原因。

验证命令:
```bash
.venv/bin/python -m pytest tests/test_runtime_updater.py tests/test_desktop_web_update_status.py -q
.venv/bin/python -m ruff check src/ tests/ && .venv/bin/python -m mypy src/
```
实机验证:用户笔记本(多值 origin)升级后不再 blocked;`Auto-update tag check failed: `空消息不再出现。

## Design invariants (MUST hold in every phase)

1. **守卫校验 git 实际使用的 URL**:允许列表作用于 `git ls-remote --get-url origin`(insteadOf 解析后)与 `git remote get-url --all origin` 的**全部**值;任一不可信即拒绝。绝不改写用户 git 配置(不自动 set-url)。
2. **官方等价主机白名单最小化**:仅 `ssh.github.com:443` / `ssh.github.com`(GitHub 官方 SSH-over-443)规范化为 `github.com`;镜像包装 URL 继续拒绝。
3. **更新器永不降级 TLS 校验**:任何路径不得以 `verify=False` 重试;TLS 失败返回 `tls_verification_failed` + 指向显式 TLS 配置开关的修复指引(这是对既有锁定行为的**故意变更**,对应测试同步改写并注明)。
4. **每个失败出口都有稳定 reason + 非空真实 cause**:`str(exc)` 为空时用 `type(exc).__name__` / `repr`;所有 git/uv/pip 子进程返回码必检;JSON 解析、守卫执行、超时、merge 各边界分类捕获,不得把无关失败归为 `branch_diverged` / `dependency_sync_failed`。
5. **apply 全进程唯一**:模块级(进程级)锁跨 config 热重载存活;并发第二个 apply 返回 `already_applying`,不得双写同一 worktree。
6. **tag 通道封闭**:`request_apply` 在任何 git 变更前用后端通道解析器 + 当前 prerelease 策略校验目标 tag;extension-v*/desktop-v*/畸形/未放行 prerelease 一律拒绝。
7. **网络路由一致性(scoped)**:`[network] mode=custom` 时 git fetch 加 `-c http.proxy=<proxy>`(git 的 http.proxy 同时管 https),uv/pip 子进程叠加 `HTTP_PROXY/HTTPS_PROXY` 环境;direct/system 行为与现状逐字节一致(测试锁定三模式的命令与环境)。
8. **修复指引可直接执行**:守卫给出的 git 命令对含空格路径加引号(Windows `C:\Users\Jane Doe\...` 可直接复制运行)。
9. **既有安全语义零回退**:凭据内嵌 URL 拒绝、dubious ownership 分类、frozen/docker check-only、uv.lock 豁免链全部保持;对应既有测试除不变量 3 声明的 TLS 一项外零修改通过。

## 裁决后的修复清单(审计 ID → 处置)

| ID | 处置 | 内容 |
| --- | --- | --- |
| C | **修** | 守卫改读 effective URL(`ls-remote --get-url` + `get-url --all`),多值全验;多值全可信→放行(直接修复当日用户故障) |
| H | **修** | `ssh.github.com[:443]` → `github.com` 规范化 |
| A | **修** | 传输层失败也尝试 Atom 兜底(现仅 403) |
| B+N4 | **修** | 空异常消息用类名/repr;六处边界分类捕获 + 子进程返回码必检 + `uv.lock` checkout 返回码检查 |
| D | **修(scoped)** | 仅 custom 模式给 git/uv/pip 补显式代理;direct/system 不动 |
| N1 | **修** | 删除 verify=False 重试;改锁定测试(故意变更) |
| N2 | **修(scoped)** | staged 变更算脏;未跟踪不算 |
| N3 | **修** | apply tag 通道校验前置 |
| N5 | **修(cheap)** | 模块级进程唯一 apply 锁 |
| N7 | **修** | 检查间隔配置加载时钳制 ≥1h,保存时拒绝非法值(pitfall #7) |
| N9 | **修** | 指引命令路径加引号 |
| N6 | **部分修** | 桌面卡片 error 态优先展示 `last_error` 细节;扩展 popup 非 git 形态禁用 auto-apply 控件;移动/CLI 文档声明排除 |
| N8 | 推迟 | 记为已知限制 |

## Expected impact

| Lever | Measured effect |
| --- | --- |
| C+H | 多值 origin 与 443-SSH 装机从永久 blocked → 正常自更新(当日用户即受益) |
| A | CN 网络检查成功率:历史日志 34 次传输失败窗口中 Atom 可救回的部分不再浪费 |
| B+N4 | 空消息 `tag check failed: ` 出现率归零;误分类 reason 归零(测试断言) |
| D | custom 代理用户 check 与 apply 网络路径一致,不再"能查不能装" |
| N1/N2/N3/N5 | 更新元数据完整性、脏树守卫、通道封闭、并发安全四个漏洞关闭 |

## Documentation obligations

- `docs/modules/runtime.md` 更新器小节:effective-URL 校验语义、443-SSH 等价、custom 代理贯通、apply 进程锁、移动/CLI 排除声明。
- `docs/modules/config.md`:检查间隔校验规则(若字段语义变化)。
- `docs/changelog.md`:当前版本块 fix bullet。
- 架构图 / CLI 文档:无需(无新模块/命令,显式声明)。
