# V2EX Content Provider

目标包 `content/providers/v2ex/` 提供匿名 topic search、hot/latest feed、topic fetch 与公开 member topic creator feed。`HttpxV2EXTransport` 通过 scoped `HttpClientFactory` 调用官方 legacy public topic endpoints，在边界映射 Topic ID、member、node、published timestamp 与 reply count 并输出 strict `V2EXPage`；V2EX 无官方全文搜索 endpoint，因此 search 当前读取 bounded hot response。canonical URL 为 `https://www.v2ex.com/t/<id>`，文字卡无虚构媒体。

Provider 声明 `builtin.manual` PAT form 与 provider-owned verifier；当前 manifest 只包含已可由公开 access 执行的只读能力，不宣称 private history/saved 或 mutation。PAT secret 不进入 native payload、projection 或错误。

当前未接入 production Composition，旧 V2EX source/API/task code暂留至 caller cutover。
