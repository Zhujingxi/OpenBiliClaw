# Mainline Mypy Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 清掉主业务链 `runtime/api/recommendation/discovery/soul` 的 mypy 错误，不扩展到 `eval/` 和外部 stub 缺失问题。

**Architecture:** 先收紧共享类型契约，再修推荐/发现/灵魂链路里的调用点，最后收边缘层返回类型与可空性。整个过程保持运行行为不变，避免用大面积 `Any` 和 `type: ignore` 假装“修好”。

**Tech Stack:** Python 3.14, mypy strict mode, pytest, Ruff, TypedDict, Protocol

---

### Task 1: 收紧共享类型契约

**Files:**
- Modify: `src/openbiliclaw/soul/tone.py`
- Modify: `src/openbiliclaw/llm/json_utils.py`
- Modify: `src/openbiliclaw/llm/embedding.py`
- Modify: `src/openbiliclaw/recommendation/delight.py`
- Test: `tests/test_tone_profile.py`

**Step 1: 记录当前失败基线**

Run:

```bash
uv run python -m mypy src/openbiliclaw/soul/tone.py src/openbiliclaw/llm/json_utils.py src/openbiliclaw/llm/embedding.py src/openbiliclaw/recommendation/delight.py
```

Expected:
- `soul/tone.py` 报 `object has no attribute get`
- `llm/json_utils.py` 报泛型缺参和 `no-any-return`
- `llm/embedding.py` 报 `no-any-return`

**Step 2: 修 `ToneProfile` 的输入契约**

实现：
- 在 `soul/tone.py` 引入窄化 helper，把 `preference_summary["style"]` 从 `object` 缩到可读字典
- 避免直接在 `object` 上调用 `.get()`
- 保持 `ToneProfile` 仍为共享 `TypedDict`

**Step 3: 修 JSON helper 返回类型**

实现：
- 为 `parse_llm_json_tolerant()` 和 `_salvage_container()` 明确 `dict[str, object] | list[object] | None`
- 避免裸 `dict` / `list`
- 必要时把 `json.loads()` 的结果用局部变量窄化后返回

**Step 4: 修 embedding cache / service 返回类型**

实现：
- 让 `EmbeddingCache.get()` 返回值在 `json.loads()` 后被显式校验为 `list[float]`
- 避免把未知 JSON 直接当 `list[float]`

**Step 5: 对齐 delight scorer 的 embedding 契约**

实现：
- 统一 `recommendation/delight.py` 的 `SupportsEmbedding` 和主 embedding service 的接口预期
- 不新增重复协议定义时，优先复用已有契约或让两者结构兼容

**Step 6: 运行回归**

Run:

```bash
uv run python -m mypy src/openbiliclaw/soul/tone.py src/openbiliclaw/llm/json_utils.py src/openbiliclaw/llm/embedding.py src/openbiliclaw/recommendation/delight.py
uv run python -m pytest tests/test_tone_profile.py
uv run python -m ruff check src/openbiliclaw/soul/tone.py src/openbiliclaw/llm/json_utils.py src/openbiliclaw/llm/embedding.py src/openbiliclaw/recommendation/delight.py tests/test_tone_profile.py
```

Expected:
- 以上目标文件 mypy 绿
- `tests/test_tone_profile.py` 绿

**Step 7: Commit**

```bash
git add src/openbiliclaw/soul/tone.py src/openbiliclaw/llm/json_utils.py src/openbiliclaw/llm/embedding.py src/openbiliclaw/recommendation/delight.py tests/test_tone_profile.py
git commit -m "fix: tighten shared typing contracts"
```

