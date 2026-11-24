import { useState } from "react";
import { useI18n } from "../lib/preferences";
import type { ExecutionAction, ExecutionSummary } from "../lib/types";

export function ExecutionOperationList({ operations, pending, onAction }: {
  operations: ExecutionSummary[]; pending: boolean;
  onAction: (operation: ExecutionSummary, action: ExecutionAction, acknowledge: boolean) => void;
}) {
  const { t } = useI18n();
  const [acknowledged, setAcknowledged] = useState<Record<string, boolean>>({});
  return <div className="space-y-2">
    {operations.map((operation) => <div key={operation.operation_id} className="rounded border border-slate-200 p-2 dark:border-slate-700">
      <p>{t(`execution.${operation.state}`)} <span className="text-xs text-slate-500 dark:text-slate-400">{operation.operation_id.slice(0, 8)}</span></p>
      {operation.current_recovery && ["queued", "running", "awaiting_local_decision"].includes(operation.state) && <p className="text-xs">{t("execution.deadline")}: {new Date(operation.current_recovery.deadline_at).toLocaleString()}</p>}
      {operation.state === "unknown" && <>
        <p className="text-amber-800 dark:text-amber-200">{t("execution.unknownNotice")}</p>
        {!operation.wait_closed_at && <button type="button" className="underline" disabled={pending} onClick={() => onAction(operation, "close-wait", false)}>{t("execution.close")}</button>}
        {operation.wait_closed_at && <label className="flex items-start gap-2"><input type="checkbox" checked={acknowledged[operation.operation_id] ?? false}
          onChange={(event) => setAcknowledged((old) => ({ ...old, [operation.operation_id]: event.target.checked }))} />{t("execution.duplicate")}</label>}
      </>}
      {(operation.state === "failed" || (operation.state === "unknown" && operation.wait_closed_at)) && <button type="button" className="underline" disabled={pending || (operation.state === "unknown" && !acknowledged[operation.operation_id])}
        onClick={() => onAction(operation, "retry", acknowledged[operation.operation_id] ?? false)}>{t("execution.retry")}</button>}
      {operation.state === "awaiting_local_decision" && <div className="flex gap-4">
        <button type="button" className="underline" disabled={pending} onClick={() => onAction(operation, "resume-local", false)}>{t("execution.resume")}</button>
        <button type="button" className="underline" disabled={pending} onClick={() => onAction(operation, "end-local", false)}>{t("execution.end")}</button>
      </div>}
    </div>)}
  </div>;
}
