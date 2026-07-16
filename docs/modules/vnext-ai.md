# vNext 类型化 AI 与 LiteLLM 基础

> 状态：基础设施已实现，尚未接入生产 composition root、API、runtime、CLI 或前端。当前 v0.3 legacy 模型路由仍是运行时权威。

## 边界

`src/openbiliclaw/infrastructure/ai/` 为后续 application use case 提供唯一的类型化 AI 边界：

- `TaskSpec[InputT, OutputT]` 固定输入、输出、延迟 lane、稳定模型别名、语义重试、超时和 usage 上限。
- `TaskRunner` 在调用前验证输入，经 PydanticAI 执行并验证输出；它只负责语义输出重试，不实现 provider fallback、网络重试、限流或本地响应缓存。`CachePolicy.BYPASS` 只转发 LiteLLM 官方请求体指令 `cache: {"no-cache": true}`。
- `LiteLLMModelResolver` 只解析 `obc-interactive` 和 `obc-analysis`，并把 OpenAI SDK transport retry 设为 0。
- `EmbeddingService` 只调用 OpenAI-compatible `/embeddings` 的 `obc-embedding` 别名；缓存 namespace 同时包含别名、实际向量维度和 profile version。
- `AIHealthService` 逐一检查三个稳定别名，只返回 alias、可用性、`healthy/degraded/unavailable` 与有限 reason，不返回 LiteLLM deployment/provider 详情。任一 deployment 健康即 alias 可用；部分失败时显式 degraded。
- `SQLAlchemyAIRunRepository` 只记录任务名、别名、状态、provider-neutral usage、错误类型和时间。ORM、Alembic 与 repository API 都不存在 input/output payload 通道，因此无需依赖字段名启发式脱敏。

```text
future application use cases
          │ typed input/output
          ▼
TaskSpec + reusable PydanticAI Agent
          │
          ▼
TaskRunner ───────────────► ai_runs (status/timing/usage/error class only)
          │ OpenAI-compatible, SDK retry=0
          ▼
LiteLLM proxy ── routing / fallback / network retry / limits / cache
          │
          └── provider deployments stored in LiteLLM PostgreSQL

EmbeddingService ── POST /embeddings, model=obc-embedding ──► LiteLLM
AIHealthService  ── GET /health?model=<stable-alias> ────────► LiteLLM
```

## 稳定别名

应用代码只允许以下三个别名，不能出现 provider/model deployment 名：

| 别名 | 用途 |
|---|---|
| `obc-interactive` | 低延迟交互与推荐解释 |
| `obc-analysis` | 画像、关键词和候选评估等后台分析 |
| `obc-embedding` | 专用 embedding endpoint |

Compose 启动后在 `http://127.0.0.1:4000/ui` 中添加 provider deployments，并把 model group 精确命名为以上别名；embedding deployment 必须按 embedding 模式配置。provider API key 只进入 LiteLLM 管理面，不写入 OpenBiliClaw 配置或 Compose。Admin 默认仅绑定宿主机 loopback；远程管理需要显式改 Compose 监听地址，并先配置防火墙、TLS 与访问控制。

## 内置任务与评测数据

`tasks.py` 目前定义 `profile_delta`、`keyword_generation`、`candidate_assessment` 和 `recommendation_explanation` 四个可复用 typed task。每个 agent 都注册了会触发 PydanticAI output retry 的 task-specific validator：

- profile evidence 输入为稳定 UUID + content，输出 facet 只能引用输入 evidence ID，且不能创建用户 override 或删除未提供/已 override facet；
- keyword 必须在请求 limit 内并大小写不敏感去重；
- candidate 输出不含 application-owned assessment row ID，并强制复制输入 content ID / profile revision；
- recommendation 强制 assessment/content/profile identity 一致，且解释至少引用一个输入 grounding term。

对应 Pydantic Evals YAML 位于 `evals/datasets/`；每份 dataset 都携带并执行 `EqualsExpected` evaluator，而不是只做 Pydantic shape 检查。当前任务不执行 live provider eval，也没有把这些任务接入 legacy 业务调用。

## Docker 与密钥

`docker-compose.yml` 和 `docker-compose.prebuilt.yml` 固定 LiteLLM `v1.92.0` 并使用独立 PostgreSQL。Compose 要求：

- `LITELLM_POSTGRES_PASSWORD`：64 位 hex 本地基础设施密码；
- `LITELLM_MASTER_KEY`：`sk-` 开头的本地管理密钥。

一行 Docker installer / `agent_bootstrap.py --mode docker` 会在跨进程锁内补齐 `.env`，拒绝 symlink，保留无关条目，用同目录 mode-`0600` 临时文件写入、flush/`fsync` 后原子替换，并在支持时同步目录。手动 Compose 用户必须先生成 `.env`。不要提交该文件。当前后端暂时使用 master key 访问同一私有 Compose 网络；后续 composition/cutover 任务应改发最小权限 virtual key。

源码与预构建 Compose 都挂载同一 `litellm/config.yaml` 并使用同一 command/policy；预构建用户必须同时下载 compose 与 policy 文件。镜像固定的是 upstream version tag `v1.92.0`，本项目没有宣称或执行签名验证。

## 验证边界

本模块用 PydanticAI `TestModel` / `FunctionModel`、HTTP mock transport、SQLite repository 和带 evaluator 的 typed Pydantic Evals dataset 做离线测试。测试禁止 live model request，且不使用真实 provider 凭据。`/health?model=...` 在真实 LiteLLM 上可能发起 provider 调用，只应用于显式诊断。Compose 只验证渲染、policy parity、loopback Admin、必填密钥 fail-closed 和镜像 manifest；本阶段没有启动容器或声明 provider E2E 成功。

LiteLLM per-request cache bypass 依据官方 [Dynamic Cache Controls](https://docs.litellm.ai/docs/proxy/caching#no-cache)，转发 `extra_body={"cache": {"no-cache": true}}`；cache 的实现与所有权仍完全在 proxy。
