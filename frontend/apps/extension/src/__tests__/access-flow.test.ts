import { describe, expect, it, vi } from "vitest";
import {
  connectFromRecipe,
  discoverRecipes,
  type BrowserArtifacts,
  type ProviderRecipe,
} from "../popup/access-flow";

const provider: ProviderRecipe = {
  providerId: "generic",
  recipe: {
    domains: ["example.com"],
    artifacts: [
      { kind: "cookie", domain: "example.com", name: "session" },
      { kind: "local_storage", domain: "example.com", name: "device" },
    ],
    warmup_url: "https://example.com/login",
    target_method_id: "builtin.manual",
  },
};

describe("recipe-driven access", () => {
  it("discovers recipes without provider-specific knowledge", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            items: [{ provider_id: "generic" }, { provider_id: "anonymous" }],
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ recipe: provider.recipe })),
      )
      .mockResolvedValueOnce(new Response("not found", { status: 404 }));

    await expect(
      discoverRecipes("http://127.0.0.1:8420", "ext-token", fetcher),
    ).resolves.toEqual([provider]);
    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8420/v1/sources/generic/access-recipe",
      expect.objectContaining({
        headers: expect.objectContaining({
          authorization: "Bearer ext-token",
        }),
      }),
    );
  });

  it("reads only declared artifacts and posts their typed identities", async () => {
    const browser: BrowserArtifacts = {
      requestOrigins: vi.fn().mockResolvedValue(true),
      open: vi.fn().mockResolvedValue(undefined),
      cookie: vi.fn().mockResolvedValue("cookie-value"),
      storage: vi.fn().mockResolvedValue("storage-value"),
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: { state: "connected" } }), {
        status: 200,
      }),
    );

    await connectFromRecipe(
      "http://127.0.0.1:8420",
      "ext-token",
      provider,
      browser,
      fetcher,
    );

    expect(browser.requestOrigins).toHaveBeenCalledWith([
      "https://*.example.com/*",
    ]);
    expect(browser.cookie).toHaveBeenCalledWith("example.com", "session");
    expect(browser.storage).toHaveBeenCalledWith(
      "example.com",
      "local_storage",
      "device",
    );
    expect(fetcher).toHaveBeenCalledWith(
      "http://127.0.0.1:8420/v1/sources/generic/access-material",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          authorization: "Bearer ext-token",
          "x-device-id": "openbiliclaw-extension",
          "x-csrf-token": "openbiliclaw-extension",
        }),
        body: JSON.stringify({
          artifacts: [
            {
              kind: "cookie",
              domain: "example.com",
              name: "session",
              value: "cookie-value",
            },
            {
              kind: "local_storage",
              domain: "example.com",
              name: "device",
              value: "storage-value",
            },
          ],
        }),
      }),
    );
  });

  it("does not post when a declared artifact is absent", async () => {
    const browser: BrowserArtifacts = {
      requestOrigins: vi.fn().mockResolvedValue(true),
      open: vi.fn().mockResolvedValue(undefined),
      cookie: vi.fn().mockResolvedValue(undefined),
      storage: vi.fn().mockResolvedValue("unused"),
    };
    const fetcher = vi.fn<typeof fetch>();

    await expect(
      connectFromRecipe(
        "http://127.0.0.1:8420",
        "ext-token",
        provider,
        browser,
        fetcher,
      ),
    ).rejects.toMatchObject({ code: "requiredArtifactUnavailable" });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
