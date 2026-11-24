import type { ExecutionSummary } from "./types";

export function executionNeedsPolling(operation?: ExecutionSummary | null): boolean {
  return Boolean(operation && (["queued", "running"].includes(operation.state)
    || (operation.state === "unknown" && !operation.wait_closed_at)));
}
