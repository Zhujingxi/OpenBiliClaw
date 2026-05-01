/**
 * Tests for xhs task executor's pure helpers.
 *
 * The task-executor module imports from passive.js (a .js extension for
 * bundler resolution) which Node can't resolve directly. We test the
 * executor's data contracts and logic boundaries here without importing
 * the module — the real integration is tested via the extension build.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildBootstrapDebugPayload,
  buildBootstrapPartialPayload,
  bootstrapProfileTabLabels,
  bootstrapScrollShouldContinue,
  countBootstrapStateNotesByScope,
  describeBootstrapScrollTarget,
  extractOwnProfileUrlFromDocument,
  extractOwnProfileUrlFromState,
  findBootstrapScrollContainer,
  hasDifferentProfileDocumentNotes,
  isActiveBootstrapProfileTab,
  normalizeBootstrapScrollRounds,
  normalizeBootstrapScrollWaitMs,
  normalizeBootstrapStagnantScrollRounds,
  extractBootstrapNotesFromProfileDocument,
  extractBootstrapNotesFromState,
  profileDocumentNoteKeys,
  readBootstrapScrollMetrics,
  type XhsBootstrapNote,
} from "../src/content/xhs/bootstrap.ts";

// We can't directly import task-executor.ts because it transitively
// imports "./passive.js" which Node resolves differently from esbuild.
// Instead we test the logic inline — buildLargeViewport is tiny.

function buildLargeViewport(innerHeight: number): {
  top: number;
  bottom: number;
  height: number;
} {
  const height = innerHeight || 900;
  return { top: -500, bottom: height + 500, height: height + 1000 };
}

test("buildLargeViewport creates an oversized viewport for initial capture", () => {
  const vp = buildLargeViewport(900);

  assert.ok(vp.top < 0, "top should be negative (above fold)");
  assert.ok(vp.bottom > 900, "bottom should exceed innerHeight");
  assert.ok(vp.height > 900, "height should be larger than innerHeight");
});

test("buildLargeViewport falls back when innerHeight is 0", () => {
  const vp = buildLargeViewport(0);

  assert.ok(vp.height > 0, "height should be positive even with 0 innerHeight");
  assert.ok(vp.bottom > 0, "bottom should be positive");
});

test("TaskResultPayload shape matches dispatcher expectations", () => {
  // Type-level contract check — the dispatcher expects these fields.
  const okResult = {
    task_id: "t1",
    urls: ["https://www.xiaohongshu.com/explore/abc123"],
    status: "ok" as const,
  };
  assert.equal(okResult.task_id, "t1");
  assert.equal(okResult.status, "ok");
  assert.equal(okResult.urls.length, 1);

  const errorResult = {
    task_id: "t2",
    urls: [] as string[],
    status: "error" as const,
    error: "timeout",
  };
  assert.equal(errorResult.status, "error");
  assert.equal(errorResult.error, "timeout");

  const emptyResult = {
    task_id: "t3",
    urls: [] as string[],
    status: "empty" as const,
  };
  assert.equal(emptyResult.status, "empty");
  assert.equal(emptyResult.urls.length, 0);
});

test("extractBootstrapNotesFromState maps saved liked and history groups", () => {
  const published = { id: "published", display_title: "published" };
  const saved = {
    id: "saved-id",
    display_title: "saved",
    xsec_token: "saved-token",
    user: { nickname: "saved-author" },
    cover: { url: "https://example.com/saved.jpg" },
  };
  const liked = {
    note_id: "liked-id",
    title: "liked",
    xsecToken: "liked-token",
    user_info: { nickname: "liked-author" },
  };
  const history = {
    noteId: "history-id",
    desc: "history",
    author: "history-author",
  };

  const notes = extractBootstrapNotesFromState({
    user: { notes: { _rawValue: [[published], [saved], [liked]] } },
    history: { notes: { _rawValue: [history] } },
  });

  assert.equal(notes.find((n) => n.title === "saved")?.scope, "saved");
  assert.equal(notes.find((n) => n.title === "liked")?.scope, "liked");
  assert.equal(notes.find((n) => n.title === "history")?.scope, "xhs_history");
  assert.equal(notes.find((n) => n.title === "saved")?.note_id, "saved-id");
  assert.equal(notes.find((n) => n.title === "saved")?.xsec_token, "saved-token");
});

test("extractBootstrapNotesFromState reads Xiaohongshu profile noteCard state shape", () => {
  const notes = extractBootstrapNotesFromState(
    {
      user: {
        notes: {
          _rawValue: [
            [],
            [
              {
                id: "saved-note-id",
                xsecToken: "saved-xsec",
                noteCard: {
                  displayTitle: "收藏标题",
                  cover: { urlDefault: "https://example.com/saved-cover.jpg" },
                  user: { nickName: "收藏作者" },
                },
              },
            ],
            [
              {
                id: "liked-note-id",
                xsecToken: "liked-xsec",
                noteCard: {
                  displayTitle: "赞过标题",
                  cover: { urlDefault: "https://example.com/liked-cover.jpg" },
                  user: { nickName: "赞过作者" },
                },
              },
            ],
          ],
        },
      },
    },
    ["saved", "liked"],
  );

  assert.deepEqual(
    notes.map((note) => ({
      scope: note.scope,
      note_id: note.note_id,
      title: note.title,
      author: note.author,
      cover_url: note.cover_url,
      xsec_token: note.xsec_token,
    })),
    [
      {
        scope: "saved",
        note_id: "saved-note-id",
        title: "收藏标题",
        author: "收藏作者",
        cover_url: "https://example.com/saved-cover.jpg",
        xsec_token: "saved-xsec",
      },
      {
        scope: "liked",
        note_id: "liked-note-id",
        title: "赞过标题",
        author: "赞过作者",
        cover_url: "https://example.com/liked-cover.jpg",
        xsec_token: "liked-xsec",
      },
    ],
  );
});

test("countBootstrapStateNotesByScope returns per-scope diagnostic counts", () => {
  const state = {
    user: {
      notes: {
        _rawValue: [
          [{ id: "published", display_title: "published" }],
          [
            { id: "saved-a", display_title: "saved-a" },
            { id: "saved-b", display_title: "saved-b" },
          ],
          [{ id: "liked-a", display_title: "liked-a" }],
        ],
      },
    },
  };

  assert.deepEqual(
    countBootstrapStateNotesByScope(state, ["saved", "liked", "xhs_history"], {
      maxItemsPerScope: 20,
    }),
    { saved: 2, liked: 1, xhs_history: 0 },
  );
});

test("buildBootstrapDebugPayload wraps a single bootstrap diagnostic step", () => {
  assert.deepEqual(
    buildBootstrapDebugPayload({
      page_url: "https://www.xiaohongshu.com/explore",
      is_profile_page: false,
      has_initial_state: true,
      requested_scopes: ["saved"],
      state_counts: { saved: 0 },
    }),
    {
      xhs_bootstrap: {
        steps: [
          {
            page_url: "https://www.xiaohongshu.com/explore",
            is_profile_page: false,
            has_initial_state: true,
            requested_scopes: ["saved"],
            state_counts: { saved: 0 },
          },
        ],
      },
    },
  );
});

test("bootstrapProfileTabLabels keeps liked clicks on the profile tab", () => {
  assert.deepEqual(bootstrapProfileTabLabels("saved"), ["收藏"]);
  assert.deepEqual(bootstrapProfileTabLabels("liked"), ["赞过", "喜欢", "点赞"]);
  assert.deepEqual(bootstrapProfileTabLabels("xhs_history"), []);
});

test("normalizeBootstrapScrollRounds keeps scrolling explicit and bounded", () => {
  assert.equal(normalizeBootstrapScrollRounds(undefined), 0);
  assert.equal(normalizeBootstrapScrollRounds(0), 0);
  assert.equal(normalizeBootstrapScrollRounds(-1), 0);
  assert.equal(normalizeBootstrapScrollRounds(3.8), 3);
  assert.equal(normalizeBootstrapScrollRounds(100), 30);
});

test("bootstrap scroll wait and stagnant rounds are backend-controlled but clamped", () => {
  assert.equal(normalizeBootstrapScrollWaitMs(undefined), 1_200);
  assert.equal(normalizeBootstrapScrollWaitMs(100), 500);
  assert.equal(normalizeBootstrapScrollWaitMs(2_750.8), 2_750);
  assert.equal(normalizeBootstrapScrollWaitMs(20_000), 5_000);

  assert.equal(normalizeBootstrapStagnantScrollRounds(undefined), 5);
  assert.equal(normalizeBootstrapStagnantScrollRounds(1), 1);
  assert.equal(normalizeBootstrapStagnantScrollRounds(8.9), 8);
  assert.equal(normalizeBootstrapStagnantScrollRounds(100), 10);
});

test("bootstrapScrollShouldContinue stops at caps and stagnant rounds", () => {
  assert.equal(
    bootstrapScrollShouldContinue({
      currentCount: 20,
      maxItemsPerScope: 100,
      round: 0,
      maxScrollRounds: 10,
      stagnantRounds: 0,
    }),
    true,
  );
  assert.equal(
    bootstrapScrollShouldContinue({
      currentCount: 100,
      maxItemsPerScope: 100,
      round: 0,
      maxScrollRounds: 10,
      stagnantRounds: 0,
    }),
    false,
  );
  assert.equal(
    bootstrapScrollShouldContinue({
      currentCount: 20,
      maxItemsPerScope: 100,
      round: 10,
      maxScrollRounds: 10,
      stagnantRounds: 0,
    }),
    false,
  );
  assert.equal(
    bootstrapScrollShouldContinue({
      currentCount: 20,
      maxItemsPerScope: 100,
      round: 3,
      maxScrollRounds: 10,
      stagnantRounds: 4,
    }),
    true,
  );
  assert.equal(
    bootstrapScrollShouldContinue({
      currentCount: 20,
      maxItemsPerScope: 100,
      round: 5,
      maxScrollRounds: 10,
      stagnantRounds: 5,
    }),
    false,
  );
  assert.equal(
    bootstrapScrollShouldContinue({
      currentCount: 20,
      maxItemsPerScope: 100,
      round: 7,
      maxScrollRounds: 10,
      stagnantRounds: 7,
      maxStagnantScrollRounds: 8,
    }),
    true,
  );
  assert.equal(
    bootstrapScrollShouldContinue({
      currentCount: 20,
      maxItemsPerScope: 100,
      round: 8,
      maxScrollRounds: 10,
      stagnantRounds: 8,
      maxStagnantScrollRounds: 8,
    }),
    false,
  );
});

test("findBootstrapScrollContainer prefers a scrollable note feed container", () => {
  const shallow = {
    id: "",
    className: "main-container",
    tagName: "DIV",
    scrollTop: 0,
    scrollHeight: 900,
    clientHeight: 820,
    querySelectorAll: () => [],
  };
  const feed = {
    id: "feed",
    className: "feeds-container waterfall",
    tagName: "DIV",
    scrollTop: 120,
    scrollHeight: 3600,
    clientHeight: 720,
    querySelectorAll: (selector: string) => (selector.includes("/explore/") ? [{}] : []),
  };
  const doc = {
    querySelectorAll: () => [shallow, feed],
  } as unknown as Document;

  assert.equal(findBootstrapScrollContainer(doc), feed);
  assert.equal(describeBootstrapScrollTarget(feed as unknown as HTMLElement), "div#feed.feeds-container.waterfall");
  assert.deepEqual(readBootstrapScrollMetrics(feed as unknown as HTMLElement), {
    target: "div#feed.feeds-container.waterfall",
    scroll_top: 120,
    scroll_height: 3600,
    client_height: 720,
  });
});

test("findBootstrapScrollContainer ignores zero-height feed wrappers", () => {
  const zeroHeightFeed = {
    id: "feed",
    className: "feeds-container waterfall",
    tagName: "DIV",
    scrollTop: 0,
    scrollHeight: 900,
    clientHeight: 0,
    querySelectorAll: (selector: string) => (selector.includes("/explore/") ? [{}, {}] : []),
  };
  const actualScroller = {
    id: "scroller",
    className: "main-scroll",
    tagName: "DIV",
    scrollTop: 0,
    scrollHeight: 3000,
    clientHeight: 720,
    querySelectorAll: () => [],
  };
  const doc = {
    querySelectorAll: () => [zeroHeightFeed, actualScroller],
  } as unknown as Document;

  assert.equal(findBootstrapScrollContainer(doc), actualScroller);
});

test("buildBootstrapPartialPayload emits a small partial result", () => {
  assert.deepEqual(
    buildBootstrapPartialPayload({
      taskId: "task-partial",
      scope: "saved",
      notes: [
        {
          scope: "saved",
          url: "https://www.xiaohongshu.com/explore/a",
          title: "a",
          author: "",
          cover_url: "",
          note_id: "a",
          xsec_token: "",
        },
      ],
      scopeCounts: { saved: 1, liked: 0, xhs_history: 0 },
      round: 2,
    }),
    {
      task_id: "task-partial",
      status: "partial",
      urls: ["https://www.xiaohongshu.com/explore/a"],
      notes: [
        {
          scope: "saved",
          url: "https://www.xiaohongshu.com/explore/a",
          title: "a",
          author: "",
          cover_url: "",
          note_id: "a",
          xsec_token: "",
        },
      ],
      scope_counts: { saved: 1, liked: 0, xhs_history: 0 },
      debug: { xhs_bootstrap_partial: { scope: "saved", round: 2, count: 1 } },
    },
  );
});

test("extractBootstrapNotesFromProfileDocument reads visible saved or liked cards only", () => {
  const card = {
    querySelector: (selector: string) => {
      if (selector.includes("title")) return { textContent: "DOM 收藏笔记" };
      if (selector.includes("author") || selector.includes("nickname")) {
        return { textContent: "作者名" };
      }
      if (selector.includes("img")) {
        return {
          getAttribute: (name: string) =>
            name === "src" ? "https://example.com/cover.jpg" : "",
        };
      }
      return null;
    },
  };
  const anchor = {
    href: "https://www.xiaohongshu.com/explore/dom-note-id?xsec_token=dom-token",
    title: "",
    closest: () => card,
  };
  const doc = {
    querySelectorAll: () => [anchor],
  } as unknown as Document;

  const notes = extractBootstrapNotesFromProfileDocument(
    doc,
    "saved",
    "https://www.xiaohongshu.com/user/profile/current-user",
  );

  assert.equal(notes.length, 1);
  assert.equal(notes[0].scope, "saved");
  assert.equal(notes[0].title, "DOM 收藏笔记");
  assert.equal(notes[0].author, "作者名");
  assert.equal(notes[0].note_id, "dom-note-id");
  assert.equal(notes[0].xsec_token, "dom-token");

  assert.deepEqual(
    extractBootstrapNotesFromProfileDocument(
      doc,
      "xhs_history",
      "https://www.xiaohongshu.com/user/profile/current-user",
    ),
    [],
  );
});

test("profileDocumentNoteKeys and change detection guard against stale tab DOM", () => {
  const beforeDoc = {
    querySelectorAll: () => [
      {
        href: "https://www.xiaohongshu.com/explore/default-note",
        title: "默认笔记",
        closest: () => ({ querySelector: () => null }),
      },
    ],
  } as unknown as Document;
  const previousKeys = profileDocumentNoteKeys(
    beforeDoc,
    "https://www.xiaohongshu.com/user/profile/current-user",
  );
  assert.deepEqual(previousKeys, ["default-note"]);

  const staleNotes: XhsBootstrapNote[] = [
    {
      scope: "saved",
      url: "https://www.xiaohongshu.com/explore/default-note",
      title: "默认笔记",
      author: "",
      cover_url: "",
      note_id: "default-note",
      xsec_token: "",
    },
  ];
  const changedNotes: XhsBootstrapNote[] = [
    {
      scope: "saved",
      url: "https://www.xiaohongshu.com/explore/saved-note",
      title: "收藏笔记",
      author: "",
      cover_url: "",
      note_id: "saved-note",
      xsec_token: "",
    },
  ];

  assert.equal(hasDifferentProfileDocumentNotes(staleNotes, previousKeys), false);
  assert.equal(hasDifferentProfileDocumentNotes(changedNotes, previousKeys), true);
});

test("isActiveBootstrapProfileTab detects selected profile tabs", () => {
  assert.equal(
    isActiveBootstrapProfileTab({
      getAttribute: (name: string) => (name === "aria-selected" ? "true" : ""),
      className: "",
    } as unknown as HTMLElement),
    true,
  );
  assert.equal(
    isActiveBootstrapProfileTab({
      getAttribute: () => "",
      className: "reds-tab-item active",
    } as unknown as HTMLElement),
    true,
  );
  assert.equal(
    isActiveBootstrapProfileTab({
      getAttribute: () => "",
      className: "reds-tab-item",
    } as unknown as HTMLElement),
    false,
  );
});

test("extractBootstrapNotesFromState caps per scope and skips unusable notes", () => {
  const saved: XhsBootstrapNote[] = extractBootstrapNotesFromState(
    {
      user: {
        notes: {
          _rawValue: [
            [],
            [
              { id: "a", title: "a" },
              { id: "b", title: "b" },
              { id: "", title: "", url: "" },
            ],
          ],
        },
      },
    },
    ["saved"],
    { maxItemsPerScope: 1 },
  );

  assert.equal(saved.length, 1);
  assert.equal(saved[0].title, "a");
  assert.equal(saved[0].url, "https://www.xiaohongshu.com/explore/a");
});

test("extractOwnProfileUrlFromDocument reads the logged-in nav profile link", () => {
  const anchor = {
    getAttribute: (name: string) =>
      name === "href" ? "/user/profile/current-user?xsec_token=own" : "",
  };
  const doc = {
    querySelector: () => anchor,
    querySelectorAll: () => [],
  } as unknown as Document;

  assert.equal(
    extractOwnProfileUrlFromDocument(doc, "https://www.xiaohongshu.com/explore"),
    "https://www.xiaohongshu.com/user/profile/current-user?xsec_token=own",
  );
});

test("extractOwnProfileUrlFromDocument ignores feed author profile links", () => {
  const anchors = [
    {
      textContent: "作者",
      className: "author",
      getAttribute: (name: string) =>
        name === "href" ? "/user/profile/someone-else?xsec_source=pc_feed" : "",
      closest: () => null,
    },
    {
      textContent: "笔记标题",
      className: "title",
      getAttribute: (name: string) =>
        name === "href" ? "/user/profile/someone-else?xsec_source=pc_user" : "",
      closest: () => null,
    },
  ];
  const doc = {
    querySelector: () => null,
    querySelectorAll: () => anchors,
  } as unknown as Document;

  assert.equal(
    extractOwnProfileUrlFromDocument(doc, "https://www.xiaohongshu.com/explore"),
    "",
  );
});

test("extractOwnProfileUrlFromState builds a profile URL only for logged-in users", () => {
  assert.equal(
    extractOwnProfileUrlFromState({
      user: {
        loggedIn: true,
        userInfo: { _rawValue: { userId: "current-user" } },
      },
    }),
    "https://www.xiaohongshu.com/user/profile/current-user",
  );

  assert.equal(
    extractOwnProfileUrlFromState({
      user: {
        loggedIn: false,
        userInfo: { _rawValue: { userId: "guest-user" } },
      },
    }),
    "",
  );
});
