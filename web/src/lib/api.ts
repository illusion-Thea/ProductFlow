import { normalizeApiErrorDetail } from "./apiErrors";
import { clearExecutionIntent, executionIntent, executionPayloadHash, readExecutionIntent } from "./executionIntent";
import type { ExecutionAction, ExecutionSummary, PrototypeDraft, PrototypeExperiment, PrototypePlan, PrototypeStatus, PrototypeTask } from "./types";
import type {
  ApplyWorkflowTemplateGroupInput,
  CanvasTemplateSummary,
  CanvasTemplateListResponse,
  ConfigResponse,
  ConfigUpdateRequest,
  CopySet,
  CopySetUpdateRequest,
  DuplicateWorkflowNodeGroupInput,
  GalleryEntry,
  GalleryEntryListResponse,
  GenerationQueueOverview,
  CreateUserTemplateGroupInput,
  CreateProductInput,
  ImageSessionDetail,
  ImageSessionListResponse,
  ImageSessionStatus,
  ImageToolOptions,
  ProductDetail,
  ProductHistory,
  ProviderBinding,
  ProviderBindingUpdateRequest,
  ProviderConfigResponse,
  ProviderProfile,
  ProviderProfileCreateRequest,
  ProviderProfileUpdateRequest,
  ProductWorkflow,
  ProductWorkflowStatus,
  ProductWritebackResponse,
  ProductListResponse,
  RuntimeConfig,
  SettingsLockState,
  SettingsExportPayload,
  SettingsImportCommitResponse,
  SettingsImportPreviewResponse,
  SessionState,
  UpdateUserTemplateGroupInput,
} from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";
export const verificationEnvironment = import.meta.env.VITE_VERIFICATION_ENVIRONMENT_ID as string | undefined;

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: unknown) {
    const message = normalizeApiErrorDetail(detail);
    super(message);
    this.status = status;
    this.detail = message;
  }
}

export class PendingSubmissionError extends ApiError {
  constructor(readonly originalPayload: unknown, readonly resume: () => Promise<unknown>) {
    super(409, "submission_unresolved：上次提交尚未确认，请先恢复原提交。当前修改仍保留。");
  }
}

function toApiUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return `${API_BASE_URL}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(toApiUrl(path), {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let detail = "请求失败";
    try {
      const payload = (await response.json()) as { detail?: unknown };
      detail = normalizeApiErrorDetail(payload.detail, detail);
    } catch {
      detail = response.statusText || detail;
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

async function controlledWrite<T>(path: string, scope: string, payload: unknown, method = "POST", recoveryKey?: string): Promise<T> {
  if (!verificationEnvironment) return request(path, { method, body: JSON.stringify(payload) });
  const identity = await request<{ environment_id: string; stub_only: boolean }>("/api/verification-context");
  if (identity.environment_id !== verificationEnvironment || !identity.stub_only) throw new ApiError(409, "verification_environment_mismatch");
  const storageKey = `productflow.execution:${verificationEnvironment}:${scope}`;
  const [root_kind, root_id, action] = scope.split(":");
  const query = new URLSearchParams({ root_kind, root_id, action });
  const saved = readExecutionIntent(sessionStorage, storageKey);
  if (recoveryKey && saved?.key !== recoveryKey) throw new ApiError(409, "submission_already_resolved：原提交已处理，请刷新查看结果。");
  if (saved) {
    // After reload, the server version may already have advanced. Reconcile the
    // old intent before comparing new UI state; never create a replacement key.
    const status = await request<{ status: string; response?: T }>(`/api/execution-requests/${saved.key}?${query}`);
    if (status.status === "committed" && status.response !== undefined) {
      clearExecutionIntent(sessionStorage, storageKey, saved.key);
      if (saved.payloadHash !== await executionPayloadHash(payload)) {
        throw new ApiError(409, "previous_submission_recovered：上次提交已恢复；当前修改仍保留，请确认后再次提交。");
      }
      return status.response;
    }
    if (saved.payloadHash !== await executionPayloadHash(payload)) {
      if (saved.payload === undefined) throw new ApiError(409, "submission_unresolved：原提交内容不可用，请先恢复原内容再重试。");
      throw new PendingSubmissionError(saved.payload, () => controlledWrite(path, scope, saved.payload, method, saved.key));
    }
  }
  // A concurrent reconciliation may clear the store while lookup is in flight.
  // Keep the captured key; recovery must never mint a replacement submission.
  const intent = saved ?? await executionIntent(sessionStorage, storageKey, payload);
  try {
    const result = await request<T>(path, { method, body: JSON.stringify(payload), headers: { "Idempotency-Key": intent.key } });
    clearExecutionIntent(sessionStorage, storageKey, intent.key);
    return result;
  } catch (error) {
    if (error instanceof ApiError && error.status < 500) clearExecutionIntent(sessionStorage, storageKey, intent.key);
    else {
      try {
        const status = await request<{ status: string; response?: T }>(`/api/execution-requests/${intent.key}?${query}`);
        if (status.status === "committed" && status.response !== undefined) {
          clearExecutionIntent(sessionStorage, storageKey, intent.key);
          return status.response;
        }
      } catch { /* Keep the original key; user retries the same intent after reconnection. */ }
    }
    throw error;
  }
}

export const api = {
  toApiUrl,
  getPrototypeExperiments(productId: string): Promise<{ items: PrototypeExperiment[] }> {
    return request(`/api/prototype-experiments?product_id=${encodeURIComponent(productId)}`);
  },
  getPrototypeTasks(productId: string): Promise<{ items: PrototypeStatus[]; next_cursor: string | null }> {
    return request(`/api/products/${productId}/prototype-tasks`);
  },
  getPrototypeTask(taskId: string): Promise<PrototypeTask> { return request(`/api/prototype-tasks/${taskId}`); },
  getPrototypeStatus(taskId: string): Promise<PrototypeStatus> { return request(`/api/prototype-tasks/${taskId}/status`); },
  createPrototype(productId: string, experimentId: string, draft: PrototypeDraft): Promise<PrototypeTask> {
    return controlledWrite(`/api/products/${productId}/prototype-tasks`, `product:${productId}:create-prototype`, { ...draft, experiment_run_id: experimentId });
  },
  savePrototypeDraft(task: PrototypeTask, draft: PrototypeDraft): Promise<PrototypeTask> {
    return controlledWrite(`/api/prototype-tasks/${task.id}/draft`, `prototype:${task.id}:draft`, { ...draft, expected_row_version: task.row_version }, "PATCH");
  },
  preparePrototypeText(task: PrototypeTask): Promise<ExecutionSummary> {
    return controlledWrite(`/api/prototype-tasks/${task.id}/text-preparations`, `prototype:${task.id}:text-preparations`, { expected_row_version: task.row_version, content_language: task.draft_content_language });
  },
  confirmPrototype(task: PrototypeTask, plan: PrototypePlan): Promise<PrototypeTask> {
    return controlledWrite(`/api/prototype-tasks/${task.id}/confirm`, `prototype:${task.id}:confirm`, { expected_row_version: task.row_version, plan_id: plan.id, plan_sha256: plan.input_sha256, preview_revision: plan.preview_revision, content_language: plan.content_language });
  },
  generatePrototype(task: PrototypeTask, plan: PrototypePlan): Promise<{ operation_ids: string[] }> {
    return controlledWrite(`/api/prototype-tasks/${task.id}/generate`, `prototype:${task.id}:generate`, { expected_row_version: task.row_version, accepted_plan_id: plan.id });
  },
  cancelPrototype(task: PrototypeTask): Promise<PrototypeTask> {
    return controlledWrite(`/api/prototype-tasks/${task.id}/cancel`, `prototype:${task.id}:cancel`, { expected_row_version: task.row_version });
  },
  executeAction(operation: ExecutionSummary, action: ExecutionAction, acknowledge: boolean): Promise<unknown> {
    const retryLegacy = action === "retry" && operation.source_kind !== "prototype";
    const scope = retryLegacy ? `${operation.source_kind === "image_session" ? "image_task" : "workflow_run"}:${operation.retry_url.split("/").at(-2)}:retry` : `operation:${operation.operation_id}:${action}`;
    return controlledWrite(action === "retry" ? operation.retry_url : `${operation.action_url}/${action}`, scope, {
      expected_row_version: retryLegacy ? operation.root_row_version : operation.row_version,
      acknowledge_possible_duplicate_charge: acknowledge,
    });
  },
  getSessionState(): Promise<SessionState> {
    return request<SessionState>("/api/auth/session");
  },
  createSession(adminKey: string): Promise<{ ok: boolean }> {
    return request("/api/auth/session", {
      method: "POST",
      body: JSON.stringify({ admin_key: adminKey }),
    });
  },
  destroySession(): Promise<{ ok: boolean }> {
    return request("/api/auth/session", { method: "DELETE" });
  },
  listProducts(input?: { page?: number; page_size?: number }): Promise<ProductListResponse> {
    const page = input?.page ?? 1;
    const pageSize = input?.page_size ?? 20;
    return request(`/api/products?page=${encodeURIComponent(page)}&page_size=${encodeURIComponent(pageSize)}`);
  },
  getProduct(productId: string): Promise<ProductDetail> {
    return request(`/api/products/${productId}`);
  },
  deleteProduct(productId: string): Promise<void> {
    return request(`/api/products/${productId}`, { method: "DELETE" });
  },
  getProductHistory(productId: string): Promise<ProductHistory> {
    return request(`/api/products/${productId}/history`);
  },
  getConfig(): Promise<ConfigResponse> {
    return request("/api/settings");
  },
  getProviderConfig(): Promise<ProviderConfigResponse> {
    return request("/api/settings/provider-config");
  },
  createProviderProfile(payload: ProviderProfileCreateRequest): Promise<ProviderProfile> {
    return request("/api/settings/provider-profiles", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateProviderProfile(profileId: string, payload: ProviderProfileUpdateRequest): Promise<ProviderProfile> {
    return request(`/api/settings/provider-profiles/${profileId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  archiveProviderProfile(profileId: string): Promise<ProviderProfile> {
    return request(`/api/settings/provider-profiles/${profileId}`, { method: "DELETE" });
  },
  updateProviderBinding(purpose: "text" | "image", payload: ProviderBindingUpdateRequest): Promise<ProviderBinding> {
    return request(`/api/settings/provider-bindings/${purpose}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  getSettingsLockState(): Promise<SettingsLockState> {
    return request("/api/settings/lock-state");
  },
  getRuntimeConfig(): Promise<RuntimeConfig> {
    return request("/api/settings/runtime");
  },
  getGenerationQueueOverview(): Promise<GenerationQueueOverview> {
    return request("/api/generation-queue");
  },
  unlockSettings(token: string): Promise<SettingsLockState> {
    return request("/api/settings/unlock", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
  },
  updateConfig(payload: ConfigUpdateRequest): Promise<ConfigResponse> {
    return request("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  exportSettings(): Promise<SettingsExportPayload> {
    return request("/api/settings/export");
  },
  previewSettingsImport(payload: SettingsExportPayload): Promise<SettingsImportPreviewResponse> {
    return request("/api/settings/import/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  importSettings(payload: SettingsExportPayload): Promise<SettingsImportCommitResponse> {
    return request("/api/settings/import", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  async createProduct(input: CreateProductInput): Promise<ProductDetail> {
    const formData = new FormData();
    formData.set("name", input.name);
    formData.set("image", input.file);
    input.referenceFiles?.forEach((referenceFile) => {
      formData.append("reference_images", referenceFile);
    });
    if (input.category) {
      formData.set("category", input.category);
    }
    if (input.price) {
      formData.set("price", input.price);
    }
    if (input.source_note) {
      formData.set("source_note", input.source_note);
    }
    if (input.canvas_template_key !== undefined) {
      formData.set("canvas_template_key", input.canvas_template_key);
    }
    if (input.template_language !== undefined) {
      formData.set("template_language", input.template_language);
    }
    return request("/api/products", {
      method: "POST",
      body: formData,
    });
  },
  async addReferenceImages(productId: string, files: File[]): Promise<ProductDetail> {
    const formData = new FormData();
    files.forEach((file) => {
      formData.append("reference_images", file);
    });
    return request(`/api/products/${productId}/reference-images`, {
      method: "POST",
      body: formData,
    });
  },
  deleteSourceAsset(assetId: string): Promise<ProductDetail> {
    return request(`/api/source-assets/${assetId}`, { method: "DELETE" });
  },
  updateCopySet(copySetId: string, payload: CopySetUpdateRequest): Promise<CopySet> {
    return request(`/api/copy-sets/${copySetId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  confirmCopySet(copySetId: string): Promise<CopySet> {
    return request(`/api/copy-sets/${copySetId}/confirm`, { method: "POST" });
  },
  listImageSessions(): Promise<ImageSessionListResponse> {
    return request("/api/image-sessions");
  },
  createImageSession(input: { title?: string }): Promise<ImageSessionDetail> {
    return request("/api/image-sessions", {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  getImageSession(sessionId: string): Promise<ImageSessionDetail> {
    return request(`/api/image-sessions/${sessionId}`);
  },
  getImageSessionStatus(sessionId: string): Promise<ImageSessionStatus> {
    return request(`/api/image-sessions/${sessionId}/status`);
  },
  updateImageSession(sessionId: string, input: { title: string }): Promise<ImageSessionDetail> {
    return request(`/api/image-sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  },
  deleteImageSession(sessionId: string): Promise<void> {
    return request(`/api/image-sessions/${sessionId}`, { method: "DELETE" });
  },
  async addImageSessionReferenceImages(sessionId: string, files: File[]): Promise<ImageSessionDetail> {
    const formData = new FormData();
    files.forEach((file) => {
      formData.append("reference_images", file);
    });
    return request(`/api/image-sessions/${sessionId}/reference-images`, {
      method: "POST",
      body: formData,
    });
  },
  deleteImageSessionReferenceImage(sessionId: string, assetId: string): Promise<ImageSessionDetail> {
    return request(`/api/image-sessions/${sessionId}/reference-images/${assetId}`, { method: "DELETE" });
  },
  generateImageSessionRound(
    sessionId: string,
    input: {
      prompt: string;
      size: string;
      base_asset_id?: string | null;
      selected_reference_asset_ids?: string[];
      generation_count?: number;
      tool_options?: ImageToolOptions | null;
    },
  ): Promise<ImageSessionDetail> {
    return controlledWrite(`/api/image-sessions/${sessionId}/generate`, `image_session:${sessionId}:generate`, input);
  },
  retryImageSessionGenerationTask(sessionId: string, taskId: string, expectedVersion?: number): Promise<ImageSessionDetail> {
    return controlledWrite(`/api/image-sessions/${sessionId}/generation-tasks/${taskId}/retry`, `image_task:${taskId}:retry`, { expected_row_version: expectedVersion });
  },
  cancelImageSessionGenerationTask(sessionId: string, taskId: string, expectedVersion?: number): Promise<ImageSessionDetail> {
    return controlledWrite(`/api/image-sessions/${sessionId}/generation-tasks/${taskId}/cancel`, `image_task:${taskId}:cancel`, { expected_row_version: expectedVersion });
  },
  attachImageSessionAssetToProduct(
    sessionId: string,
    assetId: string,
    input: { product_id: string; target: "reference" | "main_source" },
  ): Promise<ProductWritebackResponse> {
    return request(`/api/image-sessions/${sessionId}/assets/${assetId}/attach-to-product`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  listGalleryEntries(): Promise<GalleryEntryListResponse> {
    return request("/api/gallery");
  },
  saveGalleryEntry(imageSessionAssetId: string): Promise<GalleryEntry> {
    return request("/api/gallery", {
      method: "POST",
      body: JSON.stringify({ image_session_asset_id: imageSessionAssetId }),
    });
  },
  getProductWorkflow(productId: string): Promise<ProductWorkflow> {
    return request(`/api/products/${productId}/workflow`);
  },
  getProductWorkflowStatus(productId: string): Promise<ProductWorkflowStatus> {
    return request(`/api/products/${productId}/workflow/status`);
  },
  listCanvasTemplates(): Promise<CanvasTemplateListResponse> {
    return request("/api/workflow/canvas-templates");
  },
  applyWorkflowTemplateGroup(productId: string, input: ApplyWorkflowTemplateGroupInput): Promise<ProductWorkflow> {
    return request(`/api/products/${productId}/workflow/template-groups`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  duplicateWorkflowNodeGroup(
    productId: string,
    input: DuplicateWorkflowNodeGroupInput,
  ): Promise<ProductWorkflow> {
    return request(`/api/products/${productId}/workflow/node-groups/duplicate`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  createUserTemplateGroup(productId: string, input: CreateUserTemplateGroupInput): Promise<CanvasTemplateSummary> {
    return request(`/api/products/${productId}/workflow/user-template-groups`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  updateUserTemplateGroup(templateId: string, input: UpdateUserTemplateGroupInput): Promise<CanvasTemplateSummary> {
    return request(`/api/workflow/user-template-groups/${templateId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  },
  archiveUserTemplateGroup(templateId: string): Promise<void> {
    return request(`/api/workflow/user-template-groups/${templateId}`, {
      method: "DELETE",
    });
  },
  createWorkflowNode(
    productId: string,
    input: {
      node_type: ProductWorkflow["nodes"][number]["node_type"];
      title: string;
      position_x: number;
      position_y: number;
      config_json: Record<string, unknown>;
    },
  ): Promise<ProductWorkflow> {
    return request(`/api/products/${productId}/workflow/nodes`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  updateWorkflowNode(
    nodeId: string,
    input: {
      title?: string;
      position_x?: number;
      position_y?: number;
      config_json?: Record<string, unknown>;
    },
  ): Promise<ProductWorkflow> {
    return request(`/api/workflow-nodes/${nodeId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  },
  updateWorkflowNodeCopy(nodeId: string, payload: CopySetUpdateRequest): Promise<ProductWorkflow> {
    return request(`/api/workflow-nodes/${nodeId}/copy`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  async uploadWorkflowNodeImage(
    nodeId: string,
    input: { file: File; role?: string; label?: string },
  ): Promise<ProductWorkflow> {
    const formData = new FormData();
    formData.set("image", input.file);
    if (input.role) {
      formData.set("role", input.role);
    }
    if (input.label) {
      formData.set("label", input.label);
    }
    return request(`/api/workflow-nodes/${nodeId}/image`, {
      method: "POST",
      body: formData,
    });
  },
  bindWorkflowNodeImage(
    nodeId: string,
    input: { source_asset_id?: string; poster_variant_id?: string },
  ): Promise<ProductWorkflow> {
    return request(`/api/workflow-nodes/${nodeId}/image-source`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  createWorkflowEdge(
    productId: string,
    input: { source_node_id: string; target_node_id: string; source_handle?: string; target_handle?: string },
  ): Promise<ProductWorkflow> {
    return request(`/api/products/${productId}/workflow/edges`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  deleteWorkflowEdge(edgeId: string): Promise<ProductWorkflow> {
    return request(`/api/workflow-edges/${edgeId}`, { method: "DELETE" });
  },
  deleteWorkflowNode(nodeId: string): Promise<ProductWorkflow> {
    return request(`/api/workflow-nodes/${nodeId}`, { method: "DELETE" });
  },
  runProductWorkflow(productId: string, input?: { start_node_id?: string }): Promise<ProductWorkflow> {
    return controlledWrite(`/api/products/${productId}/workflow/run`, `product:${productId}:run`, input ?? {});
  },
  cancelProductWorkflowRun(productId: string, runId: string, expectedVersion?: number): Promise<ProductWorkflow> {
    return controlledWrite(`/api/products/${productId}/workflow/runs/${runId}/cancel`, `workflow_run:${runId}:cancel`, { expected_row_version: expectedVersion });
  },
  retryProductWorkflowRun(productId: string, runId: string, expectedVersion?: number): Promise<ProductWorkflow> {
    return controlledWrite(`/api/products/${productId}/workflow/runs/${runId}/retry`, `workflow_run:${runId}:retry`, { expected_row_version: expectedVersion });
  },
};
