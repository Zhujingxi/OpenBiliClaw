#!/usr/bin/env node

import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const targets = {
  web: resolve(root, "src/openbiliclaw/web/js/api-client.js"),
  extension: resolve(root, "extension/src/shared/api-client.ts"),
};
const mode = process.argv[2] ?? "--check";
if (mode !== "--write" && mode !== "--check") {
  console.error("usage: node openapi/generate-client.mjs [--write|--check]");
  process.exit(2);
}

const document = JSON.parse(await readFile(resolve(root, "openapi/openapi.json"), "utf8"));
const operations = collectOperations(document);
const banner = "// Generated from openapi/openapi.json by openapi/generate-client.mjs. Do not edit.\n";
const generated = {
  web: `${banner}export const API_OPERATIONS = Object.freeze(${JSON.stringify(operations, null, 2)});\n${jsRuntime()}`,
  extension: `${banner}${tsSchemas(document.components?.schemas ?? {})}${tsOperations(operations)}${tsRuntime()}`,
};

let stale = false;
for (const [name, path] of Object.entries(targets)) {
  if (mode === "--write") {
    await writeFile(path, generated[name], "utf8");
    console.log(`wrote ${path.slice(root.length + 1)}`);
  } else {
    let actual = "";
    try { actual = await readFile(path, "utf8"); } catch { /* missing is stale */ }
    if (actual !== generated[name]) {
      stale = true;
      console.error(`generated client is stale: ${path.slice(root.length + 1)}`);
    }
  }
}
if (stale) process.exit(1);

function collectOperations(schema) {
  const result = {};
  for (const path of Object.keys(schema.paths ?? {}).sort()) {
    const item = schema.paths[path];
    for (const method of ["get", "post", "put", "patch", "delete"]) {
      const operation = item[method];
      if (!operation?.operationId) continue;
      const parameters = [...(item.parameters ?? []), ...(operation.parameters ?? [])];
      result[operation.operationId] = {
        method: method.toUpperCase(),
        path,
        pathParameters: parameters.filter((entry) => entry.in === "path").map((entry) => entry.name),
        queryParameters: parameters.filter((entry) => entry.in === "query").map((entry) => entry.name),
        requestType: refName(operation.requestBody?.content?.["application/json"]?.schema),
        responseType: responseName(operation.responses ?? {}),
        stream: Boolean(operation.responses?.["200"]?.content?.["text/event-stream"]),
        sseEvents: operation.responses?.["200"]?.content?.["text/event-stream"]?.["x-sse-events"] ?? {},
      };
    }
  }
  return Object.fromEntries(Object.entries(result).sort(([a], [b]) => a.localeCompare(b)));
}

function refName(schema) {
  return typeof schema?.$ref === "string" ? schema.$ref.split("/").at(-1) : null;
}

function responseName(responses) {
  for (const status of ["200", "201", "202"]) {
    const schema = responses[status]?.content?.["application/json"]?.schema;
    if (schema) return refName(schema) ?? (schema.type === "array" ? refName(schema.items) : null);
  }
  return null;
}

function tsOperations(value) {
  const ids = Object.keys(value).map(JSON.stringify).join(" | ");
  return `export type ApiOperationId = ${ids};\nexport const API_OPERATIONS = ${JSON.stringify(value, null, 2)} as const;\n`;
}

function tsSchemas(schemas) {
  return Object.keys(schemas).sort().map((name) => `export type ${typeName(name)} = ${schemaType(schemas[name])};\n`).join("");
}

function typeName(name) {
  return name.replace(/[^A-Za-z0-9_$]/g, "_");
}

function schemaType(schema) {
  if (!schema || typeof schema !== "object") return "unknown";
  if (schema.$ref) return typeName(schema.$ref.split("/").at(-1));
  if (schema.const !== undefined) return JSON.stringify(schema.const);
  if (schema.enum) return schema.enum.map(JSON.stringify).join(" | ");
  const alternatives = schema.anyOf ?? schema.oneOf;
  if (alternatives) return alternatives.map(schemaType).join(" | ");
  if (schema.type === "array") return `ReadonlyArray<${schemaType(schema.items)}>`;
  if (schema.type === "object" || schema.properties || schema.additionalProperties) {
    const required = new Set(schema.required ?? []);
    const fields = Object.keys(schema.properties ?? {}).sort().map((key) => `${JSON.stringify(key)}${required.has(key) ? "" : "?"}: ${schemaType(schema.properties[key])};`);
    if (schema.additionalProperties) fields.push(`[key: string]: ${schemaType(schema.additionalProperties)};`);
    return `{ ${fields.join(" ")} }`;
  }
  if (schema.type === "string") return "string";
  if (schema.type === "integer" || schema.type === "number") return "number";
  if (schema.type === "boolean") return "boolean";
  if (schema.type === "null") return "null";
  return "unknown";
}

