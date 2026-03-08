import test from "node:test";
import assert from "node:assert/strict";

import {
  buildFeedbackPayload,
  buildVideoUrl,
  getPopupState,
  getTabButtonState,
  normalizeRecommendation,
  normalizeProfileSummary,
  validateCommentInput,
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

test("buildFeedbackPayload builds like and dislike payloads", () => {
  assert.deepEqual(buildFeedbackPayload(7, "like"), {
    recommendation_id: 7,
    feedback_type: "like",
    note: "",
  });

  assert.deepEqual(buildFeedbackPayload(8, "dislike"), {
    recommendation_id: 8,
    feedback_type: "dislike",
    note: "",
  });
});

test("validateCommentInput requires non-empty note", () => {
  assert.deepEqual(validateCommentInput(""), {
    valid: false,
    message: "请先写一句你的想法。",
  });

  assert.deepEqual(validateCommentInput("  方向不错  "), {
    valid: true,
    message: "",
  });
});

test("buildFeedbackPayload trims comment note", () => {
  assert.deepEqual(buildFeedbackPayload(9, "comment", "  方向不错，但我想看更深一点。 "), {
    recommendation_id: 9,
    feedback_type: "comment",
    note: "方向不错，但我想看更深一点。",
  });
});

test("normalizeProfileSummary fills stable fallback fields", () => {
  assert.deepEqual(
    normalizeProfileSummary({
      initialized: true,
      personality_portrait: "  喜欢深度分析  ",
      core_traits: ["理性", "好奇"],
      deep_needs: ["理解世界"],
      top_interests: ["国际新闻", "商业案例"],
    }),
    {
      initialized: true,
      personality_portrait: "喜欢深度分析",
      core_traits: ["理性", "好奇"],
      deep_needs: ["理解世界"],
      top_interests: ["国际新闻", "商业案例"],
    },
  );
});

test("getTabButtonState highlights current tab", () => {
  assert.deepEqual(getTabButtonState("recommend", "recommend"), {
    selected: true,
    tabIndex: 0,
  });

  assert.deepEqual(getTabButtonState("profile", "recommend"), {
    selected: false,
    tabIndex: -1,
  });
});
