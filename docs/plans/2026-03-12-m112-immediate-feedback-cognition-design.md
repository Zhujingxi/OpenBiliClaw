# 强反馈即时认知更新设计

## 目标

让推荐反馈中的强信号在单条提交后就能出现在 popup「阿B 最近新记住了什么」区域，而偏好层重分析与画像重建仍保持现有 `>= 3` 条反馈阈值。

## 方案

- 保留 `SoulEngine.process_feedback_batch_if_needed()` 现有批处理逻辑，不改大规模偏好/画像刷新阈值。
- 新增一条轻量即时链路：当 `feedback_type` 为 `dislike` 或 `comment` 且 note/topic 足够明确时，直接写一条 `cognition update`。
- CLI `feedback` 与 API `/api/feedback` 在反馈写入成功后都触发这条轻量链路。
- popup 已经在聊天/反馈成功后强制刷新 `profile-summary`，这次后端补上即时 cognition update 后，画像页会在下一次成功交互后立刻看到变化。

## 边界

- `like` 不生成即时 cognition update，避免把弱正反馈刷成噪声。
- 即时链路只写 `cognition_updates.json`，不改 `preference.json`、`soul.json`。
- 仍保留现有批处理链路来做真正的偏好更新和画像重建。

## 验收

- 单条 `comment` 反馈成功后，`/api/profile-summary.recent_cognition_updates` 出现新条目。
- 单条 `dislike` 反馈成功后，`/api/profile-summary.recent_cognition_updates` 出现新条目。
- 单条 `like` 不触发即时 cognition update。
- 现有 `process_feedback_batch_if_needed()` 测试和阈值逻辑保持不变。
