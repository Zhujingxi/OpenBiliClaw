# vNext 类型化 AI 与 LiteLLM 基础

> 状态：本模块是权威 vNext AI 边界。Profile/feed 在独立 worker 中运行，
> `/api/v1/chat/stream` 通过同一个 `TaskRunner` 直接输出 SSE；运维 CLI 只提供离线
> `eval` 与 `doctor`。现有 Web/extension client 已通过 generated client 消费这些接口。

## 边界

`src/openbiliclaw/infrastructure/ai/` 为 application use case 提供唯一的类型化 AI 边界：

- `TaskSpec[InputT, OutputT]` 固定输入、输出、延迟 lane、稳定模型别名、语义重试、超时和 usage 上限。
- `TaskRunner` 在调用前验证输入，经 PydanticAI 执行并验证输出；`run()` 返回完整 typed output。`stream()` 对 `semantic_retry_limit=0` 的交互任务实时产出 schema-valid typed snapshot，并在结束时运行完整 output validator；需要语义 retry 的任务则按 attempt 缓冲 snapshot，只有最终 validator 接受的 attempt 才对调用方可见，失败 attempt 不泄漏。provider/PydanticAI stream 的进入、迭代与退出固定由一个 producer task 持有，public async generator 只通过单槽 queue 转发 typed snapshot，因此调用方可安全地用 `wait_for(anext(...))` 或在不同 task 继续消费，不会跨 task 退出 AnyIO cancel scope。两条路径共享 timeout、usage、cancellation 与一次 `ai_runs` lifecycle；consumer 关闭或取消会先 cancel 并 join producer，等待 durable failure transition 完成。它不实现 provider fallback、网络重试、限流或本地响应缓存。`CachePolicy.BYPASS` 只转发 LiteLLM 官方请求体指令 `cache: {"no-cache": true}`。
- `LiteLLMModelResolver` 只解析 `obc-interactive` 和 `obc-analysis`，并把 OpenAI SDK transport retry 设为 0。
- `EmbeddingService` 只调用 OpenAI-compatible `/embeddings` 的 `obc-embedding` 别名；缓存 namespace 同时包含别名、实际向量维度和 profile version。
- `AIHealthService` 逐一检查三个稳定别名，只返回 alias、可用性、`healthy/degraded/unavailable` 与有限 reason，不返回 LiteLLM deployment/provider 详情。任一 deployment 健康即 alias 可用；部分失败时显式 degraded。system config/health 另返回可选 `admin_url`，它只来自显式 `OPENBILICLAW_LITELLM_ADMIN_URL`，不从 internal base URL 推导。
- `grounding.py` 提供不调用模型的 Latin token / CJK n-gram grounding primitive，供 production output validator 与离线 evaluator 复用。
- `evaluators.py` 提供四个 versioned Pydantic Evals evaluator；它们从 case metadata 读取约束，不把示例 expected output 当成唯一正确答案。
- `TaskRunnerProfileDeltaAI`、`TaskRunnerBatchAssessor` 与 `TaskRunnerChatResponder` 把 application Protocol 接到共享 runner；router、ChatService 与 responder 各自用同 task context 显式关闭所拥有的 child stream，客户端断开不会把 PydanticAI generator 留给 event-loop finalizer。`TransactionalAIRunRecorder` 在独立短事务中记录 lifecycle，所有同步 SQLite recorder 调用经 worker thread 执行，不阻塞 async event loop；若 cancellation 发生在 start transaction 阻塞期间，runner 会 shield 并等待 start 完成，再保证一次 cancelled terminal transition，不留下 orphan running row。Profile application 在调用 runner 前记录 expected base revision，runner 返回后若 latest 已变化则拒绝陈旧 delta，由后台 job 以 transient conflict 重算，不会把旧 proposal 应用到新画像。
- `SQLAlchemyAIRunRepository` 只记录任务名、别名、状态、provider-neutral usage、错误类型和时间；成功、失败与取消在 usage 已知时都保存累计值。ORM、Alembic 与 repository API 都不存在 input/output payload 通道，因此无需依赖字段名启发式脱敏。

```text
vNext application use cases
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
                   └─ optional public admin_url (navigation only)
```

## 稳定别名

应用代码只允许以下三个别名，不能出现 provider/model deployment 名：

| 别名 | 用途 |
|---|---|
| `obc-interactive` | 低延迟 chat 交互 |
| `obc-analysis` | 画像、关键词、候选评估和推荐解释等后台分析 |
| `obc-embedding` | 专用 embedding endpoint |

Compose 启动后可在 loopback Admin 中添加 provider deployments，并把 model group 精确命名为以上别名；embedding deployment 必须按 embedding 模式配置。provider API key 只进入 LiteLLM 管理面，不写入 OpenBiliClaw 配置或 Compose。要给 Web 客户端一个导航链接，部署者必须显式设置 `OPENBILICLAW_LITELLM_ADMIN_URL`，例如 Docker host 可使用 `http://127.0.0.1:4000/ui`；未设置时 API 返回 `null`。该 public URL 只接受 absolute HTTP(S)，拒绝 userinfo、query 与 fragment；internal `OPENBILICLAW_LITELLM_BASE_URL`、proxy key 和 provider credential 绝不投影为浏览器 URL。远程管理需要部署者先提供 TLS、访问控制与防火墙；OpenBiliClaw 不把 private proxy address 公开化。

