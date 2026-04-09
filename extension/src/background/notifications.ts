export const NOTIFICATION_PREFIX = "openbiliclaw-recommendation:";
export const COGNITION_NOTIFICATION_PREFIX = "openbiliclaw-cognition:";
export const DELIGHT_NOTIFICATION_PREFIX = "openbiliclaw-delight:";

type ExtensionUiTab = "recommend" | "profile" | "chat";

type ExtensionUiChrome = {
  runtime?: { getURL(path: string): string };
  sidePanel?: { open(options: { windowId: number }): Promise<unknown> | unknown };
  tabs?: { create(options: { url: string }): Promise<unknown> | unknown };
};

export type PendingNotification = {
  recommendation_id: number;
  bvid: string;
  title: string;
  reason: string;
};

export type PendingCognitionUpdate = {
  id: string;
  kind: string;
  summary: string;
};

export type PendingDelight = {
  bvid: string;
  title: string;
  delight_reason: string;
  delight_score: number;
  delight_hook: string;
  cover_url: string;
};

export function buildNotificationId(bvid: string): string {
  return `${NOTIFICATION_PREFIX}${bvid}`;
}

export function buildCognitionNotificationId(updateId: string): string {
  return `${COGNITION_NOTIFICATION_PREFIX}${updateId}`;
}

export function parseNotificationBvid(notificationId: string): string {
  if (!notificationId.startsWith(NOTIFICATION_PREFIX)) {
    return "";
  }
  return notificationId.slice(NOTIFICATION_PREFIX.length);
}

export function parseCognitionUpdateId(notificationId: string): string {
  if (!notificationId.startsWith(COGNITION_NOTIFICATION_PREFIX)) {
    return "";
  }
  return notificationId.slice(COGNITION_NOTIFICATION_PREFIX.length);
}

export function buildDelightNotificationId(bvid: string): string {
  return `${DELIGHT_NOTIFICATION_PREFIX}${bvid}`;
}

export function parseDelightBvid(notificationId: string): string {
  if (!notificationId.startsWith(DELIGHT_NOTIFICATION_PREFIX)) {
    return "";
  }
  return notificationId.slice(DELIGHT_NOTIFICATION_PREFIX.length);
}

export function buildChromeNotificationOptions(
  item: PendingNotification | PendingCognitionUpdate | PendingDelight,
): chrome.notifications.NotificationCreateOptions {
  if ("delight_reason" in item) {
    const hookBadge = item.delight_hook ? `【${item.delight_hook}】` : "";
    return {
      type: "basic",
      iconUrl: "icons/icon128.png",
      title: `${hookBadge}阿B 觉得这条你会意外喜欢`,
      message: item.delight_reason || "这条真的可能会戳到你。",
      priority: 2,
    };
  }
  if ("summary" in item) {
    return {
      type: "basic",
      iconUrl: "icons/icon128.png",
      title: "阿B 又对你多看清了一点",
      message: item.summary || "阿B 刚记住了一个新的偏好变化。",
      priority: 2,
    };
  }
  return {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: item.title || "阿B 给你补到一条新内容",
    message: item.reason || "这条大概率会对你的胃口。",
    priority: 2,
  };
}

export function buildProfileNotificationUrl(): string {
  return buildExtensionUiUrl("profile");
}

export function buildExtensionUiUrl(tab: ExtensionUiTab = "recommend"): string {
  const path = `popup/popup.html?tab=${tab}`;
  if (
    typeof chrome !== "undefined" &&
    chrome.runtime &&
    typeof chrome.runtime.getURL === "function"
  ) {
    return chrome.runtime.getURL(path);
  }
  return `chrome-extension://__EXTENSION_ID__/${path}`;
}

export async function openExtensionUi(
  chromeApi: ExtensionUiChrome,
  {
    windowId,
    tab = "recommend",
  }: { windowId?: number; tab?: ExtensionUiTab } = {},
): Promise<"sidePanel" | "tab"> {
  if (typeof windowId === "number" && chromeApi.sidePanel?.open) {
    await chromeApi.sidePanel.open({ windowId });
    return "sidePanel";
  }
  await chromeApi.tabs?.create({ url: buildExtensionUiUrl(tab) });
  return "tab";
}
