# M71 Discover Command Design

## Background

`7.1 核心命令` 中的 `openbiliclaw discover` 仍然是 stub，而 `ContentDiscoveryEngine`、多种 discovery strategy、缓存写入和推荐排序链路都已经具备可运行能力。当前缺口不在发现引擎本身，而在 CLI 没有把这条 P0 主流程接出来。

## Goal

把 `openbiliclaw discover` 从占位命令改成真实命令，补平 `7.1` 中尚未完成的 P0 命令，并直接支撑 `10.1` 的“发现流程：discover -> 内容发现 -> 缓存写入”验收。

## Non-Goals

- 不改 `ContentDiscoveryEngine` 的发现逻辑
- 不新增 CLI 参数，如 `--limit` 或 `--strategy`
- 不在 `discover` 中生成朋友式推荐文案
- 不把 `discover` 和 `recommend` 合并

## Command Behavior

### Preconditions

- 运行时配置完整
- 用户画像已初始化

如果画像尚未初始化：

- 命令退出码为 `1`
- 明确提示用户先执行 `openbiliclaw init`

### Main Flow

1. 读取当前 `SoulProfile`
2. 构建 `ContentDiscoveryEngine`
3. 调用 `discover(profile, limit=30)`
4. 引擎负责写入 `content_cache`
5. CLI 展示本次发现摘要与前几条预览

### Output

沿用 `7.2` 已建立的 Rich 输出风格：

- 页面标题：`本次内容发现`
- 摘要表：
  - 发现条数
  - 缓存状态
- 内容预览：
  - 前 5 条
  - 每条显示：
    - 标题
    - `UP 主`
    - `来源策略`
    - `相关性分数`

### Empty State

如果没有发现到内容：

- 退出码为 `0`
- 输出统一空状态提示
- 明确说明当前没有发现到新内容，但命令本身执行成功

## Testing Strategy

CLI 测试覆盖三条主路径：

1. 画像未初始化
2. 发现结果为空
3. 发现成功并展示预览

继续使用 fake soul engine / fake discovery engine，不做真实网络集成测试进入主门禁。

## Files

- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`
- Docs: `docs/v0.1-todolist.md`
- Docs: `docs/modules/cli.md`
- Docs: `docs/changelog.md`

## Acceptance

- `openbiliclaw discover` 不再是 stub
- 成功时能展示发现摘要和内容预览
- 空结果时给出清晰提示且不报错
- 未初始化画像时明确提示先执行 `init`
- 文档和 todo 状态同步更新
