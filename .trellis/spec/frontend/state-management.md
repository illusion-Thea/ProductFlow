# Frontend State Management

> Actual state management choices in ProductFlow.

---

## Overview

ProductFlow uses four state categories:

1. Server state: TanStack Query in pages and `AppRoutes()`.
2. Local UI/form state: React `useState`, `useMemo`, and `useEffect` inside page components.
3. URL state: React Router params and navigation.
4. Durable browser-local UI preferences: theme mode in `PreferencesProvider` plus narrowly scoped
   page-owned workbench preferences.

The current workbench UI is fixed to Chinese (2026-11-24 user confirmation). Do not read legacy
`productflow.locale` to select a UI language. `PreferencesProvider` writes `zh-CN` and sets the document language,
while preserving the independent theme preference. Content-language drafts and accepted previews remain server records.

There is no Redux, Zustand, Jotai, custom event bus, or durable browser-local onboarding state.

---

## Server State

> **Garment extension target:** The shared-login fields below describe the existing app. Named identity, one active team,
> current membership, and server-authorized resource access supersede shared-gate bypass for the extension. System
> management does not grant ordinary editing of others' personal tasks. Audited handoff applies to personal/team tasks
> and independent image sessions: personal editing and scope-change permission follow the current responsible user;
> team shared editing remains unchanged and original creator identity remains historical. Products/free canvases are
> team shared; formal tasks and sessions use their own scope. Revocation blocks new submissions/access, while accepted
> operations continue unless explicitly cancelled by a currently authorized user.

For garment full settings/provider/import-export queries, require the current management role and independent settings
unlock; neither is sufficient alone. Business pages may load necessary sanitized runtime capabilities/specifications.
Do not cache returned raw credentials or treat an old unlock flag as authorization after a role change; settings reads,
previews, and exports must omit stored raw keys. These are target checks to implement alongside named identity.

Server state is loaded through `web/src/lib/api.ts` and cached by TanStack Query. The `QueryClient` is created once in
`web/src/App.tsx` with `refetchOnWindowFocus: false`.

Current query key patterns:

- Session: `['session']` in `App.tsx`. `GET /api/auth/session` returns both `authenticated` and `access_required`; when
  login is disabled server-side, `authenticated` is true even without a login cookie.
- Product list: `['products']` in `ProductListPage.tsx` and `ImageChatPage.tsx`.
- Product detail/history: `['product', productId]` and `['product-history', productId]` in `ProductDetailPage.tsx`.
- Product workbench: `['product-workflow', productId]` and `['product-workflow-status', productId]` in
  `ProductDetailPage.tsx`.
- Image sessions: `['image-sessions']` and `['image-session', selectedSessionId]` in `ImageChatPage.tsx`.
- Runtime config: `['runtime-config']` in `ProductDetailPage.tsx`, `ProductListPage.tsx`, and `ImageChatPage.tsx`.
- Full settings config: `['config']` in `SettingsPage.tsx`; successful settings saves/resets must invalidate
  `['runtime-config']` when they can affect public runtime behavior, and `['session']` because settings can toggle
  `admin_access_required`.
- Settings lock state: `['settings-lock-state']` in `SettingsPage.tsx`; fetch full `['config']` only after the secondary
  settings token unlock succeeds.

When writing mutations, update/invalidate every key that can show stale data.

---

## Local UI and Form State

Keep short-lived UI state local to the page that owns the interaction:

- `ProductCreatePage.tsx` stores form fields, selected files, and a local error string.
- `ProductDetailPage.tsx` stores editing mode, editable copy draft, selected canvas/workbench state, and local mutation
  error strings.
- `ImageChatPage.tsx` stores selected session/generated asset, prompt draft, image size, rename mode, target product,
  and transient success/error messages.
- `SettingsPage.tsx` stores config drafts, secret touched flags, reset progress, and save/error messages.
- `SettingsPage.tsx` stores the transient settings unlock token only in local component state for the submit attempt; do
  not persist the token in localStorage, query cache, or API responses.

Local state should not duplicate server records unless the user is editing a draft. For example, `SettingsPage.tsx` creates
`drafts` from fetched config so the user can edit before saving; product details themselves remain in TanStack Query.

For the garment extension, local preview selection, base-image selection, and canvas-node selection do not adopt an
output. Persist formal composite feedback/current selection and first-round assessment on the server, keeping settled
snapshots independent of later feedback. Refresh affected queries after feedback, reuse, restore, or handoff; independent
reuse records do not share feedback/download/retention cache state just because they share bytes. Favorites use the source
version's state and permissions. See [the garment PRD](../../../docs/garment/PRD.md) and
[Technical Design Sections 4-6](../../../docs/garment/TECH_DESIGN.md) for the planned contracts.

