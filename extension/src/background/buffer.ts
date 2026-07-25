import type { BehaviorEvent } from "../shared/types.js";

const HIGH_FREQUENCY_TYPES = new Set(["scroll", "hover", "snapshot"]);
const STRONG_SIGNAL_TYPES = new Set([
  "comment",
  "coin",
  "favorite",
  "feedback",
  "follow",
  "like",
  "share",
  "view",
]);

// ---------------------------------------------------------------------------
// Persistence constants (v0.3.x — MV3 service-worker buffer survival)
// ---------------------------------------------------------------------------
//
// Chrome recycles an idle MV3 service worker after ~30s — the same order as
// the flush interval — wiping the in-memory `eventBuffer`. To stop that
// systematic event loss, every buffer mutation is mirrored (awaited
// write-through, no debouncing: a pending setTimeout would die with the SW,
// which is the exact failure mode being fixed) into chrome.storage.local, and
// restored on the next wake behind an async init gate.

/** chrome.storage.local key holding the live buffer mirror. */
export const EVENT_BUFFER_KEY = "obc_event_buffer";
/** chrome.storage.local key holding events parked while the backend is uninitialized. */
export const PARKED_KEY = "obc_parked_events";
/** Bounds in-memory + mirrored buffer growth when the backend is down for days. */
export const BUFFER_MAX_SIZE = 50;
/** Parking lot cap; oldest parked events are FIFO-evicted past this. */
export const PARKED_MAX = 500;
/** Parked events older than this are dropped on read. */
export const PARKED_TTL_MS = 48 * 3_600_000;

interface ParkedEntry {
  parkedAt: number;
  event: BehaviorEvent;
}

// ---------------------------------------------------------------------------
// chrome.storage.local wrappers (callback style, promisified — matches the
// backend-endpoint convention and works with @types/chrome). All access is
// guarded so importing this module never touches chrome (the pure-function
// helpers below are unit-tested without a chrome stub).
// ---------------------------------------------------------------------------

interface ChromeStorageLocalLike {
  get?: (key: string, callback: (items: Record<string, unknown>) => void) => void;
  set?: (items: Record<string, unknown>, callback?: () => void) => void;
  remove?: (key: string, callback?: () => void) => void;
}

function getStorageLocal(): ChromeStorageLocalLike | null {
  try {
    const chromeApi = (globalThis as { chrome?: { storage?: { local?: ChromeStorageLocalLike } } })
      .chrome;
    return chromeApi?.storage?.local ?? null;
  } catch {
    return null;
  }
}

function getLastError(): { message?: string } | undefined {
  try {
    return (globalThis as { chrome?: { runtime?: { lastError?: { message?: string } } } }).chrome
      ?.runtime?.lastError;
  } catch {
    return undefined;
  }
}

async function storageGet<T>(key: string): Promise<T | undefined> {
  const storage = getStorageLocal();
  if (!storage?.get) return undefined;
  return new Promise<T | undefined>((resolve, reject) => {
    try {
      storage.get?.(key, (items) => {
        const err = getLastError();
        if (err) {
          reject(new Error(err.message ?? "storage.get failed"));
          return;
        }
        resolve(items?.[key] as T | undefined);
      });
    } catch (err) {
      reject(err instanceof Error ? err : new Error(String(err)));
    }
  });
}

async function storageSet(items: Record<string, unknown>): Promise<void> {
  const storage = getStorageLocal();
  if (!storage?.set) return;
  return new Promise<void>((resolve, reject) => {
    try {
      storage.set?.(items, () => {
        const err = getLastError();
        if (err) {
          reject(new Error(err.message ?? "storage.set failed"));
          return;
        }
        resolve();
      });
    } catch (err) {
      reject(err instanceof Error ? err : new Error(String(err)));
    }
  });
}

