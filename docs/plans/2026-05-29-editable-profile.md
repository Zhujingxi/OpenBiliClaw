# 可编辑用户画像 — Phase 1（后端覆盖层核心）Implementation Plan

> **For Claude:** 按 task 顺序执行，每个 task 先写失败测试再实现（TDD），每个 task 末尾原子提交。
> 设计依据：`docs/plans/2026-05-29-editable-profile-design.md`（v3，已纳入两轮评审 F1–F9）。

**Goal:** 让用户能确定性地编辑画像、编辑抗重建、删/拉黑真实影响推荐（含清池）；本期只做后端，完成后可用 curl 验证四件事（编辑生效 / 抗重建 / 删·拉黑改推荐 / 清池）。UI 留 Phase 2-3。

**Architecture:** 用户编辑写入独立 `data/memory/profile_overrides.json`；AI 画像照常存 `soul.json`。**有效画像 = AI 画像 ⊕ 覆盖层**，在读收口 `SoulEngine.get_profile()` 与镜像收口 `MemoryManager.sync_profile_files()` 叠加；三处重建全量覆盖落点不动。disliked 走 **base-then-overlay** 的 `get_effective_disliked_topics()` 供硬过滤，手动拉黑复用既有 `purge_pool_for_new_dislikes` 清池，并同步两套 speculator。

**Tech Stack:** Python 3.11+, dataclasses, JSON layer storage, FastAPI, pytest, Ruff, MyPy；既有 SoulEngine / MemoryManager / speculator / avoidance_speculator / dislike_writeback。

**约定（所有 task 通用）**
- 测试命令：`./.venv/bin/python -m pytest <files> -q`
- 全量校验：`./.venv/bin/python -m ruff check src/ tests/ && ./.venv/bin/python -m mypy src/ && ./.venv/bin/python -m pytest -q`
- 类型注解齐全（mypy strict）；JSON dump 用 `ensure_ascii=False, indent=2`。

---

### Task 1: ProfileOverrides 数据结构 + MemoryManager 读写

**Files:**
- Create: `src/openbiliclaw/soul/overrides.py`
- Create: `tests/test_overrides.py`
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `tests/test_memory_manager.py`
- Modify: `src/openbiliclaw/soul/engine.py`（仅 `SOURCE_LABELS` 加 `"manual": "手动编辑"`）

**Step 1: 失败测试**
- `test_overrides.py`：`ProfileOverrides()` 默认空；`from_dict(to_dict())` 往返一致；缺字段安全回退。
- `test_memory_manager.py`：`save_profile_overrides()` 后 `load_profile_overrides()` 一致；文件缺失 → 返回空 `ProfileOverrides`。

**Step 2: 跑测试确认失败** `./.venv/bin/python -m pytest tests/test_overrides.py tests/test_memory_manager.py -q`

**Step 3: 实现**
- `overrides.py`：`@dataclass ProfileOverrides`（`version:int=1, updated_at:str="", text_pins:dict, scalar_pins:dict, list_edits:dict, interest_edits:dict`）+ `to_dict()/from_dict()`（健壮解析，复用 profile.py 的 `_as_*` 风格）。
- `manager.py`：仿 `load_feedback_state/save_feedback_state` 新增
  - `self._profile_overrides_path = data_dir / "memory" / "profile_overrides.json"`（构造函数内）
  - `load_profile_overrides() -> ProfileOverrides` / `save_profile_overrides(ov)`（写盘后 `_notify_profile_changed()`）。
- `engine.py:51` `SOURCE_LABELS` 增 `"manual": "手动编辑"`。

**Step 4: 跑测试确认通过**

**Step 5: 提交**
```bash
git add src/openbiliclaw/soul/overrides.py tests/test_overrides.py src/openbiliclaw/memory/manager.py tests/test_memory_manager.py src/openbiliclaw/soul/engine.py
git commit -m "feat(soul): add ProfileOverrides model + overrides persistence"
```

---

### Task 2: `apply_overrides()` 纯函数合并（覆盖层核心）

**Files:**
- Modify: `src/openbiliclaw/soul/overrides.py`
- Modify: `tests/test_overrides.py`

