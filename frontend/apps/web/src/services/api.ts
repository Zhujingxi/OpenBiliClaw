import {
  ApiClient,
  type Validator,
  type components,
} from "@openbiliclaw/api-client";

export type SourceStatus = Omit<
  components["schemas"]["SourceStatusEntry"],
  "capabilities"
> & { capabilities?: string[] };
export type SourceInventorySummary =
  components["schemas"]["CandidateInventorySummary"];
export type SourceListResponse = Omit<
  components["schemas"]["SourceListResponse"],
  "items"
> & { items: readonly SourceStatus[] };
export const EMPTY_SOURCE_INVENTORY: SourceInventorySummary = {
  pool_count: 0,
  queue_count: 0,
  archived_count: 0,
  by_provider: [],
  by_content_kind: [],
};
export type RecommendationPage = components["schemas"]["RecommendationPage"];
export type ProfileResponse = components["schemas"]["ProfileResponse"];
export type EditProfileResult = components["schemas"]["EditProfileResult"];
export type AssistantTurnResponse =
  components["schemas"]["AssistantTurnResponse"];
export type ConversationResponse =
  components["schemas"]["ConversationResponse"];
export type RuntimeResponse = components["schemas"]["RuntimeResponse"];
export type SearchResponse = components["schemas"]["SearchResponse"];
export type ContentResponse = components["schemas"]["ContentResponse"];
export type EventEnvelope = import("@openbiliclaw/api-client").EventEnvelope;
export type SourceMutationResponse =
  components["schemas"]["SourceMutationResponse"];
export type FeedbackResponse = components["schemas"]["FeedbackResponse"];
export type RefreshResponse = components["schemas"]["RefreshResponse"];
export type ModelCatalogResponse =
  components["schemas"]["ModelCatalogResponse"];
export type ModelConfigurationResponse =
  components["schemas"]["ModelConfigurationResponse"];
export type ModelConfigurationRequest =
  components["schemas"]["ModelConfigurationRequest"];
export type LoginResponse = components["schemas"]["LoginResponse"];

