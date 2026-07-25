import assert from "node:assert/strict";
import test from "node:test";

import {
  saveReddit,
  type RedditNativeSaveEnvironment,
  type RedditSaveControl,
} from "../src/content/native-save/reddit.ts";
import type { NativeSaveTask } from "../src/shared/native-save.ts";

const task: NativeSaveTask = {
  id: "123e4567-e89b-42d3-a456-426614174001",
  type: "native_save",
  platform: "reddit",
  platform_slug: "reddit",
  item_key: "reddit:t3_abc123",
  content_id: "t3_abc123",
  content_url: "https://www.reddit.com/r/test/comments/abc123/title/",
  content_type: "post",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "Reddit Saved",
};

function fixture(options: {
  loggedIn?: boolean;
  token?: string | null;
  initialState?: "Save" | "Unsave" | null;
  responseStatus?: number;
  confirmAfterRequest?: boolean;
  confirmAfterClick?: boolean;
  rejectRequest?: boolean;
  readyAfterSleeps?: number;
  savedStates?: Array<"saved" | "unsaved" | "unknown">;
} = {}): RedditNativeSaveEnvironment & {
  clicks: number;
  saveRequests: URLSearchParams[];
  savedStateRequests: number;
  fetchSavedState(fullname: string): Promise<"saved" | "unsaved" | "unknown">;
} {
  let state = options.initialState === undefined ? "Save" : options.initialState;
  let sleeps = 0;
  let savedStateIndex = 0;
  const ready = () => sleeps >= (options.readyAfterSleeps ?? 0);
  const env = {
    clicks: 0,
    saveRequests: [] as URLSearchParams[],
    savedStateRequests: 0,
    currentUrl: task.content_url,
    isLoggedIn: () => ready() && (options.loggedIn ?? true),
    requestToken: () => ready() ? (options.token ?? null) : null,
    async postSave(body: URLSearchParams) {
      env.saveRequests.push(body);
      if (options.rejectRequest) throw new Error("network outcome unknown");
      if (options.confirmAfterRequest) state = "Unsave";
      return { status: options.responseStatus ?? 200, ok: (options.responseStatus ?? 200) < 400 };
    },
    async fetchSavedState(_fullname: string) {
      env.savedStateRequests += 1;
      const values = options.savedStates ?? ["unknown"];
      const value = values[Math.min(savedStateIndex, values.length - 1)] ?? "unknown";
      savedStateIndex += 1;
      return value;
    },
    findControl(_fullname: string, label: "Save" | "Unsave"): RedditSaveControl | null {
      if (!ready()) return null;
      if (state !== label) return null;
      return {
        click() {
          env.clicks += 1;
          if (options.confirmAfterClick) state = "Unsave";
        },
      };
    },
    sleep: async () => { sleeps += 1; },
  } satisfies RedditNativeSaveEnvironment & {
    clicks: number;
    saveRequests: URLSearchParams[];
    savedStateRequests: number;
    fetchSavedState(fullname: string): Promise<"saved" | "unsaved" | "unknown">;
  };
  return env;
}

test("Reddit native save confirms an accepted request through exact saved item state", async () => {
  const env = fixture({
    token: "page-modhash",
    responseStatus: 200,
    savedStates: ["unsaved", "saved"],
  });

  assert.deepEqual(await saveReddit(task, env), { status: "synced" });
  assert.equal(env.saveRequests.length, 1);
  assert.equal(env.savedStateRequests, 2);
});

test("Reddit native save reports missing postcondition after an accepted request", async () => {
  const env = fixture({
    token: "page-modhash",
    responseStatus: 200,
    savedStates: ["unknown"],
  });

  assert.deepEqual(await saveReddit(task, env), {
    status: "failed",
    error_code: "native_confirmation_not_observed",
  });
  assert.equal(env.saveRequests.length, 1);
  assert.ok(env.savedStateRequests > 0);
});