**Step 1: 失败测试**（重点）
- text-pin：固定 `personality_portrait` → 有效画像取固定值；`role.life_stage` 同理。
- scalar-pin：`surface.exploration_openness` clamp [0,1]。
- list：`core.core_traits` add/remove；大小写去重。
- 兴趣树：`interest.likes` 加领域/细分、`interest.dislikes` 加领域。
- **抗重建**：构造一份"全新 AI 画像"，叠加同一覆盖层 → 用户编辑仍在。
- **remove 持续抑制**：remove 的 trait 即使新 AI 画像又含，有效画像不含。
- **dislike 传导**：加 `interest.dislikes` → `effective.preferences.disliked_topics` 含该项。

**Step 2: 跑测试确认失败**

**Step 3: 实现** `apply_overrides(profile, ov) -> OnionProfile`：
- 深拷贝（`OnionProfile.from_dict(profile.to_dict())`）后逐类叠加：
  - list 字段：`(AI − remove) ∪ add`（按归一 key 去重，保留显示原文）；
  - text/scalar pin：直接覆盖；scalar clamp；
  - interest 树：remove_domains / add_domains / weight_pins / specific_edits 逐项套用。
- 纯函数、不改入参。

**Step 4: 跑测试确认通过**

**Step 5: 提交**
```bash
git add src/openbiliclaw/soul/overrides.py tests/test_overrides.py
git commit -m "feat(soul): apply_overrides deterministic profile merge"
```

---

### Task 3: `apply_edit()` 归约器 + 校验

**Files:**
- Modify: `src/openbiliclaw/soul/overrides.py`
- Modify: `tests/test_overrides.py`

**Step 1: 失败测试**
- 每类字段 × 每个 op：`set/add/remove/reset`。
- add/remove 集互斥（add 后再 remove 同项 → 从 add 移除并进 remove）。
- 幂等：add 已存在、remove 不存在 → no-op。
- 校验：trim 后空 → 拒；超限（portrait>1200 / 列表项>40 / list add>30）→ 拒；未知 target → 拒；scalar 越界 clamp。

**Step 2: 跑测试确认失败**

**Step 3: 实现** `apply_edit(ov, *, target, op, value=None, parent="", weight=None) -> tuple[ProfileOverrides, EditResult]`
- 白名单 `target`（见设计 §可编辑字段清单）；按字段类型分派到 text_pins/scalar_pins/list_edits/interest_edits；集中校验/归一；返回新 `ProfileOverrides` + `EditResult(ok, target, message, effective_hint)`。
- 非法输入抛 `ProfileEditError`（自定义，供 API 转 422）。

**Step 4: 跑测试确认通过**

**Step 5: 提交**
```bash
git add src/openbiliclaw/soul/overrides.py tests/test_overrides.py
git commit -m "feat(soul): apply_edit reducer with validation"
```

---

### Task 4: 读路径叠加 — `get_profile()` overlay + `get_raw_profile()` + `get_effective_disliked_topics()`（F3/F6）

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `tests/test_soul_engine.py`

**Step 1: 失败测试**
- `get_profile()` 返回叠加后的有效画像；`get_raw_profile()` 返回纯 AI。
- **抗重建**：写一份新 raw 到 soul 层 → `get_profile()` 仍含用户编辑。
- **F6 base-then-overlay**：raw preference 含 "标题党"，覆盖层 remove "标题党" → `get_effective_disliked_topics()` **不含**；覆盖层 add "钓鱼贴" → 含。
- `get_overrides()` 返回 `MemoryManager.load_profile_overrides()`（F-C，供 Task 8 用）。

**Step 2: 跑测试确认失败**

**Step 3: 实现**
- `get_profile()`：`from_dict` 后 `profile = apply_overrides(profile, self._memory.load_profile_overrides())`，再挂 `_active_speculations`（顺序不变）。
- `get_raw_profile()`：同 `get_profile` 但**不**叠加（供编辑态/漂移）。
- `get_effective_disliked_topics() -> list[str]`（**同步**，供 refresh 硬过滤直接调）：`base = flatten(raw soul.interest.dislikes) ∪ raw preference.disliked_topics`，再套 `overrides.interest_edits.dislikes` 的 remove/add（**remove 最后**），归一去重输出。
- `get_overrides() -> ProfileOverrides`：`return self._memory.load_profile_overrides()`（F-C）。

