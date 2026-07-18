import { createApiClient } from "./api-client.js";
import { getBackendBaseUrl } from "./popup-backend-config.js";
import { popupAuthenticatedFetch } from "./popup-device-auth.js";

let cachedBaseUrl = "";
let cachedClient = null;

function apiOrigin(baseUrl) {
  return String(baseUrl).replace(/\/api\/v1\/?$/, "");
}

async function client() {
  const backendBaseUrl = await getBackendBaseUrl();
  const baseUrl = apiOrigin(backendBaseUrl);
  if (!cachedClient || cachedBaseUrl !== baseUrl) {
    cachedBaseUrl = baseUrl;
    cachedClient = createApiClient({
      baseUrl,
      fetchImpl: popupAuthenticatedFetch,
    });
  }
  return cachedClient;
}

/** Call one generated OpenAPI operation by its stable operation id. */
export async function requestV1(operationId, input = {}) {
  return (await client()).request(operationId, input);
}

/** Consume one generated OpenAPI SSE operation. */
export async function readV1Sse(operationId, input, onEvent) {
  return (await client()).readSse(operationId, input, onEvent);
}

export function resetPopupApiClient() {
  cachedBaseUrl = "";
  cachedClient = null;
}
