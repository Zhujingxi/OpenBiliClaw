// Generated from openapi/openapi.json by openapi/generate-client.mjs. Do not edit.
export const API_OPERATIONS = Object.freeze({
  "v1_auth_extension_token": {
    "method": "POST",
    "path": "/api/v1/auth/extension-token",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": "ExtensionTokenRequest",
    "responseType": "ExtensionTokenResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_auth_login": {
    "method": "POST",
    "path": "/api/v1/auth/login",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": "LoginRequest",
    "responseType": "AuthenticatedResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_auth_logout": {
    "method": "POST",
    "path": "/api/v1/auth/logout",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": null,
    "responseType": "AuthenticatedResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_auth_revoke": {
    "method": "POST",
    "path": "/api/v1/auth/revoke",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": null,
    "responseType": null,
    "stream": false,
    "sseEvents": {}
  },
  "v1_auth_status": {
    "method": "GET",
    "path": "/api/v1/auth/status",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": null,
    "responseType": "AuthStatusResponse",
    "stream": false,
    "sseEvents": {}
  },
  "v1_chat_history": {
    "method": "GET",
    "path": "/api/v1/chat/{conversation_id}",
    "pathParameters": [
      "conversation_id"
    ],
    "queryParameters": [
      "limit",
      "offset"
    ],
    "requestType": null,
    "responseType": "ChatHistoryPage",
    "stream": false,
    "sseEvents": {}
  },
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
    "responseType": "LibraryItem",
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
  "v1_profile_edit": {
    "method": "PATCH",
    "path": "/api/v1/profile",
    "pathParameters": [],
    "queryParameters": [],
    "requestType": "ProfileEdit",
    "responseType": "ProfileSnapshot",
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
  "v1_sources_disconnect_account": {
    "method": "DELETE",
    "path": "/api/v1/sources/{source_id}/accounts/{account_key}",
    "pathParameters": [
      "source_id",
      "account_key"
    ],
    "queryParameters": [],
    "requestType": null,
    "responseType": "SourceAccountDisconnectResult",
    "stream": false,
    "sseEvents": {}
  },
  "v1_sources_get_settings": {
    "method": "GET",
    "path": "/api/v1/sources/{source_id}/settings",
    "pathParameters": [
      "source_id"
    ],
    "queryParameters": [],
    "requestType": null,
    "responseType": "SourceSettingsState",
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
  "v1_sources_update_settings": {
    "method": "PUT",
    "path": "/api/v1/sources/{source_id}/settings",
    "pathParameters": [
      "source_id"
    ],
    "queryParameters": [],
    "requestType": "SourceSettingsUpdate",
    "responseType": "SourceSettingsState",
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
});

export class ApiClientError extends Error {
  constructor(operationId, response, details) {
    super(`${operationId} failed: ${response.status}`);
    this.name = "ApiClientError";
    this.operationId = operationId;
    this.status = response.status;
    this.details = details;
  }
}
export function createApiClient(options = {}) {
  const baseUrl = String(options.baseUrl ?? "").replace(/\/$/, "");
  const fetchImpl = options.fetchImpl ?? globalThis.fetch?.bind(globalThis);
  if (typeof fetchImpl !== "function") throw new TypeError("fetch implementation required");
  const getAccessToken = options.getAccessToken ?? (() => null);
  async function request(operationId, input = {}) {
    const operation = API_OPERATIONS[operationId];
    if (!operation) throw new TypeError(`unknown API operation: ${operationId}`);
    return requestOperation(fetchImpl, getAccessToken, baseUrl, operationId, operation, input);
  }
  async function readSse(operationId, input = {}, onEvent = () => {}) {
    const operation = API_OPERATIONS[operationId];
    if (!operation?.stream) throw new TypeError(`operation is not an SSE stream: ${operationId}`);
    const response = await fetchOperation(fetchImpl, getAccessToken, baseUrl, operation, input, true);
    if (!response.ok || !response.body) throw new ApiClientError(operationId, response, null);
    await consumeSse(response, onEvent);
  }
  return Object.freeze({ request, readSse });
}
async function requestOperation(fetchImpl, getAccessToken, baseUrl, operationId, operation, input) {
  const response = await fetchOperation(fetchImpl, getAccessToken, baseUrl, operation, input, false);
  if (!response.ok) {
    let details = null;
    try { details = await response.json(); } catch { /* response may be empty */ }
    throw new ApiClientError(operationId, response, details);
  }
  if (response.status === 204) return null;
  return response.json();
}
async function fetchOperation(fetchImpl, getAccessToken, baseUrl, operation, input, stream) {
  const headers = new Headers(input.headers ?? {});
  if (stream) headers.set("Accept", "text/event-stream");
  const token = await getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const init = { method: operation.method, headers, signal: input.signal };
  if (input.body !== undefined) { headers.set("Content-Type", "application/json"); init.body = JSON.stringify(input.body); }
  return fetchImpl(buildOperationUrl(baseUrl, operation, input.path, input.query), init);
}
async function consumeSse(response, onEvent) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const event = parseSseFrame(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (event) await onEvent(event);
      boundary = buffer.indexOf("\n\n");
    }
  }
}
export function buildOperationUrl(baseUrl, operation, pathValues = {}, queryValues = {}) {
  let path = operation.path;
  for (const name of operation.pathParameters) {
    const value = pathValues?.[name];
    if (value === undefined || value === null || value === "") throw new TypeError(`missing path parameter: ${name}`);
    path = path.replace(`{${name}}`, encodeURIComponent(String(value)));
  }
  const query = new URLSearchParams();
  for (const name of operation.queryParameters) {
    const value = queryValues?.[name];
    if (value !== undefined && value !== null) query.set(name, String(value));
  }
  return `${baseUrl}${path}${query.size ? `?${query}` : ""}`;
}
export function parseSseFrame(frame) {
  let event = "message";
  const lines = [];
  for (const line of frame.replace(/\r/g, "").split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
  }
  if (!lines.length) return null;
  const raw = lines.join("\n");
  let data = raw;
  try { data = JSON.parse(raw); } catch { /* text event */ }
  return { event, data };
}
