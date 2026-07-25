# 修复方案 v3:惊喜推荐文案格式错乱 + 账号同步 -101 呈现缺陷

分支:`fix/user-report-expression-and-authsync`(基于 main `17267f28`)
来源:用户群 GAGAGA 报告(2026-07-18)
修订:v3 —— 依据 Codex round-1 + round-2 review 修正。
**v1 根因判断有误;v2 的"时间线证伪"是过度推断,v3 已改为可辩护表述。**

---

## Bug A:LLM 返回非字符串值被 `str()` repr 后落库

### 现象

惊喜推荐卡正文渲染出整段 `'expression': ...`, `'topic_label': ...` 反复片段。
**单引号 Python repr**,不是 JSON —— `str(list)` 的指纹。

### 根因(v3 修正)

v1 归咎于 `json_utils.py:385` 外层 dict 候选优先 + `item_predicate` 只检查键存在。
v2 用 git 时间线声称"证伪"该判断 —— **该证伪不成立**(Codex round-2 指出,已复核):

数据库里的 `2026-05-13 01:18:32` 是 `content_cache.last_scored_at`。
已验证 `update_pool_copy()`(`database.py:6215`)**只写** `pool_expression` / `pool_topic_label`,
**不更新** `last_scored_at`。因此该时间戳无法证明脏文案的写入时刻。
而 recommendation 185 创建于 **2026-05-21**,晚于两次解析改动(均为 05-17)。

**v3 的可辩护表述:**

> 已确定的根因是不可信 LLM 字段经过 `str()` coercion 后落库。
> 现有证据无法判定本条污染发生在 05-17 解析改动之前还是之后,
> 因此候选选择顺序**可能**参与过;但**类型守卫是无论哪条解析链都必需的修复**。

git 事实(供参考,不作为证伪依据):

| 提交 | 日期 | 内容 |
|---|---|---|
| `775e966a` | 2026-03-08 | 引入无条件 `str()` |
| `6e2554d0` | 2026-05-17 | 引入 `_iter_object_candidates` |
| `b016e1fc` | 2026-05-17 | expression wrapper 解析接入 |

修复策略不变:**本方案不动 `json_utils`**(全局改候选顺序会波及 awareness / insight / discovery
等无关调用方),在调用方做类型守卫即可覆盖所有解析链。

### 同类风险:共 5 个污染源头(v3 修正,v2 只列了 3 个)

v2 声称源头只有单条/批量/recommendation 分类三处。**遗漏 discovery 分类两处**
(Codex round-2 指出,已复核):

| # | 位置 | 字段 | 是否受保护 |
|---|---|---|---|
| 1 | `recommendation/engine.py:2337` 单条 expression | `expression` / `topic_label` | ❌ 无条件 `str()` |
| 2 | `recommendation/engine.py:2045` 批量 expression | 同上 | ❌ 无条件 `str()` |
| 3 | `recommendation/engine.py:1611` 分类 | `reason` / `topic_group` | ❌ 无条件 `str()`(`score` 的 `float()` 恰好先抛错,**运气**) |
| 4 | `discovery/engine.py:1324` 单条分类 | `reason` / `topic_group` / `franchise_key` | ❌ **暴露面更大**,见下 |
| 5 | `discovery/engine.py:1897` 批量分类 | 同上 | ❌ **暴露面更大** |

**discovery 两处比 recommendation 更危险**:已验证 `_clamp_score()`(`discovery/engine.py:2054`)
对非数值类型走 `else` 分支返回 `0.0`,**不抛错**。不像 recommendation 侧 `float()` 会抛错挡下整条,
discovery 侧 score 被静默吞掉,`str(list)` 的 reason 照常落库。

传播:`relevance_reason` → `_evo_delight_reason()`(`engine.py:1909`)用作 `delight_reason` **fallback**
→ 用户可见的惊喜卡正文。

**结论:5 处必须在本 PR 内一并加固。** 其余弱谓词(awareness / insight)登记为后续审计项。

