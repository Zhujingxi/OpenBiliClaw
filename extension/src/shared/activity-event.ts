import type { ActivityEvent, ActivityKind, SourceId } from "./api-client.ts";
import type { BehaviorEvent } from "./types.ts";

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

export function normalizeActivityEvent(event: BehaviorEvent): ActivityEvent | null {
  const sourceId = SOURCE_ALIASES[event.source_platform.toLowerCase()];
  const kind = KIND_ALIASES[event.type.toLowerCase()];
  if (!sourceId || !kind || !Number.isFinite(event.timestamp)) return null;

  const metadata = sanitizeMetadata(event.metadata);
  const externalId = CONTENT_ID_FIELDS.map((field) => metadata[field])
    .find((value) => typeof value === "string" && value.trim()) as string | undefined;
  const duration = numberValue(metadata.watch_seconds) ?? numberValue(metadata.duration_seconds);
  delete metadata.watch_seconds;
  delete metadata.duration_seconds;
  metadata.page_type = event.context.pageType;
  metadata.scroll_position = event.context.scrollPosition;
  metadata.viewport = {
    width: event.context.viewport.width,
    height: event.context.viewport.height,
  };

  return {
    source_id: sourceId,
    kind,
    occurred_at: new Date(event.timestamp).toISOString(),
    content_external_id: externalId ?? null,
    url: event.url || null,
    title: event.title || null,
    duration_seconds: duration ?? null,
    metadata,
  };
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function sanitizeMetadata(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(value).filter(([, child]) => isJsonValue(child)),
  );
}

function isJsonValue(value: unknown): boolean {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  if (typeof value === "object") return Object.values(value as Record<string, unknown>).every(isJsonValue);
  return false;
}
