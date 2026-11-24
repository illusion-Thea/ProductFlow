import { useState } from "react";
import { ExecutionOperationList } from "../../components/ExecutionOperationList";
import { api } from "../../lib/api";
import { executionErrorMessage } from "../../lib/executionErrors";
import { useI18n } from "../../lib/preferences";
import type { ContentLanguage, PrototypeDraft } from "../../lib/types";
import { CONTENT_LANGUAGES, previewCopyText, selectedPlan } from "./prototypeExecution";
import type { usePrototypeExecution } from "./usePrototypeExecution";

export function PrototypeExecutionPanel({ controller }: { controller: ReturnType<typeof usePrototypeExecution> }) {
  const { t } = useI18n();
  const [edit, setEdit] = useState<Partial<PrototypeDraft> | null>(null);
  const [experimentId, setExperimentId] = useState("");
  const { task, mutation } = controller;
  const experiment = controller.experiments.data?.items.find((item) => item.id === (task?.experiment_run_id || experimentId)) ?? controller.experiments.data?.items[0];
  const draft: PrototypeDraft = { sku: task?.draft.sku ?? "", content_language: task?.draft_content_language ?? "zh-CN",
    source_asset_ids: task?.draft.evidence.map((item) => item.source_asset_id) ?? experiment?.source_asset_ids ?? [],
    output_spec: task?.draft.output_spec ?? { width: 900, height: 1600, test_only: true }, ...edit };
  const plan = selectedPlan(task);
  const busy = mutation.isPending;
  const closed = task?.business_state === "cancelled" || task?.business_state === "cancelling";
  const started = task?.slots.some((slot) => slot.first_attempt_id);
  const fieldClass = "rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-900";
  const buttonClass = "rounded border border-slate-300 px-3 py-1.5 disabled:opacity-40 dark:border-slate-600";
  if (!controller.enabled) return null;
  return <details className="shrink-0 border-b border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900" data-testid="prototype-panel">
    <summary className="cursor-pointer px-4 py-2 font-medium">{t("prototype.title")}</summary>
    <div className="max-h-[55vh] space-y-3 overflow-y-auto p-4 text-sm">
      <p className="text-slate-600 dark:text-slate-300">{t("prototype.scope")}</p>
      {controller.loading && <p role="status">{t("app.loading")}</p>}
      {controller.error && <p role="alert" className="text-red-700 dark:text-red-300">{executionErrorMessage(controller.error, t)}</p>}
      <div className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1">{t("prototype.task")}<select className={fieldClass} value={controller.taskId} onChange={(event) => { controller.selectTask(event.target.value); setEdit(null); }}>
          <option value="">{t("prototype.empty")}</option>{controller.tasks.data?.items.map((item) => <option key={item.id} value={item.id}>{item.id.slice(0, 8)}</option>)}
        </select></label>
        <button type="button" className={buttonClass} onClick={() => void controller.refresh()}>{t("prototype.refresh")}</button>
        {task && <span>{t(`prototype.${task.business_state}`)} · {t("prototype.quota", { used: task.image_reserved_count, limit: task.image_limit })}</span>}
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="grid gap-1">{t("prototype.experiment")}<select className={fieldClass} value={experiment?.id ?? ""} disabled={Boolean(task) || busy} onChange={(event) => setExperimentId(event.target.value)}>
          {controller.experiments.data?.items.map((item) => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} ({task?.experiment_run_id === item.id ? task.image_limit - task.image_reserved_count : item.quota_remaining}/12)</option>)}
        </select></label>
        <label className="grid gap-1">SKU<input className={fieldClass} value={draft.sku ?? ""} disabled={closed || started || busy} onChange={(event) => setEdit((old) => ({ ...old, sku: event.target.value }))} /></label>
        <label className="grid gap-1">{t("prototype.language")}<select className={fieldClass} value={draft.content_language} disabled={closed || started || busy}
          onChange={(event) => setEdit((old) => ({ ...old, content_language: event.target.value as ContentLanguage }))}>
          {CONTENT_LANGUAGES.map((language) => <option key={language} value={language}>{t(`prototype.language.${language}`)}</option>)}
        </select></label>
      </div>
      {!experiment && <p>{t("prototype.syntheticRequired")}</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={buttonClass} disabled={busy || !experiment || closed || Boolean(started)} onClick={() => {
          if (experiment) void mutation.mutateAsync({ action: task ? "save" : "create", draft, experimentId: experiment.id }).then(() => setEdit(null)).catch(() => undefined);
        }}>{t(task ? "prototype.save" : "prototype.create")}</button>
        {task && <>
          <button type="button" className={buttonClass} disabled={busy || closed || started || Boolean(edit) || task.operations.some((operation) => ["queued", "running"].includes(operation.state))} onClick={() => mutation.mutate({ action: "text" })}>{t("prototype.text")}</button>
          <button type="button" className={buttonClass} disabled={busy || closed || Boolean(edit) || !draft.sku || plan?.status !== "validated" || plan?.content_language !== draft.content_language} onClick={() => mutation.mutate({ action: "confirm" })}>{t("prototype.confirm")}</button>
          <button type="button" className={buttonClass} disabled={busy || closed || started || Boolean(edit) || plan?.status !== "accepted"} onClick={() => mutation.mutate({ action: "generate" })}>{t("prototype.generate")}</button>
          <button type="button" className={buttonClass} disabled={busy || closed} onClick={() => mutation.mutate({ action: "cancel" })}>{t("prototype.cancel")}</button>
        </>}
      </div>
      {Boolean(edit) && <p>{t("prototype.unsaved")}</p>}
      {plan && <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">{plan.slots.map((slot) => <article key={slot.item_id} className="rounded border border-slate-200 p-2 dark:border-slate-700">
        <h3 className="font-medium">{t("prototype.slot", { index: slot.slot_index })}</h3><p className="break-words">{slot.copy_payload.summary}</p>
        <p className="whitespace-pre-wrap break-words">{previewCopyText(slot.copy_payload)}</p>
        <p className="text-xs text-slate-500 dark:text-slate-400">{slot.base_prompt}</p>
      </article>)}</div>}
      {task?.issue_code && <p>{t(task.issue_code === "composition_not_implemented" ? "prototype.baseOnly" : "prototype.executionAttention")}</p>}
      {task && <ExecutionOperationList operations={task.operations} pending={busy || closed} onAction={(operation, operationAction, acknowledge) => mutation.mutate({ action: "operation", operation, operationAction, acknowledge })} />}
      {task && task.artifacts.filter((artifact) => artifact.asset_kind === "base_image").length > 0 && <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {task.artifacts.filter((artifact) => artifact.asset_kind === "base_image").map((artifact) => <a key={artifact.id} href={api.toApiUrl(artifact.content_url)} target="_blank" rel="noreferrer">
          <img className="max-h-36 w-full object-contain" src={api.toApiUrl(artifact.content_url)} alt={t(artifact.is_late ? "prototype.late" : "prototype.base")} />
          <span>{t(artifact.is_late ? "prototype.late" : "prototype.base")}</span>
        </a>)}
      </div>}
    </div>
  </details>;
}
