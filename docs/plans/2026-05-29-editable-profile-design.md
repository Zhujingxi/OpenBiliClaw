# 可编辑用户画像设计（插件端 + PC/移动 Web）

> 对应 issue **#19 — 用户可编辑/修正 AI 生成的个人画像**
> 状态：设计稿 v3 · **Phase 1（后端）+ Phase 2（插件）+ Phase 3（移动 + 桌面 Web）均已实现**（见 `2026-05-29-editable-profile.md` 与 changelog）
> 注：实现时发现桌面 Web（`/web`，`web/desktop/`）与移动 SPA（`/m`，`web/`）是**两套独立前端**，已分别接入；本文 §前端设计原述「一份 web 覆盖 /web + /m」有误。
> 日期：2026-05-29

## 评审修订（v1 → v2）

首轮评审（gpt-5.5）确认核心方案「读时叠加、不改重建写路径」正确，范围（滑块 v2 / 软回灌 Phase 4 / 鉴权交 #45）也认可。以下 5 点已并入本版：

1. **[High] 推荐抑制不是"零接线"**：delight 硬过滤 `refresh.py:666` 直接读 raw `preference.disliked_topics`；已入池内容需 `apply_new_dislikes()`（`dislike_writeback.py:111`）那套 pool purge。→ 见 §与现有子系统交互·1、Phase 1 任务 F1。
2. **[High] `profile-summary` 是截断视图**：`likes[:12] / dislikes[:8] / favorite_up[:8] / core_traits[:6] / values[:5]…`（`app.py:1535,1683`）。编辑 UI 不能基于它，否则删不到第 13 个兴趣。→ 新增全量 `GET /api/profile/edit-state`，Phase 1 任务 F2。
3. **[Med] drift 需要 raw + effective 双视图**：`get_profile()` 一旦返回有效画像就丢了 AI 原值。→ 新增 `get_raw_profile()` + `build_profile_view(raw, effective, overrides)` 收口，Phase 1 任务 F3。
4. **[Med] `sync_profile_files()` 渲染有效画像设为硬要求**：否则重建后 `soul_profile.md/.json` 镜像显示 raw。→ 在 `sync_profile_files()` 内部统一叠加 overlay + 文件镜像测试，Phase 1 任务 F4。
5. **[Med] "唯一读收口"仅对用户可见推荐成立，对所有 LLM context 不成立**：cognition cycle（`cognition_cycle.py:208`）、dialogue tone（`service.py:546`）、manager LLM 摘要（`manager.py:503+`）仍读 raw。→ 收窄表述，统一 effective memory 归 Phase 4。见 §有效画像覆盖范围。

二轮评审（gpt-5.5，结论"可开 Phase 1"）再并入 4 点：

6. **[Med] `get_effective_disliked_topics()` 不能简单 union**：`raw preference ∪ overlay` 会让用户从 `interest.dislikes` 移除的项被 raw preference 反向加回。→ 必须 **base-then-overlay**：`base = raw soul.interest.dislikes ∪ raw preference.disliked_topics`，再套 overlay 的 remove/add，remove 最后生效。见 §接口·`get_effective_disliked_topics`。
7. **[Med] Speculator 同步漏了 avoidance speculator**：手动 dislike 命中活跃避雷猜测须 `user_confirm_avoidance()`，否则避雷卡片继续出现；移除命中则 `user_reject_avoidance()`。→ §交互·2 补两套 speculator 矩阵。
8. **[Low] 附录 curator 描述不准**：`PoolCurator.build_context()`（`curator.py:138`）只读 DB 反馈行、不接 profile。→ 修正附录：manual dislike 不进 curator，靠 profile 级 scoring + 清池。
9. **[Nit] `sync_profile_files` 伪代码丢了 dict 入参兼容**：现方法支持 `OnionProfile|dict`（`manager.py:191-195`）。→ 伪代码改为先 normalize 成 `OnionProfile` 再叠 overlay。

## 背景

#19 来自评论区高赞反馈，诉求三层：(1) **可读性**——画像给人看懂；(2) **手动修正**——删/改不准确的部分；(3) **主动补充**——直接告诉系统偏好。

