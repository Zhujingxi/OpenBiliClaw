import type { ActivityEvent } from "../shared/api-client.ts";

/** Payload validation failures cannot become valid by replaying the same event. */
export function isTerminalActivityError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const status = (error as { status?: unknown }).status;
  return status === 400 || status === 413 || status === 422;
}

export async function deliverActivityEvent<T extends ActivityEvent>(
  event: T,
  post: (event: T) => Promise<void>,
  deadLetter: (event: T, status: number) => Promise<void>,
): Promise<void> {
  try {
    await post(event);
  } catch (error) {
    if (!isTerminalActivityError(error)) throw error;
    await deadLetter(event, (error as { status: number }).status);
  }
}
