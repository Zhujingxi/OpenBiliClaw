# Bilibili Search 412 Fix Design

## Background

The search path used by discovery is currently the main weak point in pool replenishment. Runtime logs show repeated `412 Precondition Failed` responses, which reduces `search` supply and leaves the pool overly dependent on `explore`.

Further debugging against the live Bilibili search page shows that the browser-side app has already moved to the signed WBI search endpoint, while our client is still calling the older unsigned search path. That mismatch is the main reason the current request keeps getting rejected.

## Goal

Make Bilibili search requests more likely to pass anti-bot checks, and make the remaining `412` failures degrade into an isolated search miss instead of dragging down the whole refresh cycle.

## Non-Goals

- Implement a full reverse-engineered anti-bot or signature workflow
- Add new persistent config just for search headers
- Change discovery strategy ranking or recommendation logic in this task

## Approach Options

### Option 1: Failure-only degradation

Catch `412` in `search()` and return `[]`.

Pros:
- Smallest change

Cons:
- Does not improve actual search success rate
- Leaves the pool structurally biased toward `explore`

### Option 2: Browser-like search context plus graceful degradation

For search requests only, send a more realistic search-page `Referer` and a small set of browser headers, keep using the existing auth cookie when present, and treat `412` as a search-specific soft failure.

Pros:
- Improves the chance that search works again
- Keeps the remaining `412` from poisoning refresh latency
- Small enough to fit the current bugfix scope

Cons:
- Still depends on Bilibili’s changing anti-bot behavior

### Option 3: Full anti-bot reconstruction

Add a heavier request-signing and dynamic-cookie flow.

Pros:
- Highest theoretical success rate

Cons:
- Overkill for current scope
- High maintenance and verification cost

## Chosen Design

Use Option 2, but with one adjustment from runtime debugging: switch search to the WBI-signed endpoint instead of trying to rescue the old unsigned one.

### Request behavior

For `BilibiliAPIClient.search()` only:

- Fetch the current WBI image/sub keys from `nav`
- Sign search params using the WBI algorithm
- Call `/x/web-interface/wbi/search/type`
- Set `Referer` to a search page URL derived from the query, for example `https://search.bilibili.com/all?keyword=...`
- Add a small search-specific header set that is consistent with normal browser navigation
- Reuse the existing cookie if present through the shared `httpx` client

The rest of the API client stays unchanged.

### Error handling

Add a search-specific `412` branch:

- If Bilibili search returns `412`, log a concise warning with the query and status
- Return an empty result list from `search()`
- Do not raise `BilibiliAPIError` for this specific case

Other HTTP and application errors keep existing behavior.

This keeps search failures local to the search strategy instead of turning them into broad discovery noise.

### Logging

Add a dedicated warning message for search throttling / anti-bot rejection so logs distinguish:

- search blocked by `412`
- normal API failures

That makes future debugging easier without adding new runtime state.

## Testing Strategy

1. Add a unit test that verifies `search()` uses the WBI search endpoint, signs params, and sends a search-page referer.
2. Add a unit test that verifies `412` during `search()` returns `[]` instead of raising.
3. Keep existing Bilibili API tests green to ensure other endpoints still use the shared client behavior.

## Files Expected To Change

- `src/openbiliclaw/bilibili/api.py`
- `tests/test_bilibili_api.py`
- `docs/modules/bilibili.md`
- `docs/changelog.md`

## Risks

- Bilibili may still reject requests even with a better referer.
- Returning `[]` on `412` hides the error from callers by design; this is acceptable because the user-facing issue is degraded supply, not correctness of a single query.

## Success Criteria

- Search requests use the same signed endpoint family as the browser search page.
- Search requests stop surfacing repeated `412` tracebacks in normal refresh logs for the handled path.
- Discovery continues when search is blocked.
- Search supply has a better chance to recover without adding a large anti-bot subsystem.
