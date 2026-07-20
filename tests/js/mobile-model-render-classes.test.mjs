// @ts-check
// Reproduction of the mobile model-settings styling regression (P2) and its
// fix. The shared renderers must emit class names that the mobile stylesheet
// actually styles when the caller passes the mobile class prefix. Desktop
// callers must continue to receive the legacy model-* classes unchanged.
// Run: node tests/js/mobile-model-render-classes.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  renderConnectionTypeGroups,
  renderCredentialEditor,
} from "../../src/openbiliclaw/web/shared/model-config-render.js";

const root = resolve(import.meta.dirname, "..", "..");
const mobileCss = readFileSync(resolve(root, "src/openbiliclaw/web/css/app.css"), "utf-8");
const desktopCss = readFileSync(
  resolve(root, "src/openbiliclaw/web/desktop/assets/css/app.css"),
  "utf-8",
);

const DESCRIPTOR = {
  id: "openai_compatible",
  label: "OpenAI 兼容",
  help: "任何 OpenAI 兼容端点",
  category: "api_protocol",
  capabilities: ["chat", "embedding"],
  fields: [
    {
      name: "credential",
      label: "凭据",
      required: false,
      capabilities: ["chat", "embedding"],
      presets: [],
    },
  ],
  preset_definitions: [],
};

const GROUPS = [
  {
    category: "api_protocol",
    connection_types: [DESCRIPTOR],
  },
];

/**
 * @param {Record<string, unknown>} [overrides]
 * @returns {Record<string, unknown>}
 */
function makeRecord(overrides = {}) {
  return {
    id: "conn-1",
    name: "Primary",
    type: "openai_compatible",
    preset: "custom",
    credential: {
      action: "set",
      value: "sk-test",
      status: {
        source: "inline",
        configured: true,
        env_name: "",
        credential_ref: "",
        oauth_logged_in: false,
      },
    },
    ...overrides,
  };
}

/**
 * @param {string} html
 * @returns {string[]}
 */
function extractClasses(html) {
  const classes = new Set();
  for (const match of html.matchAll(/class="([^"]+)"/g)) {
    for (const cls of match[1].split(/\s+/)) {
      if (cls) classes.add(cls);
    }
  }
  return [...classes];
}

/**
 * The shared renderers' JSDoc currently types `options` as
 * `{ classPrefix?: string }` only; the remaining parameters are accepted at
 * runtime but not declared (pre-TS-conversion source). Cast to a call shape
 * that matches the real API.
 * @type {(options: { groups?: unknown[], record?: Record<string, unknown>, kind?: string, classPrefix?: string }) => string}
 */
const renderTypeGroups = /** @type {any} */ (renderConnectionTypeGroups);

/**
 * @type {(options: { record?: Record<string, unknown>, descriptor?: Record<string, unknown>, kind?: string, classPrefix?: string }) => { html: string }}
 */
const renderCredEditor = /** @type {any} */ (renderCredentialEditor);

test("mobile prefix emits classes styled by the mobile stylesheet", () => {
  const typeHtml = renderTypeGroups({
    groups: GROUPS,
    record: makeRecord(),
    kind: "chat",
    classPrefix: "mobile-model",
  });
  const credentialHtml = renderCredEditor({
    record: makeRecord(),
    descriptor: DESCRIPTOR,
    kind: "chat",
    classPrefix: "mobile-model",
  }).html;

  const typeClasses = extractClasses(typeHtml);
  const credentialClasses = extractClasses(credentialHtml);

  // Connection-type classes must exist in the mobile stylesheet.
  assert.ok(
    typeClasses.includes("mobile-model-type-group"),
    `expected mobile-model-type-group in ${typeClasses}`,
  );
  assert.ok(
    typeClasses.includes("mobile-model-type-option"),
    `expected mobile-model-type-option in ${typeClasses}`,
  );
  assert.match(mobileCss, /\.mobile-model-type-group\b/);
  assert.match(mobileCss, /\.mobile-model-type-option\b/);
  assert.match(mobileCss, /\.mobile-model-type-option\[aria-selected="true"\]/);

  // Credential-action classes must exist in the mobile stylesheet.
  assert.ok(
    credentialClasses.includes("mobile-model-credential-actions"),
    `expected mobile-model-credential-actions in ${credentialClasses}`,
  );
  assert.ok(
    credentialClasses.includes("mobile-model-credential-action"),
    `expected mobile-model-credential-action in ${credentialClasses}`,
  );
  assert.match(mobileCss, /\.mobile-model-credential-actions\b/);
  assert.match(mobileCss, /\.mobile-model-credential-actions button\[aria-pressed="true"\]/);

  // The desktop-only classes must NOT leak into the mobile render.
  assert.ok(!typeClasses.includes("model-type-group"), "desktop class leaked");
  assert.ok(!typeClasses.includes("model-type-option"), "desktop class leaked");
  assert.ok(!credentialClasses.includes("model-credential-actions"), "desktop class leaked");
  assert.ok(!credentialClasses.includes("model-credential-action"), "desktop class leaked");
});

test("default prefix preserves desktop classes byte-for-byte", () => {
  const typeHtml = renderTypeGroups({
    groups: GROUPS,
    record: makeRecord(),
    kind: "chat",
  });
  const credentialHtml = renderCredEditor({
    record: makeRecord(),
    descriptor: DESCRIPTOR,
    kind: "chat",
  }).html;

  const typeClasses = extractClasses(typeHtml);
  const credentialClasses = extractClasses(credentialHtml);

  assert.ok(typeClasses.includes("model-type-group"));
  assert.ok(typeClasses.includes("model-type-group-title"));
  assert.ok(typeClasses.includes("model-type-option"));
  assert.ok(credentialClasses.includes("model-credential-actions"));
  assert.ok(credentialClasses.includes("model-credential-action"));

  // Desktop stylesheet must still style these exact classes.
  assert.match(desktopCss, /\.model-type-group\b/);
  assert.match(desktopCss, /\.model-type-option\b/);
  assert.match(desktopCss, /\.model-credential-actions\b/);
  assert.match(desktopCss, /\.model-credential-action\b/);
});
