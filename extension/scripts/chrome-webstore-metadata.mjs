#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

import {
  buildMetadataPayload,
  parseListingMarkdown,
  summarizeDraft,
  validateListingMetadata,
  verifyMetadataReadback,
} from "./chrome-webstore-metadata-lib.mjs";

const OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token";
const CWS_V1_API_BASE = "https://www.googleapis.com/chromewebstore/v1.1";
const CWS_V2_API_BASE = "https://chromewebstore.googleapis.com/v2";
const CWS_SCOPE = "https://www.googleapis.com/auth/chromewebstore";
const REQUEST_TIMEOUT_MS = 30_000;

function usage() {
  console.log(`Usage:
  node scripts/chrome-webstore-metadata.mjs --listing <markdown> --mode <probe|apply> [options]

Required environment variables:
  CHROME_WEBSTORE_CLIENT_ID
  CHROME_WEBSTORE_CLIENT_SECRET
  CHROME_WEBSTORE_REFRESH_TOKEN
  CHROME_WEBSTORE_PUBLISHER_ID
  CHROME_WEBSTORE_EXTENSION_ID

Options:
  --listing <path>      Canonical Chrome Web Store listing Markdown.
  --mode <probe|apply>  Probe is read-only; apply can update the draft.
  --replace-pending     Permit apply to cancel an active review before writing.
  --publish             Re-submit only after exact metadata read-back.
  --help                Show this help.
`);
}

export function parseArgs(argv) {
  const options = {
    listing: "",
    mode: "",
    replacePending: false,
    publish: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      return { ...options, help: true };
    }
    if (arg === "--listing") {
      options.listing = argv[++index] ?? "";
      continue;
    }
    if (arg === "--mode") {
      options.mode = argv[++index] ?? "";
      continue;
    }
    if (arg === "--replace-pending") {
      options.replacePending = true;
      continue;
    }
    if (arg === "--publish") {
      options.publish = true;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }

  if (!options.listing) {
    throw new Error("--listing is required");
  }
  if (options.mode !== "probe" && options.mode !== "apply") {
    throw new Error("--mode must be probe or apply");
  }
  if (options.mode === "probe" && (options.replacePending || options.publish)) {
    throw new Error("Mutation flags require --mode apply");
  }
  if (options.publish && !options.replacePending) {
    throw new Error("--publish requires explicit --replace-pending authorization");
  }
  return options;
}

function requiredEnv(env, name) {
  const value = env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function credentialsFromEnv(env) {
  return {
    clientId: requiredEnv(env, "CHROME_WEBSTORE_CLIENT_ID"),
    clientSecret: requiredEnv(env, "CHROME_WEBSTORE_CLIENT_SECRET"),
    refreshToken: requiredEnv(env, "CHROME_WEBSTORE_REFRESH_TOKEN"),
    publisherId: requiredEnv(env, "CHROME_WEBSTORE_PUBLISHER_ID"),
    extensionId: requiredEnv(env, "CHROME_WEBSTORE_EXTENSION_ID"),
  };
}

function transientStatus(status) {
  return status === 429 || status >= 500;
}

export async function requestJson(
  operation,
  url,
  options,
  { fetchImpl, sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)) },
) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    let response;
    try {
      response = await fetchImpl(url, {
        ...options,
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
    } catch (error) {
      throw new Error(`${operation} request failed: ${error?.message ?? String(error)}`);
    }

    const text = await response.text();
    let payload = {};
    if (text.trim()) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = {};
      }
    }
    if (response.ok) {
      return payload;
    }
    if (attempt === 0 && transientStatus(response.status)) {
      await sleep(500);
      continue;
    }
    const apiMessage =
      typeof payload?.error?.message === "string" ? `: ${payload.error.message}` : "";
    throw new Error(`${operation} failed with HTTP ${response.status}${apiMessage}`);
  }
  throw new Error(`${operation} exhausted its bounded retry`);
}

async function getAccessToken(credentials, fetchImpl) {
  const body = new URLSearchParams({
    client_id: credentials.clientId,
    client_secret: credentials.clientSecret,
    refresh_token: credentials.refreshToken,
    grant_type: "refresh_token",
  });
  const payload = await requestJson(
    "OAuth token exchange",
    OAUTH_TOKEN_URL,
    {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    },
    { fetchImpl },
  );
  if (typeof payload.access_token !== "string" || !payload.access_token) {
    throw new Error("OAuth token response did not include access_token");
  }
  if (typeof payload.scope === "string" && !payload.scope.includes(CWS_SCOPE)) {
    throw new Error(`OAuth token is missing required scope: ${CWS_SCOPE}`);
  }
  return payload.access_token;
}