**行为变更(必须声明并测试)**:"非字符串按分类失败"会改变现状 ——
合法 score + 非法 reason 以前会被接纳,现在会降为 `0.01 / classification_failed` 或被丢弃。
该变化合理,但**不能再标为"无行为影响的低风险改动"**。

### 污染传播

`pool_expression` → `engine.py:1909-1911` `_evo_delight_reason()` 原样复制进 `delight_reason`
→ 惊喜卡正文;`engine.py:615` `rec.expression = item.pool_expression` → `recommendations.expression`。

### 为什么零告警

解析"成功"、校验通过、静默落库,不打任何日志。
日志里 56 条 `Failed to generate recommendation expression` 是解析彻底失败走 fallback 的**安全**分支,与本 bug 无关。
CLAUDE.md 铁律 4 的反面案例。

### 修复

**A1(v2 改写)。共享校验器,不改 `json_utils`**

新增模块级 helper:

```python
def _validate_expression_item(value: object) -> tuple[str, str] | None:
    """Return (expression, topic_label) only when both are non-empty strings."""
    if not isinstance(value, dict):
        return None
    expression = value.get("expression")
    topic_label = value.get("topic_label")
    if not isinstance(expression, str) or not isinstance(topic_label, str):
        logger.warning(
            "expression payload field type invalid (expression=%s, topic_label=%s)",
            type(expression).__name__, type(topic_label).__name__,
        )
        return None
    expression, topic_label = expression.strip(), topic_label.strip()
    if not expression or not topic_label:
        return None
    return (expression, topic_label)
```

理由(采纳 Codex):
- prompt 契约本就要求两字段同时存在,严格校验不会误伤合法响应。
- **不**全局修改 `_iter_object_candidates` 顺序 —— 会波及 awareness / insight / discovery 等无关调用方。
- list extractor 只要求"任意一项"通过谓词(`json_utils.py:322`),谓词层面保证不了逐项干净,
  必须在调用方逐项校验。

**A2(v3 改写)。批量路径没有"单条重试"这回事**

v1 把同一段代码同时用于 `2045` 和 `2337`,三处硬错误(已确认):
- batch 路径 `payload` 是 list,调用 `payload.get()` 直接 `AttributeError`;
- batch 路径变量名是 `item` 不是 `content`,日志会 `NameError`;
- `_precompute_batch()` 返回 `int`,遇脏项 `return None` 破坏上层完成数计算。

**v2 说"缺失项经 `ExpressionBatchMalformed` 走拆分/单条重试"—— 该机制不存在**
(Codex round-2 指出,已复核):

- `ExpressionBatchMalformed` 签名确为 `missing_items, completed=0`,好项完成数不会重复计算;
  单次调用受 `max_extra_requests=6` 限制,不会无限递归 —— 这部分 v2 说对了。
- 但 `_precompute_batch_with_split_retry()`(`engine.py:2084`)在 `len(missing) <= 1` 时
  **直接返回 `completed`,不调用 single fallback**。
- 现有测试明确锁定该行为:`test_recommendation_engine.py:2986`
  "singleton stays pending without single fallback"。`fallback_to_single` 参数目前**未被使用**。

后果:同一次 drain 内重试有界,但**坏 singleton 会留空并在后续每个 refresh cycle 被反复请求**,
多个高分 poison row 会持续占据查询 limit,挤压后续候选。

**v3 定案:不实现 singleton fallback(会推翻既有测试契约),改为给永久坏项加状态。**

- 坏项计入 `eval_attempts` / 新增 `copy_attempts`,超阈值后标记 quarantine 并退避;
- 待补文案查询排除 quarantine 项,避免反复请求与 limit 挤压;
- quarantine 需可观测(计数 + WARNING),便于发现新污染源。

正确接法:

- **单条**(`engine.py:2337`):`_validate_expression_item(payload)` 返回 `None` → 走模板 fallback。
- **批量**(`engine.py:2045`):对坏项 `continue`,保留已完成项,缺失项计入退避/quarantine。
- **分类 4 处**(`recommendation/engine.py:1611`、`discovery/engine.py:1324` / `:1897`):
  `reason` / `topic_group` / `franchise_key` 加 `isinstance(str)` 守卫,
  非字符串按分类失败处理并 WARNING。