test("Reddit browser state endpoint accepts only the exact saved fullname", async () => {
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const originalLocation = Object.getOwnPropertyDescriptor(globalThis, "location");
  const originalFetch = globalThis.fetch;
  const documentFixture = {
    querySelector(selector: string) {
      if (selector === "input[name='uh']") return { value: "page-modhash" };
      if (selector.includes("user-menu")) return {};
      return null;
    },
  };
  Object.defineProperty(globalThis, "document", { configurable: true, value: documentFixture });
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: {
      href: task.content_url,
      origin: "https://www.reddit.com",
      pathname: "/r/test/comments/abc123/title/",
    },
  });
  try {
    for (const [reportedName, expected] of [
      [task.content_id, { status: "already_synced" }],
      ["t3_different", { status: "failed", error_code: "native_request_rejected" }],
    ] as const) {
      globalThis.fetch = (async (input: string | URL | Request) => {
        const url = new URL(String(input));
        if (url.pathname === "/api/info.json") {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              data: { children: [{ data: { name: reportedName, saved: true } }] },
            }),
          } as Response;
        }
        return { ok: false, status: 500 } as Response;
      }) as typeof fetch;
      assert.deepEqual(await saveReddit(task), expected);
    }
  } finally {
    globalThis.fetch = originalFetch;
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
    else delete (globalThis as { document?: unknown }).document;
    if (originalLocation) Object.defineProperty(globalThis, "location", originalLocation);
    else delete (globalThis as { location?: unknown }).location;
  }
});

test("Reddit native save accepts post and comment fullnames and confirms request saves", async () => {
  for (const candidate of [
    task,
    {
      ...task,
      item_key: "reddit:t1_def456",
      content_id: "t1_def456",
      content_url: "https://www.reddit.com/r/test/comments/abc123/title/def456/",
      content_type: "comment",
    },
  ]) {
    const env = fixture({ token: "page-modhash", confirmAfterRequest: true });
    env.currentUrl = candidate.content_url;
    assert.deepEqual(await saveReddit(candidate, env), { status: "synced" });
    assert.equal(env.saveRequests.length, 1);
    assert.equal(env.saveRequests[0]?.get("id"), candidate.content_id);
    assert.equal(env.saveRequests[0]?.get("uh"), "page-modhash");
    assert.equal(env.clicks, 0);
  }
});

test("Reddit native save waits for the logged-in correlated post controls", async () => {
  const env = fixture({ readyAfterSleeps: 2, token: null, confirmAfterClick: true });
  assert.deepEqual(await saveReddit(task, env), { status: "synced" });
  assert.equal(env.clicks, 1);
});

test("Reddit native save accepts a redd.it task after Reddit redirects to the canonical post", async () => {
  const env = fixture({ token: "page-modhash", confirmAfterRequest: true });
  assert.deepEqual(await saveReddit({ ...task, content_url: "https://redd.it/abc123" }, env), {
    status: "synced",
  });
  assert.equal(env.saveRequests.length, 1);
});

test("Reddit native save detects login and existing Saved state before mutation", async () => {
  const loggedOut = fixture({ loggedIn: false, token: "must-not-be-used" });
  loggedOut.currentUrl = "https://www.reddit.com/login/";
  assert.deepEqual(await saveReddit(task, loggedOut), { status: "login_required" });
  assert.equal(loggedOut.saveRequests.length, 0);

  const saved = fixture({ initialState: "Unsave", token: "must-not-be-used" });
  assert.deepEqual(await saveReddit(task, saved), { status: "already_synced" });
  assert.equal(saved.saveRequests.length, 0);

  const apiSaved = fixture({
    initialState: null,
    token: null,
    savedStates: ["saved"],
  });
  assert.deepEqual(await saveReddit(task, apiSaved), { status: "already_synced" });
  assert.equal(apiSaved.saveRequests.length, 0);
  assert.equal(apiSaved.clicks, 0);
});

test("Reddit native save maps rate limits and unsupported identities exactly", async () => {
  const limited = fixture({ token: "page-modhash", responseStatus: 429 });
  assert.deepEqual(await saveReddit(task, limited), { status: "rate_limited" });

  for (const unsupported of [
    { ...task, content_id: "t5_test", item_key: "reddit:t5_test", content_type: "subreddit", content_url: "https://www.reddit.com/r/test/" },
    { ...task, content_id: "t2_user", item_key: "reddit:t2_user", content_type: "user", content_url: "https://www.reddit.com/user/test/" },
  ]) {
    const env = fixture({ token: "must-not-be-used" });
    assert.deepEqual(await saveReddit(unsupported, env), {
      status: "unsupported",
      error_code: "unsupported_content_type",
    });
    assert.equal(env.saveRequests.length, 0);
  }
});

