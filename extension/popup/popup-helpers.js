const DEFAULT_TITLE = "未命名推荐";
const DEFAULT_UP_NAME = "未知UP主";
const DEFAULT_EXPRESSION = "这条内容已经进入你的推荐列表，点开看看。";

function normalizeText(value) {
  return typeof value === "string" ? value.trim() : "";
}

export function buildVideoUrl(bvid) {
  return `https://www.bilibili.com/video/${normalizeText(bvid)}`;
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