当前已有**对话式修正**（`learn_from_dialogue` → `dialogue_insight_analyzer` → 偏好重分析 → 画像重建），但**非确定性**（要累计过阈值、经 LLM、还可能被重新推断回来）且**不可见**（点不到具体字段）。本设计补上**确定性、字段级、所见即所得**的编辑，插件端与 Web 端都可用。

## 现状摸底（关键约束）

| 事实 | 位置 | 对设计的影响 |
|---|---|---|
| 画像是 `OnionProfile`（五层：Core/Values/Interest/Role/Surface + `personality_portrait`） | `soul/profile.py:490` | 编辑对象就是这棵树 |
| 持久化 `data/memory/soul.json`（单份，无版本历史） | `memory/manager.py:139` | 覆盖层独立存盘 |
| `data/memory/` 既有约定：每类状态各自一个 json，**不混进 soul.json** | `manager.py:124-129` + m92 原则 | 新增 `profile_overrides.json` 顺着既有模式 |
| **用户可见推荐链路的读收口**：`get_profile()` 读 soul.json → `from_dict` → 挂 `_active_speculations` | `engine.py:315` | 在这里叠加 overlay，**发现/推荐/画像页**自动生效 |
| ⚠️ **但部分 LLM 消费方绕过它直接读 raw 层**：dialogue tone `service.py:527,546`、cognition cycle `cognition_cycle.py:208`、manager 摘要 `manager.py:503+` | 见附录·raw 读取方审计 | overlay 不自动覆盖这些；v1 收窄范围（F5） |
| ⚠️ **delight 硬过滤直接读 raw preference dislikes** | `refresh.py:666` | 手动 dislike 需改它读"有效 dislikes"（F1） |
| **写路径 3 处全量覆盖** `clear()+update(to_dict())+sync_profile_files()` | `engine.py:259-263 / 453-457 / 529-533` | 不能直接改 soul.json；overlay 在读时叠加 → 天然抗重建 |
| 既有 confirmed-avoidance 经 `apply_new_dislikes()`：写 flat preference + 重建 soul + sync + **清池**（`purge_pool_for_new_dislikes`/`semantic_purge_*`） | `dislike_writeback.py:111-147` | 手动拉黑必须复用这套清池，否则旧池内容不消失（F1） |
| `sync_profile_files(profile)` 渲染**传入对象**；重建传入 raw AI 画像 | `manager.py:186` / `engine.py:259` | 只改 `get_profile()` 不够，镜像会显 raw（F4） |
| **不存在任何 pinned/locked/override 机制**；只有 `InterestTag.source`、`InsightHypothesis.validated`，都不防覆盖 | `profile.py:24,78` | overlay 是全新机制 |
| `OnionProfile.preferences` 是 property，从 `interest.dislikes` 合成 `disliked_topics` | `profile.py:546-571` | 拉黑写 `interest.dislikes` → 经 property 传导给走 `get_profile()` 的消费方 |
| 读接口 `GET /api/profile-summary` **截断**：`likes[:12] dislikes[:8] favorite_up[:8] core_traits[:6] deep_needs[:5] values[:5] motivational_drivers[:4] cognitive_style[:5]` | `app.py:1535-1542,1683-1697` | 编辑必须用全量接口，不能用 summary（F2） |
| 两端前端 vanilla DOM + innerHTML；插件 `popup.js:2608`，Web `web/js/views/profile.js:45`；speculative 卡片已有内联 confirm/reject；`/web` 与 `/m` 共用同一 `web/` SPA | — | 编辑 UI 复用现有渲染/刷新；一份 web 覆盖两端 |

## 目标 / 非目标

**目标**：① 关键字段所见即所得、立即确定生效；② 编辑抗重建；③ 真实影响推荐（删/拉黑不再推、补充进发现）；④ 随时可撤销恢复 AI 建议；⑤ 插件 + Web（含 `/m`）一致，一端改另一端自动刷新。

**非目标（本期不做）**：画像版本历史/回滚；编辑只读派生项（`recent_awareness`/`active_insights`/`speculative_*`，后者复用既有 confirm/reject）；多用户/权限（沿用单用户 localhost，见 §安全）。

## 核心设计：覆盖层（Overrides Overlay）

