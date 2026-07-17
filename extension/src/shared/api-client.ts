// Generated from openapi/openapi.json by openapi/generate-client.mjs. Do not edit.
export type AIHealthResponse = { "aliases": ReadonlyArray<AliasHealthResponse>; "proxy_reachable": boolean; };
export type ActivityEvent = { "account_id"?: string | null; "content_external_id"?: string | null; "duration_seconds"?: number | null; "id"?: string; "kind": ActivityKind; "metadata"?: { [key: string]: unknown; }; "occurred_at"?: string; "source_id": string; "text"?: string | null; "title"?: string | null; "url"?: string | null; };
export type ActivityKind = "import" | "view" | "dwell" | "like" | "favorite" | "search" | "follow" | "feedback" | "chat_learning" | "profile_override";
export type AliasHealthResponse = { "alias": "obc-interactive" | "obc-analysis" | "obc-embedding"; "available": boolean; "reason"?: string | null; "state": "healthy" | "degraded" | "unavailable"; };
export type ChatChunk = { "content": string; "kind": ChatChunkKind; "turn_id": string; };
export type ChatChunkKind = "delta" | "done";
export type ChatDoneEvent = { "content"?: string | null; "kind"?: "done" | null; "status"?: "failed" | null; "turn_id"?: string | null; };
export type ChatRequest = { "conversation_id": string; "learn"?: boolean; "message": string; };
export type ClaimedSourceTask = { "id": string; "lease_expires_at": string; "lease_token": string; "operation": SourceOperation; "payload"?: { [key: string]: unknown; }; "request_deadline_at": string; "source_id": SourceId; };
export type CollectionItem = { "added_at"?: string; "collection": CollectionKind; "content_id": string; "id"?: string; "note"?: string; };
export type CollectionKind = "favorites" | "watch_later";
export type CompleteSourceTask = { "lease_token": string; "result": { [key: string]: unknown; }; };
export type ContentItem = { "creator"?: string | null; "external_id": string; "id"?: string; "media_type"?: string; "metadata"?: { [key: string]: unknown; }; "published_at"?: string | null; "source_id": string; "summary"?: string; "title": string; "url": string; };
export type EventIngestResponse = { "event_id": string; "signals": ReadonlyArray<ProfileSignal>; };
export type FeedEntry = { "admitted_at"?: string; "assessment_id"?: string | null; "content_id": string; "explanation"?: string; "id"?: string; "position": number; };
export type FeedItem = { "content": ContentItem; "entry": FeedEntry; };
export type HTTPValidationError = { "detail"?: ReadonlyArray<ValidationError>; };
export type Interaction = { "content_id": string; "id"?: string; "kind": InteractionKind; "metadata"?: { [key: string]: unknown; }; "occurred_at"?: string; };
export type InteractionKind = "impression" | "open" | "positive" | "negative" | "save_favorite" | "save_watch_later" | "dismiss";
export type InteractionResponse = { "interaction": Interaction; "signal": ProfileSignal; };
export type JobPriorityLane = "interactive" | "user-triggered" | "scheduled";
export type JobRunResponse = { "attempts": number; "created_at": string; "dispatched_at"?: string | null; "error"?: string | null; "finished_at"?: string | null; "id": string; "idempotency_key": string; "job_name": string; "priority": number; "progress": number; "started_at"?: string | null; "status": "pending" | "running" | "succeeded" | "failed" | "cancelled"; "updated_at": string; };
export type OnboardingProgressEvent = { "onboarding_complete": boolean; "root_run_id": string; "run": JobRunResponse; "stage": "source_sync" | "profile_projection" | "feed_replenishment"; };
export type OnboardingStart = { "source_ids": ReadonlyArray<SourceId>; };
export type OnboardingTerminalEvent = { "onboarding_complete": boolean; "root_run_id": string; "run_id": string; "stage": "source_sync" | "profile_projection" | "feed_replenishment"; "status": "succeeded" | "failed" | "cancelled"; };
export type ProfileFacet = { "confidence": number; "evidence_ids": ReadonlyArray<string>; "name": "interests" | "avoidances" | "style_preferences" | "values" | "source_affinities"; "overridden"?: boolean; "value": string; "weight": number; };
export type ProfileSignal = { "confidence": number; "evidence_ids": ReadonlyArray<string>; "facet": string; "override"?: boolean; "value": string; "weight": number; };
export type ProfileSnapshot = { "confidence"?: number; "created_at"?: string; "facets"?: ReadonlyArray<ProfileFacet>; "id"?: string; "narrative"?: string; "revision": number; };
export type ReadinessResponse = { "ready": boolean; "version": string; };
export type SaveCollectionItem = { "content_id": string; "note"?: string; };
export type ScheduleJob = { "idempotency_key": string; "job_name": "source_sync" | "profile_projection" | "feed_replenishment" | "cleanup"; "priority"?: JobPriorityLane | null; };
export type SourceAccountStatus = { "account_key": string; "configured"?: boolean; "enabled": boolean; "source_id": SourceId; };
export type SourceCapability = "authentication" | "bootstrap_import" | "activity_collection" | "search" | "trending_feed" | "related_discovery" | "creator_discovery" | "community_discovery" | "browser_assisted";
export type SourceConfiguration = { "account_key": string; "credentials": { [key: string]: unknown; }; };
export type SourceId = "bilibili" | "xiaohongshu" | "douyin" | "youtube" | "twitter" | "zhihu" | "reddit";
export type SourceManifest = { "capabilities": ReadonlyArray<SourceCapability>; "display_name": string; "operations": ReadonlyArray<SourceOperationSpec>; "source_id": SourceId; };
export type SourceOperation = "bootstrap_import" | "search" | "trending" | "feed" | "related" | "creator" | "community";
export type SourceOperationSpec = { "capability": SourceCapability; "fallback_transport_kind"?: SourceTransportKind | null; "operation": SourceOperation; "requires_auth": boolean; "result_kind": SourceResultKind; "transport_kind": SourceTransportKind; };
export type SourceResultKind = "activity" | "content";
export type SourceTaskCompletion = { "completed_at": string; "id": string; "idempotent": boolean; };
export type SourceTransportKind = "direct" | "cli" | "browser";
export type StreamErrorEvent = { "code": string; };
export type StreamTerminalEvent = { "id"?: string | null; "status": "succeeded" | "failed" | "cancelled"; };
export type UserSettings = { "feed_high_watermark"?: number; "feed_low_watermark"?: number; "onboarding_complete"?: boolean; "source_enabled"?: { [key: string]: boolean; }; "source_sync_interval_minutes"?: number; "source_weights"?: { [key: string]: number; }; };
export type UserSettingsPatch = { "feed_high_watermark"?: number | null; "feed_low_watermark"?: number | null; "source_enabled"?: { [key: string]: boolean; } | null; "source_sync_interval_minutes"?: number | null; "source_weights"?: { [key: string]: number; } | null; };
export type ValidationError = { "ctx"?: {  }; "input"?: unknown; "loc": ReadonlyArray<string | number>; "msg": string; "type": string; };
export type ApiOperationId = "v1_chat_stream" | "v1_events_ingest" | "v1_feed_list" | "v1_interactions_create" | "v1_jobs_cancel" | "v1_jobs_events" | "v1_jobs_get" | "v1_jobs_list" | "v1_jobs_schedule" | "v1_library_add" | "v1_library_list" | "v1_library_remove" | "v1_onboarding_events" | "v1_onboarding_get" | "v1_onboarding_start" | "v1_profile_get" | "v1_settings_get" | "v1_settings_patch" | "v1_source_tasks_claim" | "v1_source_tasks_complete" | "v1_sources_configure_account" | "v1_sources_list" | "v1_sources_status" | "v1_system_ai_health" | "v1_system_readiness";
export const API_OPERATIONS = {
  "v1_chat_stream": {
    "method": "POST",
    "path": "/api/v1/chat/stream",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": "ChatRequest",
    "responseType": null,
    "stream": true,
    "sseEvents": {
      "delta": {
        "schema": {
          "$ref": "#/components/schemas/ChatChunk"
        }
      },
      "done": {
        "schema": {
          "$ref": "#/components/schemas/ChatDoneEvent"
        }
      },
      "error": {
        "schema": {
          "$ref": "#/components/schemas/StreamErrorEvent"
        }
      }
    }
  },
  "v1_events_ingest": {
    "method": "POST",
    "path": "/api/v1/events",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": "ActivityEvent",
    "responseType": "EventIngestResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_feed_list": {
    "method": "GET",
    "path": "/api/v1/feed",
    "pathParameters": [],
    "queryParameters": [
      "limit",
      "offset"
    ],
    "requestType": null,
    "responseType": "FeedItem",
    "stream": false,
    "sseEvents": {}
  },
  "v1_interactions_create": {
    "method": "POST",
    "path": "/api/v1/interactions",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": "Interaction",
    "responseType": "InteractionResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_jobs_cancel": {
    "method": "DELETE",
    "path": "/api/v1/jobs/{run_id}",
    "pathParameters": [
      "run_id"
    ],
    "queryParameters": [],
    "requestType": null,
    "responseType": "JobRunResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_jobs_events": {
    "method": "GET",
    "path": "/api/v1/jobs/{run_id}/events",
    "pathParameters": [
      "run_id"
    ],
    "queryParameters": [],
    "requestType": null,
    "responseType": null,
    "stream": true,
    "sseEvents": {
      "done": {
        "schema": {
          "$ref": "#/components/schemas/StreamTerminalEvent"
        }
      },
      "error": {
        "schema": {
          "$ref": "#/components/schemas/StreamErrorEvent"
        }
      },
      "progress": {
        "schema": {
          "$ref": "#/components/schemas/JobRunResponse"
        }
      }
    }
  },
  "v1_jobs_get": {
    "method": "GET",
    "path": "/api/v1/jobs/{run_id}",
    "pathParameters": [
      "run_id"
    ],
    "queryParameters": [],
    "requestType": null,
    "responseType": "JobRunResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_jobs_list": {
    "method": "GET",
    "path": "/api/v1/jobs",
    "pathParameters": [],
    "queryParameters": [
      "limit"
    ],
    "requestType": null,
    "responseType": "JobRunResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_jobs_schedule": {
    "method": "POST",
    "path": "/api/v1/jobs",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": "ScheduleJob",
    "responseType": "JobRunResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_library_add": {
    "method": "POST",
    "path": "/api/v1/library/{collection}",
    "pathParameters": [
      "collection"
    ],
    "queryParameters": [],
    "requestType": "SaveCollectionItem",
    "responseType": "CollectionItem",
    "stream": false,
    "sseEvents": {}
  },
  "v1_library_list": {
    "method": "GET",
    "path": "/api/v1/library/{collection}",
    "pathParameters": [
      "collection"
    ],
    "queryParameters": [],
    "requestType": null,
    "responseType": "CollectionItem",
    "stream": false,
    "sseEvents": {}
  },
  "v1_library_remove": {
    "method": "DELETE",
    "path": "/api/v1/library/{collection}/{content_id}",
    "pathParameters": [
      "collection",
      "content_id"
    ],
    "queryParameters": [],
    "requestType": null,
    "responseType": null,
    "stream": false,
    "sseEvents": {}
  },
  "v1_onboarding_events": {
    "method": "GET",
    "path": "/api/v1/onboarding/{run_id}/events",
    "pathParameters": [
      "run_id"
    ],
    "queryParameters": [],
    "requestType": null,
    "responseType": null,
    "stream": true,
    "sseEvents": {
      "done": {
        "schema": {
          "$ref": "#/components/schemas/OnboardingTerminalEvent"
        }
      },
      "error": {
        "schema": {
          "$ref": "#/components/schemas/StreamErrorEvent"
        }
      },
      "progress": {
        "schema": {
          "$ref": "#/components/schemas/OnboardingProgressEvent"
        }
      }
    }
  },
  "v1_onboarding_get": {
    "method": "GET",
    "path": "/api/v1/onboarding",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": null,
    "responseType": "UserSettings",
    "stream": false,
    "sseEvents": {}
  },
  "v1_onboarding_start": {
    "method": "POST",
    "path": "/api/v1/onboarding/start",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": "OnboardingStart",
    "responseType": "JobRunResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_profile_get": {
    "method": "GET",
    "path": "/api/v1/profile",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": null,
    "responseType": "ProfileSnapshot",
    "stream": false,
    "sseEvents": {}
  },
  "v1_settings_get": {
    "method": "GET",
    "path": "/api/v1/settings",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": null,
    "responseType": "UserSettings",
    "stream": false,
    "sseEvents": {}
  },
  "v1_settings_patch": {
    "method": "PATCH",
    "path": "/api/v1/settings",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": "UserSettingsPatch",
    "responseType": "UserSettings",
    "stream": false,
    "sseEvents": {}
  },
  "v1_source_tasks_claim": {
    "method": "GET",
    "path": "/api/v1/source-tasks/claim",
    "pathParameters": [],
    "queryParameters": [
      "source_id",
      "wait_seconds"
    ],
    "requestType": null,
    "responseType": null,
    "stream": false,
    "sseEvents": {}
  },
  "v1_source_tasks_complete": {
    "method": "POST",
    "path": "/api/v1/source-tasks/{task_id}/complete",
    "pathParameters": [
      "task_id"
    ],
    "queryParameters": [],
    "requestType": "CompleteSourceTask",
    "responseType": "SourceTaskCompletion",
    "stream": false,
    "sseEvents": {}
  },
  "v1_sources_configure_account": {
    "method": "PUT",
    "path": "/api/v1/sources/{source_id}/accounts",
    "pathParameters": [
      "source_id"
    ],
    "queryParameters": [],
    "requestType": "SourceConfiguration",
    "responseType": "SourceAccountStatus",
    "stream": false,
    "sseEvents": {}
  },
  "v1_sources_list": {
    "method": "GET",
    "path": "/api/v1/sources",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": null,
    "responseType": "SourceManifest",
    "stream": false,
    "sseEvents": {}
  },
  "v1_sources_status": {
    "method": "GET",
    "path": "/api/v1/sources/status",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": null,
    "responseType": "SourceAccountStatus",
    "stream": false,
    "sseEvents": {}
  },
  "v1_system_ai_health": {
    "method": "GET",
    "path": "/api/v1/system/ai-health",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": null,
    "responseType": "AIHealthResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_system_readiness": {
    "method": "GET",
    "path": "/api/v1/system/readiness",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": null,
    "responseType": "ReadinessResponse",
    "stream": false,
    "sseEvents": {}
  }
} as const;

export type ApiPathValues = Readonly<Record<string, string | number>>;
export type ApiQueryValues = Readonly<Record<string, string | number | boolean | null | undefined>>;
export interface ApiRequestInput { readonly path?: ApiPathValues; readonly query?: ApiQueryValues; readonly body?: unknown; readonly headers?: HeadersInit; readonly signal?: AbortSignal; }
export interface ApiSseEvent<T = unknown> { readonly event: string; readonly data: T; }
export interface ApiClientOptions { readonly baseUrl?: string; readonly fetchImpl?: typeof fetch; readonly getAccessToken?: () => string | null | Promise<string | null>; }
export interface ApiClient { request<T = unknown>(operationId: ApiOperationId, input?: ApiRequestInput): Promise<T>; readSse<T = unknown>(operationId: ApiOperationId, input?: ApiRequestInput, onEvent?: (event: ApiSseEvent<T>) => void | Promise<void>): Promise<void>; }
export class ApiClientError extends Error {
  readonly operationId: ApiOperationId; readonly status: number; readonly details: unknown;
  constructor(operationId: ApiOperationId, response: Response, details: unknown) { super(`${operationId} failed: ${response.status}`); this.name = "ApiClientError"; this.operationId = operationId; this.status = response.status; this.details = details; }
}
type Operation = (typeof API_OPERATIONS)[ApiOperationId];
export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const baseUrl = String(options.baseUrl ?? "").replace(/\/$/, "");
  const fetchImpl = options.fetchImpl ?? globalThis.fetch?.bind(globalThis);
  if (typeof fetchImpl !== "function") throw new TypeError("fetch implementation required");
  const getAccessToken = options.getAccessToken ?? (() => null);
  async function fetchOperation(operation: Operation, input: ApiRequestInput, stream: boolean): Promise<Response> {
    const headers = new Headers(input.headers ?? {}); if (stream) headers.set("Accept", "text/event-stream");
    const token = await getAccessToken(); if (token) headers.set("Authorization", `Bearer ${token}`);
    const init: RequestInit = { method: operation.method, headers, signal: input.signal };
    if (input.body !== undefined) { headers.set("Content-Type", "application/json"); init.body = JSON.stringify(input.body); }
    return fetchImpl(buildOperationUrl(baseUrl, operation, input.path, input.query), init);
  }
  async function request<T = unknown>(operationId: ApiOperationId, input: ApiRequestInput = {}): Promise<T> {
    const response = await fetchOperation(API_OPERATIONS[operationId], input, false);
    if (!response.ok) { let details: unknown = null; try { details = await response.json(); } catch { /* empty */ } throw new ApiClientError(operationId, response, details); }
    if (response.status === 204) return null as T; return await response.json() as T;
  }
  async function readSse<T = unknown>(operationId: ApiOperationId, input: ApiRequestInput = {}, onEvent: (event: ApiSseEvent<T>) => void | Promise<void> = () => {}): Promise<void> {
    const operation = API_OPERATIONS[operationId]; if (!operation.stream) throw new TypeError(`operation is not an SSE stream: ${operationId}`);
    const response = await fetchOperation(operation, input, true); if (!response.ok || !response.body) throw new ApiClientError(operationId, response, null);
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader(); let buffer = "";
    while (true) { const { value, done } = await reader.read(); if (done) break; buffer += value; let boundary = buffer.indexOf("\n\n"); while (boundary >= 0) { const event = parseSseFrame<T>(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2); if (event) await onEvent(event); boundary = buffer.indexOf("\n\n"); } }
  }
  return Object.freeze({ request, readSse });
}
export function buildOperationUrl(baseUrl: string, operation: Operation, pathValues: ApiPathValues = {}, queryValues: ApiQueryValues = {}): string {
  let path: string = operation.path;
  for (const name of operation.pathParameters) { const value = pathValues[name]; if (value === undefined || value === "") throw new TypeError(`missing path parameter: ${name}`); path = path.replace(`{${name}}`, encodeURIComponent(String(value))); }
  const query = new URLSearchParams(); for (const name of operation.queryParameters) { const value = queryValues[name]; if (value !== undefined && value !== null) query.set(name, String(value)); }
  return `${baseUrl}${path}${query.size ? `?${query}` : ""}`;
}
export function parseSseFrame<T = unknown>(frame: string): ApiSseEvent<T> | null {
  let event = "message"; const lines: string[] = [];
  for (const line of frame.replace(/\r/g, "").split("\n")) { if (line.startsWith("event:")) event = line.slice(6).trim(); if (line.startsWith("data:")) lines.push(line.slice(5).trimStart()); }
  if (!lines.length) return null; const raw = lines.join("\n"); let data: unknown = raw; try { data = JSON.parse(raw) as unknown; } catch { /* text */ } return { event, data: data as T };
}
