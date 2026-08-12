# Douyin Content Provider（目标包，尚未接入生产组合根）

`content/providers/douyin/` 保留严格的 aweme native schema、canonical video identity、purpose-specific projections 与 presentation descriptor。当前只声明 projection；strict native schema、canonical identity、purpose projections 与 presentation descriptor 保留，供未来可重放 `AccessMethod` 解锁 read capabilities。

Provider manifest 为 `degraded` 且只声明 `projection`。旧 direct-cookie 搜索仍依赖随 session 变化的 msToken/X-Bogus/risk-control 状态，不是可安全重放的匿名或 manual credential 能力；推荐 feed、creator、fetch、个人历史、收藏、写操作、Cookie 登录、browser/extension task dispatch 均不属于当前 target provider。未来只有新的可重放 `AccessMethod` 通过独立验证后，才可扩 manifest capability。

Legacy `sources/douyin_*` / `dy_tasks.py` 继续服务当前 production graph，等待 Plans 10–15 caller/composition cutover；本包没有 compatibility facade、双写或网络测试。
