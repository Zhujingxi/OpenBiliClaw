import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def test_mobile_recommendation_header_exposes_correction_actions() -> None:
    js = (ROOT / "src/openbiliclaw/web/js/views/recommend.js").read_text(encoding="utf-8")
    assert "推荐不准？" in js
    assert 'data-correction-target="profile"' in js
    assert 'data-correction-target="chat"' in js
    assert 'navigateToTab("profile")' in js
    assert "enterProfileEditMode" in js
    assert 'navigateToTab("chat")' in js
    assert "focusChatInputWhenReady()" in js


@pytest.mark.skipif(NODE is None, reason="node is required for mobile web behavior tests")
def test_mobile_chat_correction_focuses_after_delayed_history_render() -> None:
    source_path = ROOT / "src/openbiliclaw/web/js/views/recommend.js"
    script = f"""
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync({json.dumps(str(source_path))}, "utf8");
let chatInput = null;
let focusCount = 0;
let nextTimerId = 1;
let nextFrameId = 1;
const timers = new Map();
const frames = new Map();
const observers = [];

const setTimer = (callback) => {{
  const id = nextTimerId++;
  timers.set(id, callback);
  return id;
}};
const clearTimer = (id) => timers.delete(id);
const requestFrame = (callback) => {{
  const id = nextFrameId++;
  frames.set(id, callback);
  return id;
}};
const cancelFrame = (id) => frames.delete(id);
const fireFrame = (id) => {{
  const callback = frames.get(id);
  assert.equal(typeof callback, "function");
  frames.delete(id);
  callback(16);
}};

class FakeMutationObserver {{
  constructor(callback) {{
    this.callback = callback;
    this.active = false;
    this.disconnectCount = 0;
    observers.push(this);
  }}

  observe(target, options) {{
    assert.equal(target, document.body);
    assert.equal(options.childList, true);
    assert.equal(options.subtree, true);
    this.active = true;
  }}

  disconnect() {{
    this.active = false;
    this.disconnectCount += 1;
  }}
}}

const document = {{
  body: {{}},
  getElementById(id) {{
    assert.equal(id, "chat-input");
    return chatInput;
  }},
}};
const context = vm.createContext({{
  console,
  document,
  MutationObserver: FakeMutationObserver,
  setTimeout: setTimer,
  clearTimeout: clearTimer,
  requestAnimationFrame: requestFrame,
  cancelAnimationFrame: cancelFrame,
}});
const module = new vm.SourceTextModule(source, {{
  context,
  identifier: {json.dumps(str(source_path))},
}});

const importNames = new Map();
for (const match of source.matchAll(/import\\s*\\{{([\\s\\S]*?)\\}}\\s*from\\s*"([^"]+)";/g)) {{
  importNames.set(
    match[2],
    match[1].split(",").map((name) => name.trim()).filter(Boolean),
  );
}}
const mocks = new Map();
await module.link(async (specifier) => {{
  if (mocks.has(specifier)) return mocks.get(specifier);
  const names = importNames.get(specifier);
  assert.ok(names, `unexpected import: ${{specifier}}`);
  const mock = new vm.SyntheticModule(names, function initialize() {{
    for (const name of names) {{
      this.setExport(name, name === "state" ? {{}} : () => undefined);
    }}
  }}, {{ context, identifier: specifier }});
  mocks.set(specifier, mock);
  return mock;
}});
await module.evaluate();

const focusChatInputWhenReady = module.namespace.focusChatInputWhenReady;
assert.equal(typeof focusChatInputWhenReady, "function");

let resolveHistory;
const historyLoaded = new Promise((resolve) => {{ resolveHistory = resolve; }});
const rendered = historyLoaded.then(() => {{
  chatInput = {{ focus() {{ focusCount += 1; }} }};
  for (const observer of observers) {{
    if (observer.active) observer.callback([], observer);
  }}
}});

const stopWaiting = focusChatInputWhenReady({{ timeoutMs: 1000 }});
assert.equal(focusCount, 0);
assert.equal(observers.length, 1);
assert.equal(observers[0].active, true);
assert.equal(timers.size, 1);

resolveHistory();
await rendered;
assert.equal(focusCount, 0);
assert.equal(frames.size, 1);
fireFrame(frames.keys().next().value);
assert.equal(focusCount, 1);
assert.equal(observers[0].active, false);
assert.equal(observers[0].disconnectCount, 1);
assert.equal(timers.size, 0);
assert.equal(frames.size, 0);
observers[0].callback([], observers[0]);
assert.equal(focusCount, 1);
stopWaiting();
assert.equal(observers[0].disconnectCount, 1);

const observerCount = observers.length;
const stopPresentWait = focusChatInputWhenReady({{ timeoutMs: 1000 }});
assert.equal(focusCount, 1);
assert.equal(observers.length, observerCount);
assert.equal(timers.size, 1);
assert.equal(frames.size, 1);
fireFrame(frames.keys().next().value);
assert.equal(focusCount, 2);
assert.equal(timers.size, 0);
assert.equal(frames.size, 0);
stopPresentWait();

chatInput = null;
const stopTimeoutWait = focusChatInputWhenReady({{ timeoutMs: 1000 }});
const timeoutObserver = observers.at(-1);
assert.equal(timeoutObserver.active, true);
assert.equal(timers.size, 1);
assert.equal(frames.size, 0);
const [timerId, timeoutCallback] = timers.entries().next().value;
timers.delete(timerId);
timeoutCallback();
assert.equal(timeoutObserver.active, false);
assert.equal(timeoutObserver.disconnectCount, 1);
assert.equal(timers.size, 0);
assert.equal(frames.size, 0);
stopTimeoutWait();
assert.equal(timeoutObserver.disconnectCount, 1);

const stopScheduledWait = focusChatInputWhenReady({{ timeoutMs: 1000 }});
const scheduledObserver = observers.at(-1);
chatInput = {{ focus() {{ focusCount += 1; }} }};
scheduledObserver.callback([], scheduledObserver);
assert.equal(focusCount, 2);
assert.equal(frames.size, 1);
const [scheduledTimerId, scheduledTimeoutCallback] = timers.entries().next().value;
timers.delete(scheduledTimerId);
scheduledTimeoutCallback();
assert.equal(scheduledObserver.active, false);
assert.equal(scheduledObserver.disconnectCount, 1);
assert.equal(timers.size, 0);
assert.equal(frames.size, 0);
assert.equal(focusCount, 2);
stopScheduledWait();
assert.equal(scheduledObserver.disconnectCount, 1);
"""
    subprocess.run(
        [NODE, "--no-warnings", "--experimental-vm-modules", "--input-type=module", "-e", script],
        check=True,
    )
