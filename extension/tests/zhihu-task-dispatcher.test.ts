import test from "node:test";
import assert from "node:assert/strict";

import {
  computeZhihuTaskTimeoutMs,
  isValidZhihuTask,
} from "../src/background/zhihu-task-dispatcher.ts";

test("isValidZhihuTask accepts discovery task types", () => {
  assert.equal(isValidZhihuTask({ id: "hot", type: "hot", max_items: 10 }), true);
  assert.equal(isValidZhihuTask({ id: "feed", type: "feed", max_items: 10 }), true);
  assert.equal(
    isValidZhihuTask({
      id: "creator",
      type: "creator",
      creator_urls: ["https://www.zhihu.com/people/demo"],
      max_items_per_creator: 5,
    }),
    true,
  );
  assert.equal(
    isValidZhihuTask({
      id: "related",
      type: "related",
      related_urls: ["https://www.zhihu.com/question/1"],
      max_items_per_seed: 5,
    }),
    true,
  );
});

test("isValidZhihuTask rejects malformed discovery tasks", () => {
  assert.equal(isValidZhihuTask({ id: "hot", type: "hot", max_items: 0 }), false);
  assert.equal(isValidZhihuTask({ id: "creator", type: "creator", creator_urls: [] }), false);
  assert.equal(isValidZhihuTask({ id: "related", type: "related", related_urls: [] }), false);
});

test("computeZhihuTaskTimeoutMs scales discovery task breadth", () => {
  assert.ok(
    computeZhihuTaskTimeoutMs({
      id: "creator",
      type: "creator",
      creator_urls: ["a", "b", "c"],
    }) > computeZhihuTaskTimeoutMs({ id: "feed", type: "feed" }),
  );
});