用户编辑写进独立 **`profile_overrides.json`**；AI 画像照常存 `soul.json` 保持"纯 AI 视角"。**有效画像 = AI 画像 ⊕ 用户覆盖**，合并在**读时**完成。

为什么"读时叠加"而非"写时合并进 soul.json"：对脆弱的 3-4 条重建写路径**零侵入**；编辑天然抗重建（overlay 是独立文件，重建碰不到）；AI 原值与用户覆盖天然分离（可撤销、可做漂移提示）。代价：读时多一次轻量纯函数合并 + 镜像渲染需叠加（F4）。

### 合并语义（按字段类型）

- **文本/标量固定**：固定后忽略重建产出值，但记录 AI 当前值用于漂移提示；`reset` 解除回到 AI 值。
- **列表增删**：覆盖层存 **add 集 + remove 集**，`有效 = (AI生成 − remove) ∪ add`，大小写归一去重；**remove 持续生效**（AI 下轮又推断出也被持续抑制，用户看不到"打架"）。
- **兴趣树**：增删领域/细分、固定领域权重。拉黑写 `interest.dislikes`。

合并函数纯、确定、可单测：

```python
# 新增 src/openbiliclaw/soul/overrides.py
def apply_overrides(profile: OnionProfile, ov: ProfileOverrides) -> OnionProfile:
    """返回叠加用户覆盖后的有效画像（深拷贝，不改原对象）。"""
```

### 有效画像覆盖范围（F5 — 明确边界）

**v1 保证读有效画像**（overlay 已接线）：画像页（summary/edit-state）、内容发现（producers + `refresh.py` 发现路径）、推荐排序（`recommendation/engine.py`、`discovery/.../_utils.py`）、用户可见硬过滤（delight，经 F1 改造后）。

**v1 不覆盖**（仍读 raw 层，归 Phase 4「统一 effective memory / 软回灌」）：

- cognition cycle：`cognition_cycle.py:208-209,241-242`
- dialogue tone / LLM context builder：`llm/service.py:527,546-547`
- manager 给 LLM 的偏好摘要：`memory/manager.py:503,520,542,563`

> 表述纪律：**不要**再说"所有 LLM context 自动生效"。v1 = 画像页 + 发现 + 推荐 + 可见硬过滤。

## 可编辑字段清单（v1 范围）

| 字段 | 洋葱路径 | 类型 | 操作 | v1 |
|---|---|---|---|---|
| 人格画像长文 | `personality_portrait` | text-pin | set / reset | ✅ |
| 核心特质 | `core.core_traits` | list | add / remove / reset | ✅ |
| 深层需求 | `core.deep_needs` | list | add / remove / reset | ✅ |
| 价值观 | `values_layer.values` | list | add / remove / reset | ✅ |
| 内在驱动 | `values_layer.motivational_drivers` | list | add / remove / reset | ✅ |
| 认知风格 | `surface.cognitive_style` | list | add / remove / reset | ✅ |
| 喜欢（兴趣树） | `interest.likes` | tree | add/remove 领域·细分，pin 权重 | ✅ |
| 不喜欢（兴趣树） | `interest.dislikes` | tree | add / remove（驱动推荐抑制 + 清池） | ✅ |
| 常看 UP 主 | `interest.favorite_up_users` | list | add / remove / reset | ✅ |
| 人生阶段 | `role.life_stage` | text-pin | set / reset | ✅ |
| 当前阶段 | `role.current_phase` | text-pin | set / reset | ✅ |
| 风格偏好（5 项 0-1） | `surface.style.*` | scalar-pin | set / reset（滑块） | v2 |
| 探索开放度 | `surface.exploration_openness` | scalar-pin | set / reset（滑块） | v2 |
| 时段/工作日模式 | `surface.context.*` | text-pin | set / reset | v2 |
| MBTI | `core.mbti` | — | 只读 + 一键重置 | v2 |
| awareness/insights/speculative_* | — | 只读；走既有 confirm/reject | 不编辑 |

## 数据结构

