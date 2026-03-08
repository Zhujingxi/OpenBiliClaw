const DEFAULT_TITLE = "未命名推荐";
const DEFAULT_UP_NAME = "未知UP主";
const DEFAULT_EXPRESSION = "这条内容已经进入你的推荐列表，点开看看。";
const DEFAULT_PORTRAIT = "阿B 还在观察你，先多用一会儿。";

function normalizeText(value) {
  return typeof value === "string" ? value.trim() : "";
}

export function buildVideoUrl(bvid) {
  return `https://www.bilibili.com/video/${normalizeText(bvid)}`;
}

export function getTabButtonState(activeTab, tabName) {
  return {
    selected: activeTab === tabName,
    tabIndex: activeTab === tabName ? 0 : -1,
  };
}

export function normalizeRecommendation(item) {
  return {
    id: Number(item?.id ?? 0),
    bvid: normalizeText(item?.bvid),
    title: normalizeText(item?.title) || DEFAULT_TITLE,
    up_name: normalizeText(item?.up_name) || DEFAULT_UP_NAME,
    expression: normalizeText(item?.expression) || DEFAULT_EXPRESSION,
    topic_label: normalizeText(item?.topic_label),
    presented: Boolean(item?.presented),
  };
}

export function buildFeedbackPayload(recommendationId, feedbackType, note = "") {
  return {
    recommendation_id: Number(recommendationId),
    feedback_type: normalizeText(feedbackType),
    note: normalizeText(note),
  };
}

export function normalizeProfileSummary(summary) {
  return {
    initialized: Boolean(summary?.initialized),
    personality_portrait: normalizeText(summary?.personality_portrait) || DEFAULT_PORTRAIT,
    core_traits: Array.isArray(summary?.core_traits)
      ? summary.core_traits.map(normalizeText).filter(Boolean)
      : [],
    deep_needs: Array.isArray(summary?.deep_needs)
      ? summary.deep_needs.map(normalizeText).filter(Boolean)
      : [],
    top_interests: Array.isArray(summary?.top_interests)
      ? summary.top_interests.map(normalizeText).filter(Boolean)
      : [],
  };
}

export function validateCommentInput(note) {
  if (!normalizeText(note)) {
    return {
      valid: false,
      message: "请先写一句你的想法。",
    };
  }
  return {
    valid: true,
    message: "",
  };
}

export function getPopupState({ online, items = [], error = null }) {
  if (!online) {
    return {
      kind: "offline",
      message: "后端未连接，请先运行 openbiliclaw start",
      items: [],
    };
  }

  if (error) {
    return {
      kind: "error",
      message: "推荐暂时不可用，请稍后重试",
      items: [],
    };
  }

  const normalizedItems = items.map(normalizeRecommendation);
  if (normalizedItems.length === 0) {
    return {
      kind: "empty",
      message: "还没有可展示的推荐，先运行 init、discover 或 recommend",
      items: [],
    };
  }

  return {
    kind: "ready",
    message: "",
    items: normalizedItems,
  };
}
