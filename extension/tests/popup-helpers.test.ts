import test from "node:test";
import assert from "node:assert/strict";

import {
  buildVideoUrl,
  getPopupState,
  normalizeRecommendation,
} from "../popup/popup-helpers.js";

test("buildVideoUrl builds bilibili video url from bvid", () => {
  assert.equal(
    buildVideoUrl("BV1xx411c7mD"),
    "https://www.bilibili.com/video/BV1xx411c7mD",
  );
});

test("normalizeRecommendation fills stable fallback fields", () => {
  const item = normalizeRecommendation({
    id: 7,
    bvid: "BV1popup",
    title: "",
    up_name: "",
    expression: "",
    topic_label: "",
    presented: 0,
  });

  assert.equal(item.title, "未命名推荐");
  assert.equal(item.up_name, "未知UP主");
  assert.equal(item.expression, "这条内容已经进入你的推荐列表，点开看看。");
  assert.equal(item.topic_label, "");
  assert.equal(item.presented, false);
});

test("getPopupState distinguishes offline empty and ready states", () => {
  assert.deepEqual(getPopupState({ online: false, items: [] }), {
    kind: "offline",
    message: "后端未连接，请先运行 openbiliclaw start",
    items: [],
  });

  assert.deepEqual(getPopupState({ online: true, items: [] }), {
    kind: "empty",
    message: "还没有可展示的推荐，先运行 init、discover 或 recommend",
    items: [],
  });

  const ready = getPopupState({
    online: true,
    items: [
      {
        id: 3,
        bvid: "BV1ready",
        title: "讲透城市叙事",
        up_name: "城市观察局",
        expression: "这条会对上你最近那股想把问题想透的劲头。",
        topic_label: "你最近那股想把问题想透的劲头",
        presented: true,
      },
    ],
  });

  assert.equal(ready.kind, "ready");
  assert.equal(ready.items.length, 1);
  assert.equal(ready.items[0]?.bvid, "BV1ready");
});
