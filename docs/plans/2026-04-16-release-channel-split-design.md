# Split Release Channels Design

## Background

当前仓库的 GitHub Releases 入口同时承担两类分发诉求：

- 浏览器插件 zip
- 桌面后端包

这两类产物面向的用户动作、变更频率和版本节奏并不一致。如果继续共用同一套 `v*` tag / Release 语义，后续会出现几个问题：

- 用户难以判断自己应该下载哪个附件
- 插件和后端的发布说明混在一起，日志可读性差
- 两边被迫共享同一个版本节奏，独立迭代成本高
- 回滚和兼容性沟通会越来越模糊

用户已经确认采用“同仓库、双发布通道、历史不动”的策略。

## Goal

把插件和后端从“同一个 release 语义”拆成“同仓库下的两条独立 release 通道”，满足：

- 后端只响应 `backend-v*` tag
- 插件只响应 `extension-v*` tag
- 两条 workflow 分别创建各自的 GitHub Release
- 文档下载入口明确区分“插件下载”和“后端下载”
- 现有 `v0.1.0`、`v0.1.2` 历史保持不动，不做迁移或清理

## Non-Goals

- 本轮不拆仓库
- 不整理旧 tag / 旧 Release 历史
- 不引入插件与后端的自动兼容矩阵
- 不新增复杂的发布编排服务
- 不改变当前后端 PyInstaller 技术路线

## Chosen Approach

采用“**同仓库、按 tag 前缀拆分 release 通道**”：

### Backend channel

- 触发 tag：`backend-vX.Y.Z`
- workflow：`release-backend.yml`
- Release 只上传桌面后端包
- 后端桌面包文件名仍保持用户视角的版本格式，例如：
  - `OpenBiliClaw-macos-v0.1.3.zip`
  - `OpenBiliClaw-windows-v0.1.3.zip`

### Extension channel

- 触发 tag：`extension-vX.Y.Z`
- 新增独立 workflow，例如 `release-extension.yml`
- Release 只上传插件 zip
- 插件 zip 文件名保持：
  - `openbiliclaw-extension-v0.1.3.zip`

这种做法的原因：

- 用户面对的下载资产天然分组，不再混淆
- 仍然保留单仓库维护成本低的优势
- workflow 和 tag 语义清楚，后续可继续扩展
- 不需要现在就引入跨仓库同步或兼容矩阵系统

## Version Semantics

tag 前缀只用于“分发通道识别”，不应污染最终产物名和应用版本元数据。

因此本轮需要统一一条规则：

- `backend-v0.1.3` -> 对外显示版本 `v0.1.3`
- `extension-v0.1.3` -> 对外显示版本 `v0.1.3`

也就是说：

- Release tag 保留前缀
- 产物文件名去掉通道前缀，只保留真正版本号
- 桌面后端 bundle metadata 也只写纯版本号 `0.1.3`

## Workflow Changes

### Backend workflow

现有 `release-backend.yml` 需要收紧为后端专用：

- `on.push.tags` 从 `v*` 改为 `backend-v*`
- 继续保留 macOS / Windows 双平台构建
- 打包脚本接收完整 tag，但内部负责提取真实版本
- Release 页面只包含后端 zip

### Extension workflow

新增插件 release workflow，职责只有：

1. 检出仓库
2. 安装 Node 依赖
3. 构建插件
4. 产出插件 zip
5. 校验 zip 存在
6. 创建或更新 `extension-v*` 对应 Release
7. 上传插件 zip

## Script Changes

### Backend build script

`packaging/build.py` 需要支持带通道前缀的 tag：

- `make_bundle_version()` 不能只处理裸 `v*`
- 需要能把 `backend-v0.1.3` 归一化为 `0.1.3`
- 生成 zip 文件名时输出 `v0.1.3`，而不是 `backend-v0.1.3`

### Extension packaging script

`extension/scripts/package.mjs` 需要支持“从 release tag 派生归档版本”的能力：

- 输入可以是 `extension-v0.1.3`
- 输出 zip 仍然是 `openbiliclaw-extension-v0.1.3.zip`
- 保留现有本地开发默认行为，不强制依赖 release workflow

## Documentation Changes

下载入口不能再使用统一的 `releases/latest` 模糊表达。

本轮应改成：

- README 中文：区分插件下载入口和后端下载入口
- README 英文：同步区分两个入口
- `docs/index.md`：导航里说明有两条独立 release 通道
- `docs/changelog.md`：记录“插件 / 后端拆分发布”
- `docs/modules/extension.md`：补充插件 release 分发方式

文案重点不是“最新 release”，而是“去插件 release / 去后端 release”。

## Risks And Trade-Offs

### History remains mixed

旧的 `v0.1.0`、`v0.1.2` 仍然存在，因此仓库历史不会立即变得完全整齐。

这是本轮接受的折衷，因为用户已经明确选择“不整理历史，只改以后”。

### Version mismatch risk

如果 tag 和项目文件内版本号不一致，仍可能造成认知混乱。

本轮先通过脚本归一化和 workflow 约束减少风险；如后续需要，可以再补“tag 版本必须等于 manifest / pyproject 版本”的硬校验。

### More workflows to maintain

拆分后会多一份插件 workflow，但整体复杂度是可控的，且比混合 release 带来的混乱更低。

## Acceptance

- 后端 release 只由 `backend-v*` 触发
- 插件 release 只由 `extension-v*` 触发
- 两类 Release 各自只包含自己的附件
- 文档下载入口明确区分插件和后端
- 旧历史保持不变，新的发布开始按双通道执行