**A3(v2 改写)。结构化探测替代前缀猜测**

v1 的"以 `[{` / `{'` 开头即拒绝"被 Codex 判为既误伤又覆盖不全:
- 误伤:编程类文案可能合法地以 `{'name': ...}` 代码片段开头;
- 漏网:前导空白、`[ {`、双引号 JSON、tuple repr、被包装进其他文本的结构化输出;
- `[{` 与 `[{'` 冗余。

改为:`update_pool_copy()`(`database.py:6215`)对**长度受限**的字符串尝试
`json.loads` / `ast.literal_eval`,**仅当**解析结果是含 `expression` / `topic_label` 特征的
dict / list 时拒绝,并记 WARNING + 计数。

**v3 补:必须定义探测边界**(Codex round-2)——
最大检测长度、捕获的异常集合(含 `RecursionError` / `MemoryError`)、检测哪个字段、
以及合法纯 JSON 教程类文案被拒绝时的预期行为。
**该防线只是 sink defense,不能代替上游 schema 校验。**

**A4(v2 推翻重写)。清洗必须区分已推荐与未推荐**

v1 的"置空让管线补齐"**不成立**。已验证 `database.py:4207` 待补文案查询含:

```sql
AND NOT EXISTS (SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid)
```

即**已存在 recommendation 的 bvid 被排除在重算之外**。而脏行 `BV1ExG8zcE4U` 恰有
recommendation(id=185)→ 置空后不会重算,`database.py:6503` 的历史推荐接口继续返回该卡片,
**正文为空**。v1 方案会把"脏文案"换成"空文案",并未修复。

**v3 定案:确定性 fallback 文案事务性回填**(Codex round-2 基于真实数据核验后推荐)。

对 id=185 的只读核验结果:
- `presented=0`,无 `feedback_type` / `feedback_note` / `feedback_at`
- `content_cache.pool_status='shown'`
- 无引用 recommendation 185 的 feedback event
- `recommendations` 只有指向 `content_cache` 的外键,**无子表外键阻止删除**

处置(同一事务内更新):
`content_cache.pool_expression`、必要时 `pool_topic_label`、`content_cache.delight_reason`、
`recommendations.expression`、必要时 `recommendations.topic`。

保留 recommendation id、创建时间、shown 状态与历史信号。
fallback 复用 `_fallback_expression()`(`engine.py:2412`)的确定性逻辑,
**不重新解释污染 repr**。

**否决的两个选项及理由:**
- **标记隔离**:`recommendations` 当前没有 quarantine/isolation 状态;
  仅改 `pool_status` 挡不住历史推荐查询;借用 `feedback_type=dismiss` 会**伪造用户反馈和统计**。
- **删除重算**:本条无反馈,技术上可删;但会丢失 recommendation id / created_at /
  推荐历史与疲劳信号,且可能让该内容重新出现。
  **通用清洗尤其不能删带反馈的行 —— 反馈直接存在 `recommendations` 里,不是独立表。**

其余要求:
- **未推荐池项**:清空 pool copy,可自动重算。
- **判据**:`LIKE "%'topic_label':%"` 不能作唯一判据(会命中讨论该字段的正常编程文案,
  也会漏掉无 `topic_label` / 不同空格 / 双引号的污染)。改用锚定结构特征 + `ast.literal_eval` 验证。
- **必须提供 dry-run**:输出候选 bvid、各字段命中数、最终变更数、备份与回滚说明。
- **不要**从 repr 里解析第一条文案回填 —— 未必对应本视频,会制造错位。

本机实测脏行(仅供规模参考,他人库未知):

| 表.字段 | 脏行 | 非空总数 |
|---|---|---|
| `content_cache.pool_expression` | 1 | 5856 |
| `content_cache.delight_reason` | 1 | 2390 |
| `recommendations.expression` | 1 | 4220 |

