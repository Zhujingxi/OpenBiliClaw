# V2EX Content Provider

The target package `content/providers/v2ex/` provides anonymous hot/latest feeds, topic fetch, and public member-topic creator feeds. `HttpxV2EXTransport` uses the scoped `HttpClientFactory` to call the official legacy public topic endpoints, maps Topic ID, member, node, publication timestamp, and reply count at the boundary, and emits a strict `V2EXPage`. V2EX has no official full-text search endpoint, so the manifest does not advertise Search. The canonical URL is `https://www.v2ex.com/t/<id>`, and text cards do not invent media.

The provider retains a `builtin.manual` PAT form, but production Composition currently wires a fail-closed unavailable identity verifier; the supported live path is anonymous public read. The manifest claims only Feed, Fetch, Creator, and Projection and does not claim private history/saved or mutation capabilities.

Production Composition registers `V2EX_MANIFEST` with `V2EXProvider(V2EXClient(HttpxV2EXTransport()))`. Deleted legacy source/API/task code has no compatibility caller.
