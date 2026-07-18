# Backend Release Packaging Design

## Background

当前仓库已经具备桌面后端打包的初始骨架：

- `packaging/openbiliclaw.spec` 定义了 PyInstaller 打包入口
- `packaging/build.py` 能在本机执行 PyInstaller 构建
- `packaging/entry.py` 会以桌面应用方式启动本地后端 API

但这套能力还停留在“维护者本地可以打包”的阶段，缺少用户可消费的发布链路：

- 没有针对 tag 的自动构建
- 没有把后端包自动上传到 GitHub Releases
- README 仍以源码安装、Docker 和脚本安装为主
- 插件已经有 release 下载路径，后端没有对应的统一下载入口

用户目标是“不要手动发包”，因此本轮需要把后端打包提升到和插件一致的 Release 分发层。

## Goal

为后端建立一条完整的自动发布链路，满足：

- 推送版本 tag 后自动构建 macOS / Windows 后端包
- 自动创建 GitHub Release 并上传后端附件
- 用户可以像下载插件一样，从 Releases 页面下载后端包
- 不要求维护者手工打包、手工上传、手工发 Release

## Non-Goals

- 本轮不做 macOS notarization / Windows code signing
- 不产出真正安装器（`.dmg` / `.msi` / NSIS 安装向导）
- 不实现自动更新客户端
- 不替换现有 Docker / `install.sh` / 源码安装路径
- 不把插件和后端强行耦合成单一大安装包

## Chosen Approach

采用“**tag 驱动的 GitHub Releases 自动发包**”作为首版方案：

1. 保留现有 `packaging/` 的 PyInstaller 路线
2. 新增独立 release workflow，而不是把发布逻辑塞进现有 CI
3. workflow 在 `push tags: v*` 时触发
4. 分别在 macOS 和 Windows runner 上构建
5. 每个平台输出一个 zip 产物并上传到同名 GitHub Release

这样做的原因：

- 对用户最直观，和插件 release 分发模型一致
- 对维护者动作最少，只需要创建版本 tag
- 对现有仓库侵入小，不需要重做打包技术栈
- 后续如果要补签名、draft release、自动更新，都可以在这条链路上渐进增强

## Release Shape

### Trigger

- 触发源：`git push origin vX.Y.Z`
- 版本名直接来自 tag，例如 `v0.1.1`

### Build Matrix

- `macos-latest`
- `windows-latest`

首版不额外覆盖 Linux，因为当前用户安装诉求集中在桌面分发。

### Artifacts

每个平台产出一个压缩包，命名带平台和版本，例如：

- `OpenBiliClaw-macos-v0.1.1.zip`
- `OpenBiliClaw-windows-v0.1.1.zip`

压缩包内至少包含：

- 主程序产物（`.app` 或 `OpenBiliClaw.exe` 所在目录）
- `config.example.toml`
- 最低限度的运行上下文（PyInstaller 已收集依赖）

## Runtime Expectations

下载后的用户体验保持简单：

- 解压即得可运行产物
- 首次启动时自动生成默认 `config.toml`
- 数据目录、日志目录与可执行文件同级
- 默认仍监听 `127.0.0.1:8420`

这意味着首版后端包更接近“绿色桌面应用”，而不是系统级安装器。

## Workflow Structure

新增一个 release workflow，逻辑拆成以下阶段：

1. 检出代码
2. 安装 Python 和项目依赖
3. 安装 PyInstaller
4. 运行 `packaging/build.py`
5. 验证预期产物存在
6. 组装 zip 文件
7. 创建或更新对应 tag 的 GitHub Release
8. 上传 zip 附件

推荐使用独立 job：

- `build-macos`
- `build-windows`
- `publish-release`

其中发布 job 依赖两个构建 job，避免单平台失败时生成不完整 release。

## Verification Strategy

本轮不做 GUI 级自动化验收，改做“产物存在性 + 内容正确性”校验：

- 打包脚本执行成功
- 预期输出目录存在
- zip 文件存在且命名正确
- 压缩包内包含主程序和 `config.example.toml`

仓库当前基线测试并非全绿，因此发布链路的验收应以新增 workflow / packaging / docs 的定向验证为主，不把现存无关失败混入本轮判断。

## Documentation Changes

本轮需要同步更新：

- `README.md`：增加“后端从 Releases 下载”的入口说明
- `README_EN.md`：同步英文说明
- `docs/modules/cli.md`：如桌面打包影响启动方式或发布说明，补充说明
- `docs/modules/integrations.md` 或 `docs/index.md`：让 release 下载入口可被发现
- `docs/changelog.md`：记录自动化后端发布能力

如果已有插件 release 描述，后端部分应保持术语和下载入口的一致性。

## Risks And Trade-Offs

### Unsigned builds

首版不做签名，因此：

- macOS 可能出现 Gatekeeper 警告
- Windows 可能出现 SmartScreen 警告

这是可接受的首版折衷，但文档必须提前说明，避免用户误判为包损坏。

### PyInstaller platform quirks

PyInstaller 在不同平台的输出形态不同：

- macOS 可能生成 `.app` 与目录并存
- Windows 主要是 `.exe` + 依赖目录

因此 workflow 需要按平台分别验证和压缩，不能假设完全同构。

## Acceptance

- 仓库存在 tag 驱动的 release workflow
- 推送 tag 后自动构建 macOS / Windows 后端包
- GitHub Release 自动生成，并带有后端附件
- README 能明确告诉用户去 Releases 下载后端
- 后端发布不再依赖维护者本地手工打包和手工上传