---

## Bug B:账号同步 -101 呈现缺陷

### 根因:`last_sync_error_kind` 从未被持久化

桌面端**本来就有**中文分支 `app.js:5669-5673`:
`kind === "auth_expired"` → 「B 站登录已失效,账号同步已停止 — 请重新登录」

字段在落盘时被丢弃:

- `runtime/account_sync.py:307` 正常写入
- `memory/manager.py:304-323` `save_account_sync_state()` 显式 key 白名单**没有**该字段 → 静默丢弃
- `memory/manager.py:288-302` `load_account_sync_state()` 同样漏
- `runtime/account_sync.py:621-628` `get_runtime_status()` 重新读盘 → 恒为 `""`

于是永远走 `app.js:5677-5679` 兜底分支,英文原文直出。

`git log -S` 确认**出生即坏**:字段由 `d54ac69a` 引入 account_sync 侧,manager 侧从无对应提交。

**API 层无问题**(已验证):`api/models.py:331` 有 `last_account_sync_error_kind`,
`app.py:5756` 会合并 account-sync 状态,桌面 normalizer 也保留该字段。
缺口**只在** manager 白名单 + popup / mobile / CLI。

### 修复

**B1(v2 补充)。三处白名单,不是两处**

`memory/manager.py` 的 `load_account_sync_state`、`save_account_sync_state`,
**以及 `default_state`(`manager.py:272`)** 都要加 `last_sync_error_kind`,否则 state schema 仍不一致。

向后兼容:旧 state 文件无该字段时 `.get(..., "")` 已足够,**无需格式迁移**。

**B2(v2 改写)。不手抄第三份白名单**

v1 提出把真实白名单复刻进 `_FakeMemoryManager`。Codex 指出这只是制造**第三份会漂移的契约**,采纳。
改为保留轻量 fake,另加针对**真实 `MemoryManager`** 的测试:

- 缺文件时的默认值测试
- 旧 JSON(无该字段)读取测试
- 新字段真实 round-trip 测试
- runtime status 经真实 manager 仍能得到 `auth_expired` 的集成测试

**B3(v2 改写)。诊断数据与用户文案分离**

`account_sync.py:306` 的 `" | ".join(errors)` 不去重。三次来源已确认:
history 打 `/x/web-interface/history/cursor`;favorites 与 following **各自先调一次**
`get_nav_info()` 取 mid(`api.py:780` / `:914`),cookie 失效时两次都在 `api.py:395-401` 抛同一条 -101。

v1 想直接把持久化错误替换成中文展示串。Codex 指出这会丢失诊断信息,采纳改为:

- 内部保留**去重后**的诊断 errors(原始英文,供排查)
- 状态持久化稳定的 `kind` / `code`
- 后端**另生成**用户 `message` / `action`
- OpenClaw 是否返回原始 errors 单独定义

可选加固:单 tick 内缓存 nav 结果,省一次冗余请求。

**B4。时间戳本地化**

`app.js:5677-5679` 直接插 ISO 串。复用同文件 `:7437` 的 `formatUpdateCheckTime(iso)`
(`toLocaleString("zh-CN", { hour12: false })`)。建议改名为通用 `formatLocalTime()`,
补无效时间 / `Z` / 带偏移时间的测试。

**B5。呈现降级为可操作引导**

cookie 过期是**必然发生的正常生命周期事件**。代码本身也认同:`_record_stage_error`
(`account_sync.py:316-327`)专门归类 `auth_expired` 并让其优先级压过通用 `error`,
注释明写目的就是让 UI 呈现"re-login needed"。设计意图正确,被持久化 bug 掐断。

当前:红色 danger(`app.css:484`)+ 英文栈 + 三倍重复 + 机器时间戳。
改为 warning/info 色 + 两条明确出路。

**注意**:"保持扩展在线即可同步 Cookie"这条指引**必须先用实际扩展行为验证**,
避免给出不可兑现的操作承诺。

