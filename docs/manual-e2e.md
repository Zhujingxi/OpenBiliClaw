# vNext 手动端到端联调

本 runbook 覆盖新的 `/api/v1`、独立 worker、LiteLLM、Web 与 extension generated clients。

## 1. 启动基础设施

推荐：

```bash
MODE=docker bash scripts/install.sh
docker compose ps
```

在 LiteLLM Admin 配置 `obc-interactive`、`obc-analysis`、`obc-embedding`。
不要在记录或截图中包含 `.env`、bearer token 或 provider key。

## 2. 系统检查

```bash
curl -fsS http://127.0.0.1:8420/api/v1/system/readiness
docker compose logs worker
```

从 `.env` 在本地读取 `OPENBILICLAW_ACCESS_TOKEN`，在 API client 中作为 bearer
使用；不要把它贴进 runbook。确认：

- `/api/v1/settings` 返回 200；
- `/api/v1/system/ai-health` 显示三个 alias 的脱敏状态；
- 不带 bearer 的受保护请求返回 401；
- 错误响应不含 secret、traceback 或 provider payload。

## 3. 来源连接与 bootstrap

按 source manifest 逐个验证 Bilibili、小红书、抖音、YouTube、X、知乎和
Reddit：

1. `GET /api/v1/sources` 检查 capability set。
2. 对有凭据来源调用 configure；response 不应回显 credential。
3. 调用 `/api/v1/onboarding` 启动 retained bootstrap。
4. 浏览器辅助来源通过 generic claim/complete 处理，不调用平台专属 task API。
5. 确认 normalized `ActivityEvent` 已入库。

只测试 manifest 声明支持的 capability；unsupported operation 应不存在，而不是
由 fallback 模拟。

## 4. Evidence profile

等待 `profile_projection` job 完成，确认：

- profile revision 增加；
- facet 包含 `weight`、`confidence`、`evidence_ids`；
- AI proposal 经过 deterministic clamp/dedup/evidence rules；
- user edit 形成 high-confidence override signal；
- 没有 profile JSON 写入。

## 5. Feed 与 feedback

触发 `feed_replenishment`，确认 deficit、source allocation、normalize、dedup、batch
assessment、diversity/novelty admission 均可从记录解释。提交 like/dislike 后再次
补货，确认后续排名变化且原 interaction 保留。

## 6. Library 与 chat

把同一 content 分别加入 favorites 与 watch-later，确认它们是 predefined
collection 上的 `CollectionItem`，没有 native platform mutation。通过 chat SSE
发送消息，确认 chunk、usage、terminal event 与可选 learning signal 正常。

## 7. Worker recovery

在隔离测试环境验证：

- duplicate schedule 保持 idempotent；
- worker restart 恢复 pending run；
- retry 和 cancel 更新应用库 `job_runs`；
- interactive、user-triggered、scheduled priority 顺序正确；
- Huey result 不是 user-visible job status authority。

## 8. 完成条件

完整旅程为：first-run → LiteLLM aliases → source connection → bootstrap → evidence
profile → feed → feedback changes later ranking → chat → local save。Web 与 extension
对同一 Docker backend 执行 generated-client smoke；不恢复 legacy API。
