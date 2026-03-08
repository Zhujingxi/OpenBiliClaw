const BACKEND_URL = "http://127.0.0.1:8420/api";

async function requestJson(path, options = {}) {
  const response = await fetch(`${BACKEND_URL}${path}`, options);
  if (!response.ok) {
    throw new Error(`${path} request failed: ${response.status}`);
  }
  return response.json();
}

export async function checkBackendStatus() {
  try {
    const response = await fetch(`${BACKEND_URL}/health`, { method: "GET" });
    return response.ok;
  } catch {
    return false;
  }
}

export async function fetchRecommendations() {
  const payload = await requestJson("/recommendations", { method: "GET" });
  return Array.isArray(payload.items) ? payload.items : [];
}

export async function fetchProfileSummary() {
  return requestJson("/profile-summary", { method: "GET" });
}

export async function submitFeedback(payload) {
  return requestJson("/feedback", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function sendChatMessage(message) {
  return requestJson("/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message }),
  });
}
