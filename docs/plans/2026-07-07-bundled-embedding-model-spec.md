# Bundled Embedding Model Spec — 把 bge-m3 打进交付物,消灭装机时的模型下载

状态:定稿(经 codex 3 轮对抗 review,判定 IMPLEMENTABLE,零 blocker) · 日期:2026-07-07 · 作者:Claude

> 本文已把三轮 review 的结论合并为单一契约(旧的分层 Revision 段已折叠)。评审收敛记录见文末附录。

## Goal

让"向量模型缺失"这一整类装机失败在两条交付路径上彻底消失:

1. **Docker**:把 bge-m3 权重烤进镜像,容器启动零 `ollama pull`,离线可用。
2. **桌面安装包**:同一版本发布两个变体 —— `with-embedding`(预置权重,开箱即用/离线)与 `lean`(精简,首启按现有逻辑下载),在 Release 页由用户自选。

真实约束(实测):bge-m3(ollama `bge-m3:latest`)= **~1.1GB**。这是一切取舍的核心。

## Scope

**In scope**
- Docker sidecar 镜像内置 bge-m3(build-time 烤入 + 首启把权重播进持久卷)。
- 桌面 `with-embedding` 变体:预置 ollama blobs+manifest,首启前播种,使 embedding 直接就绪、跳过下载。
- `release-desktop.yml` 每 OS 产 lean + with-embedding 两资产;`release-docker.yml` 产内置模型的 ollama 镜像。
- 聚合 Release 页(`sync-aggregate-release.sh`)列出两桌面资产 + 选择指引 + 体积。
- 文档同步。

**Out of scope**(记录为后续,避免范围蔓延)
- 不改 embedding 运行时(仍是 ollama;不引入 ONNX 替代)。
- 不做权重镜像源切换(gitee/Release 托管权重 + 续传)——lean 与 git 安装仍面临,列为独立后续(见文末 Deferred)。本 spec 用"打进包"绕过而非修下载源。
- 不改 git / 一键脚本安装(源码装,烤 1.1GB 进 git = 灾难);继续首启下载。
- 不换默认 embedding 模型。

## Background — 现状(据实,含锚点)

- 桌面包已打 **ollama 运行时**,但从不打模型权重。两条路径不同:**mac** 由 `packaging/build.py` `bundle_ollama_binary` 打包(连 `llama-server`+dylib);**Windows** release 走 `--no-bundle-ollama`,由 `release-desktop.yml` 单独下载 `ollama-windows-amd64.zip`(pin `0.30.6`)、拷 `ollama.exe`+`lib/`、删 CUDA/ROCm runner,再由 Inno(`openbiliclaw.iss`)封装。**给 Windows 加东西改 workflow/Inno,不是改 `build.py`。**
- 首启下载:`packaging/entry.py:468-519` `_ensure_embedding_model_async()` → `pull_ollama_model(base_url,"bge-m3",...)` → `POST {root}/api/pull`,源是默认 ollama registry(`llm/ollama_diagnostics.py:341-400`,无镜像)。启动顺序:`_packaged_ollama_preflight()`(起 `ollama serve`)**先于**该函数(`entry.py:953/957`)。preflight 检测到 ollama 已在跑则**跳过**(`entry.py:458`)。
- 模型落盘:`<root>/models/blobs/sha256-*` + `<root>/models/manifests/registry.ollama.ai/library/bge-m3/latest`。`root` 优先级 `managed_models_dir()` → `$OLLAMA_MODELS` → `~/.ollama`(`ollama_diagnostics.py:161-166`)。
- managed dir:`ollama_models_relocation_candidate()` = Win `%PROGRAMDATA%\OpenBiliClaw\ollama-models` / 其它 `~/.openbiliclaw/ollama-models`,**路径含非 ASCII 时返回 None**(CJK 守卫;注意 mac/linux 中文用户名下 home 即非 ASCII → None);`managed_models_dir()` 仅在该目录已存在时返回;`_ollama_start_serve_background()` 用 `env.setdefault("OLLAMA_MODELS", ...)` 注入,**目前不设 `OLLAMA_HOST`**(`ollama_supervisor.py:114-155`)。一键修复对外部(非本进程拉起的)daemon 明确拒绝(`app.py` `restart_managed_ollama...` → `external_ollama`)。
- Docker:后端镜像不含 ollama;sidecar `ollama/ollama:latest` entrypoint 启动时 `ollama list | grep bge-m3 || ollama pull bge-m3`,落命名卷 `openbiliclaw_ollama:/root/.ollama`。`release-docker.yml` 只构建后端多架构镜像到 GHCR(`IMAGE=openbiliclaw-backend`)。
- 发布:`release-desktop.yml`(`desktop-v*`)mac 传 `--archive-version {ver}-arm64` → `OpenBiliClaw-macos-v{ver}-arm64.dmg`;win 由 Inno `OutputBaseFilename` → `OpenBiliClaw-windows-{ver}-Setup.exe`。`sync-aggregate-release.sh` **删全部包资产再传当前**到聚合 tag(`:274/284/332`)。GitHub 单资产上限 < 2GiB(仓库未记录)。

