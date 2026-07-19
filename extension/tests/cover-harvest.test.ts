/**
 * Tests for the scrape-time xhs cover harvester.
 *
 * The harvester exists because background-tab scrapes only see lazy-load
 * data: placeholders (never a real cover URL), and because server-side
 * fetching races the rotating xhscdn token and the backend's own network —
 * the 2026-07 「没头图」 report. Correctness here decides whether xhs cards
 * have covers at all.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  arrayBufferToBase64,
  attachCoverData,
  backfillCoverUrlsFromState,
  extractCoverUrlsFromState,
  isHarvestableCoverUrl,
  isPlaceholderCoverUrl,
  MAX_COVER_BYTES,
  MAX_COVERS_PER_BATCH,
} from "../src/content/xhs/cover-harvest.ts";
import type { XhsNoteMetadata } from "../src/content/xhs/passive.ts";

function note(coverUrl: string, extra: Partial<XhsNoteMetadata> = {}): XhsNoteMetadata {
  return { url: "https://www.xiaohongshu.com/explore/abc", title: "t", author: "a", cover_url: coverUrl, ...extra };
}

function fakeImageResponse(bytes: Uint8Array, contentType = "image/webp"): Response {
  return new Response(bytes.slice(), { status: 200, headers: { "content-type": contentType } });
}

test("arrayBufferToBase64 round-trips bytes, including >32KB chunk boundaries", () => {
  const bytes = new Uint8Array(0x8000 + 17);
  for (let i = 0; i < bytes.length; i += 1) bytes[i] = i % 251;
  const encoded = arrayBufferToBase64(bytes.buffer);
  const decoded = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0));
  assert.deepEqual(decoded, bytes);
});

test("isHarvestableCoverUrl accepts xhscdn hosts only", () => {
  assert.equal(isHarvestableCoverUrl("https://sns-webpic-qc.xhscdn.com/202607191557/ab/cd"), true);
  assert.equal(isHarvestableCoverUrl("http://sns-img-hw.xhscdn.com/x"), true);
  assert.equal(isHarvestableCoverUrl("https://i0.hdslb.com/bfs/archive/x.jpg"), false);
  assert.equal(isHarvestableCoverUrl("https://evil.example/xhscdn.com/x"), false);
  assert.equal(isHarvestableCoverUrl("https://notxhscdn.com/x"), false);
  assert.equal(isHarvestableCoverUrl("not a url"), false);
  assert.equal(isHarvestableCoverUrl(""), false);
});

test("attachCoverData attaches base64 bytes for harvestable covers", async (t) => {
  const bytes = new Uint8Array([1, 2, 3, 4]);
  const calls: string[] = [];
  t.mock.method(globalThis, "fetch", async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return fakeImageResponse(bytes);
  });

  const notes = [note("https://sns-webpic-qc.xhscdn.com/202607191557/tok/path")];
  await attachCoverData(notes);

  assert.equal(calls.length, 1);
  assert.equal(notes[0].cover_content_type, "image/webp");
  assert.deepEqual(
    Uint8Array.from(atob(notes[0].cover_data ?? ""), (c) => c.charCodeAt(0)),
    bytes,
  );
});

test("attachCoverData skips non-harvestable, already-harvested, and coverless notes", async (t) => {
  const calls: string[] = [];
  t.mock.method(globalThis, "fetch", async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return fakeImageResponse(new Uint8Array([9]));
  });

  const bili = note("https://i0.hdslb.com/bfs/x.jpg");
  const done = note("https://sns-webpic-qc.xhscdn.com/1/t/p", { cover_data: "AA==" });
  const bare = note("");
  await attachCoverData([bili, done, bare]);

  assert.equal(calls.length, 0);
  assert.equal(bili.cover_data, undefined);
  assert.equal(done.cover_data, "AA==");
});

test("attachCoverData caps the batch at MAX_COVERS_PER_BATCH", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    calls += 1;
    return fakeImageResponse(new Uint8Array([1]));
  });

  const notes = Array.from({ length: MAX_COVERS_PER_BATCH + 5 }, (_, i) =>
    note(`https://sns-webpic-qc.xhscdn.com/1/t/p${i}`),
  );
  await attachCoverData(notes);

  assert.equal(calls, MAX_COVERS_PER_BATCH);
});

test("attachCoverData leaves notes untouched on non-OK, non-image, oversize, or throwing fetch", async (t) => {
  const responses: Array<() => Response | Promise<Response>> = [
    () => new Response("nope", { status: 403 }),
    () => new Response("<html>", { status: 200, headers: { "content-type": "text/html" } }),
    () => fakeImageResponse(new Uint8Array(MAX_COVER_BYTES + 1)),
    () => {
      throw new TypeError("network down");
    },
  ];
  let i = 0;
  t.mock.method(globalThis, "fetch", async () => responses[i++]());

  const notes = responses.map((_, idx) => note(`https://sns-webpic-qc.xhscdn.com/1/t/p${idx}`));
  await attachCoverData(notes);

  for (const n of notes) {
    assert.equal(n.cover_data, undefined);
    assert.equal(n.cover_content_type, undefined);
  }
});

// ── Placeholder rejection & state-based URL backfill ──────────────
// Background tabs never upgrade lazy-loaded card images past their data:
// placeholder — the root cause of the 2026-07 「没头图」 report.

test("isPlaceholderCoverUrl flags data:/blob:/empty, passes real URLs", () => {
  assert.equal(isPlaceholderCoverUrl("data:image/png;base64,iVBOR"), true);
  assert.equal(isPlaceholderCoverUrl("BLOB:https://x"), true);
  assert.equal(isPlaceholderCoverUrl("  "), true);
  assert.equal(isPlaceholderCoverUrl("https://sns-webpic-qc.xhscdn.com/1/t/p"), false);
});

test("extractCoverUrlsFromState finds covers by note id across unknown shapes", () => {
  const state = {
    search: {
      feeds: {
        // Vue-reactive style wrapper on the way down.
        _value: [
          {
            id: "note-aaa",
            noteCard: { cover: { urlDefault: "//sns-webpic-qc.xhscdn.com/1/t/aaa" } },
          },
          { id: "note-bbb", cover: { url: "https://sns-webpic-qc.xhscdn.com/1/t/bbb" } },
          { id: "note-unrelated", cover: { url: "https://sns-webpic-qc.xhscdn.com/1/t/x" } },
        ],
      },
    },
  };
  const covers = extractCoverUrlsFromState(state, new Set(["note-aaa", "note-bbb"]));
  // Protocol-relative URLs are normalized to https.
  assert.equal(covers.get("note-aaa"), "https://sns-webpic-qc.xhscdn.com/1/t/aaa");
  assert.equal(covers.get("note-bbb"), "https://sns-webpic-qc.xhscdn.com/1/t/bbb");
  assert.equal(covers.size, 2);
});

test("extractCoverUrlsFromState survives cycles and ignores data: values", () => {
  const cyclic: Record<string, unknown> = {
    id: "note-cyc",
    cover: { url: "data:image/png;base64,iVBOR" },
  };
  cyclic.self = cyclic;
  const covers = extractCoverUrlsFromState({ root: [cyclic] }, new Set(["note-cyc"]));
  assert.equal(covers.size, 0);
});

test("backfillCoverUrlsFromState fills only placeholder covers", () => {
  const placeholderNote = note("", {
    url: "https://www.xiaohongshu.com/explore/note-fill?xsec_token=z",
  });
  const realNote = note("https://sns-webpic-qc.xhscdn.com/1/t/keep", {
    url: "https://www.xiaohongshu.com/explore/note-keep",
  });
  const state = {
    feeds: [
      { noteId: "note-fill", cover: { url: "https://sns-webpic-qc.xhscdn.com/1/t/fill" } },
      { noteId: "note-keep", cover: { url: "https://sns-webpic-qc.xhscdn.com/1/t/WRONG" } },
    ],
  };
  const filled = backfillCoverUrlsFromState([placeholderNote, realNote], state);
  assert.equal(filled, 1);
  assert.equal(placeholderNote.cover_url, "https://sns-webpic-qc.xhscdn.com/1/t/fill");
  assert.equal(realNote.cover_url, "https://sns-webpic-qc.xhscdn.com/1/t/keep");
});
