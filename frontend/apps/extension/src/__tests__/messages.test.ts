import { describe, expect, it } from "vitest";
import { parseExtensionMessage } from "../shared/messages";

describe("extension message boundary", () => {
  it("accepts each bounded discriminated message", () => {
    expect(parseExtensionMessage({ kind: "connection.get" })).toEqual({
      kind: "connection.get",
    });
    expect(
      parseExtensionMessage({
        kind: "connection.set",
        backendUrl: "http://127.0.0.1:8765",
        deviceToken: "token",
      }),
    ).toEqual({
      kind: "connection.set",
      backendUrl: "http://127.0.0.1:8765",
      deviceToken: "token",
    });
    expect(
      parseExtensionMessage({
        kind: "connection.status",
        state: "connected",
        backendUrl: "http://127.0.0.1:8765",
      }),
    ).toEqual({
      kind: "connection.status",
      state: "connected",
      backendUrl: "http://127.0.0.1:8765",
    });
  });

  it.each([
    null,
    {},
    { kind: "task.dispatch", provider: "bilibili" },
    {
      kind: "connection.set",
      backendUrl: "https://remote.test",
      deviceToken: "x",
    },
    {
      kind: "connection.status",
      state: "magic",
      backendUrl: "http://127.0.0.1",
    },
    {
      kind: "connection.set",
      backendUrl: "http://127.0.0.1",
      deviceToken: "x",
      cookie: "secret",
    },
  ])("rejects untrusted or excluded messages: %j", (value) => {
    expect(parseExtensionMessage(value)).toBeUndefined();
  });
});
