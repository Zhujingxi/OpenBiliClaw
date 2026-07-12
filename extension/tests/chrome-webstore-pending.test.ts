import assert from "node:assert/strict";
import test from "node:test";

import { uploadWithPendingReplacement } from "../scripts/chrome-webstore-pending.mjs";

test("replaces a pending Chrome Web Store review only when explicitly enabled", async () => {
  const calls: string[] = [];
  let uploadAttempt = 0;

  const result = await uploadWithPendingReplacement({
    replacePending: true,
    upload: async () => {
      calls.push("upload");
      uploadAttempt += 1;
      if (uploadAttempt === 1) {
        const error = new Error("item is in review") as Error & {
          chromeWebStoreReason?: string;
        };
        error.chromeWebStoreReason = "NOT_UPDATEABLE";
        throw error;
      }
      return { uploadState: "SUCCEEDED" };
    },
    cancelSubmission: async () => {
      calls.push("cancel");
    },
  });

  assert.deepEqual(calls, ["upload", "cancel", "upload"]);
  assert.deepEqual(result, { uploadState: "SUCCEEDED" });
});

test("does not cancel a pending review unless replacement is explicitly enabled", async () => {
  const error = new Error("item is in review") as Error & {
    chromeWebStoreReason?: string;
  };
  error.chromeWebStoreReason = "NOT_UPDATEABLE";
  let cancelled = false;

  await assert.rejects(
    uploadWithPendingReplacement({
      replacePending: false,
      upload: async () => {
        throw error;
      },
      cancelSubmission: async () => {
        cancelled = true;
      },
    }),
    error,
  );

  assert.equal(cancelled, false);
});

test("does not cancel or retry unrelated Chrome Web Store failures", async () => {
  const error = new Error("invalid archive") as Error & {
    chromeWebStoreReason?: string;
  };
  error.chromeWebStoreReason = "INVALID_ARGUMENT";
  let uploadAttempts = 0;
  let cancelled = false;

  await assert.rejects(
    uploadWithPendingReplacement({
      replacePending: true,
      upload: async () => {
        uploadAttempts += 1;
        throw error;
      },
      cancelSubmission: async () => {
        cancelled = true;
      },
    }),
    error,
  );

  assert.equal(uploadAttempts, 1);
  assert.equal(cancelled, false);
});
