/**
 * Every platform adapter declares which PageTypes are worth measuring
 * dwell on. Video platforms keep `["video"]` (play-state gated); the
 * text/content platforms opt their reading pages in (visibility gated).
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

test("video platforms track dwell only on video pages", () => {
  assert.deepEqual(bilibiliAdapter.dwellPageTypes, ["video"]);
  assert.deepEqual(douyinAdapter.dwellPageTypes, ["video"]);
  assert.deepEqual(youtubeAdapter.dwellPageTypes, ["video"]);
});

test("xiaohongshu tracks dwell on note pages", () => {
  assert.deepEqual(xiaohongshuAdapter.dwellPageTypes, ["note"]);
});

test("zhihu tracks dwell on answers, articles and questions", () => {
  assert.deepEqual(zhihuAdapter.dwellPageTypes, ["answer", "article", "question"]);
});

test("reddit tracks dwell on posts", () => {
  assert.deepEqual(redditAdapter.dwellPageTypes, ["post"]);
});

test("twitter tracks dwell on status pages", () => {
  assert.deepEqual(twitterAdapter.dwellPageTypes, ["status"]);
});
