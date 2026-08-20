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
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          health: {
            checked_at: "2025-01-01T00:00:00Z",
            component_id: "runtime",
            jobs: [],
            status: "healthy",
          },
        }),
        { status: 200 },
      ),
    );
    const store = useConnectionStore();
    store.backendUrl = "http://127.0.0.1:8765";
    await store.check(fetcher);
    expect(store.state).toBe("connected");
    fetcher.mockResolvedValue(new Response("secret body", { status: 503 }));
    await store.check(fetcher);
    expect(store.state).toBe("unavailable");
    expect(store.error).toEqual({ code: "backendUnavailable", status: 503 });
  });
});

it("hydrates canonical saved connection and uses the typed client", async () => {
  const storage = {
    getItem: () =>
      JSON.stringify({
        backendUrl: "http://127.0.0.1:8420",
        deviceToken: "saved",
      }),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  };
  const store = useConnectionStore();
  store.hydrate(storage);
  expect(store.backendUrl).toBe("http://127.0.0.1:8420");
  expect(store.deviceToken).toBe("saved");
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(
      JSON.stringify({
        health: {
          checked_at: "2025-01-01T00:00:00Z",
          component_id: "runtime",
          jobs: [],
          status: "healthy",
        },
      }),
      { status: 200 },
    ),
  );
  await store.check(fetcher);
  expect(fetcher).toHaveBeenCalledWith(
    "http://127.0.0.1:8420/v1/runtime/health",
    expect.objectContaining({ method: "GET" }),
  );
});