function jsRuntime() {
  return `
export class ApiClientError extends Error {
  constructor(operationId, response, details) {
    super(\`${"${operationId}"} failed: ${"${response.status}"}\`);
    this.name = "ApiClientError";
    this.operationId = operationId;
    this.status = response.status;
    this.details = details;
  }
}
export function createApiClient(options = {}) {
  const baseUrl = String(options.baseUrl ?? "").replace(/\\/$/, "");
  const fetchImpl = options.fetchImpl ?? globalThis.fetch?.bind(globalThis);
  if (typeof fetchImpl !== "function") throw new TypeError("fetch implementation required");
  const getAccessToken = options.getAccessToken ?? (() => null);
  async function request(operationId, input = {}) {
    const operation = API_OPERATIONS[operationId];
    if (!operation) throw new TypeError(\`unknown API operation: ${"${operationId}"}\`);
    return requestOperation(fetchImpl, getAccessToken, baseUrl, operationId, operation, input);
  }
  async function readSse(operationId, input = {}, onEvent = () => {}) {
    const operation = API_OPERATIONS[operationId];
    if (!operation?.stream) throw new TypeError(\`operation is not an SSE stream: ${"${operationId}"}\`);
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
  if (token) headers.set("Authorization", \`Bearer ${"${token}"}\`);
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
    let boundary = buffer.indexOf("\\n\\n");
    while (boundary >= 0) {
      const event = parseSseFrame(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (event) await onEvent(event);
      boundary = buffer.indexOf("\\n\\n");
    }
  }
}
export function buildOperationUrl(baseUrl, operation, pathValues = {}, queryValues = {}) {
  let path = operation.path;
  for (const name of operation.pathParameters) {
    const value = pathValues?.[name];
    if (value === undefined || value === null || value === "") throw new TypeError(\`missing path parameter: ${"${name}"}\`);
    path = path.replace(\`{${"${name}"}}\`, encodeURIComponent(String(value)));
  }
  const query = new URLSearchParams();
  for (const name of operation.queryParameters) {
    const value = queryValues?.[name];
    if (value !== undefined && value !== null) query.set(name, String(value));
  }
  return \`${"${baseUrl}"}${"${path}"}${"${query.size ? `?${query}` : \"\"}"}\`;
}
export function parseSseFrame(frame) {
  let event = "message";
  const lines = [];
  for (const line of frame.replace(/\\r/g, "").split("\\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
  }
  if (!lines.length) return null;
  const raw = lines.join("\\n");
  let data = raw;
  try { data = JSON.parse(raw); } catch { /* text event */ }
  return { event, data };
}
`;
}

function tsRuntime() {
  return `
export type ApiPathValues = Readonly<Record<string, string | number>>;
export type ApiQueryValues = Readonly<Record<string, string | number | boolean | null | undefined>>;
export interface ApiRequestInput { readonly path?: ApiPathValues; readonly query?: ApiQueryValues; readonly body?: unknown; readonly headers?: HeadersInit; readonly signal?: AbortSignal; }
export interface ApiSseEvent<T = unknown> { readonly event: string; readonly data: T; }
export interface ApiClientOptions { readonly baseUrl?: string; readonly fetchImpl?: typeof fetch; readonly getAccessToken?: () => string | null | Promise<string | null>; }
export interface ApiClient { request<T = unknown>(operationId: ApiOperationId, input?: ApiRequestInput): Promise<T>; readSse<T = unknown>(operationId: ApiOperationId, input?: ApiRequestInput, onEvent?: (event: ApiSseEvent<T>) => void | Promise<void>): Promise<void>; }
export class ApiClientError extends Error {
  readonly operationId: ApiOperationId; readonly status: number; readonly details: unknown;
  constructor(operationId: ApiOperationId, response: Response, details: unknown) { super(\`${"${operationId}"} failed: ${"${response.status}"}\`); this.name = "ApiClientError"; this.operationId = operationId; this.status = response.status; this.details = details; }
}
type Operation = (typeof API_OPERATIONS)[ApiOperationId];
export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const baseUrl = String(options.baseUrl ?? "").replace(/\\/$/, "");
  const fetchImpl = options.fetchImpl ?? globalThis.fetch?.bind(globalThis);
  if (typeof fetchImpl !== "function") throw new TypeError("fetch implementation required");
  const getAccessToken = options.getAccessToken ?? (() => null);
  async function fetchOperation(operation: Operation, input: ApiRequestInput, stream: boolean): Promise<Response> {
    const headers = new Headers(input.headers ?? {}); if (stream) headers.set("Accept", "text/event-stream");
    const token = await getAccessToken(); if (token) headers.set("Authorization", \`Bearer ${"${token}"}\`);
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
    const operation = API_OPERATIONS[operationId]; if (!operation.stream) throw new TypeError(\`operation is not an SSE stream: ${"${operationId}"}\`);
    const response = await fetchOperation(operation, input, true); if (!response.ok || !response.body) throw new ApiClientError(operationId, response, null);
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader(); let buffer = "";
    while (true) { const { value, done } = await reader.read(); if (done) break; buffer += value; let boundary = buffer.indexOf("\\n\\n"); while (boundary >= 0) { const event = parseSseFrame<T>(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2); if (event) await onEvent(event); boundary = buffer.indexOf("\\n\\n"); } }
  }
  return Object.freeze({ request, readSse });
}
export function buildOperationUrl(baseUrl: string, operation: Operation, pathValues: ApiPathValues = {}, queryValues: ApiQueryValues = {}): string {
  let path: string = operation.path;
  for (const name of operation.pathParameters) { const value = pathValues[name]; if (value === undefined || value === "") throw new TypeError(\`missing path parameter: ${"${name}"}\`); path = path.replace(\`{${"${name}"}}\`, encodeURIComponent(String(value))); }
  const query = new URLSearchParams(); for (const name of operation.queryParameters) { const value = queryValues[name]; if (value !== undefined && value !== null) query.set(name, String(value)); }
  return \`${"${baseUrl}"}${"${path}"}${"${query.size ? `?${query}` : \"\"}"}\`;
}
export function parseSseFrame<T = unknown>(frame: string): ApiSseEvent<T> | null {
  let event = "message"; const lines: string[] = [];
  for (const line of frame.replace(/\\r/g, "").split("\\n")) { if (line.startsWith("event:")) event = line.slice(6).trim(); if (line.startsWith("data:")) lines.push(line.slice(5).trimStart()); }
  if (!lines.length) return null; const raw = lines.join("\\n"); let data: unknown = raw; try { data = JSON.parse(raw) as unknown; } catch { /* text */ } return { event, data: data as T };
}
`;
}
