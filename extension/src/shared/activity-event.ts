import type { ActivityEvent, ActivityKind, SourceId } from "./api-client.ts";
import type { BehaviorEvent } from "./types.ts";
import { isSecretFieldName, sanitizeOutboundUrl } from "./url-sanitizer.ts";

const SOURCE_ALIASES: Readonly<Record<string, SourceId>> = {
  bili: "bilibili",
  bilibili: "bilibili",
  xhs: "xiaohongshu",
  xiaohongshu: "xiaohongshu",
  dy: "douyin",
  douyin: "douyin",
  yt: "youtube",
  youtube: "youtube",
  x: "twitter",
  twitter: "twitter",
  zhihu: "zhihu",
  reddit: "reddit",
};

const KIND_ALIASES: Readonly<Record<string, ActivityKind>> = {
  click: "view",
  view: "view",
  play: "view",
  pause: "dwell",
  dwell: "dwell",
  like: "like",
  coin: "like",
  favorite: "favorite",
  collect: "favorite",
  search: "search",
  follow: "follow",
  feedback: "feedback",
  dislike: "feedback",
};

const CONTENT_ID_FIELDS = [
  "content_id",
  "bvid",
  "note_id",
  "aweme_id",
  "video_id",
  "tweet_id",
  "question_id",
  "post_id",
] as const;

export const ACTIVITY_EXTERNAL_ID_MAX_LENGTH = 500;
export const ACTIVITY_TITLE_MAX_LENGTH = 1_000;
export const ACTIVITY_URL_MAX_LENGTH = 2_083;

export type IdentifiedActivityEvent = ActivityEvent & { readonly id: string };

export function normalizeActivityEvent(event: BehaviorEvent): IdentifiedActivityEvent | null {
  const sourceId = SOURCE_ALIASES[event.source_platform.toLowerCase()];
  const kind = KIND_ALIASES[event.type.toLowerCase()];
  if (!sourceId || !kind || !Number.isFinite(event.timestamp)) return null;

  const metadata = sanitizeMetadata(event.metadata);
  const externalId = CONTENT_ID_FIELDS.map((field) => metadata[field])
    .find((value) => typeof value === "string" && value.trim()) as string | undefined;
  const duration = numberValue(metadata.watch_seconds)
    ?? (event.type.toLowerCase() === "pause" ? numberValue(metadata.currentTime) : null)
    ?? numberValue(metadata.duration_seconds);
  delete metadata.watch_seconds;
  delete metadata.duration_seconds;
  if (event.type.toLowerCase() === "pause") delete metadata.currentTime;
  metadata.page_type = event.context.pageType;
  metadata.scroll_position = event.context.scrollPosition;
  metadata.viewport = {
    width: event.context.viewport.width,
    height: event.context.viewport.height,
  };

  const normalized = {
    source_id: sourceId,
    kind,
    occurred_at: new Date(event.timestamp).toISOString(),
    content_external_id: externalId
      ? boundActivityString(externalId, ACTIVITY_EXTERNAL_ID_MAX_LENGTH)
      : null,
    url: event.url ? boundedActivityUrl(event.url) : null,
    title: event.title ? boundActivityString(event.title, ACTIVITY_TITLE_MAX_LENGTH) : null,
    duration_seconds: duration ?? null,
    metadata,
  };
  return { id: stableActivityId(normalized), ...normalized };
}

/** Bound by Unicode code points, matching Pydantic/JSON Schema string length. */
export function boundActivityString(value: string, maxLength: number): string {
  return [...value].slice(0, maxLength).join("");
}

/** Sanitize first, then reject URLs that cannot satisfy the ActivityEvent contract. */
export function boundedActivityUrl(value: string): string | null {
  const sanitized = sanitizeOutboundUrl(value);
  if (!sanitized || [...sanitized].length > ACTIVITY_URL_MAX_LENGTH) return null;
  return sanitized;
}

export function stableActivityId(event: Omit<ActivityEvent, "id">): string {
  const parts = hash128(stableJson(event));
  const bytes = new Uint8Array(16);
  for (let index = 0; index < 4; index += 1) {
    const value = parts[index]!;
    bytes[index * 4] = value >>> 24;
    bytes[index * 4 + 1] = value >>> 16;
    bytes[index * 4 + 2] = value >>> 8;
    bytes[index * 4 + 3] = value;
  }
  // UUIDv8 reserves the payload bits for application-defined deterministic
  // hashes; unlike v5, it does not falsely imply SHA-1 namespace semantics.
  bytes[6] = (bytes[6]! & 0x0f) | 0x80;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
      .map(([key, child]) => `${JSON.stringify(key)}:${stableJson(child)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function hash128(value: string): [number, number, number, number] {
  let h1 = 0x239b961b;
  let h2 = 0xab0e9789;
  let h3 = 0x38b34ae5;
  let h4 = 0xa1e38b93;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    h1 = Math.imul(h1 ^ code, 597399067);
    h2 = Math.imul(h2 ^ code, 2869860233);
    h3 = Math.imul(h3 ^ code, 951274213);
    h4 = Math.imul(h4 ^ code, 2716044179);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 18), 597399067);
  h2 = Math.imul(h2 ^ (h2 >>> 22), 2869860233);
  h3 = Math.imul(h3 ^ (h3 >>> 17), 951274213);
  h4 = Math.imul(h4 ^ (h4 >>> 19), 2716044179);
  return [h1 >>> 0, h2 >>> 0, h3 >>> 0, h4 >>> 0];
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function sanitizeMetadata(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, child]) => {
      if (isSecretFieldName(key) || !isJsonValue(child)) return [];
      return [[key, sanitizeJsonValue(child)]];
    }),
  );
}

function sanitizeJsonValue(value: unknown): unknown {
  if (typeof value === "string") return sanitizeOutboundUrl(value);
  if (Array.isArray(value)) return value.map(sanitizeJsonValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => (
        isSecretFieldName(key) ? [] : [[key, sanitizeJsonValue(child)]]
      )),
    );
  }
  return value;
}

function isJsonValue(value: unknown): boolean {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  if (typeof value === "object") return Object.values(value as Record<string, unknown>).every(isJsonValue);
  return false;
}
