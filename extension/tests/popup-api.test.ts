import test from "node:test";
import assert from "node:assert/strict";

import {
  appendRecommendations,
  fetchActivityFeed,
  fetchProfileSummary,
  reshuffleRecommendations,
} from "../popup/popup-api.js";

test("reshuffleRecommendations posts to reshuffle endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          items: [
            {
              id: 11,
              bvid: "BV1NEW",
              title: "新的一批",
              up_name: "UPA",
              cover_url: "//i0.hdslb.com/bfs/archive/new-cover.jpg",
              expression: "先给你捞一条新的。",
              topic_label: "",
              presented: false,
            },
          ],
        };
      },
    };
  };

  const result = await reshuffleRecommendations();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/recommendations/reshuffle");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(result, {
    items: [
      {
        id: 11,
        bvid: "BV1NEW",
        title: "新的一批",
        up_name: "UPA",
        cover_url: "https://i0.hdslb.com/bfs/archive/new-cover.jpg",
        expression: "先给你捞一条新的。",
        topic_label: "",
        presented: false,
      },
    ],
  });
});

test("appendRecommendations posts excluded bvids to append endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          items: [
            {
              id: 21,
              bvid: "BV1APPEND",
              title: "追加的一条",
              up_name: "UPB",
              cover_url: "http://i0.hdslb.com/bfs/archive/append-cover.jpg",
              expression: "",
              topic_label: "",
              presented: false,
            },
          ],
        };
      },
    };
  };

  const result = await appendRecommendations(["BV1A", "BV1B"]);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/recommendations/append");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(calls[0].options.body, JSON.stringify({ excluded_bvids: ["BV1A", "BV1B"] }));
  assert.deepEqual(result, {
    items: [
      {
        id: 21,
        bvid: "BV1APPEND",
        title: "追加的一条",
        up_name: "UPB",
        cover_url: "https://i0.hdslb.com/bfs/archive/append-cover.jpg",
        expression: "这条已经进了你的推荐区，点开看看。",
        topic_label: "",
        presented: false,
      },
    ],
  });
});

test("fetchRecommendations normalizes cover urls from the recommend endpoint", async () => {
  globalThis.fetch = async () => ({
    ok: true,
    async json() {
      return {
        items: [
          {
            id: 31,
            bvid: "BV1FETCH",
            title: "初始推荐",
            up_name: "UPC",
            cover_url: "http://i1.hdslb.com/bfs/archive/fetch-cover.jpg",
            expression: "",
            topic_label: "",
            presented: 0,
          },
        ],
      };
    },
  });

  const { fetchRecommendations } = await import("../popup/popup-api.js");
  const result = await fetchRecommendations();

  assert.deepEqual(result, [
    {
      id: 31,
      bvid: "BV1FETCH",
      title: "初始推荐",
      up_name: "UPC",
      cover_url: "https://i1.hdslb.com/bfs/archive/fetch-cover.jpg",
      expression: "这条已经进了你的推荐区，点开看看。",
      topic_label: "",
      presented: false,
    },
  ]);
});

test("fetchActivityFeed loads popup activity summaries", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          live_summary: "正在补候选",
          headline: "阿B 刚记下了你最近更吃深拆",
          items: [],
        };
      },
    };
  };

  const result = await fetchActivityFeed();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/activity-feed");
  assert.equal(calls[0].options.method, "GET");
  assert.deepEqual(result, {
    live_summary: "正在补候选",
    headline: "阿B 刚记下了你最近更吃深拆",
    items: [],
  });
});

test("fetchProfileSummary forwards limit and cursor for cognition history pagination", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          initialized: true,
          recent_cognition_updates: [],
          has_more_cognition_updates: false,
          next_cognition_cursor: "",
        };
      },
    };
  };

  await fetchProfileSummary({ limit: 3, cursor: "6" });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/profile-summary?limit=3&cursor=6");
  assert.equal(calls[0].options.method, "GET");
});
