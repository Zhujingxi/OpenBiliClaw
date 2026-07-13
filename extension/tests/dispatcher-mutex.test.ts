import assert from "node:assert/strict";
import test from "node:test";

import {
  dispatcherMutexHolder,
  releaseDispatcherMutex,
  tryAcquireDispatcherMutex,
} from "../src/background/dispatcher-mutex.ts";

interface MutexGlobals {
  __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
  __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
}

test("dispatcher mutex shares the established legacy global keys", () => {
  const globals = globalThis as MutexGlobals;
  globals.__OBC_DISPATCHER_MUTEX_HOLDER__ = "legacy-xhs";
  globals.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();
  try {
    assert.equal(dispatcherMutexHolder(), "legacy-xhs");
    assert.equal(tryAcquireDispatcherMutex("native-save:reddit"), false);
    globals.__OBC_DISPATCHER_MUTEX_HOLDER__ = undefined;
    globals.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = undefined;
    assert.equal(tryAcquireDispatcherMutex("native-save:reddit"), true);
    assert.equal(globals.__OBC_DISPATCHER_MUTEX_HOLDER__, "native-save:reddit");
    releaseDispatcherMutex("native-save:reddit");
    assert.equal(globals.__OBC_DISPATCHER_MUTEX_HOLDER__, undefined);
  } finally {
    globals.__OBC_DISPATCHER_MUTEX_HOLDER__ = undefined;
    globals.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = undefined;
  }
});