**Step 4: 跑测试确认通过**

**Step 5: 提交**
```bash
git add src/openbiliclaw/soul/engine.py tests/test_soul_engine.py
git commit -m "feat(soul): overlay-aware get_profile + raw view + effective dislikes"
```

---

### Task 5: 镜像渲染有效画像（F4）

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `tests/test_memory_manager.py`

**Step 1: 失败测试**
- 写覆盖层（改 `personality_portrait`）→ 用 raw 画像调 `sync_profile_files(raw)` → 读 `data/soul_profile.md`/`.json` 含用户编辑。
- dict 入参与 `OnionProfile` 入参都覆盖。

**Step 2: 跑测试确认失败**

**Step 3: 实现** 改 `sync_profile_files()`：先 `isinstance` normalize 成 `OnionProfile`（保留现有 dict 兼容），`effective = apply_overrides(onion, self.load_profile_overrides())`，用 `effective` 渲染 `.md/.json` 镜像；末尾 `_notify_profile_changed()` 不变。

**Step 4: 跑测试确认通过**

**Step 5: 提交**
```bash
git add src/openbiliclaw/memory/manager.py tests/test_memory_manager.py
git commit -m "feat(memory): sync_profile_files renders effective profile"
```

---

### Task 6: delight 硬过滤改读有效 dislikes（F1·读取方）

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`（含 `SupportsProfileEngine` Protocol）
- Modify: `tests/test_refresh_runtime.py`（及其中 soul_engine fake/stub）

**Step 1: 失败测试**
- 覆盖层 add 一个 dislike → `_load_disliked_topic_phrases()`（改造后）命中并过滤含该词候选。
- 覆盖层 remove 一个 raw preference 已有 dislike → 不再过滤（不被 raw 打穿）。

**Step 2: 跑测试确认失败**

**Step 3: 实现**
- `refresh.py:666 _load_disliked_topic_phrases()` 改为调用 `self.soul_engine.get_effective_disliked_topics()`（小写归一），不再直接读 `get_layer("preference")`。审计并处理其它直接读 raw `disliked_topics` 的硬过滤点（若有）。
- **F-D（mypy）**：在 `SupportsProfileEngine` Protocol（`refresh.py:132`）补 `def get_effective_disliked_topics(self) -> list[str]: ...`；同步更新测试里实现该 Protocol 的 fake/stub，否则 mypy 挂。
- **跑 `mypy src/`** 确认 Protocol 改动无类型错误。

**Step 4: 跑测试确认通过**

**Step 5: 提交**
```bash
git add src/openbiliclaw/runtime/refresh.py tests/test_refresh_runtime.py
git commit -m "fix(runtime): delight hard filter honors effective dislikes"
```

---

### Task 7: `apply_user_edit()` — 写覆盖层 + 清池 + 两套 speculator 同步 + cognition（F1/F7）

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `tests/test_soul_engine.py`

**Step 1: 失败测试**
- 调用 `apply_user_edit(target="core.core_traits", op="add", value="务实")` → 覆盖层落盘 + `_notify_profile_changed` 触发 + 记一条带 `source_label="手动编辑"` 的 cognition。
- `dislike-add`（新主题）→ 调 `purge_pool_for_new_dislikes`（mock 断言被调、`newly_added` 仅含真正新增的归一主题）。
- **F-A 差集**：对**已存在**的 dislike 再次 add（或归一后等价）→ 差集为空 → **不调** purge（mock 断言未被调）。
- speculator：mock 两套 speculator，`like-add` 命中活跃正向猜测 → `user_confirm_speculation`；`dislike-add` 命中活跃避雷猜测 → `user_confirm_avoidance`；对应 remove → reject。
- 非法 target → 抛 `ProfileEditError`。

**Step 2: 跑测试确认失败**

**Step 3: 实现**
`async def apply_user_edit(self, *, target, op, value=None, parent="", weight=None, database=None, embedding_service=None, llm_service=None) -> dict`：
1. **清池前快照**：`before = set(self.get_effective_disliked_topics())`；
2. `ov = load_profile_overrides()` → `ov2, res = apply_edit(ov, ...)` → `save_profile_overrides(ov2)`；
3. **清池（F-A 差集，禁用原始 value）**：`after = set(self.get_effective_disliked_topics())`；`newly_added = after - before`；仅当 `newly_added` 非空 `await purge_pool_for_new_dislikes(database=database, embedding_service=embedding_service, llm_service=llm_service, newly_added=sorted(newly_added), all_dislikes=sorted(after))`（deps 缺省则跳过清池 + 记日志）。重复 add / no-op → 差集空 → 不清池；
4. speculator 同步（getattr 防御）：like↔`speculator`，dislike↔`_avoidance_speculator`，add→confirm/remove→reject；
5. `memory.save_cognition_updates([{ "source": "manual", "source_label": "手动编辑", "summary": ... }])`（**显式写 `source_label`**，避免依赖 `api/app.py` 的 SOURCE_LABELS，F-E）；
6. `self._memory.sync_profile_files(await self.get_raw_profile())`（**await**，`get_raw_profile` 是 async，F-F）——其内部叠加 overlay 渲染镜像并触发 `_notify_profile_changed` 两端刷新；
7. 返回 `{ok, target, effective}`。

**Step 4: 跑测试确认通过**

**Step 5: 提交**
```bash
git add src/openbiliclaw/soul/engine.py tests/test_soul_engine.py
git commit -m "feat(soul): apply_user_edit with pool purge + speculator sync"
```

---

### Task 8: API — `GET /api/profile/edit-state`（全量,F2）+ `build_edit_state`（F3）+ `POST /api/profile/edit`

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/api/models.py`（或 app 内既有模型定义处）
- Modify: `src/openbiliclaw/soul/overrides.py`（加 `build_edit_state(raw, effective, ov) -> dict`）
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_overrides.py`

**Step 1: 失败测试**
- `build_edit_state`：>12 likes / >8 favorite_up / >6 traits 时**全量返回**；固定文本 raw≠pin → 该项带 `ai_suggestion`，相等 → 不带。
- `GET /api/profile/edit-state` 返回未截断 + overrides 块。
- `POST /api/profile/edit`（合法）→ 200 + 覆盖层改变 + 返回 `edit_state`；非法 target → 422；引擎未初始化 → 503。

**Step 2: 跑测试确认失败**

**Step 3: 实现**
- `overrides.build_edit_state(raw, effective, ov, *, full=True)`：组装未截断字段 + `overrides` 标注 + 每固定项 `ai_suggestion`（raw 当前值 vs pin）。
- 新模型 `ProfileEditIn{target,op,value?,parent?,weight?}`、`ProfileEditResponse{ok,target,edit_state}`、`ProfileEditStateResponse`（同 summary 字段但不截断 + overrides）。
- `GET /api/profile/edit-state`：`raw=await ctx.soul_engine.get_raw_profile()`、`eff=await ctx.soul_engine.get_profile()`、`ov=ctx.soul_engine.get_overrides()` → `build_edit_state`。
- `POST /api/profile/edit`：`ProfileEditIn` → `await ctx.soul_engine.apply_user_edit(...)` 传清池 deps，**取法与既有 `apply_new_dislikes` 调用址 `app.py:3431-3435` 一致**：`database=ctx.database`、`embedding_service=getattr(ctx.soul_engine, "_embedding_service", None)`、`llm_service` 同址取法（勿用不存在的 `ctx.embedding_service`）→ 回 `edit_state`；`ProfileEditError`→422；`SoulProfileNotInitializedError`→503（复用 degraded/既有异常处理）。

**Step 4: 跑测试确认通过**

**Step 5: 提交**
```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py src/openbiliclaw/soul/overrides.py tests/test_api_app.py tests/test_overrides.py
git commit -m "feat(api): profile edit + full edit-state endpoints"
```

---

### Task 9: `profile-summary` 增 overrides 标注（展示态，向后兼容）

**Files:**
- Modify: `src/openbiliclaw/api/models.py`（`ProfileSummaryResponse` 在此，`models.py:408`）
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `tests/test_api_app.py`

**Step 1: 失败测试** `GET /api/profile-summary` 仍按 `[:12]/[:8]/...` 截断，但响应新增 `overrides` 块（标注哪些项被编辑/固定）；无覆盖时该块为空、其余字段不变。

**Step 2: 跑测试确认失败**

**Step 3: 实现** `ProfileSummaryResponse` 加可选 `overrides` 字段（默认空），由 `get_overrides()` 填充；不解除截断、不破坏现有字段。

**Step 4: 跑测试确认通过**

**Step 5: 提交**
```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat(api): annotate profile-summary with overrides state"
```

---

### Task 10: 文档同步

**Files:**
- Modify: `docs/modules/soul.md`（overlay、`apply_user_edit`、`get_raw_profile`、`get_effective_disliked_topics`、可编辑字段、新 API 端点）
- Modify: `docs/modules/memory.md`（`profile_overrides.json`、load/save、`sync_profile_files` 叠加）
- Modify: `docs/modules/recommendation.md`（用户 dislike → 有效 dislikes + 清池）
- Modify: `docs/changelog.md`（PR 条目）
- Modify: `docs/architecture.md` + `docs/spec.md` §3 + `README.md` / `README_EN.md` 顶部图（新增"用户覆盖层"数据块：画像存储 →〔overlay〕→ `get_profile`/`sync_profile_files`）
- Modify: `docs/plans/2026-05-29-editable-profile-design.md`（Phase 1 标记完成）

**Step 1: 更新文档**（按 CLAUDE.md 文档要求；架构图新增覆盖层块务必反映 main 现状）
**Step 2: 校对 diff** `git diff -- docs/`（仅本特性相关改动）
**Step 3: 提交**
```bash
git add docs/
git commit -m "docs: document editable-profile overrides layer (phase 1)"
```

---

### Task 11: 全量验证 + 手动联调

**Step 1: Python 全量校验**
```bash
./.venv/bin/python -m ruff check src/ tests/
./.venv/bin/python -m mypy src/
./.venv/bin/python -m pytest -q
```
Expected: ruff clean / mypy clean / pytest all pass。

**Step 2: 扩展未受影响 sanity（Phase 1 不动 extension）**
```bash
cd extension && npm run typecheck && npm test
```
Expected: PASS（如本机无 node 依赖可跳过并记录）。

**Step 3: 手动 curl 验收四件事**（后端 `openbiliclaw serve-api` 起在 127.0.0.1:8420）
- **编辑生效**：`POST /api/profile/edit {target:"core.core_traits",op:"add",value:"务实"}` → `GET /api/profile/edit-state` 含"务实"。
- **抗重建**：触发一次画像重建（如足量反馈 / `rebuild-profile`）→ 再 `GET /api/profile/edit-state` 仍含"务实"。
- **删/拉黑改推荐**：`POST .../edit {target:"interest.dislikes",op:"add",value:"标题党测评"}` → 新推荐/ delight 不再出含该主题候选。
- **清池**：拉黑后确认候选池中命中该主题的旧内容被 `purge_pool_for_new_dislikes` 清除（日志 / 池计数）。
- **撤销**：`{op:"reset",target:"core.core_traits"}` → 恢复 AI 原值。

**Step 4: 收尾提交（如有零散修复）**
> ⚠️ **禁用 `git add -A`**（F-B）：worktree 有无关未跟踪文件（`.tmp/`、`probe-real-1.png`）。只**显式列出**本特性改动文件，提交前 `git status` 核一遍。
```bash
git status                                  # 确认暂存区只有本特性文件
git add src/openbiliclaw/... tests/...      # 显式列出本次改动文件
git commit -m "test: editable-profile phase 1 verification fixes"
```

---

## 完成定义（Phase 1 DoD）
- [ ] `overrides.py`（model + `apply_overrides` + `apply_edit` + `build_edit_state`）含完整单测
- [ ] `get_profile` 叠加 / `get_raw_profile` / `get_effective_disliked_topics`（base-then-overlay）
- [ ] `sync_profile_files` 渲染有效画像（含文件镜像测试）
- [ ] delight 硬过滤读有效 dislikes（F1 读取方）
- [ ] `apply_user_edit`：清池（F1）+ 两套 speculator 同步（F7）+ cognition + notify
- [ ] `POST /api/profile/edit` + `GET /api/profile/edit-state`（全量）+ summary overrides 标注
- [ ] ruff / mypy / pytest 全绿；四件事 curl 验收通过
- [ ] 文档 + 架构图同步（含本 plan 文件纳入版本控制）
