export interface OutboxStorage {
  get(key: string): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
}

export interface OutboxRecord {
  readonly id: string;
}

export interface DurableOutbox<T extends OutboxRecord> {
  enqueue(record: T): Promise<void>;
  flush(deliver: (record: T) => Promise<void>): Promise<number>;
  size(): Promise<number>;
  snapshot(): Promise<T[]>;
}

interface DurableOutboxOptions {
  readonly storage: OutboxStorage;
  readonly storageKey: string;
}

/**
 * A small MV3-safe FIFO. Every mutation is persisted before it resolves, and
 * delivery removes a record only after the caller observes a successful POST.
 */
export function createDurableOutbox<T extends OutboxRecord>(
  options: DurableOutboxOptions,
): DurableOutbox<T> {
  let mutationTail: Promise<void> = Promise.resolve();
  let flushInFlight: Promise<number> | null = null;

  function serialized<R>(operation: () => Promise<R>): Promise<R> {
    const run = mutationTail.then(operation, operation);
    mutationTail = run.then(() => undefined, () => undefined);
    return run;
  }

  async function read(): Promise<T[]> {
    const stored = await options.storage.get(options.storageKey);
    const value = stored[options.storageKey];
    if (!Array.isArray(value)) return [];
    return value.filter(isOutboxRecord) as T[];
  }

  async function write(records: T[]): Promise<void> {
    await options.storage.set({ [options.storageKey]: records });
  }

  async function flushOnce(deliver: (record: T) => Promise<void>): Promise<number> {
    let delivered = 0;
    while (true) {
      const next = await serialized(async () => (await read())[0]);
      if (!next) return delivered;
      await deliver(structuredClone(next));
      await serialized(async () => {
        const records = await read();
        const index = records.findIndex((record) => record.id === next.id);
        if (index < 0) return;
        records.splice(index, 1);
        await write(records);
      });
      delivered += 1;
    }
  }

  return Object.freeze({
    enqueue(record: T): Promise<void> {
      return serialized(async () => {
        const records = await read();
        if (records.some((existing) => existing.id === record.id)) return;
        records.push(structuredClone(record));
        await write(records);
      });
    },
    flush(deliver: (record: T) => Promise<void>): Promise<number> {
      if (flushInFlight) return flushInFlight;
      flushInFlight = flushOnce(deliver).finally(() => {
        flushInFlight = null;
      });
      return flushInFlight;
    },
    size(): Promise<number> {
      return serialized(async () => (await read()).length);
    },
    snapshot(): Promise<T[]> {
      return serialized(async () => structuredClone(await read()));
    },
  });
}

function isOutboxRecord(value: unknown): value is OutboxRecord {
  return Boolean(value)
    && typeof value === "object"
    && !Array.isArray(value)
    && typeof (value as { id?: unknown }).id === "string";
}
