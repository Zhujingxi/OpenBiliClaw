/**
 * Shared user-text sanitizer for captured comment / danmaku bodies.
 *
 * Extension-side first line of a two-layer defense: the MAIN-world taps
 * (X reply, bilibili interact) route extracted user text through
 * `sanitizeUserText` before forwarding it as a BEHAVIOR_EVENT. The backend
 * repeats the identical 200-char truncate + Unicode category-C strip as the
 * authoritative final defense (`sources/event_format.py::sanitize_comment_text`).
 *
 * Keep the two implementations in lockstep — same cap, same category-C rule.
 */

/** Max characters kept for a captured comment / danmaku (mirrors backend). */
export const COMMENT_TEXT_MAX_CHARS = 200;

// Unicode category-C = control (Cc), format (Cf), surrogate (Cs), private-use
// (Co), unassigned (Cn). \p{C} covers all of them; strips NUL, newlines,
// zero-width spaces, and bidi marks while leaving ordinary whitespace (Zs).
const CATEGORY_C = /\p{C}/gu;

/**
 * Strip Unicode category-C code points, trim surrounding whitespace, and
 * truncate to `maxChars` code points. Non-string / empty input returns "".
 * Truncation counts code points (via spread) so astral characters aren't split.
 */
export function sanitizeUserText(text: string, maxChars: number): string {
  if (typeof text !== "string" || text.length === 0) return "";
  const stripped = text.replace(CATEGORY_C, "").trim();
  const codePoints = [...stripped];
  if (codePoints.length <= maxChars) return stripped;
  return codePoints.slice(0, maxChars).join("");
}