```jsonc
// data/memory/profile_overrides.json （新增）
{
  "version": 1,
  "updated_at": "2026-05-29T10:00:00",
  "text_pins": {
    "personality_portrait": { "value": "用户改写…", "ai_value_at_pin": "固定时 AI 原值", "pinned_at": "…" },
    "role.life_stage":   { "value": "在读研究生", "ai_value_at_pin": "…", "pinned_at": "…" }
  },
  "scalar_pins": { "surface.exploration_openness": { "value": 0.3, "ai_value_at_pin": 0.6, "pinned_at": "…" } },
  "list_edits": {
    "core.core_traits":           { "add": ["务实"], "remove": ["完美主义"] },
    "interest.favorite_up_users": { "add": ["老师好我叫何同学"], "remove": [] }
  },
  "interest_edits": {
    "likes":    { "add_domains": [{ "domain": "户外", "weight": 0.8, "specifics": ["徒步"] }],
                  "remove_domains": ["二次元"], "weight_pins": { "科技": 0.9 },
                  "specific_edits": { "科技": { "add": ["自托管"], "remove": [] } } },
    "dislikes": { "add_domains": [{ "domain": "标题党测评" }], "remove_domains": [] }
  }
}
```

对应 `@dataclass ProfileOverrides`（`to_dict/from_dict`，缺文件回退空覆盖 → 有效 == AI，向后兼容）。
`ai_value_at_pin` 仅作"固定时原值"留痕；**漂移（ai_suggestion）按 raw 当前值 vs 固定值实时算**（见 F3）。

## 接口设计

### `soul/overrides.py`（新增）
- `ProfileOverrides`（dataclass）+ `to_dict/from_dict`
- `apply_overrides(profile, ov) -> OnionProfile`：纯函数合并（深拷贝）
- `apply_edit(ov, *, target, op, value=None, parent="", weight=None) -> tuple[ProfileOverrides, EditResult]`：唯一归约器，集中做白名单校验、归一、add/remove 集互斥维护

### `SoulEngine`（改动 + 新增）

```python
# 改动：get_profile() 读时叠加 overlay（用户可见推荐链路的有效画像）
async def get_profile(self) -> OnionProfile:
    soul_data = self._memory.get_layer("soul").data
    if not soul_data:
        raise SoulProfileNotInitializedError(...)
    profile = OnionProfile.from_dict(soul_data)
    profile = apply_overrides(profile, self._memory.load_profile_overrides())  # ← 新增
    ... # 挂 _active_speculations 不变
    return profile

# 新增：raw 画像（纯 AI，不叠加）——仅供编辑态/漂移比对（F3）
async def get_raw_profile(self) -> OnionProfile: ...

# 新增：有效 disliked topics（F1）——必须 base-then-overlay，禁止简单 union
#   base = flatten(raw soul.interest.dislikes) ∪ raw preference.disliked_topics
#   effective = (base − overlay.dislikes.remove) ∪ overlay.dislikes.add
#   关键：overlay 的 remove 最后套用，否则用户移除项会被 raw preference 反向加回（评审 F6）
def get_effective_disliked_topics(self) -> list[str]: ...

# 新增：用户编辑入口
async def apply_user_edit(self, *, target, op, value=None, parent="", weight=None) -> dict:
    """归约 → 校验 → 存 profile_overrides.json → 记 cognition → 触发 profile_updated。
       dislike-add 分支：额外调用 purge_pool_for_new_dislikes(...) 清已入池内容（F1）。
       speculator 同步（两套，按 like/dislike 分流，见 §交互·2）：
         like-add→interest confirm；dislike-add→avoidance confirm；
         like-remove→interest reject；dislike-remove→avoidance reject/cooldown。
       返回 {ok, target, effective}。"""

def get_overrides(self) -> ProfileOverrides: ...
```

> 三处重建全量覆盖落点（`engine.py:259/453/529`）**保持不变**——写纯 AI 画像，overlay 读时叠加，天然不被抹掉。

### 读视图统一收口（F3）

```python
# build_profile_view 接收三方输入，统一组装 summary / edit-state，drift 由 raw vs 固定值算
def build_profile_view(raw: OnionProfile, effective: OnionProfile,
                       ov: ProfileOverrides, *, full: bool) -> dict: ...
```

### `MemoryManager`（新增 + 改动）

