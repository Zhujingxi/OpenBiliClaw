# 📖 OpenBiliClaw 文档导航

> 本页面是项目文档的一站式入口。用户看第一区块就够了；第二区块起面向开发者和贡献者。

## 👤 我是用户

- [项目主页](index.html) — GitHub Pages 首页，桌面安装包 / 一句话安装、插件下载和产品卖点概览
- [常见问题 FAQ](faq.md) — macOS 安全阻挡、插件连不上后端、embedding 配置、手机访问等高频问题
- [GitHub Releases](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) — Latest Release 的 `openbiliclaw-v*` 聚合页，下载浏览器插件 zip 和桌面安装包；维护者通道仍保留 `extension-v*` / `desktop-v*` / `backend-v*`
- [隐私权政策](privacy.md) — 插件数据收集披露与本地优先数据流说明
- [变更日志](changelog.md) — 各版本交付记录
- [Docker 部署指南](docker-deployment.md) — 手动 Docker / docker compose 部署步骤
- [可选 HTTPS 部署](https-deployment.md) — 公网域名 Caddy 自动证书，以及可信 LAN 的 TLS profile / 本地 CA 流程
- [OpenClaw 接入最短指南](openclaw-quickstart.md) — 把 OpenBiliClaw 接进 OpenClaw / AI 编码助手

## 🛠️ 我是开发者 / 贡献者

- [项目规格说明书 (SPEC)](spec.md) — 完整的项目设计与规划
- [架构设计](architecture.md) — 系统架构与模块关系
- [记忆系统设计](memory-design.md) — 多层网状记忆架构详解
- [v0.1 开发任务清单](v0.1-todolist.md) — 当前版本的开发主线
- [技术债清单](technical-debt.md) — 已确认技术债、风险解析、建议治理方向和待确认 TODO 线索
- [新平台来源接入指南](platform-source-integration.md) — 事件抓取、插件任务、discover、配置页、推荐卡、真实 E2E 和发布文档的标准接入流程（含知乎 / Reddit 接入经验沉淀的检查清单）
- [Bangumi 来源文档](modules/bangumi.md) / [接入 Spec](plans/2026-07-17-bangumi-source-spec.md) / [实施计划](plans/2026-07-17-bangumi-source-plan.md) — 官方只读 API、公开收藏初始化、统一 discover、三端体验与验收边界
- [微博来源文档](modules/weibo.md) — 后端匿名访客、search / hot-as-seed / creator 主动发现、统一候选池与明确的无 Cookie / 无写回 / 无初始化边界
- [微博来源可执行契约](platform-source-contract.weibo.toml) / [验收台账](platform-source-acceptance.weibo.md) — `discovery-only` integration level、逐面适用性、排除项与真实验证证据
- [手动端到端联调](manual-e2e.md) — CLI、插件与 SQLite 的真实联调步骤
- [Agent 机器契约 (短)](agent-install.md) — 给 AI 智能体读取的短部署契约,配合 README 的短粘贴语句
- [Agent 部署详细说明](agent-deployment.md) — 给人看的详细版本 + 所有 JSON 事件/错误码/排查表
- [后端自动更新 SPEC](specs/auto-update.md) — 后端源码自动应用、默认关闭的更新开关、git 安全边界与插件商店原生更新边界
- [Chrome Web Store 商店页文案](chrome-webstore-listing.md) — 可直接复制到商店后台的项目入口、安装使用说明和隐私引导
- [主页 SEO 维护指南](seo.md) — Search Console / Bing 提交清单、sitemap / OG / JSON-LD 长期维护要点

## 可视化架构图

- [Soul 模块架构与流程图](diagrams/soul-architecture.html) — Soul 真实写回口、pipeline 输入边界、完整 rebuild 与局部写回路径
- [Soul 更新变化流程图](diagrams/soul-update-flow.html) — 事件来源矩阵、分层路由、典型场景和专属名词注释
- [Recommendation 模块架构与流程图](diagrams/recommendation-architecture.html) — 候选池 readiness、serve 热路径、PoolCurator、MMR 和反馈回流
- [Web HTML 模块架构与流程图](diagrams/web-architecture.html) — `/web` 桌面端、`/m` 移动端、REST hydration、runtime-stream 和用户动作边界
- [Discovery 模块架构图](diagrams/discovery-architecture.html) — 多源发现、刷新调度、评估优化和模块协议边界

## 模块文档

