# Frontend Type Safety

> TypeScript and API typing conventions used by ProductFlow.

---

## Overview

The frontend uses strict TypeScript. `web/tsconfig.app.json` sets `strict: true`, `allowJs: false`,
`isolatedModules: true`, `moduleResolution: "Bundler"`, and `jsx: "react-jsx"`. The build command in `web/package.json`
runs TypeScript checks before Vite build:

```bash
pnpm --dir web build
# tsc --noEmit -p tsconfig.app.json && tsc --noEmit -p tsconfig.node.json && vite build
```

Runtime API typing is centralized in:

- `web/src/lib/types.ts`
- `web/src/lib/api.ts`

---

## API DTO Types

`web/src/lib/types.ts` mirrors backend Pydantic response/request shapes. It intentionally preserves backend field names,
including `snake_case`:

- `ProductSummary.workflow_state`
- `CopySet.creative_brief_id`
- `ImageSessionGenerationTask.failure_reason`
- `ImageSessionRound.provider_response_id`
- `SessionState.access_required`
- `RuntimeConfig.admin_access_required`
- `ConfigUpdateRequest.reset_keys`

Do not silently convert these to camelCase in frontend types unless the API layer also performs explicit mapping.

String union types mirror backend enums:

```ts
export type ProductWorkflowState = "draft" | "copy_ready" | "poster_ready" | "failed";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
```

If backend enum values in `backend/src/productflow_backend/domain/enums.py` change, update these unions and all UI maps
such as `StatusPill.tsx::CONFIG`.

Workflow run DTOs mirror backend run action metadata. When the backend adds `is_retryable`, `is_cancelable`, or queue
fields (`queue_active_count`, `queue_running_count`, `queue_queued_count`, `queue_max_concurrent_tasks`,
`queued_ahead_count`, `queue_position`), update both `WorkflowRun` and `WorkflowRunStatusSummary` because full detail and
lightweight status polling merge through the same cache.

---

## API Client Typing

`web/src/lib/api.ts` exposes typed methods on the `api` object. The internal `request<T>(...)` returns a `Promise<T>` and
throws typed `ApiError` on non-2xx responses.

Examples:

```ts
getProduct(productId: string): Promise<ProductDetail> {
  return request(`/api/products/${productId}`);
}

updateConfig(payload: ConfigUpdateRequest): Promise<ConfigResponse> {
  return request("/api/settings", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}
```

Form uploads build `FormData` in API methods such as `createProduct(...)`, `addReferenceImages(...)`, and
`addImageSessionReferenceImages(...)`. The fetch wrapper omits `Content-Type` for `FormData` so the browser can set the
multipart boundary.

### Scenario: Create-product API input typing

#### 1. Scope / Trigger

- Trigger: changes to the product creation form, `api.createProduct(...)`, or backend `POST /api/products` multipart
  fields.
- Product creation is a cross-layer form-upload contract. Keep the shape centralized in `web/src/lib/types.ts` and have
  `web/src/lib/api.ts` translate it into `FormData`.

#### 2. Signatures

- Shared frontend DTO: `CreateProductInput`.
- API method: `api.createProduct(input: CreateProductInput): Promise<ProductDetail>`.
- Multipart fields currently mirrored from the backend:
  - `name: string`
  - `file: File` -> form field `image`
  - `referenceFiles?: File[]` -> repeated form field `reference_images`
  - `category?: string`
  - `price?: string`
  - `source_note?: string`
  - `canvas_template_key?: string`

#### 3. Contracts

- Keep backend field names in the DTO for optional form values such as `source_note` and `canvas_template_key`.
- `canvas_template_key` is the backend-recognized key. UI labels should be merchant-facing output plans, but the submitted
  value remains the key.
- Blank/default product-creation plans may submit an empty string or omit `canvas_template_key`; the backend owns default
  alias handling.
- Product creation large previews for backend-recognized built-in plans must mirror the backend `full_canvas` template
  layout for the same key. When changing preview node titles, edges, or coordinates, update the backend template and
  backend regression tests in the same change.
- The page component must not duplicate the full mutation object type inline when a shared DTO exists.
- The API method owns `FormData` construction. Page components should call `api.createProduct(...)` with typed values, not
  construct raw multipart bodies themselves.

#### 4. Validation & Error Matrix

