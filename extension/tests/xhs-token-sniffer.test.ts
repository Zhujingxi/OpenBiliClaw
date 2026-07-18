/**
 * Tests for the MAIN-world xhs token sniffer's pure extractor.
 *
 * The sniffer itself wraps window.fetch / XMLHttpRequest and can't be
 * exercised under node --test without jsdom, but the pure JSON walker
 * (extractTokenPairs) carries all the interesting logic and is the
 * likeliest place for regressions when xhs changes its response shapes.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { extractTokenPairs } from "../src/main/xhs-token-sniffer.ts";

test("extracts a single note_id + xsec_token pair from a flat object", () => {
  const pairs = extractTokenPairs({
    note_id: "69c7a7b000000000220030c9",
    xsec_token: "ABC_xsec_deadbeef",
    other: "ignored",
  });
  assert.deepEqual(pairs, [
    { note_id: "69c7a7b000000000220030c9", xsec_token: "ABC_xsec_deadbeef" },
  ]);
});

test("accepts camelCase variants (noteId, xsecToken)", () => {
  const pairs = extractTokenPairs({
    noteId: "69c7a7b000000000220030c9",
    xsecToken: "ABC_xsec_deadbeef",
  });
  assert.deepEqual(pairs, [
    { note_id: "69c7a7b000000000220030c9", xsec_token: "ABC_xsec_deadbeef" },
  ]);
});

test("walks nested feed-style payloads and collects every pair", () => {
  const pairs = extractTokenPairs({
    data: {
      items: [
        {
          note_id: "1111111111111111aaaaaaaa",
          xsec_token: "tok-a",
          note_card: { unrelated: 1 },
        },
        {
          id: "2222222222222222bbbbbbbb",
          xsec_token: "tok-b",
        },
      ],
    },
  });
  assert.deepEqual(pairs.sort((a, b) => a.note_id.localeCompare(b.note_id)), [
    { note_id: "1111111111111111aaaaaaaa", xsec_token: "tok-a" },
    { note_id: "2222222222222222bbbbbbbb", xsec_token: "tok-b" },
  ]);
});

test("deduplicates identical pairs seen in multiple places", () => {
  const pair = { note_id: "69c7a7b000000000220030c9", xsec_token: "tok-x" };
  const pairs = extractTokenPairs({
    a: pair,
    b: { ...pair },
    c: { list: [{ ...pair }, { ...pair }] },
  });
  assert.equal(pairs.length, 1);
});

test("ignores objects that have an id but no token", () => {
  const pairs = extractTokenPairs({
    note_id: "69c7a7b000000000220030c9",
    // no xsec_token
  });
  assert.deepEqual(pairs, []);
});

test("ignores objects whose id is not a 24-hex string", () => {
  const pairs = extractTokenPairs({
    id: "not-a-note-id",
    xsec_token: "tok",
  });
  assert.deepEqual(pairs, []);
});

test("ignores empty-string tokens", () => {
  const pairs = extractTokenPairs({
    note_id: "69c7a7b000000000220030c9",
    xsec_token: "",
  });
  assert.deepEqual(pairs, []);
});

test("returns an empty list for null / non-object input", () => {
  assert.deepEqual(extractTokenPairs(null), []);
  assert.deepEqual(extractTokenPairs(undefined), []);
  assert.deepEqual(extractTokenPairs("string"), []);
  assert.deepEqual(extractTokenPairs(42), []);
});

test("handles arrays at the top level", () => {
  const pairs = extractTokenPairs([
    { note_id: "1111111111111111aaaaaaaa", xsec_token: "tok-a" },
    { note_id: "2222222222222222bbbbbbbb", xsec_token: "tok-b" },
  ]);
  assert.equal(pairs.length, 2);
});
