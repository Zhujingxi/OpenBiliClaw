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

export type SourceTaskFailure = {
  readonly code: "claim_mismatch" | "operation_mismatch" | "result_mismatch" | "deadline_exceeded" | "execution_failed";
  readonly error_type: "TaskContractError" | "TaskDeadlineError" | string;
};

export interface SourceTaskTransport {
  claim(sourceId: SourceId): Promise<ClaimedSourceTask | null>;
  complete(taskId: string, leaseToken: string, result: BrowserTaskResult): Promise<void>;
  fail(taskId: string, leaseToken: string, failure: SourceTaskFailure): Promise<void>;
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
    throw new TaskContractError(
      "claim_mismatch",
      `source mismatch: expected ${sourceId}, got ${task.source_id}`,
    );
  }
  const operation = task.payload.operation;
  if (!operations.includes(operation)) {
    throw new TaskContractError(
      "operation_mismatch",
      `operation mismatch: ${sourceId} does not dispatch ${operation}`,
    );
  }
  return task;
}

export function createSourceTaskDispatcher(options: DispatcherOptions): SourceTaskDispatcher {
  let inFlight: Promise<boolean> | null = null;

  async function poll(): Promise<boolean> {
    const claimed = await options.transport.claim(options.sourceId);
    if (!claimed) return false;
    try {
      const task = validateClaimedTask(claimed, options.sourceId, options.operations);
      const result = normalizeResult(
        task.payload,
        await beforeRequestDeadline(task.request_deadline_at, options.execute(task)),
      );
      await options.transport.complete(task.id, task.lease_token, result);
    } catch (error) {
      await options.transport.fail(claimed.id, claimed.lease_token, failureFrom(error));
    }
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
    throw new TaskContractError(
      "result_mismatch",
      `result operation mismatch: expected ${request.operation}`,
    );
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
    async fail(taskId, leaseToken, failure) {
      await apiClient.request("v1_source_tasks_complete", {
        path: { task_id: taskId },
        body: { lease_token: leaseToken, failure },
      });
    },
  };
}

class TaskContractError extends Error {
  readonly code: SourceTaskFailure["code"];

  constructor(
    code: SourceTaskFailure["code"],
    message: string,
  ) {
    super(message);
    this.name = "TaskContractError";
    this.code = code;
  }
}

class TaskDeadlineError extends Error {
  constructor() {
    super("source task request deadline reached");
    this.name = "TaskDeadlineError";
  }
}

function beforeRequestDeadline<T>(deadlineAt: string, work: Promise<T>): Promise<T> {
  // Reserve enough time for the authenticated failure POST to arrive before
  // the backend's durable request deadline closes the lease.
  const reserveForFailureMs = 2_000;
  const budgetMs = Date.parse(deadlineAt) - Date.now() - reserveForFailureMs;
  if (!Number.isFinite(budgetMs) || budgetMs <= 0) {
    void work.catch(() => undefined);
    return Promise.reject(new TaskDeadlineError());
  }
  return new Promise<T>((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new TaskDeadlineError()),
      Math.min(budgetMs, 2_147_483_647),
    );
    work.then(
      (value) => {
        clearTimeout(timeout);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timeout);
        reject(error);
      },
    );
  });
}

function failureFrom(error: unknown): SourceTaskFailure {
  if (error instanceof TaskContractError) {
    return { code: error.code, error_type: "TaskContractError" };
  }
  if (error instanceof TaskDeadlineError) {
    return { code: "deadline_exceeded", error_type: "TaskDeadlineError" };
  }
  return {
    code: "execution_failed",
    error_type: safeErrorType(error),
  };
}

function safeErrorType(error: unknown): string {
  const value = error instanceof Error ? error.name : "UnknownError";
  return /^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(value) ? value : "UnknownError";
}
