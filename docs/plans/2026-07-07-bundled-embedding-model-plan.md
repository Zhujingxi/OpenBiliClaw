# Bundled Embedding Model — Implementation Plan

配套 spec:`docs/plans/2026-07-07-bundled-embedding-model-spec.md`(定稿,经 codex 3 轮对抗 review)。日期:2026-07-07。

> 本 plan 已把三轮 review 结论合并进任务体(旧分层 Revision 段已折叠)。

顺序原则:先做可独立验证的底座(digest 基线 → 制种脚本 → 播种核心 + effective dir 收口),再接 Docker,再接桌面双变体,最后接发布与文档。每个 Task 自带验证。

---

### Task 0: 许可证 + digest allowlist 前置(阻塞项)

- 确认 bge-m3(BAAI/BGE)许可证允许再分发(预期 MIT),在 `NOTICE`/`docs` 标注来源、许可证、digest。
- 联网机 `ollama pull bge-m3`,记录 manifest + config + 各 layer 的 digest 与字节数为 **allowlist**,落档 spec 附录(可复现基线)。
- 验证:allowlist 落档;许可证结论落档。**未过此关不进后续。**

---

### Task 1: 模型快照制备脚本

- 新增 `packaging/make_model_seed.py`:输入 ollama root + 模型名,输出可移植 seed 目录(`blobs/sha256-*` + `manifests/.../bge-m3/<tag>`)+ `seed.manifest.json`(每 blob 的 digest+size)。
- 自校验:先验源 blob sha256 名实相符再拷;拉取产物与 Task 0 allowlist 比对,漂移即 fail。
- 验证:对本机 bge-m3 跑通产出 seed;单测覆盖"blob 名实不符报错""digest 漂移 fail"。

---

### Task 2: 播种核心 + effective dir 收口(桌面 python)

- 新增 `src/openbiliclaw/runtime/embedding_seed.py` `seed_embedding_model(seed_dir, target_dir) -> SeedResult`:
  - 幂等:目标 manifest+blobs 齐全 → `already_present`;
  - 逐 blob 拷 `blobs/.tmp-<digest>` → 校验 sha256 → 原子 rename `blobs/sha256-<digest>`(存在跳过);全部就位后**最后**原子写 manifest;持文件锁;
  - 失败只清自己 `.tmp-*`、不写 manifest、返回 `failed`、**不动其它模型 blob**。
- 新增 `effective_embedding_models_dir(cfg, env)`(spec §Effective dir 优先级),**保证存在、ASCII、可写**;不可得 → 触发调用方回落下载。
- 单测:already_present / 成功 / 坏 blob 回落 / 半拷贝清理 / 目标不可写 / 共享 blobs 目录下不影响既有其它模型 / manifest 最后写 / 锁并发 / effective dir 五场景优先级。
- 验证:纯单测,不依赖真实 ollama。

---

### Task 3: 桌面 with-embedding 运行时(私有托管 Ollama)

- `_ollama_start_serve_background`(或私有变体)增支持 **`OLLAMA_HOST=127.0.0.1:<free_port>`**(探空闲端口 + 处理占用),与 `OLLAMA_MODELS=effective 目录` 一起注入。
- `packaging/entry.py` 新增 `_seed_bundled_embedding_model()`,在**任何 `ollama serve` / `_ensure_embedding_model_async` / `create_app` 之前**执行 spec §C2 五步:算 effective 目录 → 播种 → 起私有 daemon(`OLLAMA_HOST`+`OLLAMA_MODELS`)→ 改写 embedding `base_url` 为私有端点 → 就绪检查。
- lean:无 seed 目录 → 全程 no-op,启动顺序与今天逐字一致。
- 验证(V2/V3/V4/V5/V9):with-embedding 断网首启零下载就绪、base_url 指私有端点;lean 回归零变化;坏 blob 回落;已有幂等;四处同源。

---

### Task 4: 桌面 with-embedding 打包

- `packaging/build.py`:读 `OPENBILICLAW_BUNDLE_EMBEDDING=1`(对齐 `OPENBILICLAW_BUNDLE_X` 风格),把 Task 1 产的 seed 拷进产物资源目录 —— mac `OpenBiliClaw.app/Contents/Resources/bge-m3-seed/`,win 同级 `bge-m3-seed/`;mac archive 名经 `make_archive_name` 追加 `-with-embedding`(现网 mac 名已含 `-arm64`)。
- **Windows 变体名**:Inno 增 `MyAppVariantSuffix` define(lean 空 / full `-with-embedding`)或上传前重命名产物;`%PROGRAMDATA%\OpenBiliClaw\ollama-models` 由 Inno 安装时创建。
- 验证:两变体产物名不冲突;with-embedding 产物含 seed 目录;`stat` < 2GB。

