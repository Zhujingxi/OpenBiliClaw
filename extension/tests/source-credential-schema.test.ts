import assert from "node:assert/strict";
import test from "node:test";

import {
  credentialFieldDefinitions,
  credentialsFromValues,
} from "../popup/source-credential-schema.js";
import { normalizeSettingsPatch } from "../popup/settings-patch.js";

test("credential editor derives write-only fields and requiredness from the manifest schema", () => {
  const definitions = credentialFieldDefinitions({
    type: "object",
    required: ["cookie"],
    properties: {
      cookie: { type: "string", title: "Platform Cookie", writeOnly: true, maxLength: 16384 },
    },
  });
  assert.deepEqual(definitions, [{ name: "cookie", label: "Platform Cookie", required: true, secret: true }]);
  assert.deepEqual(credentialsFromValues(definitions, { cookie: "  a=b  " }), { cookie: "a=b" });
});

test("sources with an empty credential schema render no credential controls", () => {
  assert.deepEqual(credentialFieldDefinitions({}), []);
  assert.deepEqual(credentialFieldDefinitions(undefined), []);
});

test("non-custom network modes clear a stale custom proxy URL", () => {
  assert.deepEqual(
    normalizeSettingsPatch({ network: { mode: "system", proxy_url: "http://proxy.internal:8080" } }),
    { network: { mode: "system", proxy_url: "" } },
  );
  assert.deepEqual(
    normalizeSettingsPatch({ network: { mode: "custom", proxy_url: "http://proxy.internal:8080" } }),
    { network: { mode: "custom", proxy_url: "http://proxy.internal:8080" } },
  );
});