## Contract

### C1 — Docker:镜像自带模型,离线开箱,失败响亮

- 新增内置模型镜像 `ghcr.io/whiteguo233/openbiliclaw-ollama:{version}`(+`:latest`),build-time 已含 bge-m3 快照,**烤在 `/opt/bge-m3-seed/`**(不在 `/root/.ollama`,避免命名卷遮盖)。
- 镜像内置**独立 shell seeder** `docker/seed-bge-m3.sh`(用 `sha256sum` 逐 blob 校验)——**不依赖** OpenBiliClaw 的 python(sidecar 镜像里没有)。
- entrypoint:目标模型目录缺 bge-m3 → 从 `/opt/bge-m3-seed/` shell 播种(逐 blob 校验)→ `ollama serve`。**播种失败 → healthcheck 明确 unhealthy + 打印种子完整性诊断**;网络 pull 兜底改**显式 opt-in**(`OPENBILICLAW_OLLAMA_ALLOW_PULL=1`),默认不隐式触网(离线镜像 = 要么开箱即用、要么响亮报错,绝不静默降级)。
- compose 两文件的 sidecar 换 `image: ...openbiliclaw-ollama:${OPENBILICLAW_VERSION:-latest}`(静态文件无法硬编码版本),healthcheck/`depends_on` 语义不变。
- 多架构:GGUF blob 架构无关,`linux/amd64`+`linux/arm64` 各层内嵌同一份权重。

### C2 — 桌面:两个变体,with-embedding 走私有托管 Ollama

- `lean`(默认,不设旗标):**行为与今天逐字一致**;新增播种代码全程 no-op。
- `with-embedding`:embedding **一律走本应用自起的私有 Ollama 实例**,不依赖、不写、不指望任何外部/官方 Ollama 的 store。启动顺序固定,且**早于**任何 `ollama serve` / `_ensure_embedding_model_async` / `create_app`:
  1. 算 **effective 模型目录**(§Effective dir);
  2. `seed_embedding_model` 播种进它(§Seeding);
  3. 起**私有 daemon**:`OLLAMA_HOST=127.0.0.1:<free_port>` + `OLLAMA_MODELS=effective 目录`;
  4. 把 embedding `base_url` 改写为 `http://127.0.0.1:<free_port>/v1`;
  5. 就绪检查(既有"已存在则跳过"命中,不下载)。
- 外部官方 Ollama 即便在跑也与本流程无关。**没有**"往运行中的外部 daemon store 播种、指望免重启识别"这类竞态——因为我们先播种、后启动自己的 daemon,daemon 启动即读到齐全目录。
- 诊断/一键修复在私有模式下也经 effective dir + 私有端点收口,不与默认 `11434` 混淆。

### C3 — Release:两资产可选 + 两 docker 镜像 + prune 安全

- 命名(对齐现网):
  - mac:`OpenBiliClaw-macos-v{ver}-arm64.dmg`(lean)/ `OpenBiliClaw-macos-v{ver}-arm64-with-embedding.dmg`(full,`make_archive_name` 追加)
  - win:`OpenBiliClaw-windows-{ver}-Setup.exe`(lean)/ `OpenBiliClaw-windows-{ver}-with-embedding-Setup.exe`(full)——变体名**由 Inno `MyAppVariantSuffix` define 或上传前重命名**产生,改 `build.py` archive 名对 `.exe` 无效。
