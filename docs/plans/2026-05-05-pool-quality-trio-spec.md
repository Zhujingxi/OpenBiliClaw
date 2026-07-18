# 2026-05-05 — Pool Quality Trio Spec (v0.3.57 + extension v0.3.10)

> 起源:2026-05-05 一份 ~17 分钟的 daemon 会话日志(`openbiliclaw.log`
> + `agent-bootstrap.log`)+ 用户截图,暴露三个互不耦合但都直接污染
> popup 显示质量的问题。本 spec 给出每个问题的现象、根因、修法、
> 改动点、验收方式、风险,以及多波执行计划。
>
> 命名沿用 `2026-05-05-discovery-runtime-fix-spec.md`(已交付 U1-U9)
> 的契约风格——后续对应执行 plan 见
> `2026-05-05-pool-quality-trio.md`。

## 0. 范围

| 编号 | 严重度 | 一句话 |
|------|--------|--------|
| **P1** | 🔴 HIGH | Daemon 启动时 cookie 还没到位,`fetch_history` 失败后不会等 cookie sync 抵达再重试,造成 7 分钟空窗 |
| **P2** | 🔴 HIGH | XHS 自己发布的笔记进推荐池——三条入池路径只有 bootstrap_profile 抽 self_info,passive collector + search/creator 任务都缺这一步 |
| **P3** | 🔴 HIGH | popup 推荐文案还在用占位模板("这条切口挺顺的,先丢给你看看…"),原因是未 precompute 的 row 也算"在池里",serve() 取到就走 fallback |

不在本 spec 范围(后续单独发):
- XHS bootstrap 默认值上调 → v0.3.58
- Bilibili search WBI 限流缓解 → v0.3.58
- 扩展日志落地(`/api/extension/log`) → v0.3.58 + extension v0.3.11

---

## 1. P1 — Cookie race 阻塞 history 7 分钟

### 现象

日志(`openbiliclaw.log`):
```
03:33:25 WARNING [bilibili.api] Cannot fetch history without authentication.
03:33:27 INFO    [bilibili.auth] Cookie set and saved.
              ... 7 分钟 silent ...
03:40:22 INFO    [httpx] GET /x/web-interface/history/cursor "200 OK"  ← 第一次成功
```

### 根因

1. Daemon `runtime/refresh.py` 启动时 cookie 文件可能不存在(扩展尚未同步过),`BilibiliAPIClient.get_user_history` 命中 `is_authenticated=False` 分支,WARN 一行后返回 `[]`,**没有任何重试机制**。
2. 扩展约 1-2s 后通过 `POST /api/bilibili/cookie` 同步 cookie,`api/app.py:331` `event_hub.publish({"type": "bilibili_cookie_synced", ...})` 发布事件,但 daemon **没有订阅这个事件触发 history 重拉**。
3. 直到下一个自然刷新周期(由 daemon 业务流程触发,本 case 是 ~7 min),才偶然以新 cookie 重试。

### 修法

在 `runtime/refresh.py` 的 `ContinuousRefreshController` 加一个一次性钩子:
- 新增字段 `_history_first_fetch_done: bool = False` + `_pending_history_retry: bool = False`
- 启动时订阅 `event_hub` 的 `bilibili_cookie_synced` 事件 → 标记 `_pending_history_retry=True`
- 每次 refresh tick 开头调用 `_on_cookie_ready_if_first_history()`:
  - 若 `_history_first_fetch_done` 已经为 True → 直接 return
  - 若 `_pending_history_retry=False` 且 cookie 文件存在 → 触发一次 `_fetch_history_and_propagate()`,成功后置 `_history_first_fetch_done=True`
  - 若拿到事件 → 立即 fire 一次 retry,无视 throttle
- `_fetch_history_and_propagate()` 调用 `BilibiliAPIClient.get_user_history(max_items=300)`,把结果走 `event_format.build_event` 后塞进 `memory_manager.propagate_event`,**和 cli.py:init 的初始化逻辑等价**

### 改动点

| 文件 | 改动 |
|------|------|
| `src/openbiliclaw/runtime/refresh.py` | 新增 `_on_cookie_ready_if_first_history` + `_fetch_history_and_propagate` + 字段。`run_refresh_loop` 开头调用一次 |
| `src/openbiliclaw/api/runtime_context.py` 或入口处 | `event_hub` 订阅 `bilibili_cookie_synced` → 写入 controller 的 pending 标志 |
| `tests/test_runtime_refresh.py` | 新增测试:模拟 cookie-未就绪→ready 切换,验证 history fetch 在 cookie 事件后 ≤1 个 tick 内被触发 |

