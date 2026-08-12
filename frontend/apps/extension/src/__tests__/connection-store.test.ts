import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useConnectionStore } from "../popup/connection-store";

describe("connection store", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("persists only backend URL and opaque device token", () => {
    const storage = new Map<string, string>();
    const store = useConnectionStore();
    store.configure("http://127.0.0.1:8765", "device-token", {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    });
    expect(
      JSON.parse(storage.get("openbiliclaw.connection") ?? "null"),
    ).toEqual({
      backendUrl: "http://127.0.0.1:8765",
      deviceToken: "device-token",
    });
  });

  it("reports connected and unavailable outcomes without leaking response bodies", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ status: "healthy" }), { status: 200 }),
      );
    const store = useConnectionStore();
    store.backendUrl = "http://127.0.0.1:8765";
    await store.check(fetcher);
    expect(store.state).toBe("connected");
    fetcher.mockResolvedValue(new Response("secret body", { status: 503 }));
    await store.check(fetcher);
    expect(store.state).toBe("unavailable");
    expect(store.error).toBe("Backend unavailable (503)");
  });
});
