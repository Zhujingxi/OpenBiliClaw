import test from "node:test";
import assert from "node:assert/strict";

import { createAuthApi, initAuthControl } from "../popup/popup-auth-control.js";

const BASE = "http://127.0.0.1:8420/api";
const getBaseUrl = async () => BASE;

function fakeEl(extra: Record<string, unknown> = {}) {
  const handlers: Record<string, (() => void)[]> = {};
  return {
    ...extra,
    addEventListener(type: string, fn: () => void) {
      (handlers[type] ||= []).push(fn);
    },
    fire(type: string) {
      for (const fn of handlers[type] || []) fn();
    },
    focus() {},
  } as any;
}

test("createAuthApi.status fetches /api/auth/status", async () => {
  const calls: any[] = [];
  const fetchImpl = async (url: string, options: any = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { enabled: true, can_manage: true };
      },
    };
  };
  const api = createAuthApi({ getBaseUrl, fetchImpl });
  const s = await api.status();
  assert.deepEqual(s, { enabled: true, can_manage: true });
  assert.equal(calls[0].url, `${BASE}/auth/status`);
});

test("createAuthApi.setEnabled posts to /api/auth/admin with CSRF header", async () => {
  const calls: any[] = [];
  const fetchImpl = async (url: string, options: any = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { ok: true, enabled: true };
      },
    };
  };
  const api = createAuthApi({ getBaseUrl, fetchImpl });
  const r = await api.setEnabled(true, "hunter2");
  assert.equal(r.ok, true);
  assert.equal(calls[0].url, `${BASE}/auth/admin`);
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["X-OBC-Auth"], "1");
  assert.deepEqual(JSON.parse(calls[0].options.body), { enabled: true, password: "hunter2" });

  await api.setEnabled(false);
  assert.deepEqual(JSON.parse(calls[1].options.body), { enabled: false });
});

test("initAuthControl reflects status and disables control when not manageable", async () => {
  const checkbox = fakeEl({ checked: false, disabled: false });
  const password = fakeEl({ value: "", hidden: true });
  const saveBtn = fakeEl({ hidden: true });
  const hint = fakeEl({ textContent: "" });
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    async json() {
      return { enabled: true, can_manage: false, env_managed: false };
    },
  });
  const ctl = initAuthControl({ checkbox, password, saveBtn, hint }, { getBaseUrl, fetchImpl });
  await ctl.reload();
  assert.equal(checkbox.checked, true);
  assert.equal(checkbox.disabled, true); // can_manage false → disabled
  assert.match(hint.textContent, /仅本机/);
});

test("initAuthControl enable flow posts password then reloads", async () => {
  const checkbox = fakeEl({ checked: false, disabled: false });
  const password = fakeEl({ value: "", hidden: true });
  const saveBtn = fakeEl({ hidden: true });
  const hint = fakeEl({ textContent: "" });
  let enabledState = false;
  const posts: any[] = [];
  const fetchImpl = async (url: string, options: any = {}) => {
    if (String(url).endsWith("/auth/status")) {
      return {
        ok: true,
        status: 200,
        async json() {
          return { enabled: enabledState, can_manage: true, env_managed: false };
        },
      };
    }
    posts.push(JSON.parse(options.body));
    enabledState = true;
    return {
      ok: true,
      status: 200,
      async json() {
        return { ok: true, enabled: true };
      },
    };
  };
  const ctl = initAuthControl({ checkbox, password, saveBtn, hint }, { getBaseUrl, fetchImpl });
  await ctl.reload();
  assert.equal(password.hidden, true); // disabled state → password hidden

  // user checks the box → password field revealed
  checkbox.checked = true;
  checkbox.fire("change");
  assert.equal(password.hidden, false);
  assert.equal(saveBtn.hidden, false);

  // enter a password and Save → POST admin enable, then reloads to enabled
  password.value = "s3kret";
  saveBtn.fire("click");
  await new Promise((r) => setTimeout(r, 5)); // let the async click handler settle
  assert.deepEqual(posts[0], { enabled: true, password: "s3kret" });
  assert.equal(checkbox.checked, true);
  assert.equal(password.value, ""); // cleared after success
});

test("initAuthControl unchecking disables immediately", async () => {
  const checkbox = fakeEl({ checked: true, disabled: false });
  const password = fakeEl({ value: "", hidden: false });
  const saveBtn = fakeEl({ hidden: false });
  const hint = fakeEl({ textContent: "" });
  let enabledState = true;
  const posts: any[] = [];
  const fetchImpl = async (url: string, options: any = {}) => {
    if (String(url).endsWith("/auth/status")) {
      return {
        ok: true,
        status: 200,
        async json() {
          return { enabled: enabledState, can_manage: true, env_managed: false };
        },
      };
    }
    posts.push(JSON.parse(options.body));
    enabledState = false;
    return {
      ok: true,
      status: 200,
      async json() {
        return { ok: true, enabled: false };
      },
    };
  };
  const ctl = initAuthControl({ checkbox, password, saveBtn, hint }, { getBaseUrl, fetchImpl });
  await ctl.reload();
  checkbox.checked = false;
  checkbox.fire("change");
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(posts[0], { enabled: false });
  assert.equal(checkbox.checked, false);
});

test("popup wires the auth control into the general settings panel", async () => {
  const { readFileSync } = await import("node:fs");
  const { resolve } = await import("node:path");
  const html = readFileSync(resolve("popup/popup.html"), "utf8");
  assert.match(html, /id="cfgAuthEnabled"/);
  assert.match(html, /id="cfgAuthPassword"/);
  assert.match(html, /id="cfgAuthSave"/);
  const js = readFileSync(resolve("popup/popup.ts"), "utf8");
  assert.match(js, /initAuthControl/);
  assert.match(js, /getBackendBaseUrl/);
});
