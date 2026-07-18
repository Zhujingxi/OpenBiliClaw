# Douyin Direct-Cookie Discovery Design

## Goal

Add Douyin content discovery sources for the recommendation pool. The first version should use a backend direct-cookie client for discovery-only workloads, while keeping the existing browser-extension path for initialization bootstrap signals.

## Current State

The current code has zero Douyin discovery sources. Douyin support is limited to:

- `dy_tasks` rows of type `bootstrap_profile`
- `openbiliclaw init --yes-douyin`
- `openbiliclaw fetch-douyin`
- event conversion from `dy_post / dy_collect / dy_like / dy_follow` into the shared behavior taxonomy

Those items feed the profile pipeline. They do not enqueue discovery tasks, do not write Douyin discovery candidates to `content_cache`, and do not participate in runtime pool quota.

## Decision

Use a two-path Douyin architecture:

1. **Bootstrap path: extension remains authoritative.**
   `init --yes-douyin` continues to use the extension in a logged-in browser tab. This path imports strong account signals: posted videos, favorites, likes, and followed creators. It avoids persisting a full login cookie in the backend for first-run profile building.

2. **Discovery path: direct-cookie backend client.**
   `discover --source douyin` and the runtime producer may use a cookie supplied by the user, for example through `OPENBILICLAW_DOUYIN_COOKIE`. This path fetches public-ish discovery surfaces: search results, hot board, creator posts, and later related videos.

This split keeps initialization conservative and makes steady-state discovery practical. Discovery needs repeatable background replenishment; opening foreground Douyin tabs for every search cycle would be too disruptive.

## Source Design

The first discovery sources are:

| Source | Purpose | First Phase |
|---|---|---|
| `dy_search` | Soul-generated keywords -> Douyin search results | Yes |
| `dy_hot` | Douyin hot board -> score/filter through the user profile | Yes |
| `dy_creator` | Followed or manually subscribed creators -> recent posts | Yes |
| `dy_related_chain` | Existing aweme seed -> related recommendations | Later |
| `dy_explore` | LLM-generated exploratory domains -> reuse `dy_search` execution | Later |

The pool should account for Douyin as one source family, `douyin`, just as Xiaohongshu is collapsed into one family today. Internal labels still preserve the route through `content_cache.source`, such as `dy-direct-search`, `dy-direct-hot`, and `dy-direct-creator`.

## Open-Source Prior Art

Two projects are useful, but neither should be added as a hard dependency without a wrapper.

### `jiji262/douyin-downloader`

Use this as the P0 implementation reference.

Reasons:

- MIT license.
- It already implements direct cookie use.
- It has `search_aweme()` for keyword search.
- It has `get_hot_search_board()` for hot board snapshots.
- It has user post and favorites collection flows.
- Its API client handles `msToken`, `X-Bogus`, and optional `a_bogus`.

Constraint: the project is primarily a downloader/CLI. Its package layout uses generic top-level names such as `core`, `auth`, and `utils`, so installing it as a library would risk import namespace collisions inside OpenBiliClaw. The safer first step is to implement a small `DouyinDirectClient` in our own package and port only the minimum request/signing logic needed for search/hot/creator smoke.

### `Johnserf-Seed/f2`

Use this as a P1 reference or optional backend.

Reasons:

- Apache-2.0 license.
- Broader endpoint coverage than `douyin-downloader`.
- Explicit support for user posts, following lists, related videos, and home post search.

Constraint: dependencies are heavy and tightly pinned. Directly adding `f2` to the main dependency set would make installation and upgrades more fragile. It is most valuable as an endpoint and response-shape reference, especially for `dy_related_chain`.

## Configuration

Add a dedicated config block:

```toml
[sources.douyin]
enabled = false
mode = "direct"
cookie_env = "OPENBILICLAW_DOUYIN_COOKIE"
daily_search_budget = 30
daily_hot_budget = 5
daily_creator_budget = 20
request_interval_seconds = 2
```

`enabled=false` by default. Users must opt in because direct-cookie discovery stores and uses account cookies in the backend process.

## Data Flow

Manual search flow:

1. User sets `OPENBILICLAW_DOUYIN_COOKIE`.
2. User runs `openbiliclaw discover --source douyin --force`.
3. CLI loads Soul profile.
4. `DyDirectTaskProducer` generates or selects search/hot/creator tasks.
5. `DouyinDirectClient` fetches Douyin pages through signed Web requests.
6. Raw aweme items are normalized into `DiscoveredContent`.
7. Items are evaluated against the Soul profile through the existing discovery evaluator.
8. Kept candidates are written to `content_cache` with `source_platform="douyin"`.

Runtime flow:

1. `ContinuousRefreshController` observes pool deficit for `douyin`.
2. Runtime producer runs if enabled, due, and under daily budget.
3. Producer writes directly into `content_cache`; no extension task queue is required for direct mode.

## Normalized Candidate Shape

Each Douyin aweme candidate maps into:

- `bvid`: `dy:<aweme_id>` to avoid collision with Bilibili IDs.
- `content_id`: raw `aweme_id`.
- `content_url`: `https://www.douyin.com/video/<aweme_id>`.
- `source_platform`: `douyin`.
- `source_strategy`: `dy-direct-search`, `dy-direct-hot`, or `dy-direct-creator`.
- `title`: `desc` or fallback display text.
- `author_name` / `up_name`: `author.nickname`.
- `cover_url`: first available video cover URL.
- `duration`: `duration_ms // 1000` where available.
- `like_count` / `view_count`: best-effort stats when present.

## Error Handling

- Missing cookie: return a user-facing warning and no-op. Do not silently fall back to extension tasks.
- Expired cookie or rejected signature: log reason, return zero candidates, and keep the rest of discovery healthy.
- Empty response with 200 status: treat as possible cookie/signature rejection and surface a diagnostic message.
- Import failure for optional signing helpers: disable direct mode with a clear message rather than failing daemon startup.
- Duplicate aweme IDs: dedupe before LLM evaluation to control cost.

## Testing

Unit tests should cover:

- config parsing and `config-show` output for `[sources.douyin]`
- cookie resolution from env override or extension-synced file without printing cookie values
- aweme normalization into `DiscoveredContent`
- direct client request signing abstraction using mocked HTTP responses
- search/hot/creator source labels and dedupe
- CLI behavior for `discover --source douyin`
- pool quota family collapsing for `source_platform="douyin"`

Smoke tests should run behind an explicit environment gate, for example:

```bash
OPENBILICLAW_DOUYIN_COOKIE='...' \
OPENBILICLAW_DOUYIN_SMOKE=1 \
uv run pytest tests/integration/test_douyin_direct_smoke.py -q
```

## Non-Goals

- Do not replace `init --yes-douyin` with direct-cookie bootstrap in the first phase.
- Do not download media files.
- Do not add `f2` or `douyin-downloader` as mandatory runtime dependencies in P0.
- Do not persist raw cookies into SQLite.
- Do not attempt TikTok parity.
