# RedNote / Xiaohongshu Content Provider

`content/providers/rednote/` 保留 strict note/author native schema、canonical `xiaohongshu.com/explore/{note_id}` identity、preview/recommendation/search/card projections 与 presentation descriptor，供已经过可信 ingress 验证的 native payload 使用。

当前 manifest 明确为 `degraded` 且只声明 `projection`。搜索、creator、feed、bootstrap/history、收藏与 mutation 都依赖页面 session、动态签名或 extension task execution，无法由匿名或普通手工 Cookie 稳定重放，因此不接受 credential、不声明 read/action capabilities，也不生成 provider tools。未来 browser-extension 或 managed-browser `AccessMethod` 可以在不改变 downstream projection schema 的情况下解锁能力。

The production graph registers this degraded projection-only provider. Deleted browser automation, Cookie extraction, task execution, and legacy source adapters have no compatibility surface.
