# Bangumi Content Provider

The target package `content/providers/bangumi/` provides anonymous subject search, rank/date feeds, and subject fetch. `HttpxBangumiTransport` uses the scoped `HttpClientFactory` to call the official v0 search/subjects endpoints, maps type, publication date, rating/collection counts, cover, and offset cursor, and emits a strict `BangumiPage` at the HTTP boundary. Status/network/schema errors are uniformly classified into safe categories. The canonical URL is `https://bgm.tv/subject/<id>`, and public reads require only anonymous `READ_PUBLIC` access.

The provider retains a `builtin.manual` PAT form as a future private-collection boundary, but production Composition currently wires a fail-closed unavailable identity verifier; the supported live path is anonymous public read. The manifest therefore claims no private collection/history capability and no PAT is resolved during public calls.

Production Composition registers `BANGUMI_MANIFEST` with `BangumiProvider(BangumiClient(HttpxBangumiTransport()))`. Deleted legacy source clients/producers have no compatibility caller.
