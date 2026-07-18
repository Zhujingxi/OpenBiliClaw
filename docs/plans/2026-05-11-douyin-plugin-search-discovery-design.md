# Douyin Plugin Search Discovery Design

## Goal

Use the logged-in Douyin browser session to run search discovery when direct-cookie search soft-returns empty results.

## Context

Direct search currently returns HTTP 200 with `status_code=0`, but `data=[]` and `search_nil_info`. Direct hot search returns `word_list` / `trending_list` with `sentence_id`, so it is a seed source rather than a video source. Creator, related, and feed endpoints can return videos; search and hot should use the extension because the page bundle owns the live browser context, request signing, and risk-control state.

## Design

- Add a `search` task type to `dy_tasks`.
- The background dispatcher opens Douyin and asks the existing Douyin content script to run a search task.
- The content script navigates to the search page, asks the MAIN-world harvester to call Douyin search through page `window.fetch`, and falls back to DOM extraction of rendered `/video/<id>` anchors.
- Search task results are stored in `dy_tasks.result_json` as `videos`, but are not converted into bootstrap memory events. They are discovery candidates, not user-history signals.
- Add a standalone CLI command for smoke/debugging: `openbiliclaw search-douyin --keyword 猫 -w 180`.

## Data Flow

CLI -> `DyTaskQueue(type="search")` -> `/api/sources/dy/kick` -> extension background -> Douyin tab -> content script -> MAIN-world search harvester / DOM fallback -> `/api/sources/dy/task-result` -> CLI prints count and previews.

## Open-Source References

- F2 lists Douyin search separately from user posts, feed, and related videos. Search is less reliable than the green-list feed surfaces; hot can bridge through `/hot/{sentence_id}` to a seed video and then the related-video endpoint.

## Hot-related addendum

The implemented hot path is:

1. Backend calls `/aweme/v1/web/hot/search/list/` and keeps rows with `sentence_id`.
2. Backend enqueues `dy_tasks(type="hot")` with `hot_items`.
3. Extension opens `https://www.douyin.com/hot/{sentence_id}` in the logged-in browser.
4. Douyin redirects to `/video/{aweme_id}`; the content script extracts that seed aweme id.
5. MAIN-world bridge signs `/aweme/v1/web/aweme/related/` with page `byted_acrawler.frontierSign()`.
6. Results return as `scope="dy_hot"` and the backend maps them to `dy-plugin-hot-related`.
- Douyin_TikTok_Download_API exposes `GENERAL_SEARCH`, `VIDEO_SEARCH`, and `DOUYIN_HOT_SEARCH` as separate endpoints; hot search is not a video feed.

## Testing

- Unit tests for response parsing and search task validation.
- Unit tests for search result payload shape.
- Extension build/typecheck.
- Live smoke with the installed extension and logged-in Douyin browser.