test("Reddit native save reports readiness, request, and control stages", async () => {
  const cases: Array<[RedditNativeSaveEnvironment, string]> = [
    [fixture({ token: null, initialState: null }), "native_content_not_ready"],
    [fixture({ token: "page-modhash", responseStatus: 500 }), "native_request_rejected"],
    [
      fixture({ token: "page-modhash", responseStatus: 403, initialState: null }),
      "native_control_not_found",
    ],
  ];

  for (const [env, errorCode] of cases) {
    assert.deepEqual(await saveReddit(task, env), {
      status: "failed",
      error_code: errorCode,
    });
  }
});

test("Reddit native save falls back to the exact visible Save control", async () => {
  for (const env of [
    fixture({ token: null, confirmAfterClick: true }),
    fixture({ token: "page-modhash", responseStatus: 403, confirmAfterClick: true }),
  ]) {
    assert.deepEqual(await saveReddit(task, env), { status: "synced" });
    assert.equal(env.clicks, 1);
  }
});

test("Reddit browser save finds the exact control inside open shadow roots", async () => {
  let state: "save" | "unsave" = "save";
  let clicks = 0;
  const control = {
    get textContent() { return state; },
    getAttribute() { return null; },
    click() { clicks += 1; state = "unsave"; },
  };
  const shadowRoot = {
    querySelectorAll(selector: string) {
      return selector === "button, a, [role='button']" ? [control] : [];
    },
  };
  const post = {
    shadowRoot,
    querySelectorAll() { return []; },
  };
  const documentFixture = {
    querySelector(selector: string) {
      if (selector === "shreddit-post[id=\"abc123\"]") return post;
      if (selector.includes("user-menu")) return {};
      return null;
    },
  };
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const originalLocation = Object.getOwnPropertyDescriptor(globalThis, "location");
  const originalSetTimeout = globalThis.setTimeout;
  Object.defineProperty(globalThis, "document", { configurable: true, value: documentFixture });
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: {
      href: task.content_url,
      origin: "https://www.reddit.com",
      pathname: "/r/test/comments/abc123/title/",
    },
  });
  globalThis.setTimeout = ((callback: (...args: unknown[]) => void) => {
    callback();
    return 0 as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;
  try {
    assert.deepEqual(await saveReddit(task), { status: "synced" });
    assert.equal(clicks, 1);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
    else delete (globalThis as { document?: unknown }).document;
    if (originalLocation) Object.defineProperty(globalThis, "location", originalLocation);
    else delete (globalThis as { location?: unknown }).location;
  }
});

test("Reddit browser save fails closed when shadow DOM exposes multiple Save controls", async () => {
  let clicks = 0;
  const controls = [
    { textContent: "Save", getAttribute() { return null; }, click() { clicks += 1; } },
    { textContent: "Save", getAttribute() { return null; }, click() { clicks += 1; } },
  ];
  const shadowRoot = {
    querySelectorAll(selector: string) {
      return selector === "button, a, [role='button']" ? controls : [];
    },
  };
  const post = { shadowRoot, querySelectorAll() { return []; } };
  const documentFixture = {
    querySelector(selector: string) {
      if (selector === "shreddit-post[id=\"abc123\"]") return post;
      if (selector.includes("user-menu")) return {};
      return null;
    },
  };
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const originalLocation = Object.getOwnPropertyDescriptor(globalThis, "location");
  Object.defineProperty(globalThis, "document", { configurable: true, value: documentFixture });
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: {
      href: task.content_url,
      origin: "https://www.reddit.com",
      pathname: "/r/test/comments/abc123/title/",
    },
  });
  try {
    assert.deepEqual(await saveReddit(task), {
      status: "failed",
      error_code: "native_content_not_ready",
    });
    assert.equal(clicks, 0);
  } finally {
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
    else delete (globalThis as { document?: unknown }).document;
    if (originalLocation) Object.defineProperty(globalThis, "location", originalLocation);
    else delete (globalThis as { location?: unknown }).location;
  }
});