- CI **分别构建并验证两个变体**,防同名覆盖。
- **两 docker 镜像**:`release-docker.yml` 构建/校验/推送 `openbiliclaw-backend` **和** `openbiliclaw-ollama`;`sync-aggregate-release.sh` 的 docker 就绪需两者都可拉才标绿并都列出。
- **prune 安全**:聚合 prune 改为**只删正在被替换的确切文件名**,或**要求四桌面资产齐全**才同步(缺一不 prune),杜绝某变体构建失败误删上一版完整包。
- 聚合页两桌面变体并列 + 中英选择指引(网络差/离线→with-embedding 含 ~1.1GB 模型;否则→lean 首启自动下载)+ **实测体积**。
- 每资产打包后 `stat` 硬门 > 2GB 直接 fail。

### Effective model dir(单一收口,ASCII + 用户可写)

新增 `effective_embedding_models_dir(cfg, env)`,**播种 / 私有 daemon 的 `OLLAMA_MODELS` / 诊断 root / 一键修复四处统一改用它**,返回**保证存在、纯 ASCII、当前用户可写**的目录,优先级:

1. **合法**的用户显式 `OLLAMA_MODELS`(存在、纯 ASCII、可写)→ 尊重;
2. Windows `%PROGRAMDATA%\OpenBiliClaw\ollama-models`(Inno 安装时创建,ASCII;运行时仍校验可写);
3. 内置用户可写 ASCII 兜底:mac/linux `/var/tmp/openbiliclaw-<uid>/ollama-models`(`<uid>` 数字纯 ASCII,`0700`+锁);Windows ProgramData 不可写时回落 `C:\OpenBiliClaw\ollama-models`;
4. 平台默认 `~/.ollama` **只用于 lean / 非内置**,不作 with-embedding 候选(可能非 ASCII 且非私有)。

取不到合格目录 → 视播种失败、回落网络下载(即 lean 行为)。

### Seeding(共享 blobs 目录的原子播种)

`blobs/` 与 `manifests/` 被所有模型共享,**不能整目录 rename**。契约:
- 逐 blob:拷到 `blobs/.tmp-<digest>` → 校验 sha256 名实相符 → 原子 `rename` 成 `blobs/sha256-<digest>`(已存在跳过,内容寻址天然幂等);
- 全部 blob 就位后**最后**原子写 manifest 作为"提交标记";全程持文件锁;
- 任一 blob 校验失败 → 只清自己的 `.tmp-*`、不写 manifest、**回落网络下载**;**绝不触碰其它模型的 blob**;
- 幂等:目标 manifest+blobs 齐全则 `already_present` 直接跳过。
- 桌面(python `runtime/embedding_seed.py`)与 docker(shell `seed-bge-m3.sh`)**实现同一契约但代码不共享**。

### 版本可复现(digest allowlist)

`ollama pull bge-m3` 拉 `latest` 会移动。契约:一次性记录 manifest + config + 各 layer 的 digest 为 **allowlist**;CI 制种(桌面)与烤镜像(docker)拉取后**逐一比对,漂移即 fail**;播种端也用它校验。

## Verification(每条须有测试或真机 E2E)

- **V1 Docker E2E**:断网 `docker compose up`,sidecar 零 pull → bge-m3 就绪、后端 `embedding_ready=true`;空卷全新起容零 pull;已有卷不重播;**播种失败 → sidecar 明确 unhealthy**(不静默)。
- **V2 桌面 with-embedding E2E**:全新机断网首启 → 私有 daemon 起于 `OLLAMA_HOST` 端口、embedding 直接就绪、无下载、init 不再 `embedding_not_ready`;base_url 指向私有端点。
- **V3 桌面 lean 回归**:与今天逐字一致(首启下载、进度条、路径不变)。
- **V4 完整性回落**:损坏一个预置 blob → 校验失败 → 清理 + 回落下载 → 最终就绪;坏 blob 不进目录、不影响其它模型。
- **V5 幂等**:已有 bge-m3 的机器上首启 → 播种直接跳过。
- **V6 Release**:desktop-v* 四资产齐全;聚合页两变体 + 两 docker 镜像;prune 不互删(dry-run/真发核对)。
- **V7 体积门**:每资产 < 2GB,超限 fail;实测体积入 notes。
- **V8 冷启动 fixture**:只把 seeder 产出目录喂给全新起、`OLLAMA_HOST` 私有端口的 Ollama,断言 `/api/tags` 见 bge-m3 且一次真实 embedding 成功(证明 config blob 齐、root 对)。桌面/docker 各一。
- **V9 四处同源**:`effective_embedding_models_dir` 在 无 env / 合法 env / managed 已存在 / 中文 home / 私有端口占用 五场景下,"播种目录 = daemon 读取目录 = 诊断目录 = embedding base_url 指向实例" 一致。