- Missing `file` is handled by the page before calling the API and should produce the existing `请先上传商品图` message.
- Invalid/unknown `canvas_template_key` is backend validation and surfaces through `ApiError.detail`.
- Upload MIME/size errors are backend upload-validation errors and surface through the same `ApiError.detail` path.

#### 5. Good/Base/Bad Cases

- Good: `ProductCreatePage` stores a selected plan key in component state, displays merchant-facing labels, and passes
  `canvas_template_key` into `api.createProduct`.
- Base: a blank/basic option can use `""` while still sharing the typed DTO.
- Bad: `ProductCreatePage` creates `FormData` directly and bypasses the typed API helper.
- Bad: frontend renames `canvas_template_key` to `canvasTemplateKey` without an explicit mapping layer.

#### 6. Tests Required

- `pnpm --dir web build` must pass after any create-product DTO change.
- Add focused frontend tests for pure helper logic if plan selection or payload routing becomes non-trivial.
- Backend API tests remain the source of truth for multipart validation, template-key error status, and persisted template
  coordinates mirrored by the creation page preview.

#### 7. Wrong vs Correct

Wrong:

```ts
return api.createProduct({
  name,
  file,
  canvasTemplateKey,
});
```

Correct:

```ts
return api.createProduct({
  name,
  file,
  canvas_template_key: selectedPlanKey,
});
```

---

## Local Types

### Scenario: Settings migration API typing

> **Garment target:** Full settings, provider profiles/bindings, and import/export require current system-management
> role plus independent settings unlock. Read/preview/export responses must not return stored raw keys. The signatures
> below describe existing DTOs; change input/output DTOs, backend schemas, consumers, and tests together during implementation.

#### 1. Scope / Trigger
- Trigger: changes to settings export/import API methods, SettingsPage import/export UI, or backend
  `SettingsExportDocument` / `SettingsImportPreviewResponse` / `SettingsImportCommitResponse` schemas.
- Settings migration is a cross-layer DTO contract. Keep TypeScript types aligned with backend Pydantic schemas and keep
  backend `snake_case` field names.

#### 2. Signatures
- API methods:
  - `api.exportSettings(): Promise<SettingsExportPayload>`
  - `api.previewSettingsImport(payload: SettingsExportPayload): Promise<SettingsImportPreviewResponse>`
  - `api.importSettings(payload: SettingsExportPayload): Promise<SettingsImportCommitResponse>`
- Frontend DTOs live in `web/src/lib/types.ts` and mirror backend field names:
  - `SettingsExportPayload`
  - `SettingsExportMetadata`
  - `SettingsExportProviderProfile`
  - `SettingsExportProviderBinding`
  - `SettingsImportPreviewResponse`
  - `SettingsImportCommitResponse`

#### 3. Contracts
- `runtime_config` is a map of config key to JSON scalar/list values from the backend export.
- The legacy `provider_profiles` export contains `api_key`; the extension must exclude stored raw keys from reads,
  previews, and exports, even for management. Authorized credential input and existing-secret output are separate
  contracts. A sanitized export must not make the UI claim that a clean target already has usable credentials.
- `provider_bindings` references imported provider profile ids for non-mock bindings.
- Import preview response fields are flat DTO fields such as `runtime_config_count`,
  `provider_profile_count`, `provider_binding_count`, `includes_api_keys`, and
  `provider_profiles_with_api_key_count`; do not invent a nested `metadata.summary` layer unless the backend schema
  changes in the same commit.
- Import commit returns refreshed settings/provider config data or enough data for SettingsPage to invalidate and refetch
  `['config']`, `['provider-config']`, `['runtime-config']`, and `['session']`.

#### 4. Validation & Error Matrix
- Invalid JSON file -> SettingsPage shows a local invalid-file error before calling the API.
- API 400 from preview/commit -> show `ApiError.detail`.
- User cancels export/import confirmation -> do not call the API.
- Successful import -> invalidate settings/runtime/session queries so UI reflects the imported values.
- Missing current management role or independent settings unlock -> deny full settings/provider/import-export access;
  ordinary business users retain separate sanitized runtime capability/specification reads.

#### 5. Good/Base/Bad Cases
- Good: export downloads the sanitized typed payload; import previews counts and explains missing target credentials.
- Good: a supplied import containing credentials reports `includes_api_keys=true` and asks for confirmation without
  echoing keys in its preview or subsequent export.
- Base: import file contains `mock` provider bindings and no provider API keys.
- Bad: frontend reads `preview.metadata.summary` when backend returns flat preview fields.
- Bad: converting DTO fields to camelCase in `types.ts` without an explicit API mapping layer.

