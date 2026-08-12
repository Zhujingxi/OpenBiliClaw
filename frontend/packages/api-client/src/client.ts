import type { components, operations, paths } from "../generated/schema";

export type EventEnvelope =
  | components["schemas"]["JobEvent"]
  | components["schemas"]["RecommendationEvent"]
  | components["schemas"]["AssistantEvent"]
  | components["schemas"]["ConnectionEvent"];

export type Validator<T> = (value: unknown) => value is T;
export type ApiErrorKind = "network" | "http" | "invalid-response";
type HttpMethod = "get" | "post" | "put" | "patch" | "delete";
type ApiPath = keyof paths;
type OperationAt<P extends ApiPath, M extends HttpMethod> = paths[P][M];
type SuccessfulResponse<O> = O extends { responses: { 200: infer Response } }
  ? Response extends { content: { "application/json": infer Payload } }
    ? Payload
    : never
  : never;
type RequestBody<O> = O extends {
  requestBody: { content: { "application/json": infer Payload } };
}
  ? Payload
  : never;
type Parameters<O> = O extends { parameters: infer Value } ? Value : never;
type QueryParameters<O> =
  Parameters<O> extends { query?: infer Value } ? Value : never;
type PathParameters<O> =
  Parameters<O> extends { path: infer Value } ? Value : never;

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | undefined;

  constructor(kind: ApiErrorKind, message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

export interface ApiRequest<P extends ApiPath, M extends HttpMethod> {
  readonly path: P;
  readonly method: M;
  readonly validate: Validator<SuccessfulResponse<OperationAt<P, M>>>;
  readonly body?: RequestBody<OperationAt<P, M>>;
  readonly query?: QueryParameters<OperationAt<P, M>>;
  readonly pathParams?: PathParameters<OperationAt<P, M>>;
  readonly headers?: Readonly<Record<string, string>>;
  readonly signal?: AbortSignal | undefined;
}

export class ApiClient {
  readonly #baseUrl: string;
  readonly #fetch: typeof fetch;

  constructor(baseUrl: string, fetcher: typeof fetch = fetch) {
    this.#baseUrl = baseUrl.replace(/\/$/, "");
    this.#fetch = fetcher;
  }

  async request<P extends ApiPath, M extends HttpMethod>(
    request: ApiRequest<P, M>,
  ): Promise<SuccessfulResponse<OperationAt<P, M>>> {
    const init: RequestInit = { method: request.method.toUpperCase() };
    if (request.headers !== undefined) init.headers = request.headers;
    if (request.signal !== undefined) init.signal = request.signal;
    if (request.body !== undefined) {
      init.body = JSON.stringify(request.body);
      init.headers = { "content-type": "application/json", ...request.headers };
    }
    const path = interpolatePath(request.path, request.pathParams);
    const query = encodeQuery(request.query);
    const response = await this.#fetchResponse(
      `${path}${query}`,
      init,
      "Network request failed",
    );
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new ApiError("invalid-response", "Response was not valid JSON");
    }
    if (!request.validate(payload)) {
      throw new ApiError(
        "invalid-response",
        "Response did not match the expected schema",
      );
    }
    return payload;
  }

  async *stream(
    path: "/v1/events/stream",
    signal?: AbortSignal,
  ): AsyncGenerator<EventEnvelope, void, undefined> {
    const response = await this.#fetchResponse(
      path,
      signal === undefined ? {} : { signal },
      "Event stream connection failed",
    );
    if (response.body === null) {
      throw new ApiError(
        "invalid-response",
        "Event stream response had no body",
      );
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        pending += decoder
          .decode(chunk.value, { stream: true })
          .replaceAll("\r\n", "\n");
        const frames = pending.split("\n\n");
        pending = frames.pop() ?? "";
        for (const frame of frames) {
          const data = frame
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).replace(/^ /, ""))
            .join("\n");
          if (data) yield parseEventEnvelope(data);
        }
      }
      pending += decoder.decode();
      if (pending.trim() !== "") {
        throw new ApiError(
          "invalid-response",
          "Event stream ended with an incomplete frame",
        );
      }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError("network", "Event stream interrupted");
    } finally {
      reader.releaseLock();
    }
  }

  async #fetchResponse(
    path: string,
    init: RequestInit,
    networkMessage: string,
  ): Promise<Response> {
    let response: Response;
    try {
      response = await this.#fetch(`${this.#baseUrl}${path}`, init);
    } catch {
      throw new ApiError("network", networkMessage);
    }
    if (!response.ok) {
      throw new ApiError(
        "http",
        `Request failed with status ${response.status}`,
        response.status,
      );
    }
    return response;
  }
}

export function parseEventEnvelope(text: string): EventEnvelope {
  let value: unknown;
  try {
    value = JSON.parse(text) as unknown;
  } catch {
    throw new ApiError("invalid-response", "Event data was not valid JSON");
  }
  if (!isEventEnvelope(value)) {
    throw new ApiError(
      "invalid-response",
      "Event did not match the expected schema",
    );
  }
  return value;
}

function isEventEnvelope(value: unknown): value is EventEnvelope {
  if (
    !isRecord(value) ||
    !isPositiveInteger(value.event_id) ||
    typeof value.status !== "string"
  ) {
    return false;
  }
  switch (value.kind) {
    case "job":
      return typeof value.component_id === "string";
    case "recommendation":
      return typeof value.recommendation_id === "string";
    case "assistant":
      return typeof value.conversation_id === "string";
    case "connection":
      return typeof value.provider_id === "string";
    default:
      return false;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1;
}

function interpolatePath(path: string, parameters: unknown): string {
  if (!isRecord(parameters)) return path;
  return path.replace(/\{([^}]+)\}/g, (placeholder, name: string) => {
    const value = parameters[name];
    return value === undefined
      ? placeholder
      : encodeURIComponent(String(value));
  });
}

function encodeQuery(parameters: unknown): string {
  if (!isRecord(parameters)) return "";
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(parameters)) {
    if (value !== undefined && value !== null) query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded === "" ? "" : `?${encoded}`;
}

// Keep operation names public for callers that need generated metadata without
// weakening request() to arbitrary paths.
export type ApiOperations = operations;
