/**
 * OpenBiliClaw popup — boot autostart control.
 *
 * Keeps the UI logic testable without a browser DOM, mirroring
 * popup-auth-control.js: API creation plus a duck-typed wiring helper.
 */

type FetchLike = typeof fetch;

interface AutostartStatus {
  reason?: string;
  manage_ollama?: boolean;
  registered?: boolean;
  can_manage?: boolean;
  enabled?: boolean;
  [key: string]: unknown;
}

interface AutostartApiOptions {
  getBaseUrl: () => Promise<string>;
  fetchImpl?: FetchLike;
}

interface AutostartControlElements {
  checkbox?: Pick<HTMLInputElement, "checked" | "disabled" | "addEventListener">;
  hint?: Pick<HTMLElement, "textContent">;
}

export function createAutostartApi({ getBaseUrl, fetchImpl }: AutostartApiOptions) {
  const doFetch: FetchLike = fetchImpl || fetch.bind(globalThis);

  async function status() {
    try {
      const base = await getBaseUrl();
      const res = await doFetch(`${base}/autostart-status`);
      if (!res.ok) return null;
      return await res.json() as AutostartStatus;
    } catch {
      return null;
    }
  }

  async function apply(enabled: boolean) {
    const base = await getBaseUrl();
    const res = await doFetch(`${base}/autostart/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-OBC-Auth": "1" },
      body: JSON.stringify({ enabled: Boolean(enabled) }),
    });
    let data = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    return { ok: res.ok, status: res.status, data };
  }

  return { status, apply };
}

function disabledHint(status: AutostartStatus | null): string {
  const reason = status?.reason || "";
  if (reason === "env_managed") {
    return "检测到环境变量配置，登录会话可能拿不到这些值；请先写入 config.toml。";
  }
  if (reason === "shadowed") {
    return "config.local.toml 正在覆盖开关，无法在此修改。";
  }
  if (reason === "unsupported_docker_runtime") {
    return "当前在 Docker / 容器环境中，不能注册桌面登录自启动。";
  }
  if (reason === "unsupported_platform") {
    return "当前平台暂不支持开机自启动。";
  }
  if (reason === "local_only") {
    return "仅本机浏览器插件可修改此设置。";
  }
  return "当前环境不能在这里修改开机自启动。";
}

function enabledHint(status: AutostartStatus): string {
  const ollamaCopy = status?.manage_ollama
    ? "；本机 Ollama 配置会在需要时顺带拉起"
    : "";
  if (status?.registered === false) {
    return `配置已开启，但系统注册缺失；下次后端启动会尝试修复${ollamaCopy}。`;
  }
  return `已开启：下次登录系统会拉起后端，不启停当前进程${ollamaCopy}。`;
}

function activeHint(status: AutostartStatus | null): string {
  if (!status) return "无法读取开机自启动状态。";
  if (!status.can_manage) return disabledHint(status);
  if (status.enabled) return enabledHint(status);
  return "已关闭：不会注册登录自启动；当前后端进程不受影响。";
}

/**
 * Wire popup DOM controls to the autostart API.
 *
 * @param els {checkbox, hint} — duck-typed elements.
 * @param opts {getBaseUrl, fetchImpl}
 */
export function initAutostartControl(
  els: AutostartControlElements = {},
  opts: AutostartApiOptions,
) {
  const api = createAutostartApi(opts);
  let current: AutostartStatus | null = null;
  let busy = false;

  const setHint = (msg: string) => {
    if (els.hint) els.hint.textContent = msg;
  };

  function applyServerState() {
    const can = Boolean(current && current.can_manage);
    if (els.checkbox) {
      els.checkbox.checked = Boolean(current && current.enabled);
      els.checkbox.disabled = busy || !can;
    }
    setHint(activeHint(current));
  }

  async function load() {
    current = await api.status();
    applyServerState();
    return current;
  }

  async function apply(enabled: boolean) {
    busy = true;
    if (els.checkbox) els.checkbox.disabled = true;
    setHint(enabled ? "正在开启开机自启动…" : "正在关闭开机自启动…");
    let result;
    try {
      result = await api.apply(enabled);
    } catch {
      setHint("无法连接后端，请稍后重试。");
      busy = false;
      await load();
      return;
    }
    if (result.ok) {
      current = result.data || current;
      busy = false;
      applyServerState();
      await load();
      return;
    }
    current = result.data || current;
    if (result.status === 403) {
      setHint("仅本机浏览器插件可修改此设置。");
    } else if (result.status === 409) {
      setHint(disabledHint(current));
    } else {
      setHint("保存失败，请重试。");
    }
    busy = false;
    await load();
  }

  const checkbox = els.checkbox;
  if (checkbox && typeof checkbox.addEventListener === "function") {
    checkbox.addEventListener("change", () => {
      void apply(Boolean(checkbox.checked));
    });
  }

  void load();
  return { reload: load };
}