### Task 2: 清理推荐与发现链路的 embedding / tone 类型错误

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Modify: `src/openbiliclaw/recommendation/curator.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `src/openbiliclaw/discovery/strategies/explore.py`
- Test: `tests/test_recommendation_engine.py`
- Test: `tests/test_discovery_engine.py`

**Step 1: 记录当前失败基线**

Run:

```bash
uv run python -m mypy src/openbiliclaw/recommendation/engine.py src/openbiliclaw/recommendation/curator.py src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/strategies/explore.py
```

Expected:
- embedding service 上的 `.embed()` / `.similarity_threshold` 访问报错
- `ToneProfile` 与 `dict[str, str]` 不匹配
- `list[...]` 与 `list[object]` 变型不匹配

**Step 2: 统一 embedding service 的静态类型**

实现：
- 让推荐和发现层持有的 embedding service 字段使用明确协议，而不是 `object | None`
- 在需要可空的路径上先做空值分支，再访问 `.embed()` 和 `.similarity_threshold`

**Step 3: 对齐 `ToneProfile` 使用点**

实现：
- 把 `recommendation/engine.py` 中“看起来像 tone profile 的 dict”改成真正的 `ToneProfile`
- 避免函数注解写 `dict[str, str]`，实际又返回 `ToneProfile`

**Step 4: 修 discovery 的列表/可空问题**

实现：
- `_collect_strategy_results()` 形参从 `list[object]` 收紧到可覆盖 gather 结果的序列类型
- 把 `str | None -> str`、`SupportsStructuredTask | None` 等错误在调用前窄化

**Step 5: 运行回归**

Run:

```bash
uv run python -m mypy src/openbiliclaw/recommendation/engine.py src/openbiliclaw/recommendation/curator.py src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/strategies/explore.py
uv run python -m pytest tests/test_recommendation_engine.py tests/test_discovery_engine.py
uv run python -m ruff check src/openbiliclaw/recommendation/engine.py src/openbiliclaw/recommendation/curator.py src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/strategies/explore.py tests/test_recommendation_engine.py tests/test_discovery_engine.py
```

Expected:
- 目标文件 mypy 绿
- 推荐/发现测试绿

**Step 6: Commit**

```bash
git add src/openbiliclaw/recommendation/engine.py src/openbiliclaw/recommendation/curator.py src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/strategies/explore.py tests/test_recommendation_engine.py tests/test_discovery_engine.py
git commit -m "fix: clean typing in recommendation and discovery"
```

### Task 3: 清理 soul 主流程与 updater 类型错误

**Files:**
- Modify: `src/openbiliclaw/soul/pipeline.py`
- Modify: `src/openbiliclaw/soul/layer_updaters.py`
- Test: `tests/test_profile_pipeline.py` if present
- Test: `tests/test_soul_engine.py` if present

**Step 1: 记录当前失败基线**

Run:

```bash
uv run python -m mypy src/openbiliclaw/soul/pipeline.py src/openbiliclaw/soul/layer_updaters.py
```

Expected:
- `int(object)` / `float(object)` 报错
- 对 `object` 做迭代或构造 `set()` 报错
- updater 返回值过宽

**Step 2: 修 pipeline 的数值窄化**

实现：
- 在进入 `int()` / `float()` 前增加显式类型检查或单独 coercion helper
- 避免把原始 JSON / LLM 结果直接喂给数值转换

**Step 3: 修 layer updater 的集合/返回类型**

实现：
- 为 updater 输入结果定义更窄的本地别名或 helper
- 在返回 `LayerUpdateResult` 前显式构造，而不是把未知 callable 结果直接返回

**Step 4: 运行回归**

Run:

```bash
uv run python -m mypy src/openbiliclaw/soul/pipeline.py src/openbiliclaw/soul/layer_updaters.py
uv run python -m pytest tests/test_profile_pipeline.py tests/test_soul_engine.py
uv run python -m ruff check src/openbiliclaw/soul/pipeline.py src/openbiliclaw/soul/layer_updaters.py tests/test_profile_pipeline.py tests/test_soul_engine.py
```

Expected:
- soul 主流程目标文件 mypy 绿
- 若测试文件存在则全部通过

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/pipeline.py src/openbiliclaw/soul/layer_updaters.py tests/test_profile_pipeline.py tests/test_soul_engine.py
git commit -m "fix: tighten typing in soul pipeline"
```

### Task 4: 收边缘层签名与返回值

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/runtime/updater.py`
- Modify: `src/openbiliclaw/soul/dialogue.py`
- Test: `tests/test_api_app.py`
- Test: `tests/test_refresh_runtime.py`

**Step 1: 记录当前失败基线**

Run:

```bash
uv run python -m mypy src/openbiliclaw/api/app.py src/openbiliclaw/runtime/updater.py src/openbiliclaw/soul/dialogue.py
```

Expected:
- runtime updater 返回 `Any`
- dialogue 工具分发有可空性问题
- api handler 存在少量返回值签名不一致

**Step 2: 对齐返回类型**

实现：
- `runtime/updater.py` 返回字符串的 helper 必须在返回前强制 `str(...)`
- `soul/dialogue.py` 的工具调度路径先判空，再返回明确 `str`
- `api/app.py` 里返回 `JSONResponse` 的 handler 用准确返回注解

**Step 3: 运行回归**

Run:

```bash
uv run python -m mypy src/openbiliclaw/api/app.py src/openbiliclaw/runtime/updater.py src/openbiliclaw/soul/dialogue.py
uv run python -m pytest tests/test_api_app.py tests/test_refresh_runtime.py
uv run python -m ruff check src/openbiliclaw/api/app.py src/openbiliclaw/runtime/updater.py src/openbiliclaw/soul/dialogue.py tests/test_api_app.py tests/test_refresh_runtime.py
```

Expected:
- 目标文件 mypy 绿
- API/runtime 测试绿

**Step 4: Commit**

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/runtime/updater.py src/openbiliclaw/soul/dialogue.py tests/test_api_app.py tests/test_refresh_runtime.py
git commit -m "fix: align typing on runtime edges"
```

### Task 5: 主链总验收

**Files:**
- Verify only

**Step 1: 运行主链 mypy**

```bash
uv run python -m mypy src/openbiliclaw/runtime src/openbiliclaw/api src/openbiliclaw/recommendation src/openbiliclaw/discovery src/openbiliclaw/soul
```

Expected:
- `Success: no issues found` 或等价零错误输出

**Step 2: 运行主链 pytest**

```bash
uv run python -m pytest tests/test_api_app.py tests/test_refresh_runtime.py tests/test_recommendation_engine.py tests/test_discovery_engine.py tests/test_tone_profile.py
```

Expected:
- 全绿

**Step 3: 运行主链 Ruff**

```bash
uv run python -m ruff check src/openbiliclaw/runtime src/openbiliclaw/api src/openbiliclaw/recommendation src/openbiliclaw/discovery src/openbiliclaw/soul tests/
```

Expected:
- `All checks passed!`

**Step 4: Final Commit**

```bash
git add -A
git commit -m "fix: clean typing across the mainline stack"
```
