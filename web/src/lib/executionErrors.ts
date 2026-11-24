import { ApiError } from "./api";
import type { TranslateFunction } from "./preferences";

export function executionErrorMessage(error: unknown, t: TranslateFunction): string {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("previous_submission_recovered")) return "上次提交已恢复；当前修改仍保留，请确认后再次提交。";
  if (message.includes("row_version_conflict")) return t("execution.errorConflict");
  if (message.includes("submission_unresolved") || message.includes("commit_outcome") || message.includes("retry_same")) return t("execution.errorUncertain");
  if (message.includes("quota")) return t("execution.errorQuota");
  if (message.includes("verification_") || message.includes("real_provider")) return t("execution.errorIsolation");
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return t("execution.errorPermission");
    if (error.status === 422) return t("execution.errorInput");
    if (error.status === 409) return t("execution.errorAction");
  }
  return t("execution.errorNetwork");
}