## Invariants

- I1 lean 运行时行为零变化(未设旗标路径零执行)。
- I2 播种绝不把未校验/半拷贝 blob 留在目标目录;失败即回落。
- I3 播种幂等,不覆盖已有/更新/不同 digest 的同名模型(manifest 存在即跳过),不动其它模型 blob。
- I4 Docker 命名卷持久化不被破坏;seed 只补空缺;失败响亮。
- I5 with-embedding 下 effective dir 纯 ASCII、用户可写;播种/daemon/诊断/修复四处同源。
- I6 with-embedding 资产 < 2GB;聚合 prune 对 lean/full 双资产正确(不互删)。
- I7 redistribution 合规:bge-m3(BAAI/BGE)许可证允许再分发(预期 MIT,实现前二次确认并在 NOTICE/文档标注来源、许可证、digest)。

## Deferred(明确后续,非本 spec)

- 权重镜像源(Release 资产托管 bge-m3 + gitee 镜像 + 续传拉取),修 lean/git 安装的下载可靠性。
- 更小默认 embedding 模型(如 bge-small-zh)以进一步压缩 with-embedding 体积。

## 附录 — 评审收敛记录

经 codex(reasoning=high,只读沙箱验证 repo)3 轮对抗 review:
- **Round 1**:6 BLOCKER + 8 MAJOR(播种时序早于 ollama 启动、外部 ollama 使离线保证失效、`OLLAMA_MODELS` 播 A 读 B、共享 blobs 无法整目录 rename、Windows Inno 命名、聚合 prune 误删、docker seeder 无 python、macOS `-arm64`、CJK 路径 mac 失效…)。
- **Round 2**:11/13 RESOLVED;剩余全指向外部-daemon 状态机 → 塑缩为"私有托管 Ollama"。
- **Round 3**:REMAINING BLOCKERS = None,判定 **IMPLEMENTABLE**。

## 附录 — Task 0 基线(bge-m3:latest,已落实)

许可证:模型内置 license 层实测为 **MIT License**(允许再分发)。redistribution 合规确认 ✅。

Digest allowlist(`ollama pull bge-m3` @ 2026-07-07,总 ~1104 MiB):

| 角色 | digest | 字节 |
|---|---|---|
| config | `sha256:0c4c9c2a325fb1cdafec606e6809cb745f1cb26a6d919994400d27372303e276` | 337 |
| model(gguf) | `sha256:daec91ffb5dd0c27411bd71f29932917c49cf529a641d0168496c3a501e3062c` | 1157671200 |
| license(MIT) | `sha256:a406579cd136771c705c521db86ca7d60a6f3de7c9b5460e6193a2df27861bde` | 1068 |

制种 / 烤镜像拉取后须与此三项逐一比对,漂移即 fail(`make_model_seed.py --expect-digest`)。

## 实现进度

- **Task 0 ✅**:MIT 确认 + digest allowlist 落档(本附录)。
- **Task 1 ✅**:`packaging/make_model_seed.py`(制种 + sha256 校验 + allowlist 比对),真实 bge-m3 制种通过(1104 MiB / 3.4s)。
- **Task 2 ✅**:`src/openbiliclaw/runtime/embedding_seed.py`(`seed_embedding_model` 原子逐 blob + manifest-last + 锁 + 幂等;`effective_embedding_models_dir` ASCII+可写优先级),11 单测 + mypy/ruff 通过。
- **真机 V8 ✅**:制种→播种→私有端口(11500)ollama 零 pull 识别 `bge-m3:latest` 并产出真实 1024 维 embedding。
- **Task 3–8 待做**:桌面私有托管接线 / 打包变体 / Docker 镜像 + shell seeder / 发布流水线 / 文档 / 真机验收。