## 内置任务与评测数据

`tasks.py` 目前定义五个有生产 caller 的 typed task：`profile_delta`、`keyword_generation`、`candidate_batch_assessment`、`chat_response` 和 `recommendation_explanation`。每个 agent 都注册会触发 PydanticAI output retry 的 task-specific validator：

- profile evidence 输入为稳定 UUID + content，输出 facet 只能引用输入 evidence ID，且不能创建用户 override 或删除未提供/已 override facet；
- keyword 必须在请求 limit 内并大小写不敏感去重；
- batch candidate 输出不含 application-owned assessment row ID，并强制复制输入 content ID / profile revision；每个输入必须恰好出现一次；
- chat input 包含当前消息和最近 30 条已持久化的 typed role/content history；response 必须返回非空、长度受限的 structured content。chat task 固定 `semantic_retry_limit=0` 以保持真实 provider-time streaming，adapter 只接受单调增长 snapshot 并转成真实增量 SSE；最终 validator 成功后才持久化 assistant turn，并把同一个 `ai_run_id` 写入该 turn；
- recommendation 强制 assessment/content/profile identity 一致；grounding 使用 NFKC 归一化后的 Latin word token 与重叠 CJK 2/3-gram，并排除通用推荐话术 n-gram。Latin 的短事实允许一个 meaningful token；CJK 必须覆盖至少一个非通用 shared trigram，或命中两个字符不重叠的 semantic unit。因此已评审中文同义改写可通过，而只复制泛化文案或在无关说明里顺带提到单个“建模”会被拒绝。

对应 Pydantic Evals YAML 位于 `evals/datasets/`。四份 versioned dataset 分别检查画像 evidence/change/concept、关键词 uniqueness/relevance/source-neutral、batch candidate coverage/score range/topic 与推荐 grounding/length/concept；chat 由 application contract 测试覆盖。推荐解释 dataset 的显式 `LLMJudge` 不在 CI 运行。生产 feed 先用 keyword task 规划需要输入的 connector query，再做一次 batch assessment，以 `obc-embedding`/`feed-v1` namespace 的向量计算有界批内语义多样性，最后只为 application policy 已接纳的条目生成解释。推荐解释逐条执行时会在每次外部 model 调用前后检查 job cancellation，取消后不会继续生成剩余解释，也不会提交 feed effect。五个 task 的 alias 固定到各自 lane；UI 可调整 retry/timeout/usage limit，但不能把 analysis task 指向 interactive alias 或反向配置。

## Docker 与密钥

`docker-compose.yml` 和 `docker-compose.prebuilt.yml` 固定 LiteLLM `v1.92.0` 并使用独立 PostgreSQL。Compose 要求：

- `LITELLM_POSTGRES_PASSWORD`：64 位 hex 本地基础设施密码；
- `LITELLM_MASTER_KEY`：`sk-` 开头的本地管理密钥。

一行 Docker installer / `scripts/runtime_bootstrap.py --mode docker` 会在覆盖 credential stage/start/commit/disclosure 的跨进程锁内补齐 `.env`，拒绝 symlink，保留无关条目，用同目录私密临时文件写入、flush/`fsync` 后原子替换，并在支持时同步目录。POSIX 使用 mode `0600`；Windows 在写入前应用并在发布/读取时复核当前用户独占 DACL。手动 Compose 用户必须先生成 `.env`。不要提交该文件。私有 Compose 网络中的 API 与 worker 使用 installer 生成的 proxy key；provider credentials、key rotation 和可选的最小权限 virtual key 均由 LiteLLM Admin 管理，不是 OpenBiliClaw 的 provider 配置面。

`OPENBILICLAW_LITELLM_ADMIN_URL` 不是 credential。Docker/source 部署可在私密 runtime
environment 中设置同一个安全 public URL，使 API/system health 和 Web/extension client 得到一致
导航目标；安装/health output 仍不得打印 private LiteLLM base URL、proxy key 或 provider key。

源码与预构建 Compose 都挂载同一 `litellm/config.yaml` 并使用同一 command/policy；预构建用户必须同时下载 compose 与 policy 文件。镜像固定的是 upstream version tag `v1.92.0`，本项目没有宣称或执行签名验证。

## 验证边界

本模块用 PydanticAI `TestModel` / `FunctionModel`、HTTP mock transport、SQLite repository 和带 evaluator 的 typed Pydantic Evals dataset 做离线测试。默认测试禁止 live model request，且不使用真实 provider 凭据。`/health?model=...` 在真实 LiteLLM 上可能发起 provider 调用，只应用于显式诊断。Compose policy、alias 健康和完整保留 journey 的部署验证分别遵循 [Docker 部署](../docker-deployment.md) 与 [Docker 首次运行 E2E](../e2e/docker-first-run.md)；runbook 是验收步骤，不代表任意环境已经取得 live provider 结果。

LiteLLM per-request cache bypass 依据官方 [Dynamic Cache Controls](https://docs.litellm.ai/docs/proxy/caching#no-cache)，转发 `extra_body={"cache": {"no-cache": true}}`；cache 的实现与所有权仍完全在 proxy。
