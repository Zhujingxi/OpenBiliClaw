/**
 * Each adapter derives the search query from the result URL (covers Enter,
 * button clicks, suggestion clicks). Extraction follows each adapter's own
 * detectPageType search patterns; a query-less search page returns null so
 * the kernel emits nothing.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { bilibiliAdapter } from "../src/shared/platforms/bilibili.ts";
import { douyinAdapter } from "../src/shared/platforms/douyin.ts";
import { youtubeAdapter } from "../src/shared/platforms/youtube.ts";
import { xiaohongshuAdapter } from "../src/shared/platforms/xiaohongshu.ts";
import { zhihuAdapter } from "../src/shared/platforms/zhihu.ts";
import { redditAdapter } from "../src/shared/platforms/reddit.ts";
import { twitterAdapter } from "../src/shared/platforms/twitter.ts";
import { isDuplicateSearch } from "../src/shared/platforms/search-query.ts";

test("bilibili reads the keyword query param", () => {
  assert.equal(
    bilibiliAdapter.extractSearchQuery?.("https://search.bilibili.com/all?keyword=%E5%8E%86%E5%8F%B2"),
    "历史",
  );
  assert.equal(bilibiliAdapter.extractSearchQuery?.("https://www.bilibili.com/"), null);
});

test("xiaohongshu reads the keyword query param", () => {
  assert.equal(
    xiaohongshuAdapter.extractSearchQuery?.(
      "https://www.xiaohongshu.com/search_result?keyword=%E5%92%96%E5%95%A1",
    ),
    "咖啡",
  );
  assert.equal(
    xiaohongshuAdapter.extractSearchQuery?.("https://www.xiaohongshu.com/search_result?keyword="),
    null,
  );
});

test("douyin reads path-segment search forms and the keyword param", () => {
  assert.equal(
    douyinAdapter.extractSearchQuery?.("https://www.douyin.com/search/%E7%8C%AB"),
    "猫",
  );
  assert.equal(
    douyinAdapter.extractSearchQuery?.("https://www.douyin.com/jingxuan/search/%E7%8B%97"),
    "狗",
  );
  assert.equal(
    douyinAdapter.extractSearchQuery?.("https://www.douyin.com/root/search/x?keyword=%E9%B1%BC"),
    "x",
  );
  // Bare /search/ with no query segment → null (emit nothing).
  assert.equal(douyinAdapter.extractSearchQuery?.("https://www.douyin.com/search/"), null);
});

test("youtube reads the search_query param", () => {
  assert.equal(
    youtubeAdapter.extractSearchQuery?.("https://www.youtube.com/results?search_query=lofi"),
    "lofi",
  );
  assert.equal(youtubeAdapter.extractSearchQuery?.("https://www.youtube.com/results"), null);
});

test("zhihu reads the q param", () => {
  assert.equal(
    zhihuAdapter.extractSearchQuery?.("https://www.zhihu.com/search?type=content&q=%E6%9C%BA%E5%99%A8"),
    "机器",
  );
  assert.equal(zhihuAdapter.extractSearchQuery?.("https://www.zhihu.com/search?type=content"), null);
});

test("reddit reads the q param", () => {
  assert.equal(
    redditAdapter.extractSearchQuery?.("https://www.reddit.com/search/?q=rust"),
    "rust",
  );
  assert.equal(redditAdapter.extractSearchQuery?.("https://www.reddit.com/search/"), null);
});

test("x reads the q param and returns null for query-less /explore", () => {
  assert.equal(twitterAdapter.extractSearchQuery?.("https://x.com/search?q=ai"), "ai");
  // /explore classifies as search but has no query → emit nothing.
  assert.equal(twitterAdapter.extractSearchQuery?.("https://x.com/explore"), null);
});

test("search dedup collapses identical normalized queries within the window", () => {
  const last = { query: "历史", ts: 1000 };
  // Same query, same case, inside window → duplicate.
  assert.equal(isDuplicateSearch(last, "历史", 5000), true);
  // Case/whitespace-insensitive normalization.
  assert.equal(isDuplicateSearch({ query: "lofi", ts: 0 }, "  LoFi ", 500), true);
  // Outside the 10s window → not a duplicate.
  assert.equal(isDuplicateSearch(last, "历史", 12000), false);
  // Different query → not a duplicate.
  assert.equal(isDuplicateSearch(last, "科技", 2000), false);
  // No prior search → never a duplicate.
  assert.equal(isDuplicateSearch(null, "历史", 2000), false);
});
