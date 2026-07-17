import type {
  ApiClient,
  ClaimedSourceTask as GeneratedClaimedSourceTask,
  SourceId,
  SourceOperation,
} from "../shared/api-client.ts";

export type ClaimedSourceTask = GeneratedClaimedSourceTask;
type BrowserOperationRequest = ClaimedSourceTask["payload"];
export type BrowserTaskResult = {
  readonly operation: SourceOperation;
  readonly items: ReadonlyArray<Record<string, unknown>>;
};

export interface SourceTaskTransport {
  claim(sourceId: SourceId): Promise<ClaimedSourceTask | null>;
  complete(taskId: string, leaseToken: string, result: BrowserTaskResult): Promise<void>;
}

export interface SourceTaskDispatcher {
  readonly sourceId: SourceId;
  pollOnce(): Promise<boolean>;
}

interface DispatcherOptions {
  readonly sourceId: SourceId;
  readonly operations: ReadonlyArray<SourceOperation>;
  readonly transport: SourceTaskTransport;
  readonly execute: (task: ClaimedSourceTask) => Promise<BrowserTaskResult>;
}

export function validateClaimedTask(
  task: ClaimedSourceTask,
  sourceId: SourceId,
  operations: ReadonlyArray<SourceOperation>,
): ClaimedSourceTask {
  if (task.source_id !== sourceId) {
    throw new Error(`source mismatch: expected ${sourceId}, got ${task.source_id}`);
  }
  const operation = task.payload.operation;
  if (!operations.includes(operation)) {
    throw new Error(`operation mismatch: ${sourceId} does not dispatch ${operation}`);
  }
  return task;
}

export function createSourceTaskDispatcher(options: DispatcherOptions): SourceTaskDispatcher {
  let inFlight: Promise<boolean> | null = null;

  async function poll(): Promise<boolean> {
    const claimed = await options.transport.claim(options.sourceId);
    if (!claimed) return false;
    const task = validateClaimedTask(claimed, options.sourceId, options.operations);
    const result = normalizeResult(task.payload, await options.execute(task));
    await options.transport.complete(task.id, task.lease_token, result);
    return true;
  }

  return Object.freeze({
    sourceId: options.sourceId,
    pollOnce(): Promise<boolean> {
      if (inFlight) return inFlight;
      inFlight = poll().finally(() => {
        inFlight = null;
      });
      return inFlight;
    },
  });
}

function normalizeResult(
  request: BrowserOperationRequest,
  result: BrowserTaskResult,
): BrowserTaskResult {
  if (result.operation !== request.operation) {
    throw new Error(`result operation mismatch: expected ${request.operation}`);
  }
  if (!Array.isArray(result.items)) throw new Error("source task result items must be an array");
  rejectCredentialFields(result.items);
  return result;
}

function rejectCredentialFields(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(rejectCredentialFields);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, "");
    if (/(cookie|credential|password|secret|session|token|authorization|apikey)$/.test(normalized)) {
      throw new Error(`credential-shaped source task result field: ${key}`);
    }
    rejectCredentialFields(child);
  }
}

export function createSourceTaskTransport(apiClient: ApiClient): SourceTaskTransport {
  return {
    claim(sourceId) {
      return apiClient.request<ClaimedSourceTask | null>("v1_source_tasks_claim", {
        query: { source_id: sourceId, wait_seconds: 0 },
      });
    },
    async complete(taskId, leaseToken, result) {
      await apiClient.request("v1_source_tasks_complete", {
        path: { task_id: taskId },
        body: { lease_token: leaseToken, result },
      });
    },
  };
}