**B6(v3 定案)。拆独立 PR,本 PR 明确声明未覆盖端**

现状(已验证):

| 端 | 是否渲染 |
|---|---|
| API 层 | ✅ 完整(`models.py:331` + `app.py:5756`) |
| 桌面 Web | ✅ 保留并渲染(`app.js:5669`) |
| 扩展 popup | ❌ `popup-helpers.js:863-891` normalizer 丢弃 |
| 移动 Web | ❌ `view-models.js:673-693` 同样丢弃 |
| CLI | ❌ 无引用 |
| OpenClaw | ❌ `schemas.py:91` 无 `_kind`,英文原串透传 |

v2 定了字段名,但"popup/mobile 是否加入口""CLI 哪个命令""OpenClaw 是否加入"
仍是**问题列表而非契约**;且 B3 声称持久化 `kind` / `code`,B6 字段集合却没有 `code`。

**v3 定案(采纳 Codex):**
- **本 PR 定位**:修复持久化 + 桌面/API 呈现。
  按 CLAUDE.md 铁律 5,**明确声明 popup / mobile / CLI / OpenClaw 暂不展示**,
  不得声称已完成四端修复。
- **B6 独立 PR**:再确定字段路径、`severity` 枚举、`action` 结构、`code`、
  旧客户端兼容,以及各端入口。

**B7(v3 改写)。`asyncio.Lock` 不够:既不跨进程,也保证不了"只执行一次"**

后台 `run_forever()` 调用同步(`account_sync.py:667`),OpenClaw 可直接调 `sync_now()`
(`operations.py:93`)。`AccountSyncService` 当前无锁,两个调用可同时 load 旧 state,
最后写入者覆盖游标、错误与 `_kind`。

v2 提出实例级 `asyncio.Lock`。**两个问题使其不足**(Codex round-2):
- API daemon 与独立 OpenClaw adapter 会**各自构建自己的 `AccountSyncService`**,实例锁无法互斥;
- `asyncio.Lock` 只是把第二个调用排队,第一个完成后第二个**仍会再执行一次**,
  不符合"只执行一次"的测试目标。

**v3 契约:**
- 实例内共享 in-flight task(single-flight),重叠调用**复用结果**或返回稳定的 `already_running`;
- 跨进程用**独立的 OS 文件 run-lock**,非阻塞失败时返回 `already_running`;
  进程崩溃/重启后内核自动释放;
- `sync_if_due` 的 due check 必须在**取得 single-flight 之后重新执行**,关闭 TOCTOU;
- `account_sync_state.json` 改为**原子 replace**,避免无锁状态读取撞上半写 JSON。

可复用 `memory/json_state.py:81` 已有的跨平台锁与原子写能力,
但 **run-lock 应使用独立文件**,避免保存状态时嵌套同一文件锁。

**不要用 SQLite 长事务**:不能跨网络/LLM 调用持有;若选 DB,
应使用短事务 CAS lease + owner token/续租,而非一直 `BEGIN IMMEDIATE`。

## 测试矩阵(v3 扩充)

- **实际污染形状**:`expression=list` + `topic_label=str`(不只是两者都是 list)
- batch 正常契约 `{"results":[...]}`
- 只缺一个字段 / 混合好坏项 / schema echo + 最终答案
- **Discovery single/batch → `relevance_reason` → delight** 全链路
- **valid score + invalid reason/topic_group** 的明确分类结果(行为变更点)
- batch 混合好坏项的 completed 计数、**singleton 行为及下一 refresh cycle**(验证 quarantine 生效)
- 非字符串**永不**进入 5 个污染字段
- **清洗幂等、事务中途失败回滚、带反馈 recommendation 不丢历史**
- 清洗后:未推荐项可重算、已推荐项**不出现空卡**
- 旧 account state 读取、真实 manager round-trip、API→桌面
- **两个独立进程共享数据目录的同步竞争,以及持锁进程崩溃后恢复**
- **原子 JSON 保存期间的并发 status 读取**

