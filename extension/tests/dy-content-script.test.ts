/**
 * Tests for the Douyin content-script entry's pure helpers.
 *
 * Task 4 completion (the gap I missed in the original commit). The
 * runScope orchestration touches window.scrollBy / setTimeout /
 * postMessage and isn't unit-testable here without elaborate DOM
 * mocks; the chrome-devtools MCP real-extension probe covers that
 * surface end-to-end.
 *
 * Module isolation: zero imports from extension/src/content/xhs/.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  createScrollRoundController,
  douyinDiscoveryExecutionPolicy,
  filterDiscoveryItemsForScope,
  isDouyinSearchResultUrl,
  isValidFeedExecuteMessage,
  isValidScopeExecuteMessage,
} from "../src/content/douyin.ts";

test("isValidScopeExecuteMessage accepts a well-formed scope payload", () => {
  assert.equal(
    isValidScopeExecuteMessage({
      task_id: "t1",
      scope: "dy_post",
      max_items_per_scope: 300,
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    }),
    true,
  );
});

test("isValidScopeExecuteMessage rejects malformed input", () => {
  assert.equal(isValidScopeExecuteMessage(null), false);
  assert.equal(isValidScopeExecuteMessage("string"), false);
  assert.equal(isValidScopeExecuteMessage({}), false);
  // Missing task_id
  assert.equal(
    isValidScopeExecuteMessage({
      scope: "dy_post",
      max_items_per_scope: 300,
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    }),
    false,
  );
  // Unknown scope
  assert.equal(
    isValidScopeExecuteMessage({
      task_id: "t",
      scope: "unknown",
      max_items_per_scope: 300,
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    }),
    false,
  );
  // Wrong type for numeric field
  assert.equal(
    isValidScopeExecuteMessage({
      task_id: "t",
      scope: "dy_collect",
      max_items_per_scope: "300",
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    }),
    false,
  );
});

test("isValidScopeExecuteMessage accepts all four scopes", () => {
  for (const scope of ["dy_post", "dy_collect", "dy_like", "dy_follow"] as const) {
    assert.equal(
      isValidScopeExecuteMessage({
        task_id: "t",
        scope,
        max_items_per_scope: 1,
        max_scroll_rounds: 0,
        max_stagnant_scroll_rounds: 0,
      }),
      true,
      `expected scope=${scope} to validate`,
    );
  }
});

test("isValidFeedExecuteMessage accepts feed payload and rejects malformed input", () => {
  assert.equal(
    isValidFeedExecuteMessage({
      task_id: "feed-1",
      max_items: 10,
    }),
    true,
  );
  assert.equal(isValidFeedExecuteMessage(null), false);
  assert.equal(isValidFeedExecuteMessage({ task_id: "", max_items: 10 }), false);
  assert.equal(isValidFeedExecuteMessage({ task_id: "feed-1", max_items: 0 }), false);
});

test("douyin discovery execution policy is dom first", () => {
  assert.deepEqual(douyinDiscoveryExecutionPolicy(), {
    search: { activeApiBridge: true, passiveFetchTap: true, domInteraction: true },
    hot: { activeApiBridge: true, passiveFetchTap: true, domInteraction: true },
    feed: { activeApiBridge: false, passiveFetchTap: true, domInteraction: true },
  });
});

test("isDouyinSearchResultUrl requires a real search results route", () => {
  assert.equal(
    isDouyinSearchResultUrl(
      "https://www.douyin.com/jingxuan/search/%E7%A7%91%E6%8A%80?enter_from=discover",
      "科技",
    ),
    true,
  );
  assert.equal(isDouyinSearchResultUrl("https://www.douyin.com/jingxuan", "科技"), false);
  assert.equal(
    isDouyinSearchResultUrl("https://www.douyin.com/jingxuan/search/%E7%BE%8E%E9%A3%9F", "科技"),
    false,
  );
});

test("filterDiscoveryItemsForScope keeps only the requested discovery scope", () => {
  const items = filterDiscoveryItemsForScope(
    [
      { scope: "dy_feed", aweme_id: "feed-1", url: "", title: "feed", author: "", author_sec_uid: "", cover_url: "" },
      { scope: "dy_search", aweme_id: "search-1", url: "", title: "search", author: "", author_sec_uid: "", cover_url: "" },
      { scope: "dy_search", aweme_id: "search-1", url: "", title: "duplicate", author: "", author_sec_uid: "", cover_url: "" },
    ],
    "dy_search",
    5,
  );

  assert.equal(items.length, 1);
  assert.equal(items[0]!.scope, "dy_search");
  assert.equal(items[0]!.aweme_id, "search-1");
});

test("filterDiscoveryItemsForScope merges duplicate hot metadata", () => {
  const items = filterDiscoveryItemsForScope(
    [
      {
        scope: "dy_hot",
        aweme_id: "hot-1",
        url: "https://www.douyin.com/video/hot-1",
        title: "热点",
        author: "作者",
        author_sec_uid: "",
        cover_url: "",
        like_count: 10,
      },
      {
        scope: "dy_hot",
        aweme_id: "hot-1",
        url: "https://www.douyin.com/video/hot-1",
        title: "热点",
        author: "作者",
        author_sec_uid: "",
        cover_url: "",
        hot_word: "热点词",
        sentence_id: "2495363",
        seed_aweme_id: "7652229189183427849",
        like_count: 20,
      },
    ],
    "dy_hot",
    1,
  );

  assert.equal(items.length, 1);
  assert.equal(items[0]!.hot_word, "热点词");
  assert.equal(items[0]!.sentence_id, "2495363");
  assert.equal(items[0]!.seed_aweme_id, "7652229189183427849");
  assert.equal(items[0]!.like_count, 10);
});

test("createScrollRoundController continues while the item count grows", () => {
  const controller = createScrollRoundController({
    roundCap: 10,
    stagnantLimit: 2,
    maxItems: 100,
  });

  assert.equal(controller.shouldContinue(0), true);
  assert.equal(controller.shouldContinue(5), true);
  assert.equal(controller.shouldContinue(9), true);
  assert.equal(controller.roundsExecuted(), 3);
});

test("createScrollRoundController stops after two consecutive stagnant rounds", () => {
  const controller = createScrollRoundController({
    roundCap: 10,
    stagnantLimit: 2,
    maxItems: 100,
  });

  assert.equal(controller.shouldContinue(3), true);
  assert.equal(controller.shouldContinue(3), true); // 1st stagnant round
  assert.equal(controller.shouldContinue(3), false); // 2nd stagnant round → stop
  assert.equal(controller.roundsExecuted(), 2);
});

test("createScrollRoundController resets the stagnant streak on growth", () => {
  const controller = createScrollRoundController({
    roundCap: 10,
    stagnantLimit: 2,
    maxItems: 100,
  });

  assert.equal(controller.shouldContinue(3), true);
  assert.equal(controller.shouldContinue(3), true); // stagnant once
  assert.equal(controller.shouldContinue(7), true); // growth resets streak
  assert.equal(controller.shouldContinue(7), true); // stagnant once again
  assert.equal(controller.shouldContinue(7), false); // stagnant twice → stop
});

test("createScrollRoundController stops at the round cap", () => {
  const controller = createScrollRoundController({
    roundCap: 3,
    stagnantLimit: 2,
    maxItems: 100,
  });

  assert.equal(controller.shouldContinue(1), true);
  assert.equal(controller.shouldContinue(2), true);
  assert.equal(controller.shouldContinue(3), true);
  assert.equal(controller.shouldContinue(4), false); // cap reached
  assert.equal(controller.roundsExecuted(), 3);
});

test("createScrollRoundController stops once maxItems is reached", () => {
  const controller = createScrollRoundController({
    roundCap: 10,
    stagnantLimit: 2,
    maxItems: 5,
  });

  assert.equal(controller.shouldContinue(2), true);
  assert.equal(controller.shouldContinue(5), false);
  assert.equal(controller.roundsExecuted(), 1);
});
