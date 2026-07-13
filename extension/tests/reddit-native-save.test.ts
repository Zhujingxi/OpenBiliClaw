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
} = {}): RedditNativeSaveEnvironment & { clicks: number; saveRequests: URLSearchParams[] } {
  let state = options.initialState === undefined ? "Save" : options.initialState;
  const env = {
    clicks: 0,
    saveRequests: [] as URLSearchParams[],
    currentUrl: task.content_url,
    isLoggedIn: () => options.loggedIn ?? true,
    requestToken: () => options.token ?? null,
    async postSave(body: URLSearchParams) {
      env.saveRequests.push(body);
      if (options.rejectRequest) throw new Error("network outcome unknown");
      if (options.confirmAfterRequest) state = "Unsave";
      return { status: options.responseStatus ?? 200, ok: (options.responseStatus ?? 200) < 400 };
    },
    findControl(_fullname: string, label: "Save" | "Unsave"): RedditSaveControl | null {
      if (state !== label) return null;
      return {
        click() {
          env.clicks += 1;
          if (options.confirmAfterClick) state = "Unsave";
        },
      };
    },
    sleep: async () => {},
  } satisfies RedditNativeSaveEnvironment & { clicks: number; saveRequests: URLSearchParams[] };
  return env;
}

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

test("Reddit native save falls back to the exact visible Save control", async () => {
  for (const env of [
    fixture({ token: null, confirmAfterClick: true }),
    fixture({ token: "page-modhash", responseStatus: 403, confirmAfterClick: true }),
  ]) {
    assert.deepEqual(await saveReddit(task, env), { status: "synced" });
    assert.equal(env.clicks, 1);
  }
});

test("Reddit native save never clicks after a 2xx response without confirmation", async () => {
  const env = fixture({ token: "page-modhash", responseStatus: 200, confirmAfterClick: true });
  assert.deepEqual(await saveReddit(task, env), {
    status: "failed",
    error_code: "native_save_failed",
  });
  assert.equal(env.saveRequests.length, 1);
  assert.equal(env.clicks, 0);
});

test("Reddit native save never clicks after a network-uncertain request", async () => {
  const env = fixture({ token: "page-modhash", rejectRequest: true, confirmAfterClick: true });
  assert.deepEqual(await saveReddit(task, env), {
    status: "failed",
    error_code: "native_save_failed",
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
    error_code: "native_save_failed",
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
    error_code: "native_save_failed",
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