#### 6. Tests Required
- SettingsPage tests for export confirmation and generated JSON download path.
- SettingsPage tests for import preview summary, API-key warning, commit confirmation, and query invalidation.
- Extension tests for management plus unlock, ordinary roles with a token, role revocation, sanitized output payloads,
  and target credential readiness; update legacy raw-key export expectations with the implementation.
- `pnpm --dir web build` after any settings migration DTO change.

#### 7. Wrong vs Correct

Wrong:

```ts
const keyCount = preview.metadata.summary.providerProfilesWithApiKeyCount;
```

Correct:

```ts
const keyCount = preview.provider_profiles_with_api_key_count;
```

Keep frontend reads aligned with the backend response shape.

---

Use local `type` aliases for page-only structures:

- `EditableCopy` in `ProductDetailPage.tsx`.
- `DraftValue` in `SettingsPage.tsx`.

Use `interface` for component props and DTO object shapes:

- `TopNavProps` in `TopNav.tsx`.
- `ConfigFieldProps` in `SettingsPage.tsx`.
- API DTOs in `web/src/lib/types.ts`.

Static option arrays can use `as const`, as in `ImageChatPage.tsx::DEFAULT_SIZE_OPTIONS`.

---

## Runtime Validation Reality

The frontend currently relies on backend validation for API payloads and on TypeScript for compile-time checks. There is no
Zod/Yup/io-ts runtime validation layer in `web/src/`.

Existing frontend-side validation is lightweight and UI-oriented:

- Required form fields and file accept attributes in `ProductCreatePage.tsx`.
- Config input types/min/max from backend-provided `ConfigItem` metadata in `SettingsPage.tsx`.
- Allowed image size options derived from `/api/settings` in `ImageChatPage.tsx`.

Do not add a validation library unless a feature truly needs client-side runtime parsing beyond backend errors.

---

## Handling Unknown Data

Use `unknown`, not `any`, for flexible payloads. `CreativeBriefSummary.payload` in `web/src/lib/types.ts` allows known
optional fields and `[key: string]: unknown` for provider-specific additions.

When narrowing errors, follow current patterns:

```ts
if (mutationError instanceof ApiError) {
  setError(mutationError.detail);
  return;
}
setError(mutationError instanceof Error ? mutationError.message : "创建商品失败");
```

---

## Avoid

- `any` in API types, component props, or mutation payloads.
- Duplicating DTO interfaces inside pages instead of importing from `web/src/lib/types.ts`.
- Renaming API fields to camelCase only on the frontend.
- Type assertions that hide missing null checks; prefer `enabled: Boolean(id)` for queries and explicit null rendering.
- Adding new backend response fields without updating `web/src/lib/types.ts` and the relevant UI.

## M1-01 execution DTOs and requests

### 1. Scope
`ExecutionSummary`, `PrototypeTask`, `PrototypeStatus`, `PrototypeDraft`, and `ContentLanguage` live in `lib/types.ts`, aligned with backend presentation schemas. Legacy workflow/session responses remain their original envelopes with additive execution summaries.
### 2. Signatures
`api.executeAction(operation, action, acknowledge)` uses the operation version except legacy retry, which requires the root version. `controlledWrite<T>(path, scope, payload, method)` owns identity validation and idempotency.
### 3. Contracts
Keep null cost/wait/recovery values explicit. Unknown is a technical status, separate from business state. API requests preserve cookie credentials, JSON Content-Type and caller Idempotency-Key; FormData must retain browser boundary handling. Environment identity must match before every controlled write.
### 4. Error matrix
`executionErrorMessage` localizes known conflict, quota, authorization, uncertainty, input and isolation errors. Never render raw FastAPI detail arrays or classify unknown as zero-cost failure.
### 5. Cases
Good: spreading RequestInit before merged headers retains JSON and idempotency headers. Base: same-key lookup returns the committed DTO after reload. Bad: spreading init last silently overwrites Content-Type and causes valid JSON to fail with 422.
### 6. Required evidence
`executionApi.test.ts` asserts exact request headers and one POST after a lost response; strict build verifies new enum consumers. API integration asserts missing key/version 422 instead of globally adding them in test clients.
### 7. Wrong versus correct
Wrong: `{ headers: defaults, ...init }`. Correct: `{ ...init, credentials: 'include', headers: { ...jsonHeaders, ...init?.headers } }`.
