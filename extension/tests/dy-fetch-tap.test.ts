/**
 * Tests for the Douyin MAIN-world fetch-tap.
 *
 * Task 3 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md). The module
 * itself does NOT auto-install on import — installFetchTap is called
 * explicitly by the content-script, so importing here under node:test
 * (no window) does not trigger side effects.
 *
 * Empirical signing / endpoint behaviour was verified against a real
 * douyin.com tab on 2026-05-07 via the chrome-devtools MCP. The
 * URL-classification regex, top-level response keys, and the late-
 * inject timing model all come from that probe — see
 * docs/plans/2026-05-06-douyin-bootstrap-import-design.md §3 step 5.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  classifyDouyinResponseUrl,
  installFetchTap,
  parseAwemeListResponse,
  parseUserFollowListResponse,
  waitForDouyinSdk,
} from "../src/main/dy-fetch-tap.ts";

test("classifyDouyinResponseUrl maps the four bootstrap endpoints to scopes", () => {
  assert.equal(
    classifyDouyinResponseUrl(
      "https://www.douyin.com/aweme/v1/web/aweme/post/?count=18&sec_user_id=abc",
    ),
    "dy_post",
  );
  assert.equal(
    classifyDouyinResponseUrl(
      "https://www.douyin.com/aweme/v1/web/aweme/favorite/?count=18&sec_user_id=abc",
    ),
    "dy_collect",
  );
  assert.equal(
    classifyDouyinResponseUrl(
      "https://www.douyin.com/aweme/v1/web/aweme/like/?count=18&sec_user_id=abc",
    ),
    "dy_like",
  );
  assert.equal(
    classifyDouyinResponseUrl(
      "https://www.douyin.com/aweme/v1/web/user/follow/list/?count=20",
    ),
    "dy_follow",
  );
});

test("classifyDouyinResponseUrl returns null for endpoints we do NOT care about", () => {
  // Negatives drawn from real /jingxuan landing-page traffic
  // (chrome-devtools MCP probe 2026-05-07).
  assert.equal(
    classifyDouyinResponseUrl("https://www.douyin.com/aweme/v2/web/module/feed/?count=20"),
    null,
  );
  assert.equal(
    classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/hot/search/list/"),
    null,
  );
  assert.equal(
    classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/social/count?source=6"),
    null,
  );
  assert.equal(
    classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=x"),
    null,
  );
  assert.equal(classifyDouyinResponseUrl(""), null);
  assert.equal(classifyDouyinResponseUrl("https://example.com/"), null);
});

test("parseAwemeListResponse extracts aweme_id, desc, author, cover for dy_post", () => {
  const items = parseAwemeListResponse(
    {
      aweme_list: [
        {
          aweme_id: "111",
          desc: "demo description",
          author: { nickname: "u", sec_uid: "s" },
          video: { cover: { url_list: ["https://c1", "https://c2"] } },
          duration: 18000,
        },
      ],
    },
    "dy_post",
  );
  assert.equal(items.length, 1);
  assert.equal(items[0]!.scope, "dy_post");
  assert.equal(items[0]!.aweme_id, "111");
  assert.equal(items[0]!.title, "demo description");
  assert.equal(items[0]!.author, "u");
  assert.equal(items[0]!.author_sec_uid, "s");
  assert.equal(items[0]!.cover_url, "https://c1");
  assert.equal(items[0]!.url, "https://www.douyin.com/video/111");
});

test("parseAwemeListResponse falls back to preview_title when desc is empty", () => {
  // Real /aweme/v2/web/module/feed/ samples shipped preview_title
  // alongside a blank desc — accept both.
  const items = parseAwemeListResponse(
    {
      aweme_list: [
        {
          aweme_id: "222",
          desc: "",
          preview_title: "回退标题",
          author: { nickname: "u" },
        },
      ],
    },
    "dy_collect",
  );
  assert.equal(items[0]!.title, "回退标题");
});

test("parseAwemeListResponse drops items with no aweme_id and no title", () => {
  const items = parseAwemeListResponse(
    {
      aweme_list: [
        { aweme_id: "", desc: "" },
        { aweme_id: "333", desc: "ok" },
        null,
        "garbage",
      ],
    },
    "dy_like",
  );
  assert.equal(items.length, 1);
  assert.equal(items[0]!.aweme_id, "333");
});

test("parseAwemeListResponse tolerates missing aweme_list / wrong types", () => {
  assert.deepEqual(parseAwemeListResponse({}, "dy_post"), []);
  assert.deepEqual(parseAwemeListResponse(null, "dy_post"), []);
  assert.deepEqual(parseAwemeListResponse({ aweme_list: "string" }, "dy_post"), []);
});

test("parseUserFollowListResponse extracts creator_sec_uid + nickname", () => {
  // Shape from f2 fetch_user_following_list reference. Top-level key
  // varies (followings vs follow_list) — accept both.
  const items = parseUserFollowListResponse({
    followings: [
      { sec_uid: "abc", nickname: "@老白", avatar_thumb: { url_list: ["https://a1"] } },
      { sec_uid: "def", nickname: "另一位" },
    ],
  });
  assert.equal(items.length, 2);
  assert.equal(items[0]!.scope, "dy_follow");
  assert.equal(items[0]!.creator_sec_uid, "abc");
  assert.equal(items[0]!.title, "@老白");
  assert.equal(items[0]!.url, "https://www.douyin.com/user/abc");
});

test("parseUserFollowListResponse accepts follow_list as alternate key", () => {
  const items = parseUserFollowListResponse({
    follow_list: [{ sec_uid: "ggg", nickname: "x" }],
  });
  assert.equal(items.length, 1);
  assert.equal(items[0]!.creator_sec_uid, "ggg");
});

test("parseUserFollowListResponse drops rows with no sec_uid", () => {
  const items = parseUserFollowListResponse({
    followings: [{ nickname: "no-uid" }, { sec_uid: "y", nickname: "ok" }],
  });
  assert.equal(items.length, 1);
  assert.equal(items[0]!.creator_sec_uid, "y");
});

test("waitForDouyinSdk resolves true when byted_acrawler appears", async () => {
  type W = { byted_acrawler?: unknown };
  const target: W = {};
  // Simulate SDK loading mid-poll.
  setTimeout(() => {
    target.byted_acrawler = { frontierSign: () => null };
  }, 30);
  const ok = await waitForDouyinSdk(target as unknown as Window, 500);
  assert.equal(ok, true);
});

test("waitForDouyinSdk resolves false when SDK never loads", async () => {
  const target = {} as Window;
  const ok = await waitForDouyinSdk(target, 100);
  assert.equal(ok, false);
});

test("installFetchTap wraps target.fetch and posts captured items via callback", async () => {
  // Build a fake Window that mimics the real-page state AFTER the SDK
  // has wrapped fetch — the production install path runs in this exact
  // order, so the wrapper-of-wrapper composition is what matters.
  const calls: { items: unknown[]; scope: string }[] = [];
  const fakeFetch = async (input: RequestInfo): Promise<Response> => {
    const url = typeof input === "string" ? input : (input as Request).url;
    if (url.includes("/aweme/v1/web/aweme/favorite/")) {
      const body = JSON.stringify({
        aweme_list: [{ aweme_id: "555", desc: "favorite item" }],
      });
      return new Response(body, { status: 200 });
    }
    return new Response("{}", { status: 200 });
  };
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;

  installFetchTap(fakeWindow, (items, scope) => {
    calls.push({ items, scope });
  });

  await fakeWindow.fetch(
    "https://www.douyin.com/aweme/v1/web/aweme/favorite/?count=18&sec_user_id=abc",
  );

  assert.equal(calls.length, 1);
  assert.equal(calls[0]!.scope, "dy_collect");
  assert.equal((calls[0]!.items[0] as { aweme_id: string }).aweme_id, "555");
});

test("installFetchTap does not invoke callback for non-bootstrap endpoints", async () => {
  let called = 0;
  const fakeFetch = async (): Promise<Response> =>
    new Response(JSON.stringify({ aweme_list: [] }), { status: 200 });
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;
  installFetchTap(fakeWindow, () => {
    called += 1;
  });
  await fakeWindow.fetch("https://www.douyin.com/aweme/v2/web/module/feed/");
  await fakeWindow.fetch("https://www.douyin.com/aweme/v1/web/hot/search/list/");
  assert.equal(called, 0);
});

test("installFetchTap returns the original fetch's response unchanged", async () => {
  // The page's own consumer must still see the original Response
  // body — we only clone() to read off the side. Otherwise we'd
  // disrupt React's data flow.
  const fakeFetch = async (): Promise<Response> =>
    new Response(JSON.stringify({ aweme_list: [{ aweme_id: "777" }] }), {
      status: 200,
    });
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;
  installFetchTap(fakeWindow, () => {});
  const resp = await fakeWindow.fetch(
    "https://www.douyin.com/aweme/v1/web/aweme/like/?count=18",
  );
  const json = (await resp.json()) as { aweme_list: { aweme_id: string }[] };
  assert.equal(json.aweme_list[0]!.aweme_id, "777");
});

test("installFetchTap disposer restores the original fetch", async () => {
  const original = async (): Promise<Response> => new Response("{}");
  const fakeWindow = { fetch: original } as unknown as Window;
  const dispose = installFetchTap(fakeWindow, () => {});
  // After install, fetch is wrapped (a different function reference).
  assert.notEqual(fakeWindow.fetch, original);
  dispose();
  assert.equal(fakeWindow.fetch, original);
});
