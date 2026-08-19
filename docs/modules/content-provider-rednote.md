# RedNote / Xiaohongshu Content Provider

`content/providers/rednote/` retains strict note/author native schemas, canonical `xiaohongshu.com/explore/{note_id}` identity, preview/recommendation/search/card projections, and a presentation descriptor for native payloads validated by trusted ingress.

The current manifest is explicitly `degraded` and advertises only `projection`. Search, creator, feed, bootstrap/history, saved content, and mutations all depend on a page session, dynamic signing, or extension task execution and cannot be replayed reliably with anonymous access or ordinary manually supplied Cookies. The provider therefore accepts no credentials, advertises no read/action capabilities, and generates no provider tools. A future browser-extension or managed-browser `AccessMethod` may unlock capabilities without changing the downstream projection schema.

The production graph registers this degraded projection-only provider. Deleted browser automation, Cookie extraction, task execution, and legacy source adapters have no compatibility surface.
