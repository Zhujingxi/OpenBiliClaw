# Platform Source Inspiration Grounding Design

## Context

Google Search pages are not a good grounding backend: the search page is
blocked by robots.txt and is unreliable from the current runtime. Bing pages
and RSS are technically reachable, but the RSS payload declares restrictive
personal RSS-reader use, so it should not become a production backend.

OpenBiliClaw already has multiple platform-specific search paths. The safer
MVP is to reuse enabled platform sources as inspiration grounding only. Search
results become evidence for keyword generation; they do not enter
`discovery_candidates`, do not change pool counts, and do not consume normal
candidate admission paths.

## Decision

When `[discovery].inspiration_search_enabled=true`, the inspiration search
provider chain may include `platform_sources`. That provider chooses from
currently enabled sources and searches only a small subset per probe. The first
MVP supports synchronous providers:

- Bilibili API search, when `[sources.bilibili].enabled=true`.
- YouTube scraper search, when `[sources.youtube].enabled=true`.
- Reddit command backend search, when `[sources.reddit].enabled=true` and the
  configured command backend is ready.

Plugin-backed sources such as Xiaohongshu, Douyin, Zhihu, and Bilibili DOM
fallback remain a later phase because they are asynchronous task queues. They
should be wired through an explicit wait budget later, not hidden inside the
normal dry-run path.

## Selection Policy

The provider uses only enabled sources. It caps each probe to a small number of
platforms, defaulting to two, so one brainstorm branch does not fan out into
every source. It rotates enabled providers per query and skips providers that
recently failed via the existing fallback cooldown behavior.

This is inspiration-only: result titles, URLs, authors, and snippets are mapped
to `ExaPreviewItem`-compatible previews. The curator sees them as grounding
records and may generate platform-specific keywords. No result is inserted into
the candidate or recommendation pool.

## Error Handling

Individual platform failures are logged at debug level and do not fail the
entire inspiration cycle. A probe can return partial evidence from the sources
that did succeed. If all platform sources fail or are disabled, the provider
returns an empty list and the existing chain can continue to Exa / You.com or
fall back locally.

## Testing

Unit tests cover:

- only enabled sources are used;
- Bilibili / YouTube / Reddit rows map to preview items;
- platform source provider rotates a limited number of enabled platforms per
  query;
- provider failures are non-fatal;
- config parses and renders `platform_sources` in
  `[discovery].inspiration_search_backends`.

Manual real test:

```bash
OPENBILICLAW_PROJECT_ROOT=/Users/white/workspace/OpenBiliClaw \
uv run --project /Users/white/workspace/OpenBiliClaw/.worktrees/discovery-inspiration-mvp \
  openbiliclaw keyword-inspiration-dry-run \
  --platform bilibili --platform youtube --platform reddit \
  --kind regular --limit 2 --interest-limit 3
```
