type ProbeType = "avoidance.probe" | "interest.probe";

// TODO(types): probe payloads come from profile persistence and WebSocket
// events; tighten this interface when those backend contracts are versioned.
interface ProbeNotification {
  type?: unknown;
  domain?: unknown;
  title?: unknown;
  status?: unknown;
  [key: string]: unknown;
}

const handledProbeKeys = new Set<string>();

function normalizeText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function normalizeProbeType(type: unknown): ProbeType {
  return normalizeText(type) === "avoidance.probe" ? "avoidance.probe" : "interest.probe";
}

export function probeNotificationKey(type: unknown, domain: unknown): string {
  const normalizedDomain = normalizeText(domain).toLowerCase();
  if (!normalizedDomain) {
    return "";
  }
  return `${normalizeProbeType(type)}:${normalizedDomain}`;
}

export function rememberHandledProbe(domain: unknown, type: unknown = "interest.probe"): string {
  const key = probeNotificationKey(type, domain);
  if (key) {
    handledProbeKeys.add(key);
  }
  return key;
}

export function forgetHandledProbe(domain: unknown, type: unknown = "interest.probe"): void {
  const key = probeNotificationKey(type, domain);
  if (key) {
    handledProbeKeys.delete(key);
  }
}

export function isProbeHandled(domain: unknown, type: unknown = "interest.probe"): boolean {
  const key = probeNotificationKey(type, domain);
  return Boolean(key && handledProbeKeys.has(key));
}

export function shouldHydrateProbe(
  item: unknown,
  type: unknown = "interest.probe",
): boolean {
  const domain = normalizeText(
    (item as ProbeNotification | null | undefined)?.domain
      || (item as ProbeNotification | null | undefined)?.title,
  );
  if (!domain || isProbeHandled(domain, type)) {
    return false;
  }
  const status = normalizeText(
    (item as ProbeNotification | null | undefined)?.status,
  ).toLowerCase() || "active";
  return status === "active" || status === "pending";
}

export function shouldDisplayProbeFromWebSocket(
  event: unknown,
  type: unknown = (event as ProbeNotification | null | undefined)?.type || "interest.probe",
): boolean {
  return shouldHydrateProbe(
    { domain: (event as ProbeNotification | null | undefined)?.domain, status: "active" },
    normalizeProbeType(type),
  );
}

export function filterVisibleProbes(items: unknown, type: unknown = "interest.probe"): ProbeNotification[] {
  return Array.isArray(items)
    ? (items as ProbeNotification[]).filter((item) => shouldHydrateProbe(item, type))
    : [];
}

export function removeProbeFromNotifications(
  notifications: unknown,
  domain: unknown,
  type: unknown = "interest.probe",
): ProbeNotification[] {
  const key = probeNotificationKey(type, domain);
  if (!key || !Array.isArray(notifications)) {
    return [];
  }
  return (notifications as ProbeNotification[])
    .filter((item) => probeNotificationKey(item?.type, item?.domain || item?.title) !== key);
}

export function mergeProbeNotifications(persisted: unknown, current: unknown): ProbeNotification[] {
  const merged: ProbeNotification[] = [];
  const seen = new Set<string>();
  for (const item of Array.isArray(persisted) ? persisted as ProbeNotification[] : []) {
    const type = normalizeProbeType(item?.type);
    if (!shouldHydrateProbe(item, type)) {
      continue;
    }
    const key = probeNotificationKey(type, item.domain || item.title);
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push({ ...item, type });
  }
  for (const item of Array.isArray(current) ? current as ProbeNotification[] : []) {
    const type = normalizeProbeType(item?.type);
    if (!shouldHydrateProbe(item, type)) {
      continue;
    }
    const key = probeNotificationKey(type, item.domain || item.title);
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push({ ...item, type });
  }
  return merged;
}

export function resetHandledProbesForTests(): void {
  handledProbeKeys.clear();
}