function bearerHeaders(accessToken, json = false) {
  const headers = { Authorization: `Bearer ${accessToken}` };
  if (json) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

function v1ItemUrl(extensionId, draft = false) {
  const url = `${CWS_V1_API_BASE}/items/${encodeURIComponent(extensionId)}`;
  return draft ? `${url}?projection=DRAFT` : url;
}

function v2ItemUrl(credentials, method) {
  const publisherId = encodeURIComponent(credentials.publisherId);
  const extensionId = encodeURIComponent(credentials.extensionId);
  return `${CWS_V2_API_BASE}/publishers/${publisherId}/items/${extensionId}:${method}`;
}

async function getDraft(credentials, accessToken, fetchImpl) {
  return await requestJson(
    "Chrome Web Store v1.1 draft probe",
    v1ItemUrl(credentials.extensionId, true),
    { method: "GET", headers: bearerHeaders(accessToken) },
    { fetchImpl },
  );
}

async function putDraft(credentials, accessToken, payload, fetchImpl) {
  return await requestJson(
    "Chrome Web Store v1.1 metadata update",
    v1ItemUrl(credentials.extensionId),
    {
      method: "PUT",
      headers: bearerHeaders(accessToken, true),
      body: JSON.stringify(payload),
    },
    { fetchImpl },
  );
}

async function fetchStatus(credentials, accessToken, fetchImpl) {
  return await requestJson(
    "Chrome Web Store v2 status",
    v2ItemUrl(credentials, "fetchStatus"),
    { method: "GET", headers: bearerHeaders(accessToken) },
    { fetchImpl },
  );
}

async function cancelSubmission(credentials, accessToken, fetchImpl) {
  return await requestJson(
    "Chrome Web Store v2 cancellation",
    v2ItemUrl(credentials, "cancelSubmission"),
    {
      method: "POST",
      headers: bearerHeaders(accessToken, true),
      body: "{}",
    },
    { fetchImpl },
  );
}

async function publishItem(credentials, accessToken, fetchImpl) {
  return await requestJson(
    "Chrome Web Store v2 publish",
    v2ItemUrl(credentials, "publish"),
    {
      method: "POST",
      headers: bearerHeaders(accessToken, true),
      body: JSON.stringify({ publishType: "DEFAULT_PUBLISH" }),
    },
    { fetchImpl },
  );
}

export function findReviewState(status) {
  const state = status?.submittedItemRevisionStatus?.state;
  return typeof state === "string" ? state : "";
}

function probeSupportsSafeMetadataUpdate(draft, canonical) {
  const probe = summarizeDraft(draft);
  if (!probe.summary.present || !probe.description.present) {
    throw new Error(
      "The v1.1 probe does not expose writable listing metadata; stopped before cancellation or writes",
    );
  }
  buildMetadataPayload(draft, canonical);
  return probe;
}

export async function runMetadataCommand({
  options,
  env,
  fetchImpl = fetch,
  readFileImpl = readFile,
  log = console.log,
}) {
  const canonical = parseListingMarkdown(await readFileImpl(options.listing, "utf8"));
  validateListingMetadata(canonical);
  const credentials = credentialsFromEnv(env);
  const accessToken = await getAccessToken(credentials, fetchImpl);
  const draft = await getDraft(credentials, accessToken, fetchImpl);
  const probe = probeSupportsSafeMetadataUpdate(draft, canonical);
  log(JSON.stringify({ operation: "probe", probe }));

  if (options.mode === "probe") {
    return { operation: "probe", probe };
  }

  const statusBefore = await fetchStatus(credentials, accessToken, fetchImpl);
  const stateBefore = findReviewState(statusBefore);
  if (stateBefore === "PENDING_REVIEW") {
    if (!options.replacePending) {
      throw new Error(
        "Submission is PENDING_REVIEW; pass --replace-pending to authorize cancellation",
      );
    }
    await cancelSubmission(credentials, accessToken, fetchImpl);
    log(JSON.stringify({ operation: "cancel", previousReviewState: stateBefore }));
  }

  const payload = buildMetadataPayload(draft, canonical);
  await putDraft(credentials, accessToken, payload, fetchImpl);
  const readback = await getDraft(credentials, accessToken, fetchImpl);
  verifyMetadataReadback(readback, canonical);
  log(
    JSON.stringify({
      operation: "verify",
      summarySha256: summarizeDraft(readback).summary.sha256,
      descriptionSha256: summarizeDraft(readback).description.sha256,
    }),
  );

  if (!options.publish) {
    return { operation: "apply", updated: true, published: false };
  }

  await publishItem(credentials, accessToken, fetchImpl);
  const statusAfter = await fetchStatus(credentials, accessToken, fetchImpl);
  const reviewState = findReviewState(statusAfter);
  if (reviewState !== "PENDING_REVIEW") {
    throw new Error(
      `Metadata draft updated but submission state is ${reviewState || "unknown"}, not PENDING_REVIEW`,
    );
  }
  log(JSON.stringify({ operation: "publish", reviewState }));
  return { operation: "apply", updated: true, published: true, reviewState };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    usage();
    return;
  }
  const result = await runMetadataCommand({ options, env: process.env });
  console.log(JSON.stringify({ result }));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(`Chrome Web Store metadata command failed: ${error.message}`);
    process.exitCode = 1;
  });
}
