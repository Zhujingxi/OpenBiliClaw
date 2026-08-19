import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import {
  CardRenderer,
  parseTrustedDescriptor,
  proxyImageUrl,
  sanitizeUrl,
  type CardView,
} from "../index";

const base: CardView = {
  version: 1,
  kind: "video",
  data: {
    ref: {
      provider_id: { value: "demo" },
      content_kind: { value: "video" },
      provider_content_id: "1",
      canonical_url: "https://example.test/watch/1",
    },
    title: "A useful video",
    summary: "Plain text",
    badge: null,
    image_url: "https://cdn.example.test/1.jpg",
    source_timestamp: "2030-01-01T00:00:00Z",
    provenance: {
      ref: {
        provider_id: { value: "demo" },
        content_kind: { value: "video" },
        provider_content_id: "1",
        canonical_url: "https://example.test/watch/1",
      },
      native_schema_version: 1,
      projected_at: "2030-01-01T00:00:00Z",
    },
  },
  providerLabel: "Demo",
  availability: "available",
};

it.each(["video", "image", "article", "discussion"] as const)(
  "renders accessible %s cards with shared shell controls",
  (kind) => {
    const wrapper = mount(CardRenderer, { props: { card: { ...base, kind } } });
    expect(wrapper.get("article").attributes("aria-labelledby")).toBeTruthy();
    expect(wrapper.get("h2").text()).toBe(base.data.title);
    expect(wrapper.get("a").attributes("href")).toBe(
      `#/content/${encodeURIComponent(JSON.stringify(base.data.ref))}`,
    );
    expect(wrapper.get("img").attributes("src")).toBe(
      `/v1/media?url=${encodeURIComponent(base.data.image_url ?? "")}`,
    );
    expect(wrapper.get('[role="group"]').attributes("aria-label")).toBe(
      "Feedback actions",
    );
  },
);

it("rewrites only safe HTTPS image URLs through the local proxy", () => {
  expect(proxyImageUrl("https://cdn.example.test/image?a=1&b=2")).toBe(
    "/v1/media?url=https%3A%2F%2Fcdn.example.test%2Fimage%3Fa%3D1%26b%3D2",
  );
  expect(proxyImageUrl("http://cdn.example.test/image")).toBe(
    "/v1/media?url=https%3A%2F%2Fcdn.example.test%2Fimage",
  );
  expect(proxyImageUrl("javascript:alert(1)")).toBeUndefined();
});

it("emits feedback actions with the rendered card", async () => {
  const wrapper = mount(CardRenderer, { props: { card: base } });
  await wrapper.get('[aria-label="Like recommendation"]').trigger("click");
  await wrapper.get('[aria-label="Dismiss recommendation"]').trigger("click");
  expect(wrapper.emitted("like")).toEqual([[base]]);
  expect(wrapper.emitted("dismiss")).toEqual([[base]]);
});

it("uses an observable generic fallback for unknown versions and renderers", () => {
  const unknownVersion = mount(CardRenderer, {
    props: { card: { ...base, version: 99 } },
  });
  expect(unknownVersion.find('[data-fallback="true"]').exists()).toBe(true);
  expect(unknownVersion.text()).toContain("Unsupported card");
  const unknownKind = mount(CardRenderer, {
    props: { card: { ...base, kind: "future" as CardView["kind"] } },
  });
  expect(unknownKind.find('[data-fallback="true"]').exists()).toBe(true);
});

it("handles missing media, exact summary truncation, deletion and unavailability", () => {
  const card: CardView = {
    ...base,
    data: { ...base.data, image_url: null, summary: "x".repeat(10_000) },
    availability: "deleted",
  };
  const wrapper = mount(CardRenderer, { props: { card } });
  expect(wrapper.find("img").exists()).toBe(false);
  expect(wrapper.text()).toContain("Content unavailable");
  expect(wrapper.get("[data-card-summary]").text().length).toBe(500);
  expect(
    mount(CardRenderer, {
      props: { card: { ...base, availability: "provider-unavailable" } },
    }).text(),
  ).toContain("Provider unavailable");
});

it.each(["-", "  -  ", "\n\t", "—"])(
  "hides placeholder summary %j",
  (summary) => {
    const wrapper = mount(CardRenderer, {
      props: { card: { ...base, data: { ...base.data, summary } } },
    });
    expect(wrapper.find("[data-card-summary]").exists()).toBe(false);
  },
);

it("rejects unsafe URLs, arbitrary descriptor fields and HTML execution", () => {
  expect(sanitizeUrl("javascript:alert(1)")).toBeUndefined();
  expect(sanitizeUrl("data:text/html,boom")).toBeUndefined();
  expect(sanitizeUrl("https://example.test/path")).toBe(
    "https://example.test/path",
  );
  expect(
    parseTrustedDescriptor({
      version: 1,
      kind: "video",
      renderer: "generic",
      html: "<b>x</b>",
    }),
  ).toBeUndefined();
  expect(
    parseTrustedDescriptor({
      version: 1,
      kind: "video",
      componentName: "Injected",
    }),
  ).toBe(undefined);
  expect(parseTrustedDescriptor({ version: 1, kind: "video" })).toEqual({
    version: 1,
    kind: "video",
  });
  const wrapper = mount(CardRenderer, {
    props: {
      card: {
        ...base,
        data: {
          ...base.data,
          ref: { ...base.data.ref, canonical_url: "javascript:alert(1)" },
          title: "<img src=x onerror=alert(1)>",
        },
      },
    },
  });
  expect(wrapper.get("a").attributes("href")).toContain("#/content/");
  expect(wrapper.html()).not.toContain("<img src=x");
});

it("hides the epoch timestamp sentinel while keeping real source times", () => {
  const sentinel = mount(CardRenderer, {
    props: {
      card: {
        ...base,
        data: { ...base.data, source_timestamp: "1970-01-01T00:00:00Z" },
      },
    },
  });
  expect(sentinel.find("time").exists()).toBe(false);
  expect(
    mount(CardRenderer, { props: { card: base } })
      .get("time")
      .attributes("datetime"),
  ).toBe("2030-01-01T00:00:00Z");
});