### 验收

1. **单元测试**:启动一个 fake controller,先无 cookie tick 一次(应 skip),触发 `bilibili_cookie_synced`,下一个 tick 必须调用 `get_user_history`
2. **生产**:重启 daemon,扩展 sync cookie,日志里从 `Cookie set and saved.` 到第一行 `GET history/cursor` ≤ 30s
3. **回归**:已有测试不变

### 风险

- **低**:新增字段,新逻辑只 fire 一次,不影响后续 refresh 调度
- 重复触发风险:`_history_first_fetch_done` 是布尔 latch,确保多次 cookie 同步事件只 fire 第一次

---

## 2. P2 — XHS 用户自己发布的笔记进推荐池

### 现象

`agent-bootstrap.log` line 610–615:
```json
"sample_titles": ["泽野弘之演唱会听high了", "是胖丁！", "被Gemini嘲讽了",
                  "自家宝安领航城领秀165㎡大五房出售",
                  "宝安领航城·165㎡大五房出售/出租"]
```
"自家宝安领航城" 是用户本人发布的房产笔记,被 XHS 搜索/explore feed 推回给登录用户(XHS 平台行为),进而进了 OpenBiliClaw 的推荐池。

### 根因

XHS 笔记入池有三条路径,过滤覆盖率不全:

| 路径 | 入口 | 抽 self_info? | 后端过滤生效? |
|------|------|--------------|--------------|
| **A** Passive collector | `xiaohongshu.ts:runPassiveCollection` → `XHS_URLS_OBSERVED` → `POST /api/sources/xhs/observed-urls` | ❌ | ❌ `_cache_xhs_notes(notes, page_type)` 调用时 `self_info=None`,fallback 加载——**首次永远是 `{}`** |
| **B** search/creator 任务 | `task-executor.ts:executeTaskInPage`(615–665 非 bootstrap 分支) | ❌ | ❌ `self_info_now` 取值时 bootstrap_profile 还没跑过 |
| **C** bootstrap_profile | `task-executor.ts:executeBootstrapTaskInPage` | ✅ via `extractSelfInfoFromState` | ✅ **但只对自己路径生效** |

典型 race:xhs_producer 5 search tasks 在 bootstrap_profile 之前完成 → pool 已污染。

### 修法

**任意 XHS 页面**只要登录,`window.__INITIAL_STATE__.user` 就有 self_info——所以应该让**每条**入池路径都抽取并上报,而非只指望 bootstrap_profile。

#### 扩展端(extension v0.3.10)

| 文件 | 改动 |
|------|------|
| `extension/src/content/xiaohongshu.ts` | `runPassiveCollection` 增加 self_info 抽取(import `extractSelfInfoFromState` from `xhs/bootstrap.ts`),塞进 `XhsUrlObservation.self_info` |
| `extension/src/content/xhs/passive.ts` | `XhsUrlObservation` 类型加 `self_info?: { user_id: string; nickname: string }`;同时在 `extractNoteMetadataFromAnchor` 后做 scrape-time `if (selfInfo && note.author === selfInfo.nickname) skip` 纵深防御 |
| `extension/src/content/xhs/task-executor.ts` | 非 bootstrap 分支(615–665)在返回 `TaskResultPayload` 前抽 self_info → 写入 `payload.self_info`;同样 scrape-time 过滤 |
| `extension/src/background/service-worker.ts` | `postXhsObservedUrls` 是直传——无改动;`handleTaskResult` 把 self_info 从 payload 透传 |

#### 后端(v0.3.57)

| 文件 | 改动 |
|------|------|
| `src/openbiliclaw/api/app.py` `_extract_self_info_from_debug` | 重命名/拆出 `_extract_self_info_from_payload(payload)`:**先**看顶层 `payload.self_info`,再 fallback 到 `debug.xhs_bootstrap.steps[*].self_info` |
| `/api/sources/xhs/observed-urls` 处理函数 | 入口处 `self_info = _extract_self_info_from_payload(payload)`,若有则 `_persist_xhs_self_info(self_info)`;`_cache_xhs_notes(ctx.database, notes_raw, page_type, self_info=self_info or _load_xhs_self_info())` |
| `/api/sources/xhs/task-result` 处理函数 | 同上,把 `_extract_self_info_from_debug(debug)` 替换为 `_extract_self_info_from_payload(payload)` |
| `src/openbiliclaw/api/app.py` 启动钩子 | 启动后一次性调用 `_purge_self_authored_pool_items(database, self_info)`:扫 `content_cache where source_platform='xiaohongshu' and lower(up_name)=lower(?)`,标记为 `pool_status='suppressed'`(不删,保留追溯) |

