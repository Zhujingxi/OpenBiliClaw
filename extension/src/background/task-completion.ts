import type {
  CompleteSourceTask,
} from "../shared/api-client.ts";
import type {
  BrowserTaskResult,
  SourceTaskFailure,
} from "./generic-source-task-dispatcher.ts";

export type TaskCompletionOutcome =
  | { readonly result: BrowserTaskResult }
  | { readonly failure: SourceTaskFailure };

export type PendingTaskCompletion = {
  readonly id: string;
  readonly leaseToken: string;
  readonly outcome: TaskCompletionOutcome;
};

export function completionRequestBody(completion: PendingTaskCompletion): CompleteSourceTask {
  return "result" in completion.outcome
    ? { lease_token: completion.leaseToken, result: completion.outcome.result }
    : { lease_token: completion.leaseToken, failure: completion.outcome.failure };
}

export function isTerminalCompletionError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const status = (error as { status?: unknown }).status;
  return status === 404 || status === 409 || status === 410 || status === 422;
}

export async function deliverTaskCompletion(
  completion: PendingTaskCompletion,
  post: (body: CompleteSourceTask) => Promise<void>,
  deadLetter: (completion: PendingTaskCompletion, status: number) => Promise<void>,
): Promise<void> {
  try {
    await post(completionRequestBody(completion));
  } catch (error) {
    if (!isTerminalCompletionError(error)) throw error;
    await deadLetter(completion, (error as { status: number }).status);
  }
}
