const APP_LAUNCH_FALLBACK_MS = 1600;
function safeParseUrl(url) {
  const text = typeof url === "string" ? url.trim() : "";
  if (!text) return null;
  try {
    return new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`);
  } catch {
    return null;
  }
}
function hostMatches(host, hostname) {
  return host === hostname || host.endsWith(`.${hostname}`);
}
export function buildAppDeepLink(url) {
  const parsed = safeParseUrl(url);
  if (!parsed) return "";
  const host = parsed.hostname.toLowerCase();
  const path = parsed.pathname;
  if (hostMatches(host, "bilibili.com")) {
    const m = path.match(/^\/video\/(BV[0-9A-Za-z]+|av\d+)\/?$/i);
    if (!m) return "";
    const vid = m[1].toLowerCase().startsWith("av") ? m[1].slice(2) : m[1];
    return `bilibili://video/${vid}`;
  }
  if (hostMatches(host, "xiaohongshu.com")) {
    const m = path.match(/^\/(?:explore|discovery\/item)\/([0-9a-zA-Z]+)\/?$/);
    if (!m) return "";
    const params = new URLSearchParams();
    for (const key of ["xsec_token", "xsec_source"]) {
      const value = parsed.searchParams.get(key);
      if (value) params.set(key, value);
    }
    const query = params.toString();
    return `xhsdiscover://item/${m[1]}${query ? `?${query}` : ""}`;
  }
  if (hostMatches(host, "douyin.com")) {
    const m = path.match(/^\/video\/(\d+)\/?$/);
    return m ? `snssdk1128://aweme/detail/${m[1]}` : "";
  }
  if (hostMatches(host, "youtube.com") || host === "youtu.be") {
    let vid = "";
    if (host === "youtu.be") {
      vid = (path.match(/^\/([0-9A-Za-z_-]{6,})\/?$/) || [])[1] || "";
    } else if (path === "/watch") {
      vid = parsed.searchParams.get("v") || "";
    } else {
      vid = (path.match(/^\/shorts\/([0-9A-Za-z_-]{6,})\/?$/) || [])[1] || "";
    }
    return vid ? `vnd.youtube://www.youtube.com/watch?v=${vid}` : "";
  }
  if (hostMatches(host, "x.com") || hostMatches(host, "twitter.com")) {
    const m = path.match(/^\/[^/]+\/status(?:es)?\/(\d+)/);
    return m ? `twitter://status?id=${m[1]}` : "";
  }
  if (hostMatches(host, "zhihu.com")) {
    let m = path.match(/^\/question\/\d+\/answer\/(\d+)/);
    if (m) return `zhihu://answers/${m[1]}`;
    m = path.match(/^\/p\/(\d+)/);
    if (m && host.startsWith("zhuanlan.")) return `zhihu://articles/${m[1]}`;
    m = path.match(/^\/question\/(\d+)/);
    if (m) return `zhihu://questions/${m[1]}`;
    return "";
  }
  return "";
}
export function isMobileUserAgent(ua, maxTouchPoints = 0) {
  const text = typeof ua === "string" ? ua : "";
  if (/android|iphone|ipad|ipod/i.test(text)) return true;
  return /macintosh/i.test(text) && maxTouchPoints > 1;
}
function isMobilePlatform() {
  if (typeof navigator === "undefined") return false;
  return isMobileUserAgent(navigator.userAgent, navigator.maxTouchPoints || 0);
}
const APP_LAUNCH_REFOCUS_GRACE_MS = 900;
function launchAppThenFallback(schemeUrl, webUrl) {
  let timer = 0;
  let settled = false;
  const clearTimer = () => {
    if (timer) window.clearTimeout(timer);
    timer = 0;
  };
  const cleanup = () => {
    settled = true;
    clearTimer();
    document.removeEventListener("visibilitychange", onVisibilityChange);
    window.removeEventListener("pagehide", cleanup);
    window.removeEventListener("blur", onBlur);
    window.removeEventListener("focus", onFocus);
  };
  const onVisibilityChange = () => {
    if (document.hidden) cleanup();
  };
  const onBlur = () => clearTimer();
  const onFocus = () => {
    if (!settled && !document.hidden && !timer) {
      timer = window.setTimeout(fallback, APP_LAUNCH_REFOCUS_GRACE_MS);
    }
  };
  const fallback = () => {
    const hidden = document.hidden;
    cleanup();
    if (hidden) return;
    const opened = window.open(webUrl, "_blank");
    if (!opened) showWebFallbackToast(webUrl);
  };
  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("pagehide", cleanup);
  window.addEventListener("blur", onBlur);
  window.addEventListener("focus", onFocus);
  timer = window.setTimeout(fallback, APP_LAUNCH_FALLBACK_MS);
  window.location.href = schemeUrl;
}
function showWebFallbackToast(webUrl) {
  const existing = document.getElementById("obc-app-launch-toast");
  if (existing) existing.remove();
  const bar = document.createElement("div");
  bar.id = "obc-app-launch-toast";
  bar.style.cssText = "position:fixed;left:12px;right:12px;bottom:calc(64px + env(safe-area-inset-bottom));z-index:9999;display:flex;align-items:center;gap:10px;padding:10px 14px;background:rgba(30,30,36,.94);color:#fff;border-radius:12px;font-size:14px;box-shadow:0 4px 16px rgba(0,0,0,.25);";
  const text = document.createElement("span");
  text.textContent = "没能拉起 App";
  text.style.cssText = "flex:1;";
  const link = document.createElement("a");
  link.textContent = "打开网页版";
  link.href = webUrl;
  link.target = "_blank";
  link.rel = "noopener";
  link.style.cssText = "color:#7ec8ff;font-weight:600;text-decoration:none;white-space:nowrap;";
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "×";
  close.setAttribute("aria-label", "关闭");
  close.style.cssText = "background:none;border:none;color:#aaa;font-size:18px;line-height:1;padding:0 2px;";
  const remove = () => bar.remove();
  close.addEventListener("click", remove);
  link.addEventListener("click", remove);
  window.setTimeout(remove, 8e3);
  bar.appendChild(text);
  bar.appendChild(link);
  bar.appendChild(close);
  document.body.appendChild(bar);
}
export function openContentUrl(url) {
  if (!url) return;
  const deepLink = isMobilePlatform() ? buildAppDeepLink(url) : "";
  if (deepLink) {
    launchAppThenFallback(deepLink, url);
  } else {
    window.open(url, "_blank");
  }
}
//# sourceMappingURL=app-launch.js.map