## 落地顺序(v3 改写:维护窗口内一次做完)

v2 把 A4 清洗排在一组无关 B 修复之后,会留下"发布后旧脏卡继续展示数天"的窗口。
**v3 改为单一维护窗口**(采纳 Codex):

1. **停止所有 writer**(daemon / serve-api / OpenClaw adapter)
2. 部署 A1 / A2 / A3 + **Discovery 两处加固**(5 个源头全覆盖)
3. **dry-run → 备份 → 事务清洗**(A4 确定性 fallback 回填)
4. 验证后启动 daemon

这样既不会在清洗后被重新污染,也没有脏卡继续可见的窗口期。

B 侧(B1/B2/B3/B4/B5/B7)可独立于该窗口发布,风险低。
B6 拆独立 PR。

| 步 | 内容 | 风险 |
|---|---|---|
| 1 | B1 + B2(三处白名单 + 真实 manager 测试) | 极低 |
| 2 | B3 + B4 + B5 + B7(去重/时间戳/呈现/single-flight+文件锁) | 中(B7 涉及跨进程锁) |
| 3 | **维护窗口**:A1+A2+A3+Discovery 加固 → dry-run → 清洗 → 验证 | 中高 |
| 4 | B6 四端契约(独立 PR) | 中 |

### 行为变更声明(必须在 PR 里点明)

B1 修好后,此前归入通用 `error` 的 cookie 过期会**开始走 `auth_expired` 分支**。
期望结果,但需写明以免被误读为回归。

### 文档要求(v2 扩充)

按实际改动同步:`docs/modules/recommendation.md`、`memory.md`、`llm.md`、`storage.md`、
`runtime.md`、`extension.md`、`docs/changelog.md`。
若 B6 改变跨模块数据流 → 触发架构图强制同步集合。

---

## v3 遗留问题:已定案(Codex round-3)

1. **Quarantine:阈值 3,新增 `content_cache.copy_attempts` 列**
   不复用 `eval_attempts` —— 它属于 `discovery_candidates` 的分类生命周期,
   而待补文案队列读取的是 `content_cache`,两者生命周期不同。
   规则:每个完整 refresh cycle 最多 +1;限流/超时等**瞬态失败不计数**;成功回填重置为 0。
   `copy_attempts >= 3` 即 quarantine,从待补查询排除,并记 WARNING + 指标。
   (涉及一次 migration 加列。)

2. **Run-lock 位置:`<data_dir>/memory/account_sync.run.lock`**
   与 `account_sync_state.json`(`manager.py:141`)相邻,确保所有共享状态的进程看到同一路径。
   **不要**放 `/tmp`、cwd 或状态 JSON 自身。锁文件保持稳定 inode —— 不删除、不 `replace`。
   已验证 `json_state.py:31` 的 `_file_lock()` 是**阻塞锁**(`LK_LOCK` / POSIX 同理),
   run-lock 需**新增非阻塞模式**,不能原样复用。

3. **剩余弱谓词:登记后续 issue,不阻塞本 PR**
   `awareness_analyzer.py` / `insight_analyzer.py` 的字段类型守卫、WARNING 与非字符串测试
   确有同类 `str()` 污染风险,但不在本次用户可见推荐链路内。

### 实现边界(Codex round-3 补充)

B7 应由**统一的 single-flight coordinator 调用私有同步核心**,
避免 `sync_if_due()` 取得 single-flight 后再调用同样受该机制保护的公开 `sync_now()`(自死锁/重入)。

---

## Review 结论

三轮 Codex 对抗 review(max effort):

| 轮次 | 结论 | 主要收获 |
|---|---|---|
| Round 1 | 需要修改后再开工 | 推翻 v1 根因;A2 三处硬错误;A4 会产生空文案卡 |
| Round 2 | 仍需修改 | v2 时间线证伪不成立;漏 2 个污染源头;singleton fallback 不存在;`asyncio.Lock` 不够 |
| Round 3 | **无硬伤,可以开工** | 三个遗留问题定案 |
