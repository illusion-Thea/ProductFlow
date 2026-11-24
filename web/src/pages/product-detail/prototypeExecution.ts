import type { CopyPayloadV2, PrototypeStatus, PrototypeTask } from "../../lib/types";
import { executionNeedsPolling } from "../../lib/executionPolling";

export const CONTENT_LANGUAGES = ["zh-CN", "ms-MY", "id-ID", "fil-PH"] as const;

export function previewCopyText(payload: CopyPayloadV2): string {
  const content = payload.content;
  if (content.kind === "freeform") return content.text;
  if (content.kind === "blocks") return content.blocks.map((block) => [block.label, block.text].filter(Boolean).join(" · ")).join("\n");
  return content.sections.map((section) => [section.title, section.body, ...section.items.map((block) => block.text)].filter(Boolean).join("\n")).join("\n\n");
}

export function prototypeNeedsPolling(task?: PrototypeStatus): boolean {
  return Boolean(task && (task.business_state === "cancelling" || task.operations.some(executionNeedsPolling)));
}

export function mergePrototypeStatus(task: PrototypeTask, status?: PrototypeStatus): PrototypeTask {
  if (!status || task.id !== status.id || status.status_version <= task.status_version) return task;
  const changed = new Map(status.operations.map((operation) => [operation.operation_id, operation]));
  const existing = new Set(task.operations.map((operation) => operation.operation_id));
  return { ...task, ...status, row_version: Math.max(task.row_version, status.row_version),
    operations: [...task.operations.map((operation) => changed.get(operation.operation_id) ?? operation),
      ...status.operations.filter((operation) => !existing.has(operation.operation_id))] };
}

export function selectedPlan(task?: PrototypeTask) {
  return task?.plans.find((plan) => plan.id === task.current_plan_id);
}
