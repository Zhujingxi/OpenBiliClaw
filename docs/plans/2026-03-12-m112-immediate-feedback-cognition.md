# 强反馈即时认知更新 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让单条强反馈即时进入 popup 的「阿B 最近新记住了什么」，但不打破现有 3 条反馈才重建偏好/画像的阈值。

**Architecture:** 在反馈成功后增加一条轻量即时认知更新链路，只写 `cognition_updates.json`。现有批处理刷新链继续保留，负责真正的偏好层和画像更新。

**Tech Stack:** Python, FastAPI, pytest

---

### Task 1: 给即时认知更新写失败测试

**Files:**
- Modify: `tests/test_soul_engine.py`

**Steps:**
1. 增加一个测试，验证单条 `comment` 反馈可立即写入 `cognition_updates.json`。
2. 增加一个测试，验证单条 `dislike` 反馈可立即写入 `cognition_updates.json`。
3. 增加一个测试，验证单条 `like` 不会触发即时 cognition update。

### Task 2: 在 SoulEngine 中实现即时认知更新

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`

**Steps:**
1. 新增一个公开入口，例如 `record_immediate_feedback_cognition(...)`。
2. 将 `dislike/comment` 转成轻量 `cognition update`。
3. 对重复 summary 做去重，避免一次反馈反复写同一句。
4. 不修改现有 `process_feedback_batch_if_needed()` 阈值逻辑。

### Task 3: 接到 CLI 与 API 反馈入口

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/api/app.py`

**Steps:**
1. 在反馈成功写库后调用即时认知更新入口。
2. 再继续调用现有 `process_feedback_batch_if_needed()`。
3. 保持已有错误处理不变。

### Task 4: 文档与验证

**Files:**
- Modify: `docs/changelog.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/extension.md`

**Steps:**
1. 更新文档，说明强反馈可即时出现在「阿B 最近新记住了什么」。
2. 运行：
   - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_soul_engine.py -q`
   - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api_app.py -q`
   - `PYTHONPATH=src .venv/bin/python -m pytest -q`
