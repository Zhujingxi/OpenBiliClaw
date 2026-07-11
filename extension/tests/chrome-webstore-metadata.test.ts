import assert from "node:assert/strict";
import test from "node:test";

import {
  buildMetadataPayload,
  parseListingMarkdown,
  summarizeDraft,
  validateListingMetadata,
  verifyMetadataReadback,
} from "../scripts/chrome-webstore-metadata-lib.mjs";

const markdown = `# Chrome Web Store 商店页文案与素材

- 项目主页 / Website URL: <https://whiteguo233.github.io/OpenBiliClaw/>
- 支持 / Support URL: <https://github.com/whiteguo233/OpenBiliClaw/issues>

## Short Description

\`\`\`text
需本地后端的七平台内容发现 AI Agent：跨平台推荐、私有画像与可反馈侧边栏
\`\`\`

## Detailed Description

\`\`\`text
OpenBiliClaw 需要本地后端，平台数据默认保存在你的本机。
\`\`\`
`;

test("parses canonical copy and validates local-data claims", () => {
  const listing = parseListingMarkdown(markdown);

  assert.match(listing.summary, /需本地后端/);
  assert.match(listing.description, /保存在你的本机/);
  assert.equal(listing.homepageUrl, "https://whiteguo233.github.io/OpenBiliClaw/");
  assert.equal(listing.supportUrl, "https://github.com/whiteguo233/OpenBiliClaw/issues");
  assert.doesNotThrow(() => validateListingMetadata(listing));
});

test("rejects empty, overlong, or misleading canonical copy", () => {
  const listing = parseListingMarkdown(markdown);

  assert.throws(
    () => validateListingMetadata({ ...listing, summary: "字".repeat(133) }),
    /132/,
  );
  assert.throws(
    () => validateListingMetadata({ ...listing, summary: "普通插件", description: "云端服务" }),
    /本地后端/,
  );
  assert.throws(
    () => validateListingMetadata({ ...listing, description: "需要本地后端" }),
    /本机|本地数据/,
  );
});

test("summarizes drafts without exposing raw values", () => {
  const draft = {
    title: "OpenBiliClaw",
    summary: "old summary secret",
    description: "old description secret",
    screenshots: ["private-image-id"],
    promotionalImages: ["private-promo-id"],
  };

  const summary = summarizeDraft(draft);
  const serialized = JSON.stringify(summary);

  assert.deepEqual(summary.fieldNames, [
    "description",
    "promotionalImages",
    "screenshots",
    "summary",
    "title",
  ]);
  assert.deepEqual(summary.assetFieldNames, ["promotionalImages", "screenshots"]);
  assert.equal(summary.summary.present, true);
  assert.equal(summary.summary.length, 18);
  assert.equal(summary.summary.sha256.length, 64);
  assert.doesNotMatch(serialized, /old summary secret|old description secret|private-image-id/);
});

test("builds an allowlisted payload and replaces documented URLs only when present", () => {
  const listing = parseListingMarkdown(markdown);
  const draft = {
    title: "OpenBiliClaw",
    category: "PRODUCTIVITY",
    defaultLocale: "zh_CN",
    homepageUrl: "https://old.example/",
    supportUrl: "https://old.example/issues",
    summary: "old",
    description: "old private description",
    status: "PENDING_REVIEW",
    screenshots: ["secret"],
  };

  assert.deepEqual(buildMetadataPayload(draft, listing), {
    title: "OpenBiliClaw",
    category: "PRODUCTIVITY",
    defaultLocale: "zh_CN",
    homepageUrl: listing.homepageUrl,
    supportUrl: listing.supportUrl,
    summary: listing.summary,
    description: listing.description,
  });
  assert.deepEqual(
    buildMetadataPayload(
      {
        title: "OpenBiliClaw",
        defaultLocale: "zh_CN",
        summary: "old",
        description: "old",
      },
      listing,
    ),
    {
      title: "OpenBiliClaw",
      defaultLocale: "zh_CN",
      summary: listing.summary,
      description: listing.description,
    },
  );
});

test("requires enough draft identity and exact metadata read-back", () => {
  const listing = parseListingMarkdown(markdown);

  assert.throws(
    () => buildMetadataPayload({ summary: "old", description: "old" }, listing),
    /identity/,
  );
  assert.doesNotThrow(() => verifyMetadataReadback({ ...listing }, listing));
  assert.throws(
    () =>
      verifyMetadataReadback(
        { ...listing, description: `${listing.description} changed` },
        listing,
      ),
    /read-back/,
  );
});
