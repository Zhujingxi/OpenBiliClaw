# Bangumi Content Provider

目标包 `content/providers/bangumi/` 提供匿名 subject search、rank/date feed 与 subject fetch。`HttpxBangumiTransport` 通过 scoped `HttpClientFactory` 调用官方 v0 search/subjects endpoint，映射 type、published date、评分/收藏计数、cover 与 offset cursor，并在 HTTP 边界输出 strict `BangumiPage`；status/network/schema 错误统一安全分类。canonical URL 为 `https://bgm.tv/subject/<id>`，公开读取仅要求 anonymous `READ_PUBLIC`。

Provider 声明 `builtin.manual` PAT form 与 provider-owned verifier，为后续私有 collection capability 提供可信 credential boundary；当前 manifest 不宣称尚未实现的 private collection/history capability。token 只由 CredentialVault resolver 传给 verifier/client identity callback，不进入 projections/status。

当前未接入 production Composition，旧 Bangumi source client/producer 暂留至 caller cutover。
