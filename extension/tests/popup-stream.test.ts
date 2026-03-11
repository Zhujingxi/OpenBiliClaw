import test from "node:test";
import assert from "node:assert/strict";

import {
  createRuntimeStreamClient,
  createRuntimeStreamUrl,
} from "../popup/popup-stream.js";

test("createRuntimeStreamUrl converts backend http url to websocket runtime stream", () => {
  assert.equal(
    createRuntimeStreamUrl("http://127.0.0.1:8420/api"),
    "ws://127.0.0.1:8420/api/runtime-stream",
  );
  assert.equal(
    createRuntimeStreamUrl("https://api.example.com/api"),
    "wss://api.example.com/api/runtime-stream",
  );
});

test("runtime stream client dispatches parsed events", async () => {
  const received: Array<Record<string, unknown>> = [];

  class FakeWebSocket {
    static latest: FakeWebSocket | null = null;
    url: string;
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: (() => void) | null = null;

    constructor(url: string) {
      this.url = url;
      FakeWebSocket.latest = this;
    }

    close() {}
  }

  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    onEvent(event) {
      received.push(event);
    },
  });

  client.connect();
  FakeWebSocket.latest?.onmessage?.({
    data: JSON.stringify({
      type: "refresh.strategy",
      message: "先从你刚刚的口味里搜一轮",
      pool_available_count: 42,
    }),
  });

  assert.deepEqual(received, [
    {
      type: "refresh.strategy",
      message: "先从你刚刚的口味里搜一轮",
      pool_available_count: 42,
    },
  ]);
});
