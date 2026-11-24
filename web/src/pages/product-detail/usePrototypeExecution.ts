import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, verificationEnvironment } from "../../lib/api";
import type { ExecutionAction, ExecutionSummary, PrototypeDraft } from "../../lib/types";
import { mergePrototypeStatus, prototypeNeedsPolling, selectedPlan } from "./prototypeExecution";

type Command = { action: "create" | "save"; draft: PrototypeDraft; experimentId: string }
  | { action: "text" | "confirm" | "generate" | "cancel" }
  | { action: "operation"; operation: ExecutionSummary; operationAction: ExecutionAction; acknowledge: boolean };

export function usePrototypeExecution(productId: string) {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const enabled = Boolean(verificationEnvironment && productId);
  const experiments = useQuery({ queryKey: ["prototype-experiments", productId], queryFn: () => api.getPrototypeExperiments(productId), enabled, retry: false });
  const tasks = useQuery({ queryKey: ["prototype-tasks", productId], queryFn: () => api.getPrototypeTasks(productId), enabled, retry: false });
  const taskId = params.get("prototype") === "new" ? "" : params.get("prototype") ?? tasks.data?.items[0]?.id ?? "";
  const detail = useQuery({ queryKey: ["prototype-task", taskId], queryFn: () => api.getPrototypeTask(taskId), enabled: enabled && Boolean(taskId), retry: false });
  const status = useQuery({ queryKey: ["prototype-status", taskId], queryFn: () => api.getPrototypeStatus(taskId), enabled: enabled && Boolean(taskId), retry: false,
    refetchInterval: (query) => prototypeNeedsPolling(query.state.data ?? detail.data) ? 1000 : false });
  const task = detail.data ? mergePrototypeStatus(detail.data, status.data) : undefined;
  useEffect(() => {
    if (!status.data || !detail.data) return;
    const changed = status.data.current_plan_id !== detail.data.current_plan_id || status.data.operations.some((operation) =>
      !["queued", "running"].includes(operation.state) && detail.data.operations.find((old) => old.operation_id === operation.operation_id)?.state !== operation.state);
    if (changed) void queryClient.invalidateQueries({ queryKey: ["prototype-task", taskId] });
  }, [status.data, detail.data, queryClient, taskId]);
  const selectTask = (identifier: string) => setParams((old) => { const next = new URLSearchParams(old); next.set("prototype", identifier || "new"); return next; });
  const refresh = async () => {
    await Promise.all([queryClient.invalidateQueries({ queryKey: ["prototype-tasks", productId] }),
      queryClient.invalidateQueries({ queryKey: ["prototype-task", taskId] }), queryClient.invalidateQueries({ queryKey: ["prototype-status", taskId] }),
      queryClient.invalidateQueries({ queryKey: ["prototype-experiments", productId] })]);
  };
  const mutation = useMutation({ mutationFn: async (command: Command) => {
    if (command.action === "create") {
      const result = await api.createPrototype(productId, command.experimentId, command.draft);
      queryClient.setQueryData(["prototype-task", result.id], result);
      selectTask(result.id);
      return;
    }
    if (!task) throw new Error("prototype_task_not_loaded");
    if (command.action === "save") return api.savePrototypeDraft(task, command.draft);
    if (command.action === "text") return api.preparePrototypeText(task);
    if (command.action === "cancel") return api.cancelPrototype(task);
    if (command.action === "operation") return api.executeAction(command.operation, command.operationAction, command.acknowledge);
    const plan = selectedPlan(task);
    if (!plan) throw new Error("prototype_preview_required");
    return command.action === "confirm" ? api.confirmPrototype(task, plan) : api.generatePrototype(task, plan);
  }, onSuccess: refresh, onError: () => { void refresh(); }, retry: false });
  return { enabled, experiments, tasks, task, taskId, selectTask, refresh, mutation,
    loading: experiments.isLoading || tasks.isLoading || detail.isLoading,
    error: mutation.error ?? experiments.error ?? tasks.error ?? detail.error ?? status.error };
}
