import test from "node:test";
import assert from "node:assert/strict";

import { createAutostartApi, initAutostartControl } from "../popup/popup-autostart-control.js";

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
  } as any;
}

test("createAutostartApi.status fetches /api/autostart-status", async () => {
  const calls: any[] = [];
  const fetchImpl = async (url: string, options: any = {}) => {
    calls.push({ url, options });
    return { ok: true, status: 200, async json() { return { enabled: true, can_manage: true }; } };
  };
  const api = createAutostartApi({ getBaseUrl, fetchImpl });
  const status = await api.status();
  assert.deepEqual(status, { enabled: true, can_manage: true });
  assert.equal(calls[0].url, `${BASE}/autostart-status`);
});

test("createAutostartApi.apply posts to /api/autostart/apply", async () => {
  const calls: any[] = [];
  const fetchImpl = async (url: string, options: any = {}) => {
    calls.push({ url, options });
    return { ok: true, status: 200, async json() { return { enabled: true, registered: true }; } };
  };
  const api = createAutostartApi({ getBaseUrl, fetchImpl });
  const result = await api.apply(true);
  assert.equal(result.ok, true);
  assert.equal(calls[0].url, `${BASE}/autostart/apply`);
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), { enabled: true });
});

test("initAutostartControl reflects disabled non-manageable status", async () => {
  const checkbox = fakeEl({ checked: false, disabled: false });
  const hint = fakeEl({ textContent: "" });
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    async json() { return { enabled: true, can_manage: false, reason: "env_managed" }; },
  });
  const ctl = initAutostartControl({ checkbox, hint }, { getBaseUrl, fetchImpl });
  await ctl.reload();
  assert.equal(checkbox.checked, true);
  assert.equal(checkbox.disabled, true);
  assert.match(hint.textContent, /环境变量/);
});

test("initAutostartControl toggles immediately and reloads", async () => {
  const checkbox = fakeEl({ checked: false, disabled: false });
  const hint = fakeEl({ textContent: "" });
  let enabled = false;
  const posts: any[] = [];
  const fetchImpl = async (url: string, options: any = {}) => {
    if (String(url).endsWith("/autostart-status")) {
      return { ok: true, status: 200, async json() {
        return { enabled, can_manage: true, registered: enabled, manage_ollama: true };
      } };
    }
    posts.push(JSON.parse(options.body));
    enabled = true;
    return { ok: true, status: 200, async json() {
      return { enabled: true, can_manage: true, registered: true, manage_ollama: true };
    } };
  };
  const ctl = initAutostartControl({ checkbox, hint }, { getBaseUrl, fetchImpl });
  await ctl.reload();
  checkbox.checked = true;
  checkbox.fire("change");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.deepEqual(posts[0], { enabled: true });
  assert.equal(checkbox.checked, true);
  assert.match(hint.textContent, /下次登录/);
});

test("popup wires the autostart control into the general settings panel", async () => {
  const { readFileSync } = await import("node:fs");
  const { resolve } = await import("node:path");
  const html = readFileSync(resolve("popup/popup.html"), "utf8");
  assert.match(html, /id="cfgAutostartEnabled"/);
  assert.match(html, /id="cfgAutostartHint"/);
  const js = readFileSync(resolve("popup/popup.js"), "utf8");
  assert.match(js, /initAutostartControl/);
});
