# Hacker News Content Provider

`src/openbiliclaw/content/providers/hackernews/` implements anonymous reads through the official Hacker News Firebase API. Production Composition registers it as `hackernews`; `builtin.anonymous` grants public-read access without credentials.

## Implemented features

- `top` feed from `/v0/topstories.json`, with provider-owned opaque offset cursors;
- bounded concurrent item hydration from `/v0/item/<id>.json`;
- item fetch by stable Hacker News ID;
- strict native `HackerNewsItem` validation and separate preview, recommendation, search-document, and card projections;
- canonical identity at `https://news.ycombinator.com/item?id=<id>`;
- safe HTML-to-text normalization, deleted/dead item filtering, and typed HTTP failure mapping.

The manifest advertises Feed, Fetch, and Projection only. Hacker News has no official full-text search endpoint, so Search is not claimed. External story URLs remain provider-native metadata; the canonical content reference stays on Hacker News so identity and discussion links are stable.

## Public API

- `HACKER_NEWS_MANIFEST`
- `HackerNewsProvider`
- `HackerNewsClient`
- `HttpxHackerNewsTransport`
- `HackerNewsTransport`