```python
def load_profile_overrides(self) -> ProfileOverrides   # data/memory/profile_overrides.json，缺失回退空
def save_profile_overrides(self, ov) -> None           # 写盘后 _notify_profile_changed()

# 改动（F4 硬要求）：sync_profile_files 内部统一叠加 overlay 再渲染镜像
def sync_profile_files(self, profile) -> None:
    # 保留现有 dict|OnionProfile 双入参兼容（manager.py:191-195）——先 normalize 再叠加
    onion = profile if isinstance(profile, OnionProfile) else OnionProfile.from_dict(profile)
    effective = apply_overrides(onion, self.load_profile_overrides())
    # 渲染 soul_profile.md/.json 用 effective；所有调用方（重建/init/dislike_writeback）自动一致
```

（`load/save_profile_overrides` 与既有 `load_feedback_state/save_feedback_state` 同构。）

### API（`api/app.py`）

**新增 `POST /api/profile/edit`**

```jsonc
// 请求 ProfileEditIn        op ∈ {set, add, remove, reset}
{ "target": "interest.dislikes", "op": "add", "value": "标题党测评", "parent": "", "weight": null }
// 响应 ProfileEditResponse
{ "ok": true, "target": "interest.dislikes", "edit_state": { /* 全量 edit-state，省一次往返 */ } }
```

**新增 `GET /api/profile/edit-state`（F2）**：返回**未截断**全量可编辑字段 + `overrides` 块 + 每个固定项 `ai_suggestion`（drift）。**编辑模式必须用它**（`profile-summary` 截断，删不到第 13 个兴趣/第 9 个 UP）。

**`GET /api/profile-summary`（展示态，保持不变）**：仅新增 `overrides` 标注（向后兼容纯增字段），不解除截断。

校验：`target` 不在白名单 → 422；空值/超限 → 422；引擎未初始化 → 503（复用 degraded 守卫）。
副作用：写 overlay → 记一条 cognition（来源标签新增 `manual`/"手动编辑"）→ `_notify_profile_changed()` 触发 `profile_updated` WS → 另一端自动刷新。

### 校验与限额
trim 后空 → 拒；大小写+空白归一去重（复用 `_normalize_text`）；`personality_portrait ≤ 1200`、列表项 `≤ 40`、`life_stage/current_phase ≤ 200`；list add 集 `≤ 30`；标量 clamp `[0,1]`；幂等（add 已存在/remove 不存在 → no-op ok）。

## 与现有子系统的交互

1. **推荐抑制（需显式接线，F1）**
   - **自动生效**（走 `get_profile().preferences.disliked_topics`）：`recommendation/engine.py:87`、`discovery/strategies/_utils.py:383`、`refresh.py` 发现/排序路径（401/450/698/844/930 经 `get_profile()`）。
   - **必修①**：delight 硬过滤 `refresh.py:666 _load_disliked_topic_phrases()` 直接读 raw preference → 改走 `get_effective_disliked_topics()`（**base-then-overlay，remove 最后生效**，避免被 raw preference 反向打穿，F6）。审计所有 raw `disliked_topics` 读取方（附录），统一走有效来源。
   - **必修②**：手动 `interest.dislikes` add 时，`apply_user_edit` 复用 `purge_pool_for_new_dislikes(...)`（`dislike_writeback.py`）清已入池命中内容；否则池里旧内容不消失。
2. **Speculator 一致性（两套独立生命周期，F7）**
   - 正向（interest）`soul/speculator.py`：like-add 命中活跃猜测 → `user_confirm_speculation(domain)`；like-remove → `user_reject_speculation(domain, cooldown_days=30)`。
   - 负向（avoidance）`soul/avoidance_speculator.py`：**dislike-add 命中活跃避雷猜测 → `user_confirm_avoidance(domain)`（否则避雷卡片继续出现）；dislike-remove 命中 → `user_reject_avoidance(domain, cooldown_days=30)`**。
   - 两 speculator 分别挂在 soul_engine 上；既有 `avoidance-probes/respond`（`app.py:3406`）走同一套方法，沿用 getattr 防御式调用。