export interface WebApi {
  login(password: string, signal?: AbortSignal): Promise<LoginResponse>;
  listSources(signal?: AbortSignal): Promise<SourceListResponse>;
  connectSource(
    body: components["schemas"]["ConnectSourceRequest"],
    signal?: AbortSignal,
  ): Promise<SourceMutationResponse>;
  recommendations(signal?: AbortSignal): Promise<RecommendationPage>;
  refreshRecommendations(
    body: components["schemas"]["RefreshRequest"],
    signal?: AbortSignal,
  ): Promise<RefreshResponse>;
  feedback(
    body: components["schemas"]["FeedbackRequest"],
    signal?: AbortSignal,
  ): Promise<FeedbackResponse>;
  profile(profileId: string, signal?: AbortSignal): Promise<ProfileResponse>;
  editProfile(
    body: components["schemas"]["ProfileEditRequest"],
    signal?: AbortSignal,
  ): Promise<EditProfileResult>;
  assistantTurn(
    body: components["schemas"]["AssistantTurnRequest"],
    deviceId: string,
    signal?: AbortSignal,
  ): Promise<AssistantTurnResponse>;
  conversation(
    conversationId: string,
    deviceId: string,
    signal?: AbortSignal,
  ): Promise<ConversationResponse>;
  runtimeHealth(signal?: AbortSignal): Promise<RuntimeResponse>;
  search(
    providerId: string,
    query: string,
    signal?: AbortSignal,
  ): Promise<SearchResponse>;
  content(reference: string, signal?: AbortSignal): Promise<ContentResponse>;
  modelCatalog(signal?: AbortSignal): Promise<ModelCatalogResponse>;
  currentModel(signal?: AbortSignal): Promise<ModelConfigurationResponse>;
  updateModel(
    body: ModelConfigurationRequest,
    signal?: AbortSignal,
  ): Promise<ModelConfigurationResponse>;
  events(after?: number, signal?: AbortSignal): AsyncIterable<EventEnvelope>;
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function validator<T>(
  check: (value: Record<string, unknown>) => boolean,
): Validator<T> {
  return (value: unknown): value is T => record(value) && check(value);
}
const listValidator = <T>(key: string): Validator<T> =>
  validator<T>((value) => Array.isArray(value[key]));
const objectValidator = <T>(key: string): Validator<T> =>
  validator<T>((value) => record(value[key]));

/** Typed host API adapter. Runtime guards validate envelopes; generated types own field shapes. */
export function createWebApi(client: ApiClient): WebApi {
  return {
    login: (password, signal) =>
      client.request({
        path: "/v1/auth/login",
        method: "post",
        body: { password },
        validate: validator<LoginResponse>(
          (value) =>
            typeof value.token === "string" && value.label === "session",
        ),
        signal,
      }),
    listSources: (signal) =>
      client.request({
        path: "/v1/sources",
        method: "get",
        validate: validator<components["schemas"]["SourceListResponse"]>(
          (value) => Array.isArray(value.items) && record(value.inventory),
        ),
        signal,
      }),
    connectSource: (body, signal) =>
      client.request({
        path: "/v1/sources/connect",
        method: "post",
        body,
        validate: objectValidator<SourceMutationResponse>("status"),
        signal,
      }),
    recommendations: (signal) =>
      client.request({
        path: "/v1/recommendations",
        method: "get",
        validate: listValidator<RecommendationPage>("items"),
        signal,
      }),
    refreshRecommendations: (body, signal) =>
      client.request({
        path: "/v1/recommendations/refresh",
        method: "post",
        body,
        validate: validator<RefreshResponse>(
          (value) => typeof value.decision === "string",
        ),
        signal,
      }),
    feedback: (body, signal) =>
      client.request({
        path: "/v1/feedback",
        method: "post",
        body,
        validate: objectValidator<FeedbackResponse>("result"),
        signal,
      }),
    profile: (profileId, signal) =>
      client.request({
        path: "/v1/profiles/{profile_id}",
        method: "get",
        pathParams: { profile_id: profileId },
        validate: objectValidator<ProfileResponse>("profile"),
        signal,
      }),
    editProfile: (body, signal) =>
      client.request({
        path: "/v1/profiles/edit",
        method: "post",
        body,
        validate: objectValidator<EditProfileResult>("profile"),
        signal,
      }),
    assistantTurn: (body, deviceId, signal) =>
      client.request({
        path: "/v1/assistant/turns",
        method: "post",
        body,
        headers: { "X-Device-ID": deviceId },
        validate: objectValidator<AssistantTurnResponse>("output"),
        signal,
      }),
    conversation: (conversationId, deviceId, signal) =>
      client.request({
        path: "/v1/assistant/conversations/{conversation_id}",
        method: "get",
        pathParams: { conversation_id: conversationId },
        headers: { "X-Device-ID": deviceId },
        validate: objectValidator<ConversationResponse>("conversation"),
        signal,
      }),
    runtimeHealth: (signal) =>
      client.request({
        path: "/v1/runtime/health",
        method: "get",
        validate: objectValidator<RuntimeResponse>("health"),
        signal,
      }),
    search: (providerId, query, signal) =>
      client.request({
        path: "/v1/content/search",
        method: "get",
        query: { provider_id: providerId, q: query, limit: 20 },
        validate: listValidator<SearchResponse>("items"),
        signal,
      }),
    content: (reference, signal) =>
      client.request({
        path: "/v1/content/detail",
        method: "get",
        query: { reference },
        validate: objectValidator<ContentResponse>("content"),
        signal,
      }),
    modelCatalog: (signal) =>
      client.request({
        path: "/v1/models/catalog",
        method: "get",
        validate: listValidator<ModelCatalogResponse>("providers"),
        signal,
      }),
    currentModel: (signal) =>
      client.request({
        path: "/v1/models/current",
        method: "get",
        validate: objectValidator<ModelConfigurationResponse>("current"),
        signal,
      }),
    updateModel: (body, signal) =>
      client.request({
        path: "/v1/models/current",
        method: "put",
        body,
        validate: objectValidator<ModelConfigurationResponse>("current"),
        signal,
      }),
    events: (after, signal) =>
      client.stream("/v1/events/stream", after, signal),
  };
}