| 模块 | 文档 | 对应代码 | 状态 |
|------|------|----------|------|
| 后端 API | [modules/api.md](modules/api.md) | `src/openbiliclaw/api/` | ✅ durable 对话卡片 action + 待聊列表/count + 双轨冷却附着 + legacy 转发 |
| LLM 多模型支持 | [modules/llm.md](modules/llm.md) | `src/openbiliclaw/llm/` | ✅ v0.3.74 统一结构化 JSON 容错 + Ollama embedding 空凭据静默 |
| B 站接入层 | [modules/bilibili.md](modules/bilibili.md) | `src/openbiliclaw/bilibili/` | ✅ M3 完成 |
| 多源适配层 | [modules/discovery.md](modules/discovery.md#多源适配层) | `src/openbiliclaw/sources/` | ✅ v0.3.x 落地 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / 微博 / 通用 Web 多源 discovery |
| Bangumi 接入 | [modules/bangumi.md](modules/bangumi.md) | `src/openbiliclaw/sources/bangumi*.py` + `runtime/bangumi_producer.py` | ✅ 官方匿名只读 API + 公开收藏 init + search/ranked/latest discovery |
| 微博接入 | [modules/weibo.md](modules/weibo.md) | `src/openbiliclaw/sources/weibo*.py` + `runtime/weibo_producer.py` | ✅ 匿名访客只读 + search / hot seeds→posts / same-round creator discovery |
| 平台来源接入契约 | [modules/source-auth.md](modules/source-auth.md) | `src/openbiliclaw/api/source_auth/` | ✅ 正交登录态契约 + `verify_method` 证据强度 + 一键验证；Bangumi 可选 token、微博纯匿名无凭据 |
| YouTube 接入 | [modules/youtube.md](modules/youtube.md) | `src/openbiliclaw/youtube/` + `src/openbiliclaw/sources/yt_tasks.py` | ✅ init / fetch smoke / Google Takeout 导入 |
| 记忆系统 | [modules/memory.md](modules/memory.md) | `src/openbiliclaw/memory/` | ✅ 完成 |
| 灵魂引擎 | [modules/soul.md](modules/soul.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 内容发现引擎 | [modules/discovery.md](modules/discovery.md) | `src/openbiliclaw/discovery/` | ✅ v0.3.x 多源 + 统一待评估池 + 跨源跨轮 topic 配额 |
| 推荐引擎 | [modules/recommendation.md](modules/recommendation.md) | `src/openbiliclaw/recommendation/` | ✅ v0.3.x 双轴 fatigue + per-group 候选窗口 + reshuffle 0.6s |
| 存储层 | [modules/storage.md](modules/storage.md) | `src/openbiliclaw/storage/` | ✅ SQLite schema + discovery_candidates 待评估池 + pool readiness 计数 |
| 原生保存同步 | [modules/saved-sync.md](modules/saved-sync.md) | `src/openbiliclaw/saved_sync/` | ✅ canonical API + runtime + B 站 direct adapter + 六平台 extension adapter/executor + 三端后端状态驱动保存界面；CLI 可见配置 |
| 灵魂管线架构 | [modules/soul-pipeline-architecture.md](modules/soul-pipeline-architecture.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 浏览器插件 | [modules/extension.md](modules/extension.md) | `extension/` | ✅ 多平台任务桥与行为采集；微博仅显示设置 / 状态 / 推荐卡，不申请站点权限、不派任务、不采行为 |
| CLI 命令参考 | [modules/cli.md](modules/cli.md) | `src/openbiliclaw/cli.py` | ✅ 持续更新 (含 `autostart` / `setup-embedding` / `discover-douyin` / `fetch-bangumi` / `discover-bangumi*` / `discover-weibo*`) |
| 配置参考 | [modules/config.md](modules/config.md) | `config.example.toml` | ✅ 持续更新 (含 `[sources.reddit]`、`[sources.bangumi]`、`[sources.weibo]`、`[autostart]`、`/api/config` 回滚与 `reset_fields`) |
| 局域网密码门禁 | [modules/api-auth.md](modules/api-auth.md) | `src/openbiliclaw/auth_core.py` + `src/openbiliclaw/api/auth.py` | ✅ 可选 `[api.auth]` 密码门禁 + `/api/auth/*` + `set-password` |
| 公网 HTTPS 网关 | [HTTPS 部署](https-deployment.md) | `docker-compose.https.yml` | ✅ 默认关闭的 Caddy 自动证书 + shared-loopback upstream + REST/WebSocket |
| TLS 反向代理 | [modules/tls-proxy.md](modules/tls-proxy.md) | `src/openbiliclaw/tls_proxy.py` | ✅ 默认关闭的 LAN/self-managed HTTPS + 精确 Origin/Host + WebSocket + SAN 检测 |
| 集成适配层 | [modules/integrations.md](modules/integrations.md) | `src/openbiliclaw/integrations/` | ✅ OpenClaw adapter 已接入 |
| 运行时服务 | [modules/runtime.md](modules/runtime.md) | `src/openbiliclaw/runtime/` | ✅ refresh / candidate pipeline / presence gate / autostart / Ollama preflight / degraded boot / runtime-stream / 扩展 E2E 控制事件 / backend tag auto-update |
| 原生保存授权 E2E | [native-save-e2e.md](native-save-e2e.md) | 手动验证 runbook | ⚠️ 仅在明确授权命名 BV 号 / 测试账号后执行平台写入 |
| 六平台原生保存安全 E2E | [testing/six-platform-native-save-e2e.md](testing/six-platform-native-save-e2e.md) | 精确授权、安全结果与手动验证矩阵 | ⚠️ 默认只做 local-only；六平台真实写入必须逐项获得当前授权 |
| 引导初始化 | [modules/init.md](modules/init.md) | `src/openbiliclaw/cli.py`（`run_guided_init`）+ `runtime/init_coordinator.py` + `runtime/init_prereqs.py` | ✅ v0.3.102 共享流水线 + `InitCoordinator` 状态机 + `/api/init*` + 写者门控 + 插件推荐 tab CTA |

## 开发指南

- [贡献指南](contributing.md) — 环境搭建、代码规范、文档更新要求
- [AGENTS.md](../AGENTS.md) — AI 代理开发规则（含文档更新强制要求）