---

### Task 5: Docker 内置模型镜像

- 新增 `docker/ollama-bundled.Dockerfile`:`FROM ollama/ollama:<pinned>`,build 阶段 `ollama serve & ollama pull bge-m3`(与 Task 0 allowlist 比对,漂移 fail),快照到 `/opt/bge-m3-seed/`,清临时 serve。
- 新增 `docker/seed-bge-m3.sh`(shell + `sha256sum` 逐 blob 校验,同 spec §Seeding 契约,**不依赖 python**)。
- entrypoint:缺 bge-m3 → shell 播种 → `ollama serve`;**播种失败 → healthcheck unhealthy + 完整性诊断**;网络 pull 兜底改 `OPENBILICLAW_OLLAMA_ALLOW_PULL=1` opt-in。
- compose 两文件 sidecar → `image: ...openbiliclaw-ollama:${OPENBILICLAW_VERSION:-latest}`。
- 验证(V1/V8):断网起容零 pull 就绪;空卷零 pull;已有卷不重播;播种失败明确 unhealthy;冷启动 fixture(播种后起 daemon 验 `/api/tags`+真实 embedding)。

---

### Task 6: 发布流水线

- `release-desktop.yml`:mac/win 各矩阵 `variant: [lean, with-embedding]`;with-embedding 前先制 seed(actions cache 按模型 digest 键缓存)。上传四资产;`publish` 的 `assets=()`/`--clobber` 含 `-with-embedding`;每资产 `stat` > 2GB fail。
- `release-docker.yml`:构建/校验/推送 `openbiliclaw-backend` **和** `openbiliclaw-ollama` 两多架构镜像。
- `.github/scripts/sync-aggregate-release.sh`:资产识别扩展认 `-with-embedding`;prune 改**只删被替换的确切文件名**或**四桌面资产齐全才同步**;docker 就绪需两镜像都可拉;notes 列两桌面变体 + 两镜像 + 选择指引 + 实测体积。
- 验证(V6/V7):四资产齐全;两镜像可拉;prune 不互删;体积门生效。

---

### Task 7: 文档同步

- README CN/EN 安装区:两变体 + 选择指引;📌 亮点可提"离线完整版"。
- `docs/docker-deployment.md`:内置模型镜像,删"首启拉取 bge-m3",标注离线可用。
- `docs/agent-install.md` / `install.sh` / `install.ps1` 摘要:git 安装仍首启下载,指向 with-embedding 桌面包作国内加速替代。
- 更正过期体积文案 "~568MB" → 实测 ≈1.1GB(`packaging/entry.py:471`、`config.example.toml:182`、`docker-compose.yml:3`)。
- `docs/modules/*`(打包/运行时/init)记录私有 daemon 播种路径与两变体;`docs/changelog.md` 新条目;触发架构图门槛则同步 `architecture.md`/`spec.md`/README 图。

---

### Task 8: 端到端真机验收

- Docker:干净机断网 `docker compose up` → 全绿 + `embedding_ready`;播种失败态 unhealthy 可见。
- 桌面:mac(arm64)+ win 各一台,with-embedding 断网首启就绪(私有端口 daemon)、lean 联网首启走下载,两者最终态一致。
- 回归:lean 与现网 0.3.x 无差异。
- 记录实测体积、首启耗时、断网成功截图,回填 changelog。

---

## 风险与缓解

- **卷阴影(Docker)**:seed 烤 `/opt` 而非 `/root/.ollama`,缺失才播 → 持久化 + 离线两全。
- **CI 时长**:with-embedding 需 1.1GB 制 seed;actions cache 按 digest 键缓存。
- **资产体积**:~1.2GB < 2GB,CI 硬守卫防未来更大模型。
- **prune 误删**:只删被替换文件名 / 四资产齐全门 + 发前 dry-run。
- **坏权重**:sha256 名实校验 + 原子 rename + 回落下载。
- **lean 回归**:未设旗标零执行,单测 + 真机双证。
- **私有端口占用**:端口探测 + 重试;失败回落 lean 下载路径。
