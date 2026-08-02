import { createHmac, randomUUID } from "node:crypto";

export const AMO_BASE_URL = "https://addons.mozilla.org/api/v5/";

export function readAmoCredentials() {
  const apiKey = process.env.AMO_JWT_ISSUER;
  const apiSecret = process.env.AMO_JWT_SECRET;
  if (!apiKey || !apiSecret) {
    throw new Error(
      "AMO API access requires AMO_JWT_ISSUER and AMO_JWT_SECRET environment variables",
    );
  }
  return { apiKey, apiSecret };
}

export function createAmoJwt({ apiKey, apiSecret }) {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const unsigned = `${encode({ alg: "HS256", typ: "JWT" })}.${encode({
    iss: apiKey,
    jti: randomUUID(),
    iat: now,
    exp: now + 300,
  })}`;
  const signature = createHmac("sha256", apiSecret)
    .update(unsigned)
    .digest("base64url");
  return `${unsigned}.${signature}`;
}

export async function amoRequest(path, options = {}) {
  const credentials = options.credentials ?? readAmoCredentials();
  const response = await fetch(new URL(path, AMO_BASE_URL), {
    ...options,
    credentials: undefined,
    headers: {
      Authorization: `JWT ${createAmoJwt(credentials)}`,
      Accept: "application/json",
      "User-Agent": "OpenBiliClaw Firefox AMO publisher",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" ? JSON.stringify(payload) : String(payload);
    throw new Error(`AMO API ${options.method ?? "GET"} ${path} returned ${response.status}: ${detail}`);
  }
  return payload;
}