3. **活动流**：每次编辑记 cognition（`save_cognition_updates`，`source="manual"`）。
4. **两端刷新**：复用 `_notify_profile_changed` → `profile_updated` WS。
5. **重建不打架**：读时叠加保证用户视角稳定；Phase 4 可选软回灌让 AI 推断逐步对齐。

## 前端设计（两端）

两端复用 `POST /api/profile/edit` + 编辑后"重拉 **edit-state** 全量重渲染"；CSS 复用 `.chip-list/.action-button/.action-primary/.chat-input`。统一**「编辑画像」开关**：

- **chip 列表**：每 chip ✕ 删除；尾部"＋ 添加"输入。
- **兴趣树**：领域/细分各带 ✕；"＋ 添加领域/细分"；权重滑块 v1 可不暴露。
- **长文/人生阶段/当前阶段**：铅笔 → `<textarea>` + 保存/取消 → `op:set`。
- 每个被编辑字段显示"已编辑 · 恢复 AI 建议"（`op:reset`）；固定文本检测到 `ai_suggestion` 漂移 → "AI 想更新此项 →"。

落点：插件 `popup.js:renderProfileSummary()`（`#viewProfile`）+ `popup-api.js:submitProfileEdit()/fetchEditState()`；Web（含 `/m`）`web/js/views/profile.js` + `web/js/api.js` 同名方法。
**编辑模式数据源 = `GET /api/profile/edit-state`（全量），不是 summary。**

## 安全
沿用现有姿态：默认绑 `127.0.0.1`、API 无鉴权（仅 degraded 守卫）。画像编辑是写接口，公网暴露风险与 **#45 同源**——`--host 0.0.0.0` 部署时任何人可改画像。本期**不**自带鉴权，文档显著交叉提示 #45（前端访问密码），鉴权由 #45 统一做。

## 分阶段实施

- **Phase 1 — 后端覆盖层核心**
  - `overrides.py`（结构 + `apply_overrides` + `apply_edit`）
  - `MemoryManager.load/save_profile_overrides`
  - **F4**：`sync_profile_files()` 内部叠加 overlay + 文件镜像测试
  - **F3**：`get_profile()` 叠加 + `get_raw_profile()` + `build_profile_view(raw, effective, ov)`
  - **F1**：`get_effective_disliked_topics()`；改 `refresh.py:666` 等 raw 硬过滤读取方；`apply_user_edit` dislike-add 触发 `purge_pool_for_new_dislikes`
  - `apply_user_edit`（含 speculator 同步、cognition 记录）
  - **F2**：`GET /api/profile/edit-state`（全量）；`POST /api/profile/edit`；`profile-summary` 增 `overrides` 标注
  - 完成后即可 curl 编辑 + 验证"重建不覆盖"+"删/拉黑改推荐"+"清池"。
- **Phase 2 — 插件编辑 UI**（`#viewProfile` 编辑模式，源 edit-state）
- **Phase 3 — Web/移动编辑 UI**（`profile.js` 编辑模式，一份覆盖 `/web`+`/m`）
- **Phase 4 — 打磨（可选）**：统一 effective memory（让 cognition/dialogue/manager 摘要也读有效画像）、软学习信号回灌、风格/探索滑块、撤销 toast、MBTI 重置、漂移提示完善。

（Phase 2/3 共用 Phase 1 API，可并行。）

## 测试策略

**`apply_overrides` / `apply_edit`（纯函数，重点）**
- text-pin 固定/解除；list add/remove/reset；标量 clamp；校验（空/超限/未知 target/去重）。
- **抗重建**：编辑 → 模拟重建（写全新纯 AI 画像入 soul.json）→ `get_profile()` 仍反映编辑。
- **remove 持续抑制**：remove 后新 AI 画像又含该项，有效画像仍不含。
- **dislike 传导**：加 `interest.dislikes` → `effective.preferences.disliked_topics` 含该项。

**F1 — 有效 dislikes + 清池**
- 手动 add dislike 后，`get_effective_disliked_topics()` 含该项；改造后的 delight 硬过滤能命中。
- 手动 add dislike 触发 `purge_pool_for_new_dislikes`（mock 池，断言被调用 / 清除条数）。