### 验收

1. **后端单元测试**(`tests/test_api_xhs_ingest.py`):
   - `POST /api/sources/xhs/observed-urls` 带顶层 `self_info` 的 payload → 验证 `_load_xhs_self_info()` 立即拿到值,且后续同 author 的 note 被 drop
   - `POST /api/sources/xhs/task-result` 同样路径
   - 已存在的 self-author note 在调用 `_purge_self_authored_pool_items` 后变成 suppressed
2. **扩展单元测试**(`extension/tests/passive.test.ts` + `task-executor.test.ts`):
   - 给一个 mock XHS state 含 `user.userInfo.{userId,nickname}`,`runPassiveCollection` 必须把 `self_info` 写进 payload
   - 笔记 author 等于 self.nickname 时被 scrape-time drop
3. **生产**:用户重新登录 XHS,任意页滚一下,日志可见 `xhs self_info persisted`,后续 `xhs ingest filter: dropped N self-authored note(s)` 频繁出现

### 风险

- **存量数据清理**:启动钩子 `_purge_self_authored_pool_items` 需要 self_info 已持久化才能跑。第一次升级用户没有 self_info,扫描 no-op——这是正确行为(纯新装就没存量污染)。等用户下次访问 XHS 抓到 self_info 后,下次重启会 purge。文档要写明这一点。
- **scrape-time drop 误判**:author 字段在 XHS 不同卡片格式下可能拿不到。policy 是"author 拿不到 → 不 drop"(保守),由后端 fallback 兜底。

---

## 3. P3 — popup 推荐文案落到占位模板

### 现象

用户截图显示推荐卡片下文案:
> 《别用claude拉片了，这个ai拉片工具送给你》这条切口挺顺的，先丢给你看看，说不定正好能对上你当下的兴趣。

直接命中 `recommendation/engine.py:1370`:
```python
return f"《{title}》这条切口挺顺的，先丢给你看看，说不定正好能对上你当下的兴趣。"
```
即 `_fallback_expression` 的最后一个分支(11 个 style_key 模板的兜底)。

### 根因

入池流程:
1. Discovery 评估完 → `cache_content(...)` → `pool_status='fresh'`,`pool_expression=''`
2. 后台 `precompute_pool_copy` 跑 LLM 生成 expression + topic_label,写回(60–90s)
3. `get_pool_candidates` 的 SQL **没**对 `pool_expression` 做非空过滤
4. 在 (1)→(2) 之间,用户点"换一批"/serve 命中,row 取出来后 `engine.py:320` `if not rec.expression: rec.expression = self._fallback_expression(item)` —— popup 看到模板

### 用户期望(原话)

> 我要 discovery 如果被选中的内容没有完成推荐理由生成，就不进入推荐池，有了才进推荐池

### 修法

把"在池里"的语义改为 **`pool_status='fresh' AND pool_expression != '' AND pool_topic_label != ''`**。
- 未 precompute 的 row 仍然存在于 `content_cache`,只是对 `serve()` 不可见
- `precompute_pool_copy` 用的 `get_pool_candidates_needing_copy` 已经在反向过滤,不变
- `_fallback_expression` 路径变成"理论上不该触发"的兜底,触发即 `logger.warning`

### 改动点

| 文件 | 改动 |
|------|------|
| `src/openbiliclaw/storage/database.py` `get_pool_candidates`(L766) | 两个 SQL 分支(`max_per_topic_group<=0` 和 window function)的 WHERE 都加 `AND COALESCE(pool_expression, '') != '' AND COALESCE(pool_topic_label, '') != ''` |
| 同文件 `count_pool_candidates`(L871) | 同样加上,否则 popup "还有 N 条" 数字会大于 serve() 实际能拿到的 |
| `src/openbiliclaw/recommendation/engine.py` L320 | `if not rec.expression: rec.expression = self._fallback_expression(item)` 之前加 `logger.warning("Pool gate leak: bvid=%s pool_expression empty at serve time", item.bvid)` |
| 测试 fixtures | 凡是手动塞 `pool_expression=''` 又期望进 pool 的,补上非空 expression |

