import { ApiClientError, createApiClient } from "./api-client.js";

const authenticatedFetch = (input, init = {}) => {
  const method = String(init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers || {});
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    headers.set('X-OBC-Auth', '1');
  }
  return fetch(input, {
    ...init,
    headers,
    credentials: "same-origin",
  });
};

export const api = createApiClient({ fetchImpl: authenticatedFetch });

export async function request(operationId, input = {}) {
  try {
    return await api.request(operationId, input);
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) {
      window.dispatchEvent(new CustomEvent("obc:auth-required"));
    }
    throw error;
  }
}

export async function readSse(operationId, input, onEvent) {
  try {
    return await api.readSse(operationId, input, onEvent);
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) {
      window.dispatchEvent(new CustomEvent("obc:auth-required"));
    }
    throw error;
  }
}

export const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

export function errorMessage(error, fallback = "请求失败，请稍后重试") {
  const detail = error?.details?.error?.message || error?.details?.detail;
  return String(detail || error?.message || fallback);
}

export function safeWebUrl(value) {
  try {
    const url = new URL(String(value || ""), location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

export function newConversationId(storageKey = "obc.chat.conversation") {
  const current = localStorage.getItem(storageKey);
  if (current) return current;
  const next = crypto.randomUUID();
  localStorage.setItem(storageKey, next);
  return next;
}