Copy language is business state, with Chinese as default and explicit Chinese/Malay/Indonesian/Filipino choices.
Keep the selected-language preview and its confirmation in server version records; selector drafts may be local.
Opening the Chinese workbench or changing market does not translate or replace confirmed copy. Require a generated preview in the chosen language
and confirmation before composition; requests to generate new text are distinct from zero-model local text edits.

Show server recovery status and finite deadlines. Expiry pauses automatic local recovery for an explicit continue/end
decision, without automatically settling first-round failure. A late result after a completed task closed its wait
stays in history and does not optimistically reopen the task; explicit inclusion, late-base composition, and any new
round require server acceptance. On old-release admission closure, keep new edits as drafts until a new round is confirmed.

Layered reuse must return target-owned layer/dependency records, so edit/restore actions use target permissions and
version IDs even when file bytes are shared. Whole-product/task/session deletion archives that root, including products
without formal tasks. Refresh list/archive/detail visibility without deleting linked roots or asset cache records;
their independent permissions, availability, and retention still apply. Keep accepted-work progress/results accessible.
Archived roots permit authorized record management, cancellation, and per-image deletion/restoration, but new content,
generation, retry, or explicit local recovery requires unarchive. Preserve drafts on rejection; unarchive does not submit
them, restore images, reset retention, or revive cancelled tasks. Root archive is separate server state from task status.

Display incomplete first-round progress when cancellation leaves original bases without composites or definite original
failure; do not derive final 7/10 from model success counts. Withdrawing an already-generated undecided revision uses a
server mutation to mark it discarded and retain prior selection/history; do not locally hide it while feedback remains
undecided. Refresh candidate feedback, selection, and completion state together. Unsubmitted drafts may be removed locally.

Keep restoration confirmation and pending editor drafts as local UI state. On confirmation, call the controlled restore
operation from the current edit entry; only successful restoration plus current input availability enables the edit.
On refusal show “不恢复所需图片，当前成品图无法修改”. Retain drafts on failure and expose its cause; do not require recycle-bin
navigation or optimistically mark a dependency live. Metadata feedback/deselection/issue closure does not require file
restoration. Implement and verify these behaviors when the corresponding APIs become available.

## URL and Navigation State

React Router owns route selection and route params:

- `useNavigate()` is used after login/logout, product creation, and page buttons.
- `useParams()` supplies `productId` for `ProductDetailPage.tsx`.
- Auth redirects are centralized in `App.tsx` route elements and `LoginPage.tsx` redirects authenticated users away from
  `/login`.

Do not introduce a global store just to track current page or product ID; use the URL.

---

## Durable UI Preferences

Global app preferences live in `PreferencesProvider`:

- Provider: `PreferencesProvider` in `web/src/lib/preferences.tsx`, mounted once in `App.tsx` inside `BrowserRouter`.
- Legacy locale storage key: `productflow.locale`; the provider writes fixed `zh-CN` and never reads it as a language choice.
- Theme storage key: `productflow.theme`; default preference is `system`.
- Supported theme preferences are `light`, `dark`, and `system`; `system` resolves from `prefers-color-scheme`.
- The provider updates `document.documentElement.lang`, root `class="dark"` when the resolved theme is dark, and root
  `data-theme` / `data-theme-preference` attributes.
- Use `useI18n()` or `usePreferences()` in components that need locale/theme values; do not create page-local duplicate
  locale/theme state.

UI language is fixed and theme is a local preference. Neither is a server record. Do not store them in TanStack Query,
add backend settings for them, or persist them with auth/session state unless a future product requirement changes that boundary.

ProductDetail owns a small set of durable workbench-local browser preferences because they are specific to that page's
canvas ergonomics rather than global app chrome:

- Workflow zoom: `productflow.workflow.zoom`, read by `ProductDetailPage.tsx` and persisted by `WorkflowCanvas`.
- Workflow inspector width: `productflow.workflow.inspectorWidth`, read and written by `ProductDetailPage.tsx`.
- Workflow snap-to-grid toggle: `productflow.workflow.snapToGrid`, read and written by `ProductDetailPage.tsx`.

Keep additional durable local preferences rare and owner-scoped. When a page adds one, document the storage key and owner
here or in the feature-specific spec, and add focused helper tests when parsing/clamping behavior is non-trivial.

Good:

```tsx
const { t } = useI18n();
return <button type="button">{t("nav.settings")}</button>;
```

Bad:

