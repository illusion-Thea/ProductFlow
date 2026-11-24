import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { ExecutionAction, ExecutionSummary } from "./types";

export function useExecutionActions(rootId: string, kind: "product" | "session") {
  const cache = useQueryClient();
  return useMutation({ mutationFn: (input: { operation: ExecutionSummary; action: ExecutionAction; acknowledge: boolean }) =>
    api.executeAction(input.operation, input.action, input.acknowledge),
  onSettled: async () => {
    const names = kind === "product" ? ["product-workflow", "product-workflow-status", "product", "product-history"] : ["image-session", "image-session-status"];
    await Promise.all(names.map((name) => cache.invalidateQueries({ queryKey: [name, rootId] })));
  }, retry: false });
}