async function storageRemove(key: string): Promise<void> {
  const storage = getStorageLocal();
  if (!storage?.remove) return;
  return new Promise<void>((resolve, reject) => {
    try {
      storage.remove?.(key, () => {
        const err = getLastError();
        if (err) {
          reject(new Error(err.message ?? "storage.remove failed"));
          return;
        }
        resolve();
      });
    } catch (err) {
      reject(err instanceof Error ? err : new Error(String(err)));
    }
  });
}

function asEventArray(value: unknown): BehaviorEvent[] {
  return Array.isArray(value) ? (value as BehaviorEvent[]) : [];
}

// ---------------------------------------------------------------------------
// Buffer state — owned here so the init gate (bufferReady) and the persistence
// invariants are unit-testable. The service worker delegates all buffer
// mutation to the functions below.
// ---------------------------------------------------------------------------

let eventBuffer: BehaviorEvent[] = [];
let bufferReadyPromise: Promise<void> | null = null;

async function restoreBuffer(): Promise<void> {
  try {
    const stored = asEventArray(await storageGet<BehaviorEvent[]>(EVENT_BUFFER_KEY));
    if (stored.length > 0) {
      // Persisted events were buffered before the restart, so they are older:
      // prepend them to the front (oldest-first), then re-enforce the cap.
      eventBuffer = [...stored, ...eventBuffer];
      if (eventBuffer.length > BUFFER_MAX_SIZE) {
        eventBuffer = eventBuffer.slice(eventBuffer.length - BUFFER_MAX_SIZE);
      }
      await storageRemove(EVENT_BUFFER_KEY);
    }
  } catch (err) {
    console.warn(
      "[OpenBiliClaw] Buffer restore failed:",
      err instanceof Error ? err.message : String(err),
    );
  }
}

/**
 * Async init gate. The classic (iife) service-worker bundle has no top-level
 * await, so restore runs behind this memoized promise; every entry point that
 * touches the buffer awaits it first, so an event arriving before restore
 * completes cannot be lost or overwritten by the restore.
 */
export function bufferReady(): Promise<void> {
  if (bufferReadyPromise === null) {
    bufferReadyPromise = restoreBuffer();
  }
  return bufferReadyPromise;
}

/** Awaited write-through mirror of the current buffer. Failures are logged and swallowed. */
export async function persistBuffer(): Promise<void> {
  try {
    await storageSet({ [EVENT_BUFFER_KEY]: eventBuffer });
  } catch (err) {
    console.warn(
      "[OpenBiliClaw] Buffer persist failed (storage), keeping in-memory buffer:",
      err instanceof Error ? err.message : String(err),
    );
  }
}

/** Current in-memory buffer length. Callers must have awaited bufferReady(). */
export function getBufferLength(): number {
  return eventBuffer.length;
}

/**
 * Enqueue an event (gated on restore), then await the mirror write so a strong
 * signal is on disk even if the service worker dies mid-flush. Returns the new
 * buffer length so the caller can decide whether to flush.
 */
export async function enqueueEvent(event: BehaviorEvent): Promise<number> {
  await bufferReady();
  const before = eventBuffer.length;
  eventBuffer = enqueueBufferedEvent(eventBuffer, event, BUFFER_MAX_SIZE);
  // enqueueBufferedEvent drops the oldest when the cap is hit; a dedupe
  // replacement keeps length flat and is not an eviction.
  if (eventBuffer.length <= before && before >= BUFFER_MAX_SIZE) {
    console.warn(
      "[OpenBiliClaw] Buffer full, evicted oldest event to stay within",
      String(BUFFER_MAX_SIZE),
    );
  }
  await persistBuffer();
  return eventBuffer.length;
}

/** Drain the buffer for a flush attempt. Caller must have awaited bufferReady(). */
export function takeBufferedEvents(): BehaviorEvent[] {
  const events = eventBuffer;
  eventBuffer = [];
  return events;
}

/** Re-buffer a failed flush's events at the front (matches the pre-persistence unshift). */
export function requeueEvents(events: BehaviorEvent[]): void {
  eventBuffer.unshift(...events);
  if (eventBuffer.length > BUFFER_MAX_SIZE) {
    eventBuffer = eventBuffer.slice(eventBuffer.length - BUFFER_MAX_SIZE);
  }
}

