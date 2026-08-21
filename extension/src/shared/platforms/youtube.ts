/**
 * YouTube platform adapter for the generic behavior collector.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";
import {
  actionClassTokens,
  matchesWord,
  shortActionLabel,
} from "../behavior.ts";
import { queryParam } from "./search-query.ts";

const WATCH_ID_PATTERN = /[?&]v=([A-Za-z0-9_-]{6,})/;
const SHORTS_ID_PATTERN = /\/shorts\/([A-Za-z0-9_-]{6,})/;
const SHORT_URL_PATTERN = /youtu\.be\/([A-Za-z0-9_-]{6,})/;

const CARD_SELECTOR = [
  "ytd-rich-item-renderer",
  "ytd-video-renderer",
  "ytd-grid-video-renderer",
  "ytd-compact-video-renderer",
  'a[href*="/watch"]',
  'a[href*="/shorts/"]',
].join(",");

const SEARCH_INPUT_SELECTOR = [
  'input[name="search_query"]',
  "ytd-searchbox input",
  'input[type="text"]',
].join(",");

export function detectYoutubePageType(url: string): PageType {
  if (url.includes("/watch") || url.includes("/shorts/")) return "video";
  if (url.includes("/results")) return "search";
  if (url.includes("/@") || url.includes("/channel/") || url.includes("/c/")) {
    return "channel";
  }
  return "home";
}

export function extractYoutubeVideoId(url: string): string | null {
  return (
    url.match(WATCH_ID_PATTERN)?.[1] ??
    url.match(SHORTS_ID_PATTERN)?.[1] ??
    url.match(SHORT_URL_PATTERN)?.[1] ??
    null
  );
}

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

export function inferYoutubeActionType(hint: ActionHint): string | null {
  // #205 calibration for YouTube's English DOM: real control labels are long
  // sentences ("like this video along with 1,234 others") or single words up
  // to "Subscribe" (9 chars), so aria-labels cannot be length-capped and the
  // text cap is 12 — keywords must instead match as whole words. Card copy /
  // video titles are still capped: a card whose title mentions "like" or
  // "dislike" is a click, not feedback. Class names match per token.
  const text = `${shortActionLabel(hint.text, 12)} ${normalizeText(hint.ariaLabel)}`
    .toLowerCase();
  const classes = actionClassTokens(hint.className);
  if (!text.trim() && classes.length === 0) return null;
  const hasToken = (keyword: string): boolean =>
    classes.some((token) => token.includes(keyword));
  if (
    matchesWord(text, "dislike") ||
    text.includes("不喜欢") ||
    text.includes("不感兴趣") ||
    hasToken("dislike")
  ) {
    return "dislike";
  }
  if (matchesWord(text, "like") || hasToken("like") || text.includes("点赞")) return "like";
  if (matchesWord(text, "save") || hasToken("save") || text.includes("收藏") || text.includes("稍后观看")) {
    return "favorite";
  }
  if (matchesWord(text, "comment") || text.includes("评论") || hasToken("comment")) return "comment";
  if (matchesWord(text, "share") || text.includes("分享") || hasToken("share")) return "share";
  if (
    matchesWord(text, "subscribe") ||
    text.includes("订阅") ||
    text.includes("关注") ||
    hasToken("subscribe")
  ) {
    return "follow";
  }
  return null;
}

export const youtubeAdapter: PlatformAdapter = {
  sourcePlatform: "youtube",
  detectPageType: detectYoutubePageType,
  extractContentId: extractYoutubeVideoId,
  extractSearchQuery: (url) => queryParam(url, "search_query"),
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: "video",
  dwellPageTypes: ["video"],
  inferActionType: inferYoutubeActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    return { video_id: extractYoutubeVideoId(url) };
  },
};