test("Reddit browser save ignores a lone Save owned by a nested identity", async () => {
  let clicks = 0;
  const post: { querySelectorAll: (selector: string) => unknown[] } = {
    querySelectorAll() { return []; },
  };
  const nestedComment = {
    tagName: "SHREDDIT-COMMENT",
    parentNode: post,
    shadowRoot: null as unknown,
  };
  const shadowRoot = {
    host: nestedComment,
    querySelectorAll(selector: string) {
      return selector === "button, a, [role='button']" ? [control] : [];
    },
  };
  const control = {
    textContent: "Save",
    parentNode: shadowRoot,
    getAttribute() { return null; },
    click() { clicks += 1; },
  };
  nestedComment.shadowRoot = shadowRoot;
  post.querySelectorAll = (selector: string) => selector === "*" ? [nestedComment] : [];
  const documentFixture = {
    querySelector(selector: string) {
      if (selector === "shreddit-post[id=\"abc123\"]") return post;
      if (selector.includes("user-menu")) return {};
      return null;
    },
  };
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const originalLocation = Object.getOwnPropertyDescriptor(globalThis, "location");
  const originalSetTimeout = globalThis.setTimeout;
  Object.defineProperty(globalThis, "document", { configurable: true, value: documentFixture });
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: {
      href: task.content_url,
      origin: "https://www.reddit.com",
      pathname: "/r/test/comments/abc123/title/",
    },
  });
  globalThis.setTimeout = ((callback: (...args: unknown[]) => void) => {
    callback();
    return 0 as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;
  try {
    assert.deepEqual(await saveReddit(task), {
      status: "failed",
      error_code: "native_content_not_ready",
    });
    assert.equal(clicks, 0);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
    else delete (globalThis as { document?: unknown }).document;
    if (originalLocation) Object.defineProperty(globalThis, "location", originalLocation);
    else delete (globalThis as { location?: unknown }).location;
  }
});

test("Reddit native save never clicks after a 2xx response without confirmation", async () => {
  const env = fixture({ token: "page-modhash", responseStatus: 200, confirmAfterClick: true });
  assert.deepEqual(await saveReddit(task, env), {
    status: "failed",
    error_code: "native_confirmation_not_observed",
  });
  assert.equal(env.saveRequests.length, 1);
  assert.equal(env.clicks, 0);
});

test("Reddit native save never clicks after a network-uncertain request", async () => {
  const env = fixture({ token: "page-modhash", rejectRequest: true, confirmAfterClick: true });
  assert.deepEqual(await saveReddit(task, env), {
    status: "failed",
    error_code: "native_confirmation_not_observed",
  });
  assert.equal(env.saveRequests.length, 1);
  assert.equal(env.clicks, 0);
});

test("Reddit native save requires the comment ID at the canonical URL position", async () => {
  for (const contentUrl of [
    "https://www.reddit.com/r/test/comments/abc123/def456/other/",
    "https://www.reddit.com/r/test/comments/abc123/title/other/def456/",
  ]) {
    const env = fixture({ token: "page-modhash", confirmAfterRequest: true });
    env.currentUrl = contentUrl;
    const commentTask = { ...task, content_id: "t1_def456", item_key: "reddit:t1_def456", content_type: "comment", content_url: contentUrl };
    assert.deepEqual(await saveReddit(commentTask, env), {
      status: "unsupported",
      error_code: "unsupported_content_type",
    });
    assert.equal(env.saveRequests.length, 0);
  }
});

test("Reddit native save requires target DOM correlation on a non-permalink comment page", async () => {
  const commentTask = {
    ...task,
    content_id: "t1_def456",
    item_key: "reddit:t1_def456",
    content_type: "comment",
    content_url: "https://www.reddit.com/r/test/comments/abc123/title/def456/",
  };
  const missing = fixture({ token: "page-modhash", initialState: null, confirmAfterRequest: true });
  assert.deepEqual(await saveReddit(commentTask, missing), {
    status: "failed",
    error_code: "native_control_not_found",
  });
  assert.equal(missing.saveRequests.length, 0);

  const correlated = fixture({ token: "page-modhash", confirmAfterRequest: true });
  assert.deepEqual(await saveReddit(commentTask, correlated), { status: "synced" });
  assert.equal(correlated.saveRequests.length, 1);
});

test("Reddit native save fails safely without post-action confirmation", async () => {
  const env = fixture({ token: null, confirmAfterClick: false });
  assert.deepEqual(await saveReddit(task, env), {
    status: "failed",
    error_code: "native_confirmation_not_observed",
  });
  assert.equal(env.clicks, 1);
});

test("watch-later fallback uses the same Reddit Saved mutation", async () => {
  const env = fixture({ token: "page-modhash", confirmAfterRequest: true });
  const result = await saveReddit({
    ...task,
    requested_action: "watch_later",
    resolved_action: "favorite",
    target_label: "Reddit Saved",
  }, env);
  assert.deepEqual(result, { status: "synced" });
  assert.equal(env.saveRequests.length, 1);
});