/** Prepend drained parked events to the front, oldest-first, respecting the cap. */
export function prependBufferedEvents(events: BehaviorEvent[]): void {
  requeueEvents(events);
}

/**
 * Move a batch the backend cannot yet accept (not_initialized) into the parking
 * lot instead of dropping it. Behaviour events (dwell/click) are exactly what we
 * are saving; history-shaped duplicates are absorbed by init backfill + dedup.
 */
export async function parkEvents(events: BehaviorEvent[]): Promise<void> {
  if (events.length === 0) return;
  try {
    const now = Date.now();
    const stored = await storageGet<ParkedEntry[]>(PARKED_KEY);
    const existing = Array.isArray(stored) ? stored : [];
    const entries: ParkedEntry[] = [
      ...existing,
      ...events.map((event) => ({ parkedAt: now, event })),
    ];
    const trimmed =
      entries.length > PARKED_MAX ? entries.slice(entries.length - PARKED_MAX) : entries;
    await storageSet({ [PARKED_KEY]: trimmed });
  } catch (err) {
    console.warn(
      "[OpenBiliClaw] Parking events failed (storage):",
      err instanceof Error ? err.message : String(err),
    );
  }
}

/**
 * Drain the parking lot oldest-first, dropping entries past the 48h TTL, and
 * delete the key. Called after a successful (backend-initialized) flush.
 */
export async function drainParkedEvents(): Promise<BehaviorEvent[]> {
  try {
    const stored = await storageGet<ParkedEntry[]>(PARKED_KEY);
    if (!Array.isArray(stored) || stored.length === 0) return [];
    const cutoff = Date.now() - PARKED_TTL_MS;
    const events = stored
      .filter((entry) => entry && typeof entry.parkedAt === "number" && entry.parkedAt >= cutoff)
      .map((entry) => entry.event);
    await storageRemove(PARKED_KEY);
    return events;
  } catch (err) {
    console.warn(
      "[OpenBiliClaw] Draining parked events failed (storage):",
      err instanceof Error ? err.message : String(err),
    );
    return [];
  }
}

/** Test-only: reset owned buffer state so the restore gate can re-run. */
export function __resetBufferForTests(): void {
  eventBuffer = [];
  bufferReadyPromise = null;
}

// ---------------------------------------------------------------------------
// Pure helpers (unchanged) — dedupe key + enqueue + strong-signal gate.
// ---------------------------------------------------------------------------

function getBucket(event: BehaviorEvent): number {
  return Math.floor(event.timestamp / 1000);
}

export function buildDedupeKey(event: BehaviorEvent): string | null {
  if (!HIGH_FREQUENCY_TYPES.has(event.type)) return null;

  if (event.type === "hover") {
    const href = String(event.metadata.href ?? "");
    return `hover:${event.url}:${href}`;
  }

  return `${event.type}:${event.url}:${getBucket(event)}`;
}

/**
 * Enqueue an event into the buffer, mutating it in place.
 * Safe because the service worker is single-threaded.
 */
export function enqueueBufferedEvent(
  buffer: BehaviorEvent[],
  event: BehaviorEvent,
  maxSize: number,
): BehaviorEvent[] {
  const dedupeKey = buildDedupeKey(event);

  if (dedupeKey) {
    const existingIndex = buffer.findIndex((item) => buildDedupeKey(item) === dedupeKey);
    if (existingIndex >= 0) {
      buffer[existingIndex] = event;
      return buffer;
    }
  }

  buffer.push(event);
  if (buffer.length > maxSize) {
    buffer.shift();
  }
  return buffer;
}

export function shouldFlushImmediately(event: BehaviorEvent): boolean {
  if (
    event.type === "click" &&
    (typeof event.metadata.watch_seconds === "number" ||
      typeof event.metadata.video_duration_seconds === "number" ||
      typeof event.metadata.dwell_source === "string")
  ) {
    return true;
  }
  return STRONG_SIGNAL_TYPES.has(event.type);
}
