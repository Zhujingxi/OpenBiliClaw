import type { ActivityEvent } from "../shared/api-client.ts";
import {
  ACTIVITY_EXTERNAL_ID_MAX_LENGTH,
  ACTIVITY_TITLE_MAX_LENGTH,
  boundActivityString,
  boundedActivityUrl,
  stableActivityId,
  type IdentifiedActivityEvent,
} from "../shared/activity-event.ts";

export function xhsObservationEvents(value: unknown): IdentifiedActivityEvent[] {
  if (!value || typeof value !== "object") return [];
  const observation = value as Record<string, unknown>;
  const notes = Array.isArray(observation.notes) ? observation.notes : [];
  const urls = Array.isArray(observation.urls) ? observation.urls : [];
  const rows: unknown[] = [...notes];
  const represented = new Set(notes.flatMap((raw) => {
    const row = asRecord(raw);
    const rawUrl = String(row.url ?? "");
    const externalId = String(row.note_id ?? noteIdFromUrl(rawUrl) ?? "");
    return externalId ? [`id:${externalId}`] : rawUrl ? [`url:${safeObservedUrl(rawUrl) ?? ""}`] : [];
  }));
  for (const rawUrl of urls) {
    const url = String(rawUrl ?? "");
    const externalId = noteIdFromUrl(url);
    const key = externalId ? `id:${externalId}` : `url:${safeObservedUrl(url) ?? ""}`;
    if (!represented.has(key)) {
      rows.push({ url });
      represented.add(key);
    }
  }

  const events: IdentifiedActivityEvent[] = [];
  for (const raw of rows) {
    const row = asRecord(raw);
    const rawUrl = String(row.url ?? "");
    const externalId = String(row.note_id ?? noteIdFromUrl(rawUrl) ?? "");
    if (!rawUrl && !externalId) continue;
    const event: Omit<ActivityEvent, "id"> = {
      source_id: "xiaohongshu",
      kind: "import",
      occurred_at: new Date(Number(observation.observed_at) || Date.now()).toISOString(),
      content_external_id: externalId
        ? boundActivityString(externalId, ACTIVITY_EXTERNAL_ID_MAX_LENGTH)
        : null,
      url: safeObservedUrl(rawUrl),
      title: typeof row.title === "string"
        ? boundActivityString(row.title, ACTIVITY_TITLE_MAX_LENGTH)
        : null,
      metadata: { page_type: String(observation.page_type ?? "other") },
    };
    events.push({ id: stableActivityId(event), ...event });
  }
  return events;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function noteIdFromUrl(value: string): string | null {
  return value.match(/\/(?:explore|discovery\/item)\/([0-9a-z]+)/i)?.[1] ?? null;
}

function safeObservedUrl(value: string): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || !isSourceHost(url.hostname, "xiaohongshu.com")) return null;
    return boundedActivityUrl(url.href);
  } catch {
    return null;
  }
}

function isSourceHost(hostname: string, expected: string): boolean {
  return hostname === expected || hostname.endsWith(`.${expected}`);
}
