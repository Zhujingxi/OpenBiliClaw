import { createHash } from "node:crypto";

const PRESERVED_FIELDS = [
  "title",
  "category",
  "defaultLocale",
  "homepageUrl",
  "supportUrl",
];

function fencedBlock(markdown, heading) {
  const marker = `## ${heading}`;
  const headingStart = markdown.indexOf(marker);
  if (headingStart < 0) {
    throw new Error(`Missing ${heading} heading`);
  }
  const sectionStart = headingStart + marker.length;
  const nextHeading = markdown.indexOf("\n## ", sectionStart);
  const section = markdown.slice(
    sectionStart,
    nextHeading >= 0 ? nextHeading : markdown.length,
  );
  const match = section.match(/```(?:text)?\s*\n([\s\S]*?)\n```/);
  if (!match) {
    throw new Error(`Missing ${heading} fenced block`);
  }
  return match[1].trim();
}

function documentedUrl(markdown, labels) {
  for (const label of labels) {
    const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = markdown.match(
      new RegExp(`^- ${escaped}:\\s*<?(https://[^>\\s]+)>?\\s*$`, "m"),
    );
    if (match) {
      return match[1];
    }
  }
  throw new Error(`Missing documented URL: ${labels.join(" or ")}`);
}

export function parseListingMarkdown(markdown) {
  return {
    summary: fencedBlock(markdown, "Short Description"),
    description: fencedBlock(markdown, "Detailed Description"),
    homepageUrl: documentedUrl(markdown, ["项目主页 / Website URL", "Homepage"]),
    supportUrl: documentedUrl(markdown, [
      "支持 / Support URL",
      "支持 / GitHub 项目页",
      "Support",
    ]),
  };
}

export function validateListingMetadata(listing) {
  if (!listing.summary || !listing.description) {
    throw new Error("Summary and description are required");
  }
  if ([...listing.summary].length > 132) {
    throw new Error("Summary exceeds the Chrome Web Store 132-character limit");
  }
  const allCopy = `${listing.summary}\n${listing.description}`;
  if (!allCopy.includes("本地后端")) {
    throw new Error("Listing copy must disclose 本地后端");
  }
  if (!/(?:保存在[^。\n]*本机|本地数据)/.test(listing.description)) {
    throw new Error("Listing copy must disclose that data stays on 本机 or uses 本地数据");
  }
}

function sha256(value) {
  return createHash("sha256").update(String(value ?? "")).digest("hex");
}

function fieldSummary(value) {
  return {
    present: typeof value === "string",
    length: [...String(value ?? "")].length,
    sha256: sha256(value),
  };
}

export function summarizeDraft(draft) {
  const fieldNames = Object.keys(draft).sort();
  return {
    fieldNames,
    summary: fieldSummary(draft.summary),
    description: fieldSummary(draft.description),
    assetFieldNames: fieldNames.filter((field) => /(image|screenshot)/i.test(field)),
  };
}

export function buildMetadataPayload(draft, listing) {
  if (typeof draft.title !== "string" || typeof draft.defaultLocale !== "string") {
    throw new Error("Draft lacks the title/defaultLocale identity fields required for a safe update");
  }

  const payload = {};
  for (const field of PRESERVED_FIELDS) {
    if (typeof draft[field] === "string") {
      payload[field] = draft[field];
    }
  }
  if (Object.hasOwn(draft, "homepageUrl")) {
    payload.homepageUrl = listing.homepageUrl;
  }
  if (Object.hasOwn(draft, "supportUrl")) {
    payload.supportUrl = listing.supportUrl;
  }
  payload.summary = listing.summary;
  payload.description = listing.description;
  return payload;
}

export function verifyMetadataReadback(actual, expected) {
  if (actual.summary !== expected.summary || actual.description !== expected.description) {
    throw new Error("Metadata read-back did not exactly match the canonical listing copy");
  }
}
