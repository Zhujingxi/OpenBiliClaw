/**
 * Tests for the shared user-text sanitizer.
 *
 * Extension-side first line of the two-layer comment/danmaku text defense
 * (the server repeats the same 200-char truncate + Unicode category-C strip
 * as the authoritative final defense). Both X's reply tap and bilibili's
 * interact tap route their extracted text through `sanitizeUserText`.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { sanitizeUserText, COMMENT_TEXT_MAX_CHARS } from "../src/shared/text-sanitize.ts";

test("sanitizeUserText truncates to the 200-char cap", () => {
  assert.equal(COMMENT_TEXT_MAX_CHARS, 200);
  const long = "字".repeat(250);
  const cleaned = sanitizeUserText(long, COMMENT_TEXT_MAX_CHARS);
  assert.equal([...cleaned].length, 200);
  assert.equal(cleaned, "字".repeat(200));
});

test("sanitizeUserText strips Unicode category-C (control + format) chars", () => {
  // \n \t \x00 are Cc; U+200B zero-width space and U+200E LRM are Cf.
  const dirty = "hello\n\tworld​ ‎ ok";
  const cleaned = sanitizeUserText(dirty, COMMENT_TEXT_MAX_CHARS);
  // Interior whitespace is preserved (only category-C is removed), so the two
  // spaces that flanked the stripped LRM survive as a double space.
  assert.equal(cleaned, "helloworld  ok");
  assert.ok(!cleaned.includes("\n"));
  assert.ok(!cleaned.includes("​"));
});

test("sanitizeUserText trims surrounding whitespace but keeps interior spaces", () => {
  assert.equal(sanitizeUserText("  hi there  ", COMMENT_TEXT_MAX_CHARS), "hi there");
});

test("sanitizeUserText returns empty for non-string / empty input", () => {
  assert.equal(sanitizeUserText(undefined as unknown as string, 200), "");
  assert.equal(sanitizeUserText(null as unknown as string, 200), "");
  assert.equal(sanitizeUserText(123 as unknown as string, 200), "");
  assert.equal(sanitizeUserText("", 200), "");
});