### 验收

1. **新增测试**(`tests/test_database_pool_gate.py`):
   - 插一行 `pool_status='fresh', pool_expression=''` → `count_pool_candidates() == 0` 且 `get_pool_candidates() == []`
   - `update_pool_copy(bvid, expression='x', topic_label='y')` 后 → 同两个查询都返回 1 / 那一行
2. **集成回归**(`tests/test_recommendation_engine.py`):
   - precompute 流程仍然能找到 `pool_expression=''` 的待办 row(`get_pool_candidates_needing_copy` 不变)
   - serve(profile, expression_mode="precomputed") 永远不返回 `_fallback_expression` 文案
3. **生产**:popup 截图复测,所有卡片文案都是 LLM 生成的个性化句子。日志里 grep `Pool gate leak` 0 条。

### 风险

- **初始化窗口体感变长**:从"discovery 完→立即可换一批"延后到"precompute 完→可换一批"(多 60–90s,init 总时间从 11 min → ~12 min)。**用户明确要求接受**。
- **count 显示数字**会比之前小。这是正确行为,popup 里"还有 N 条"现在反映的是**真正可服务**的池子,不再误导用户。
- **realtime 模式**(`expression_mode='realtime'`)不受影响,本来就是 serve 时 LLM 现生成,跳过 precomputed 这一步。

---

## 4. 版本计划

| 版本 | 内容 | 必须配套 |
|------|------|---------|
| **v0.3.57**(后端) | P1 + P2 后端部分 + P3 全部 | extension v0.3.10 |
| **extension v0.3.10** | P2 扩展端 | v0.3.57 |

两个组件**必须一起部署**。后端 v0.3.57 接受新协议字段(`payload.self_info`),老扩展不发也不报错,只是 P2 不生效。新扩展发到老后端,后端的 `_extract_self_info_from_payload` 不存在会 500——所以**先发后端,后发扩展**,或者扩展端做 graceful degrade(payload 字段一直发,后端忽略就忽略)。

实际兼容性策略:**后端的 payload 接收始终用 `dict.get` + isinstance 防御**,扩展端永远传新字段,新旧都不报错——这是已有的代码风格,无需特别处理。

### 不发 v0.3.57 的话,会怎样?

- P1 不修:每次 daemon 启动都有 7 min 假静默期,首批 history 拿不到 → soul profile 起步信号弱
- P2 不修:用户自己发布的笔记继续混入推荐 → 信任感破坏
- P3 不修:占位模板继续出现在 popup → 卖点(LLM 个性化文案)不可见

三个都直接影响用户感知,**不是渐进式优化,是 bug 性质**。

---

## 5. 执行波次(详见 `2026-05-05-pool-quality-trio.md` plan)

| 波 | 任务 | 预估 | 测试策略 |
|----|------|------|---------|
| W1 | P3 pool gate(SQL + log + 测试)| 2h | 单元测试先行(TDD)→ 改 SQL → 跑全套测试 |
| W2 | P2 后端 self_info 多路径 + purge 钩子 | 3h | 测试先行 → 改 `_extract_self_info_from_payload` + 端点处理 → purge 钩子 |
| W3 | P2 扩展 passive + search 抽取 + scrape-time 过滤 | 3h | node:test 先行 → 改 passive.ts / xiaohongshu.ts / task-executor.ts |
| W4 | P1 cookie-ready hook | 2h | 测试先行 → 改 `runtime/refresh.py` + event subscription |
| W5 | 文档同步(changelog / module docs / README highlight) | 1h | grep 检查所有 module docs 是否覆盖了改动点 |
| W6 | release 切版本(后端 0.3.56→0.3.57,扩展 0.3.9→0.3.10),tag,push | 30min | smoke 测试 |

---

## 6. 不在范围内但相关的事

下次再说,但记一下:

- **MMR 多样性引入"作者维度"**:同一 UP/作者批次内 ≤2,避免 popup 出现 3 条 Jupiter 笔记。`pool_expression gate` 上线后多样性问题会更显著(池子小了多样性指标被放大),提前识记。
- **扩展端 self_info 失效告警**:登录 XHS 但 `extractSelfInfoFromState` 拿不到(state schema 变化)的情况下,扩展应该 console.warn 并把扩展日志(v0.3.58 加的)记一行。

---

**End of spec.**
