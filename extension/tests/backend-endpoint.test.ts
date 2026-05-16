import assert from "node:assert/strict";
import test from "node:test";

import { isValidBackendPort as isValidPopupBackendPort } from "../popup/popup-backend-config.js";
import { isValidBackendPort as isValidSharedBackendPort } from "../src/shared/backend-endpoint.ts";

const validPorts: unknown[] = [1, 8420, 65535, "1", "8420", "65535", " 19090 "];
const invalidPorts: unknown[] = [
  0,
  65536,
  -1,
  "",
  "   ",
  "1.5",
  "1e3",
  "123abc",
  "0x20",
  "+8420",
  null,
  undefined,
];

test("popup backend port validation accepts only complete decimal integers in range", () => {
  for (const port of validPorts) {
    assert.equal(isValidPopupBackendPort(port), true, `${String(port)} should be valid`);
  }
  for (const port of invalidPorts) {
    assert.equal(isValidPopupBackendPort(port), false, `${String(port)} should be invalid`);
  }
});

test("shared backend port validation accepts only complete decimal integers in range", () => {
  for (const port of validPorts) {
    assert.equal(isValidSharedBackendPort(port), true, `${String(port)} should be valid`);
  }
  for (const port of invalidPorts) {
    assert.equal(isValidSharedBackendPort(port), false, `${String(port)} should be invalid`);
  }
});