```tsx
const [locale] = useState(window.localStorage.getItem("productflow.locale"));
return <button type="button">{locale === "en-US" ? "Settings" : "配置"}</button>;
```

---

## Derived State

Prefer derived values over additional state:

- `ProductDetailPage.tsx` derives source image URL, reference images, working copy, and poster variants from
  `ProductDetail`.
- `ImageChatPage.tsx` derives built-in image-size picker presets from `web/src/lib/imageSizes.ts`, selected round from
  the selected asset ID, and product source/reference images from product detail.
- `SettingsPage.tsx` derives grouped config items from the fetched config response.

Use `useMemo` where the derivation is non-trivial or passed deeply; otherwise a local helper function is fine.

---

## API Error State

The central API wrapper throws `ApiError(status, detail)` from `web/src/lib/api.ts`. Pages convert it into local user-facing
strings:

- `LoginPage.tsx` displays invalid key errors.
- `ProductCreatePage.tsx` displays create/upload validation errors.
- `ProductDetailPage.tsx` displays copy/poster/reference image mutation errors.
- `ImageChatPage.tsx` displays generation/session/attach errors.
- `SettingsPage.tsx` displays config validation errors.

Keep error display local unless multiple pages need a shared notification system.

---

## Avoid

- Adding a global store for server data already cached by TanStack Query.
- Keeping a separate local copy of fetched records unless the user is editing a draft.
- Invalidating broad caches unnecessarily when a precise `setQueryData` is already used and safe.
- Hiding route state in local storage or globals instead of using React Router params.
- Storing API keys or admin keys in frontend local storage. Authentication is session-cookie based.
- Reintroducing durable browser-local onboarding, tour, help, or tutorial state without a new approved product requirement.
- Adding new durable local preferences outside `PreferencesProvider` or a documented page-owned feature boundary without
  updating this spec and focused helper tests when parsing/clamping behavior is non-trivial.

## M1-01 prototype and execution state

### 1. Scope
The verification-only product panel uses `usePrototypeExecution`; it does not replace the existing canvas or introduce a global state store.
### 2. Keys and signatures
Query keys are `['prototype-experiments', productId]`, `['prototype-tasks', productId]`, `['prototype-task', taskId]`, and `['prototype-status', taskId]`. `mergePrototypeStatus(task, status)` ignores stale status versions. URL parameter `prototype` identifies the selected task or `new`.
### 3. Contracts
Poll lightweight status while work/unknown waits are active; refresh detail when plan/artifact/terminal state changes. Reuse `executionNeedsPolling` for queued/running and unknown with an open wait across prototype, workflow and session views. Preserve `row_version` and execution summaries during merges. Partial prototype status merges changed operations into retained history. Keep drafts and acknowledgement checkboxes local. Display experiment quota from fresh task status when selected. Content language is server draft state and independent of UI locale.
For unresolved controlled mutations only, sessionStorage retains the environment/resource/action-scoped idempotency key, payload digest and original accepted submission payload. This per-tab recovery record is not general draft persistence; never store credentials or uploaded bytes. Clear it on a definite result. An explicit recovery action replays the original payload/key while retaining newer editor text. An already-resolved recovery callback cannot create another key. Old digest-only records remain fail-closed when the original payload cannot be reconstructed.
### 4. Error matrix
409 retains the draft and refreshes server state; 422 renders a localized error. Lost response/503 retains the same key and queries the original intent before comparing newly fetched versions. Recovering a prior committed payload must not report that newer edits were saved or clear them; use `previous_submission_recovered`. `PendingSubmissionError` exposes explicit original-payload replay through `ExecutionIntentRecovery`; completed/stale recovery is fenced by its original key. Unknown retry is disabled until explicit fee acknowledgement; local resume/end are distinct actions.
### 5. Cases
Good: reloading after a committed request resolves its original operation. Base: cancel refreshes to a terminal task with unchanged quota. Bad: minting a new key because the refetched row_version changed.
### 6. Required evidence
`executionIntent.test.ts`, `executionApi.test.ts` and `prototypeExecution.test.ts` cover digest/replay, HTTP headers, response loss, stale status, partial history and UI-independent content. `scripts/m101_ui_regression.mjs` exercises actual React controls against an isolated API: lost response plus newer draft, reload plus explicit original replay, poll-then-cancel and new run, and unknown polling before/after wait closure for workflows/sessions. Its `--baseline` mode serves reviewed Git frontend bytes without modifying the worktree. Browser checks must also cover local recovery; helper tests cannot replace them.
### 7. Wrong versus correct
Wrong: show "base images saved" for any needs_attention state. Correct: use the server issue code and operation records; only `composition_not_implemented` describes the saved-base boundary.
