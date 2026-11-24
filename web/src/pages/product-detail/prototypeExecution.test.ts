import { describe, expect, it } from "vitest";
import type { ExecutionSummary, PrototypeTask } from "../../lib/types";
import { CONTENT_LANGUAGES, mergePrototypeStatus, previewCopyText, prototypeNeedsPolling, selectedPlan } from "./prototypeExecution";

const task: PrototypeTask = {
  id: "task", task_id: "task", product_id: "product", batch_id: "batch", experiment_run_id: "experiment",
  business_state: "draft", issue_code: null, row_version: 2, status_version: 3, current_plan_id: null,
  draft_content_language: "zh-CN", draft_revision: 1, image_limit: 12, image_reserved_count: 0, quota_remaining: 12,
  slots: [], operations: [], draft: { sku: null, output_spec: { width: 32, height: 32, test_only: true }, evidence: [] },
  plans: [], artifacts: [],
};
const unknown: ExecutionSummary = { operation_id: "op", attempt_id: "attempt", row_version: 1, root_row_version: 2,
  source_kind: "prototype", source_id: "task", state: "unknown", wait_closed_at: null,
  status_url: "/status", action_url: "/op", retry_url: "/op/retry", current_recovery: null };

describe("prototype status and human preview", () => {
  it("ignores stale polling without rolling back drafts or action versions", () => {
    expect(mergePrototypeStatus(task, { ...task, status_version: 2, row_version: 1 })).toBe(task);
    expect(mergePrototypeStatus(task, { ...task, status_version: 4, row_version: 1 }).row_version).toBe(2);
    expect(selectedPlan(task)).toBeUndefined();
    expect(task.business_state).not.toBe("completed");
  });
  it("polls unresolved unknown but stops after explicit wait closure", () => {
    expect(prototypeNeedsPolling({ ...task, operations: [unknown] })).toBe(true);
    expect(prototypeNeedsPolling({ ...task, operations: [{ ...unknown, wait_closed_at: "2026-11-23T00:00:00Z" }] })).toBe(false);
    expect(prototypeNeedsPolling({ ...task, business_state: "cancelling" })).toBe(true);
  });
  it("previews actual copy, independent of UI locale or its summary", () => {
    expect(CONTENT_LANGUAGES).toEqual(["zh-CN", "ms-MY", "id-ID", "fil-PH"]);
    expect(previewCopyText({ version: 2, summary: "summary", content: { kind: "freeform", text: "完整已确认文案" } })).toBe("完整已确认文案");
  });
});