**F6 — base-then-overlay 不被打穿**：raw preference 已有 "标题党"，用户 `op:remove` 移除 → `get_effective_disliked_topics()` **不含** "标题党"（验证 remove 最后生效，不被 raw union 反向加回）。

**F7 — 两套 speculator 同步**：活跃避雷猜测命中时 dislike-add → `user_confirm_avoidance` 被调；活跃正向猜测命中时 like-add → `user_confirm_speculation` 被调；对应 remove → reject。

**F2 — 全量 edit-state**：>12 likes / >8 favorite_up / >6 traits 时 `edit-state` 全返回；`summary` 仍截断。

**F3 — drift**：raw 当前值 ≠ 固定值 → `ai_suggestion` 出现；相等 → 不出现。

**F4 — 镜像**：编辑 → 重建（写 raw 入 soul.json）→ `sync_profile_files` 产出的 `soul_profile.md/.json` 含用户编辑。

**SoulEngine/MemoryManager/API**：overrides 读写与缺失回退；`apply_user_edit` 落盘 + 触发刷新 + speculator 同步分支；`POST /api/profile/edit` 改盘且 edit-state 反映；422/503 路径；WS `profile_updated`。

**前端**（node --test / 手测）：编辑模式增删改调用正确 op；改完重渲染；"恢复 AI 建议"清除覆盖。

## 文档更新清单（合并前）
- `docs/modules/soul.md`（overlay、`apply_user_edit`、`get_raw_profile`、`get_effective_disliked_topics`、可编辑字段）
- `docs/modules/memory.md`（`profile_overrides.json`、load/save、`sync_profile_files` 叠加）
- `docs/modules/api.md`（`POST /api/profile/edit`、`GET /api/profile/edit-state`、summary 新字段）
- `docs/modules/recommendation.md`（用户 dislike → 有效 dislikes + 清池）
- `docs/changelog.md`
- 架构图：新增"用户覆盖层"数据块（介于画像存储与 `get_profile`/`sync_profile_files` 之间）→ `docs/architecture.md` + `docs/spec.md` §3 + README/README_EN 顶部图
- README CN/EN 📌 highlights（发版时，≤4 条）
- 交叉提示 #45（公网鉴权）

## 已拍板（首轮评审）
- 核心：**读时叠加**，不改 3-4 条重建写路径 ✅
- 范围：风格/探索滑块 **v2**；软回灌 **Phase 4 可选**；鉴权 **交 #45**；编辑响应 **内联返回 edit-state**
- 先把 F1–F4 并入 spec（本版完成），再开 Phase 1

## 附录：`disliked_topics` / raw 层读取方审计

| 读取方 | 位置 | 来源 | v1 处理 |
|---|---|---|---|
| recommendation 排序 | `recommendation/engine.py:87` | `get_profile().preferences` | 自动有效 ✅ |
| 发现关键词构建 | `discovery/strategies/_utils.py:383` | `get_profile().preferences` | 自动有效 ✅ |
| curator 反馈信号 | `recommendation/curator.py:138 build_context()` | **DB 反馈行**（dislike/like/save → `topic_key`/`franchise_key`/`up_mid`），**不接 profile** | manual dislike **不自动进 curator**；靠 profile 级 scoring（`engine.py:87`）+ 清池（F8） |
| delight 摘要 | `recommendation/delight.py:385,661` | 入参 prefs（经 `refresh.py:698 get_profile()`） | 自动有效 ✅ |
| **delight 硬过滤** | `refresh.py:666` | **raw preference** | **改走 `get_effective_disliked_topics()`（F1）** |
| dialogue tone / LLM context | `llm/service.py:527,546-547` | raw soul + preference | v1 不覆盖（Phase 4，F5） |
| cognition cycle | `cognition_cycle.py:208-209,241-242` | raw soul + preference | v1 不覆盖（Phase 4，F5） |
| manager LLM 摘要 | `memory/manager.py:503,520,542,563` | raw preference | v1 不覆盖（Phase 4，F5） |
| 重建/写入内部 | `engine.py`/`pipeline.py`/`layer_updaters.py`/`dislike_writeback.py` | raw（应当） | 保持 raw ✅（生成纯 AI 画像，overlay 在其上） |
