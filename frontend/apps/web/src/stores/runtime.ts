import type { EventEnvelope, RuntimeResponse, WebApi } from "../services/api";
import { defineStore } from "pinia";
import { ref } from "vue";
import {
  errorMessage,
  isCancellation,
  RequestOwner,
  type LoadPhase,
} from "./state";

const delays = [100, 500, 1_000, 2_000] as const;
export type Delay = (
  milliseconds: number,
  signal: AbortSignal,
) => Promise<void>;
export const abortableDelay: Delay = (milliseconds, signal) =>
  new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });

export const useRuntimeStore = defineStore("runtime", () => {
  const phase = ref<LoadPhase>("idle");
  const health = ref<RuntimeResponse>();
  const events = ref<readonly EventEnvelope[]>([]);
  const streamConnected = ref(false);
  const error = ref<string>();
  const requests = new RequestOwner();
  let streamController: AbortController | undefined;
  let streamTask: Promise<void> | undefined;

  async function load(api: WebApi): Promise<void> {
    const signal = requests.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.runtimeHealth(signal);
      if (!requests.owns(signal)) return;
      health.value = next;
      phase.value = "success";
    } catch (caught) {
      if (isCancellation(caught) || !requests.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  function connect(api: WebApi, delay: Delay = abortableDelay): Promise<void> {
    if (streamTask !== undefined) return streamTask;
    streamController = new AbortController();
    const signal = streamController.signal;
    streamTask = (async () => {
      let attempt = 0;
      while (!signal.aborted) {
        try {
          streamConnected.value = true;
          const after = events.value.at(-1)?.event_id;
          for await (const event of api.events(after, signal)) {
            if (
              events.value.some(
                (existing) => existing.event_id === event.event_id,
              )
            )
              continue;
            events.value = [...events.value.slice(-49), event];
            attempt = 0;
          }
        } catch (caught) {
          if (signal.aborted) break;
          error.value = errorMessage(caught);
        } finally {
          streamConnected.value = false;
        }
        try {
          const wait = delays[Math.min(attempt, delays.length - 1)];
          if (wait === undefined) break;
          await delay(wait, signal);
          attempt += 1;
        } catch {
          break;
        }
      }
    })().finally(() => {
      streamTask = undefined;
      streamController = undefined;
    });
    return streamTask;
  }

  function disconnect(): void {
    streamController?.abort();
  }

  return {
    phase,
    health,
    events,
    streamConnected,
    error,
    load,
    connect,
    disconnect,
    cancel: () => requests.cancel(),
  };
});
