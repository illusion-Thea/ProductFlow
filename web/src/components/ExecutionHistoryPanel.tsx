import { ExecutionOperationList } from "./ExecutionOperationList";
import { useI18n } from "../lib/preferences";
import { executionErrorMessage } from "../lib/executionErrors";
import type { ExecutionAction, ExecutionSummary } from "../lib/types";

export function ExecutionHistoryPanel({ operations, pending, error, onAction }: {
  operations: ExecutionSummary[]; pending: boolean; error: Error | null;
  onAction: (operation: ExecutionSummary, action: ExecutionAction, acknowledge: boolean) => void;
}) {
  const { t } = useI18n();
  if (!operations.length) return null;
  return <details className="shrink-0 border-b border-slate-200 bg-white px-4 py-2 dark:border-slate-700 dark:bg-slate-900">
    <summary className="cursor-pointer">{t("execution.history")}</summary>
    <div className="max-h-[40vh] overflow-y-auto py-2">
      {error && <p role="alert" className="text-red-700 dark:text-red-300">{executionErrorMessage(error, t)}</p>}
      <ExecutionOperationList operations={operations} pending={pending} onAction={onAction} />
    </div>
  </details>;
}
