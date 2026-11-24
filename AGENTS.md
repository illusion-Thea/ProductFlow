<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->

# Repository Guidelines

## 1. Project Overview

### 1.1 Architecture and Module Organization

| Layer | Existing implementation | Extension requirements |
| --- | --- | --- |
| Frontend | React 19, strict TypeScript, Vite 7, Tailwind CSS 4, React Router 7, TanStack Query 5, ReactFlow | Extend existing workspaces and components; retain routing, themes, localization, and query conventions |
| Backend | Python 3.12+, FastAPI, Pydantic, SQLAlchemy, Alembic | Preserve presentation, application, domain, and infrastructure boundaries |
| Data and execution | PostgreSQL, Redis, Dramatiq, durable workflow runs and image-session tasks | Add versioned business records, Outbox delivery, idempotency, model-call accounting, and request-level quotas |
| Providers | `mock`, `openai_responses`, `openai_images`, `google_gemini_image` | Reuse profiles, bindings, and factories; validate the required GPT6 text and GPT image paths without removing existing adapters |
| Rendering | Pillow poster templates and remote image generation | Connect text-free base images to independent, deterministic text composition and mask editing |
| Storage and identity | `LocalStorage`, cookie sessions, shared administrator access, separate settings unlock | Implement MinIO, named accounts, resource authorization, and per-version retention in the appropriate phase |
| Deployment | Windows startup scripts and a separate Compose/Nginx deployment path | Validate environment isolation, stable-user canaries, compatible rollback, and off-host recovery |

Existing candidate groups, provider metadata, task rows, and download endpoints are reusable foundations. They do not establish production-batch semantics, complete cost accounting, adoption feedback, or successful-download retention.

All paths below are repository-relative. Backend module paths are relative to `backend/src/productflow_backend/` where marked.

### 1.2 Code Navigation

| Area | Existing entry points |
| --- | --- |
| Routes and navigation | `web/src/App.tsx`, `web/src/components/TopNav.tsx` |
| Image sessions | `web/src/pages/ImageChatPage.tsx`, `web/src/pages/image-chat/` |
| Branches, candidates, and selection | `web/src/pages/image-chat/branching.ts`; `ImageChatHistoryPanel.tsx` and `ImageChatMainStage.tsx` in the same directory |
| References and responsive layout | `web/src/pages/image-chat/ReferencePanels.tsx`; `resizableLayout.ts` and `mobileLayout.ts` in the same directory |
| API, DTOs, and UI helpers | `web/src/lib/api.ts`, `types.ts`, `apiErrors.ts`, `imageToolOptions.ts`, `imageSizes.ts`, `i18n.ts` in `web/src/lib/` |
| Product workbench | `web/src/pages/ProductDetailPage.tsx`, `web/src/pages/product-detail/` |
| Image-session HTTP contracts | Backend: `presentation/routes/image_sessions.py`, `presentation/schemas/image_sessions.py` |
| Session orchestration and admission | Backend: `application/image_sessions.py`, `application/queue_submission.py`, `application/admission.py` |
| Execution contracts and delivery | Backend: `domain/durable_generation_tasks.py`, `domain/enums.py`, `workers.py`, `infrastructure/queue.py` |
| Provider resolution and calls | Backend: `infrastructure/provider_config.py`, `infrastructure/image/factory.py`, `chat_service.py`, `images_provider.py`, `responses_provider.py` in the image directory; `config.py` |
| Persistence and migrations | `backend/src/productflow_backend/infrastructure/db/models.py`, `backend/alembic/versions/` |
| Files, previews, and composition | Backend: `infrastructure/storage.py`, `presentation/image_variants.py`, `infrastructure/poster/renderer.py` |
| Product workflows and gallery | Backend: `application/product_workflows.py`, `application/product_workflow/`, `application/gallery.py` |
| Authentication and errors | Backend: `presentation/deps.py`, `presentation/routes/auth.py`, `presentation/errors.py`, `domain/errors.py` |

Backend tests live in `backend/tests/`; shared frontend UI lives in `web/src/components/`; existing product and architecture documentation lives in `docs/`.

Production batches, fact versions, layered image versions, feedback, first-round assessments, asset references, experiment quotas, and releases require new domain records. Names such as `application/garment`, `ProductionBatch`, and `ExperimentRun` describe intended contracts, not verified existing modules.

## 2. Development Standards

### 2.1 Workflow and Requirement Sources

1. Read this file, `.trellis/workflow.md`, and relevant `.trellis/spec/`. Preserve the managed block and existing task/developer records; follow the active Trellis workflow.
2. Use backend/frontend spec indexes to load applicable database, error, component, state, type, quality, and DAG guidelines. Inspect the working tree and preserve unrelated user changes.
3. Extension requirements follow [PRD V1.13](docs/garment/PRD.md), [Technical Design V1.6](docs/garment/TECH_DESIGN.md), and [the development entry point](docs/garment/DEVELOPMENT.md). Repository `docs/PRD.md` and `docs/ARCHITECTURE.md` describe the existing app. Read applicable FR/ACs; historical drafts do not override confirmed requirements.
4. Resolve documented legacy conflicts toward confirmed extension behavior; update code, tests, and specs together (Section 4.1). Report unresolved conflicts rather than changing acceptance criteria.
5. Keep edits focused: no unrelated formatting, broad upgrades, replacement state frameworks, or whole-page rewrites. Extract domain rules and small modules instead of overloading `ImageChatPage.tsx` or `application/image_sessions.py`.
6. Coordinate DTO/API, schema/route, application, database, queue, and test changes. New tables/columns need Alembic revisions and existing `Base.metadata`.
7. Distinguish existing, implemented, and unverified behavior. Working pages, mocks, and legacy tests do not prove extension acceptance.

### 2.2 Build, Test, and Development Commands

Run these commands from the repository root. Prefer the existing `justfile`; commands are instructions for relevant development work, not actions to run automatically during documentation edits.

| Purpose | Command |
| --- | --- |
| Install backend dependencies | `just backend-install` or `uv sync --directory backend --extra dev` |
| Install frontend dependencies | `just web-install` or `pnpm --dir web install` |
| Start only Compose development dependencies | `docker compose up -d productflow-postgres productflow-redis` |
| Apply development migrations | `just backend-migrate`; loads the development environment through `scripts/with_dev_env.sh` |
| Run the development API | `just backend-run`; default port 29282 |
| Run development workers | `just backend-worker`; use the same development database, queue, storage, and configuration as the API |
| Run Vite | `just web-dev`; default port 29283, with `/api` proxied to port 29282 |
| Backend lint | `uv run --directory backend ruff check .` |
| Backend tests | `just backend-test` or `uv run --directory backend pytest` |
| Frontend lint | `pnpm --dir web lint` |
| Frontend tests | `pnpm --dir web test:run` |
| Frontend type checks and build | `just web-build` or `pnpm --dir web build` |

- Windows has `Start-ProductFlow.ps1` and `Stop-ProductFlow.ps1`, backed by the local `.local/` environment. Startup runs migrations. Identify the active deployment before starting services; do not launch overlapping stacks or restart a running user environment for documentation checks.
- Some `just` recipes require Bash. Verify available tooling before using them from PowerShell; do not invent an untested translation of environment-loading commands.
- Use `uv` with `backend/uv.lock` and `pnpm` with `web/pnpm-lock.yaml`. Do not introduce npm/yarn lockfiles. Use `test:run` for automated verification, not Vitest watch mode.
- Run migrations only against the intended environment. The `.env.dev` filename and example values do not prove database isolation; production isolation remains a separate requirement.

### 2.3 Coding Style and Frontend Contracts

- Python targets 3.12+: typed functions, `snake_case`, Ruff's 120-character limit and `E/F/I/UP/B` rules with configured exceptions. Keep imports sorted.
- Keep TypeScript strict, PascalCase component/page filenames, and camelCase hooks/helpers/API functions. Reuse Tailwind and shared components.
- Centralize requests in `web/src/lib/api.ts`; preserve `credentials: "include"`, FormData, `ApiError`, and `normalizeApiErrorDetail`. No page-level raw fetch or direct rendering of 422 detail arrays.
- Update frontend types, backend schemas, consumers, and contract tests together. Reuse configured prompt limits, dimensions, and provider capability fields.
- Keep server records in TanStack Query and drafts/panels/drawers/selections in UI state. Persist business records server-side.
- Poll `['image-session-status', sessionId]` and merge status; refresh `['image-session', sessionId]` / `['image-sessions']` for new artifacts or completion. Never frequently poll full histories.
- Preserve `['product-workflow-status', productId]`. Product/gallery writes update or invalidate related `product`, `products`, and `gallery` queries.
- The current workbench UI supports Chinese only (user confirmation, 2026-11-24). Use Chinese labels, statuses, errors, placeholders, and aria text through the shared translation helpers; do not expose a UI language switch or honor legacy non-Chinese UI preferences. Retain existing dictionaries for legacy system-label recognition; leave user content and model/provider identifiers unchanged. Verify light/dark and desktop/mobile behavior.
- Garment copy language is independent of the Chinese UI and market metadata: Chinese (default), Malay, Indonesian, and Filipino. Explicitly generate the selected-language text preview and confirm its language/version before composition; opening the workbench or changing market never triggers translation or model calls.
- Show loading, empty, error, disabled, and success states; retain invalid drafts. Selecting a base/latest candidate never implicitly adopts production output.

### 2.4 Backend, Model Execution, and Storage

- Routes handle auth, input, use cases, and serialization. Keep orchestration in `application/`, rules in `domain/`, and SDK/storage/database code in `infrastructure/`. Use `domain/errors.py` and `presentation/errors.py`.
- Generation submission persists work, enqueues, and returns promptly; never call providers synchronously from HTTP routes.
- PostgreSQL owns business/version/execution/accounting state; Redis only delivers. Save operations and Outbox together in short transactions, atomically claim stages, and exclude model calls from long transactions.
- Same idempotency key/payload returns the original operation; changed payload conflicts. Validate `row_version` and return 409; frontend click guards are insufficient.
- Reuse profiles/bindings, `infrastructure/provider_config.py`, and factories. Keep SDK details out of routes; validate capabilities before dispatch and never hardcode models, gateways, or keys in UI/workers.
- Record every actual request's Operation/Attempt/ModelCall, input/model snapshot, request ID, usage, and cost state. Separate actual/estimated/unconfirmed costs; unknown is not zero. Count candidates and requests separately.
- Disable implicit paid retries in SDKs, HTTP, fallbacks, application logic, queues, and recovery. Actor `max_retries=0` alone is insufficient (Section 4.1). Retrieving an existing request must not generate again.
- Persist dispatch intent, quota reservation, and deadlines. Possible dispatch followed by crash/timeout becomes unknown, never undispatched. Explicit model retries create new attempts with potential-duplicate-charge notice; unchanged zero-model local recovery retains original-attempt provenance.
- Share actual-request quotas across APIs, workers, and releases; task admission counts are insufficient. Local rendering, delivery, and cleanup retries never invoke models.
- Preserve storage abstraction and `LocalStorage.resolve` safety. MinIO uses private buckets, immutable keys, and SHA-256; no public URLs or unchecked user paths.

### 2.5 Testing and Acceptance

| Area | Existing regression entry points |
| --- | --- |
| Sessions, branching, retries, cancellation | `backend/tests/test_image_sessions.py`, `web/src/pages/image-chat/branching.test.ts` |
| Recovery and admission | `test_queue_recovery.py`, `test_product_workflow_queue_recovery.py`, `test_generation_admission.py`, `test_durable_generation_task_contract.py` under `backend/tests/` |
| Providers and input/error handling | `test_provider_payloads.py`, `test_image_generation_core.py`, `test_image_generation_failures.py`, `test_prompt_length_config.py` under `backend/tests/` |
| Files, settings, migrations, gallery | `test_storage_upload_validation.py`, `test_auth_settings_runtime_config.py`, `test_migrations_database_constraints.py`, `test_gallery.py` under `backend/tests/` |
| Frontend helpers and layout | `web/src/lib/imageSizes.test.ts`, `web/src/lib/imageToolOptions.test.ts`, `web/src/pages/image-chat/resizableLayout.test.ts`, `web/src/pages/image-chat/mobileLayout.test.ts` |


- pytest discovers `backend/tests/test_*.py`. Cover product/copy/poster/settings/session workflows. Backend changes require Ruff and `just backend-test`; frontend changes require lint, `test:run`, and `just web-build`. Check both sides before cross-layer commits.
- Schema changes need Alembic and migration/regression coverage. Documentation-only edits check paths, commands, links, structure, and rules without running the app.
- SQLite/mock/inline-queue fixtures do not prove PostgreSQL locking, real Redis delivery, recovery, or capacity; use isolated integration environments for those.
- Vitest uses node and `src/**/*.test.ts`; pure helper tests do not replace browser verification.
- Follow applicable PRD AC01-AC88 and Technical Design Section 8.2; AC36 is P1 and AC71 deferred F03. Real fidelity, first-round 7/10, 20-user capacity, and recovery targets need independent evidence.

### 2.6 Commits, Pull Requests, and Documentation

- Keep one focused commit per topic and follow the repository's Conventional Commit style, such as `feat: add model configuration`. Preserve unrelated work and follow the active workflow's commit process.
- PRs describe the user-visible change, verification commands and outcomes, migration/configuration impact, and screenshots for UI changes. Distinguish checks actually run from pending checks.
- Keep official documentation, release notes, PR descriptions, and contribution guidance concrete and verifiable. Avoid contrastive slogans and promotional sequencing in any language, including phrases such as "This is not ..., but ..." or "establishes the main loop".
- Preserve real technical ordering when required, including migrations, saving before execution, and troubleshooting. Label planned or unimplemented behavior explicitly.

### 2.7 Agent Resources

- Issues and task-level PRDs are tracked in GitHub Issues for `yuqie6/ProductFlow`, with local planning/execution records following Trellis; see [issue-tracker](docs/agents/issue-tracker.md). The product-level garment baseline is [docs/garment/PRD.md](docs/garment/PRD.md). Task PRDs state concrete scope, constraints, and acceptance while referencing applicable FR/AC IDs; they do not maintain a second product rule set.
- Use the existing triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`; see `docs/agents/triage-labels.md`.
- Domain documentation follows the single-context layout in `docs/agents/domain.md`: root `CONTEXT.md` and `docs/adr/` when present. Do not invent duplicate context sources.

## 3. Functional Requirements

### 3.1 Preserve Existing Capabilities

- Preserve independent `/image-chat` sessions, renaming/history, references, base-image branches, candidates, progress, explicit retries/cancellation, and downloads. Keep product, gallery, help, and settings workflows available.
- `ImageSession` no longer owns a product; see `backend/alembic/versions/20260627_0029_drop_image_session_product_scope.py`. Use explicit `attach-to-product` destinations; do not require product binding for free sessions.
- Continue branches from the user's selected base. The six-image context limit includes that base, allowing five extra references. Reject cross-session assets and silent input substitution.
- Each `ImageSessionRound` represents one candidate. Preserve `generation_group_id`, `candidate_index/count`, branch provenance, and previous images.
- Products are team-editable roots and their free DAGs use product permissions; formal tasks and independent image sessions keep their own team, personal/team scope, creator, and responsible user. Sessions explicitly choose scope at creation. Whole-product/task/session deletion archives that root, including products without formal tasks; keep archive access and independent asset lifecycle, without cascading archive, cancellation, or file deletion to linked roots.
- Archive is separate from task status, image trash, and release Archive. Allow immediate archive with accepted work still queued/running; preserve its progress/results and continue accepted stages. Archived roots allow authorized reads/downloads, feedback/selection disposition, cancellation, per-image delete/restore, and issue closure. New content/production/retry/local recovery requires unarchive. Serialize archive and admission; unarchive neither submits drafts nor restores images, resets retention, or revives cancelled tasks.
- Gallery saves, product-reference attachment, main-image selection, downloads, base selection, and adoption remain distinct actions and records. Favorites alias the original version; cross-resource attachment/reuse creates independent target records with provenance. Keep display main images separate from original product evidence; generated images cannot establish new facts.
- Preserve capability-aware controls, actual dimensions, provider notes, and actionable errors. Requested dimensions and existing 9:16 presets do not prove the frozen garment output specification.
- Keep DAG product-context, reference-slot, structured-copy, and generation-trigger semantics. Node templates store configuration/internal edges, not product facts or generated artifacts. The workflow canvas is not F03 freeform composition.

### 3.2 Ten-Slot Production and Layered Rendering

- Extend the existing product/node workbench with batch views and reusable preview/reference/history components. Keep free image chat; associate reusable assets explicitly rather than converting every session into production.
- Each formal production task owns exactly one batch containing one SKU, one specification, and slots 1-10. One product image can start ten-entry preparation; drafts may omit SKU. Reject or explicitly split mixed SKUs/specifications. Free nodes may fan out from one image to different specifications; free nodes/sessions do not enter `R0`.
- Canvas Run All executes free nodes only. Formal batches start through separate confirmation using immutable accepted plan snapshots; later canvas edits/deletions or Run All cannot rewrite those inputs or rerun formal slots.
- **`generation_count=10` means candidates for one prompt, not ten independently planned slots.** Persist each slot's copy, prompt, facts, references, template, specification, first attempt, and revisions. Do not chain each slot to the preceding generated image.
- Version fact sources, verifiable views, and confirmers. Appearance cannot establish unknown backs, interiors, openings, or performance. Mark insufficient-evidence trials; formal plans need evidence or restricted views.
- Always pass the product original. References supply pose/scene/composition only; faces must be AI-generated, never reused real-person likenesses. Resolve reference/text conflicts explicitly; outfit presets and manual descriptions are exclusive.
- FR14 explainable reference matching is P0; zero matches permit no-reference generation. Exclude disabled/unsuitable sources. FR04 is P1. Malaysia, Indonesia, and the Philippines share a default style version; shared-template updates create versions.
- Validate GPT6's ten structured entries and obtain human confirmation before generation. Do not hide text failures behind templates/manual copy. Explicit manual-copy mode leaves GPT6/FR05 unverified.
- Offer Chinese, Malay, Indonesian, and Filipino copy previews, defaulting to Chinese. Persist the selected language, preview version, and confirmation in the accepted plan. Explicit language generation is a separately accounted text request; direct manual text edits and local composition still add zero model calls.
- Generate bases without promotional text, then compose confirmed copy with Pillow. Version bases, masks, text, layouts, templates, specifications, and composites independently. Text/font-size/color/template-parameter edits reuse the base with zero calls to either model.
- Version fonts; reject missing glyphs/fonts and overflow without truncation, rewriting, or silent font substitution. Preview/download the same composite. Retry composition/storage locally without regenerating the base.
- Edit only the requested slot's base. Normalize image/Alpha-mask orientation, dimensions, PNG format, and coordinate transforms; recompose original text and inspect outside-mask fidelity. `ImagesAPIClient.edit(mask=...)` does not establish an end-to-end session editing flow.
- Outputs: 9:16 with pixels to be frozen, 1200x1200, and 800x800. All three formats for one SKU require three tasks/three batches/30 slots and separate statistics. Preserve product proportions/key structures; enforce the stricter of 25 MB and provider limits.

### 3.3 Business State, Feedback, and First-Round Assessment

- Use nine production states: Draft, Awaiting Generation Confirmation, Generating, Awaiting Output Confirmation, Revising, Needs Attention, Completed, Cancelling, Cancelled. Keep them separate from technical `JobStatus`. Draft permits text preparation with queued/running/failed progress; validated ten-entry plans enter Awaiting Generation Confirmation.
- Per-version feedback is `undecided/adopted/discarded`, with actor/time/history. New generation and edits default to undecided. Success does not adopt; discarding does not retry, delete, or abandon a slot.
- Formal deliverable candidates, current selection, and first-round outputs use CompositeVersion only. Explicitly select at most one adopted composite per slot. Revoking its adoption clears selection transactionally; other adopted versions remain. Late results and base selection never change current adoption.
- Completion requires every slot adopted or explicitly abandoned and no in-flight work, current undecided composite candidates, unresolved failures, or other open issues. Bases/masks allow feedback and downloads, but undecided intermediates alone do not block completion. Six adopted plus four abandoned, or all abandoned, can complete; unexecuted/undecided formal work is not resolved implicitly.
- `R0 = adopted original first-round slots / 10`, targeting 7/10. Initial failures count; unexecuted slots make assessment incomplete. Settle after original successful-composite feedback is resolved, retaining original slots/attempts/versions and feedback/Q01-Q09 snapshots. A reused base's first composite under the new batch plan counts, with fresh/reused sources recorded and reported separately; reuse does not prove fresh-generation quality or replace ten independent prototype calls.
- Model-call/base success is insufficient for first-round completeness. Cancellation with no original composite and no definite original failure leaves that slot interrupted/incomplete; ten bases with only seven adopted composites cannot claim final 7/10. Definite original failures still count, and reliable late original conclusions may reconcile only unsettled assessments without new composition on cancelled tasks.
- Reconciled late results from the original first attempt may complete an unsettled assessment once all ten execution conclusions and original-composite feedback are resolved. Retries/repairs never replace original results; a base alone is insufficient and cancelled tasks cannot resume composition. Settled snapshots remain immutable.
- Local composition/storage recovery with unchanged base, confirmed content, and configuration remains associated with the original attempt and adds zero model calls. Do not settle the slot while recovery remains unresolved. At its finite deadline, stop automatic recovery and wait for an authorized user to continue explicitly or end the first attempt; preserve a final local failure on explicit closure, without reclassifying external unknowns.
- After waiting was explicitly closed and the task completed, late composites stay in history with undecided feedback; they do not reopen current work or change selection. Only explicit inclusion creates current work. For a non-cancelled task with an unsettled assessment, an authorized user may explicitly compose a late original base under unchanged inputs/configuration when the slot has no active operation; retain original-attempt provenance and obey release admission constraints.
- Repairs, later feedback, cancellation, expiration, and deletion do not rewrite settled `R0`; append corrections and report current adoption separately. Completion, quality, first-round success, and downloads are independent; no purpose-approval gate.
- Withdrawing an already-generated undecided revision marks that revision discarded, preserves the prior current selection and all history, and reevaluates remaining issues transactionally. It does not delete files, call models, or create a still-undecided historical exception. Unsubmitted drafts may be withdrawn; accepted work still needs explicit cancellation.
- Serialize cancellation and dispatch under one task lock. After acceptance, start no further generation, retries, or composition. Finite deadlines and an independent scanner close execution. Archive late outputs under the original Attempt as undecided, without revival; authorized live outputs remain downloadable.
- Cancelled production tasks are terminal; continued work requires a new Task/Batch without inherited execution/success claims. Completed tasks allow revisions. Closing an unknown-result wait retains the unknown attempt and unconfirmed costs.
- Ready slots may be revised while other slots are in initial generation and the round still accepts new work, with at most one queued/running operation per slot. These revisions inherit the current round's ReleaseID. Generating takes precedence while initial work remains in flight; afterwards, remaining revision work yields Revising.

### 3.4 Accounts, Authorization, and Asset Lifecycle

- Use stable UserID/TeamID, enablement, and design/operations/management roles. MVP permits one active team per account and a system-wide management role; multi-team switching and team administrators are deferred. Management business access still follows team rules and does not grant ordinary editing of another user's personal task.
- Current same-team members may view/download personal and team tasks. Only the current responsible user (initially the creator) edits personal tasks or changes scope; current team members edit team tasks. Editing includes feedback, retry, cancel, copying the whole task, delete, restore. Deny cross-team access and promptly enforce disablement/membership changes.
- When a personal/team task or independent-session responsible user is disabled or leaves its team, system management may use a dedicated audited handoff to an enabled member of that same team. Transfer responsibility and scope-change permission, plus personal-resource editing where applicable, while preserving creator, TeamID, scope, history, costs, and settled first-round records; team editing stays shared and management gains no ordinary personal editing privilege.
- Revocation blocks new submissions and unauthorized access, but already accepted operations and their confirmed stages continue under their recorded acceptance authorization and input snapshot. Preserve the original actor. Stopping this work requires explicit cancellation by a currently authorized user; revocation alone neither cancels nor fails it.
- Reusing a live same-team image requires source view/download permission and target write permission; it does not require source-task editing or permit copying the whole personal task. The new target record has independent feedback/download/retention and provenance; shared bytes remain reference-protected.
- Reusing a layered composite preserves editability through independent target-side composite/layer records and required base/mask dependencies. Validate all source dependencies and create their target references before success; missing or unrestored inputs cannot silently produce a flattened substitute. Target edits/restores use target permissions, and later source deletion does not change target lifecycle. Immutable bytes may be shared; reused generated dependencies receive their own retention records.
- Authorize products, sessions, gallery, previews, downloads, history, and references server-side. Hidden controls/shared login flags are insufficient. Isolate legacy resources with unknown ownership.
- Full settings, provider profiles/bindings, and configuration import/export require the current system-management role plus the independent settings unlock. Ordinary business users may read only necessary sanitized runtime capabilities/specifications. Reads, import previews, and exports never return stored raw keys; possessing an unlock token alone grants no management role.
- All feedback states permit early single-image download when authorized, live, and available; no completion, 7/10, or management gate. Preview, link creation, and interrupted transfers are not successful downloads.
- Complete transfer of a specific version creates its DownloadEvent/first-download marker; any authorized teammate qualifies. Separate preview/thumbnail variants from downloads despite their shared route. Server transfer completion does not prove browser disk persistence.
- Undownloaded generated images remain live 30 days, then enter trash for 7 days. Downloaded versions remain until manual deletion; manual deletion enters trash immediately. Restore starts a new 30 days for undownloaded versions, preserves generation time/download markers, and never changes retention through feedback.
- A cross-resource reused generated-image record starts its own 30 days at successful reuse, preserving source generation time without inheriting its download marker. Favorites share the original record and do not reset retention. Trash guarantees a seven-day recovery window; later recovery is allowed only while bytes exist, purge has not started, and permission holds, with no promise of continued retention.
- Store UTC/display user time; retention uses consecutive 24-hour days. Start trash retention at actual entry and recheck download events/leases before expiration.
- Protect unknown-download legacy images without fake downloads. Evidence, references, fonts, and templates follow resource dependencies; live editable composites protect inputs. Active tasks/leases block physical deletion; history/adoption alone does not. Releasing protection does not restart the trash period. Trashed protected files still require restore before ordinary download or new file edits.
- At a composite edit entry, identify trashed required inputs and prompt for restoration. On confirmation, the system restores them there after permission/version/purge checks; do not require users to find them in the recycle bin. Proceed only after successful restoration. On refusal show “不恢复所需图片，当前成品图无法修改”; failure explains its cause and blocks file editing. Never restore or read trashed dependencies for new edits without confirmation.
- Authorized feedback changes, deselection, and closing open issues remain possible for trashed/purged images without restoration. These metadata operations do not alter settled first-round snapshots; downloads and file edits still require available inputs.
- Coordinate GC, restore, downloads, and new references with locks/claims. No age-only bucket TTL or cascading physical deletion may bypass dependencies. Delete bytes only; retain metadata, feedback, costs, and first-round snapshots.
- Inventory session/product/gallery/workflow/JSON/copy references before MinIO migration. Verify size, SHA-256, dimensions, and readability per object. Retain local files read-only and deploy compatible readers for both stores before switching writes.

### 3.5 Phased Delivery and Operations

- Prototype work stays within its validation scope. MVP covers P0 in FR01-FR14, accounts, retention, and 20 mixed active users; production adds FR15-FR16. FR04 is P1 and F01/F02/F03 deferred. Existing adapters do not establish new-model acceptance.
- Isolate test/production databases, Redis queues, MinIO namespaces, and credentials. Production stable/canary versions share compatible data. Immutable releases use Draft/Active/Archive; approximately 20% of stable UserIDs receive canary traffic.
- Require one configured business day covering upload/generation/feedback/download/delete/restore without critical blockage, unauthorized access, data loss, or unexpected duplicate charges. Management activates manually; the current Compose script does not implement this workflow.
- Pin every accepted production/revision execution, queued slot, child, and later stage to its ReleaseID, including ready-slot revisions during the current round. After all accepted work and cleanup end, new explicit revisions may use the current compatible release. Drain old workers with deadlines, never regeneration on a new version; share accounting and quotas.
- At cutover or canary stop, atomically close new-operation admission to retiring rounds. Drain only already accepted operations and their existing stages; new changes remain drafts until cleanup finishes and the user confirms a new compatible round. Local recovery retains original-attempt provenance separately from its actual execution identity; it cannot reopen a retired round's admission or alter settled statistics.
- Roll back compatible software/configuration, not business snapshots. Run compatible migrations once. Readiness checks DB/schema, Redis, MinIO, target workers, fonts, and templates beyond `/healthz`.
- Retain off-host joint database/MinIO backups 30 days; measure RPO <= 24 hours and RTO <= 4 hours. Align database recovery point, manifest, hashes, and GC protection; rehearse permissions/files/core operations. Capacity targets are 50-100 accounts and 20 mixed active users, not 20 concurrent model requests.
- Disaster-restored queued/undispatched records and quotas may predate actual paid dispatch. Quarantine affected execution, delivery, and quota state; reconcile against trustworthy evidence not rolled back with that snapshot before resuming. Unknown stays unknown, provider lookup misses do not prove no dispatch, and restored counters cannot release consumed quota. Local recovery must not cause new model calls.

## 4. Important Notes

### 4.1 Known Legacy Differences to Address

Recheck these paths when implementing the relevant feature. Update code, old test expectations, and `.trellis/spec/` together rather than weakening the confirmed extension requirements. Backend paths in this table are relative to `backend/src/productflow_backend/`.

| Existing behavior | Required adaptation |
| --- | --- |
| `application/image_sessions.py::_handle_image_generation_task_failure` automatically requeues failures | Leave paid failures for explicit user action; account for new attempts separately and retain failed prototype requests in quota usage |
| `infrastructure/queue.py` resets some stale running jobs to queued during worker recovery | Inspect persisted dispatch intent/original requests; possible dispatch becomes unknown. Heartbeat inactivity does not prove that no request was charged |
| `infrastructure/image/images_provider.py` retries without quality/style or with only one reference; its client does not explicitly disable SDK retries | Validate capabilities before sending; disable implicit SDK/fallback calls and never silently drop references or change confirmed inputs |
| Manual retry resets the same `ImageSessionGenerationTask`; existing tests assert application auto-retry limits | Preserve separate Operation/Attempt/cost records and original failures; test explicit retries, unknown outcomes, and quota conservation |
| Cancellation immediately sets cancelled; `test_image_session_generation_cancel_after_file_save_does_not_persist_round_or_asset` expects returned assets to be omitted | Implement production cancellation/late-result semantics; archive returned assets under the original attempt without starting composition and update race-condition tests |
| `_images_api_batch_count` and `generate_many` can turn candidate counts into Images API `n` | The prototype requires serial `n=1` calls; production slots need independent plans. Prevent hidden batch multiplication and request/candidate count confusion |
| `require_admin` checks a shared login flag and `admin_access_required` can disable the gate | Enforce resource identity/authorization at every production entry point without a gate-off path bypassing team isolation |
| `LocalStorage`, ordinary FileResponse, and the `deletion_enabled` whole-resource delete switch | Whole-product/task/session deletion becomes root archive; add independent version-specific download completion, retention, trash, and reference records. The delete switch does not implement either contract |
| Settings migration export includes provider API keys | Restrict full configuration to management plus independent unlock; exclude stored raw keys from reads, previews, and exports, and validate target credential readiness separately |
| Existing template and generated poster modes render locally or call a model separately | Build base-image-to-text-layer composition; template output cannot impersonate successful GPT6 output or establish zero-call editing of a generated image |
| Main-image attachment treats generated output as `ORIGINAL_IMAGE` and demotes an existing original | Separate display selection from product evidence; preserve real originals and provenance instead of allowing AI output to become fact evidence |

Provider retrieval of an existing response can help reconcile unknown results; it must not create another paid request.

### 4.2 Prototype Budget and Productization Gate

- Use the single-SKU sample manifest described in `docs/garment/DEVELOPMENT.md` Section 3, with 9:16 output, a fixed template, and no action reference. Verify actual inputs and existing ExperimentRun usage before real calls. Run one sample, freeze configuration/quality examples, execute ten independent first-round slots, then validate one text edit and one mask edit.
- One `ExperimentRun` allows at most **12 image requests**, serially with `n=1`. Failures, refusals, and timeouts count; text requests are accounted for separately. Task copies, restarts, and release changes do not reset quota. Text-only edits add zero calls of either model type.
- `R_trial` always uses denominator 10; samples and repairs do not replace original slots. Full productization requires all four gates: real GPT6 ten-entry and layered-image flow; zero-call text editing with correct quota/unknown accounting; complete first-round adoption of at least 7/10; successful mask edits with acceptable outside-mask product fidelity and correct text.
- Diagnose locally before proposing further paid validation. New real experiments require the purpose, samples, parameters, criteria, and call count specified in PRD Section 8.2 and explicit user confirmation. Retain and link the original experiment; do not bypass its limit. Freeze the later 30 exploration samples separately, not as 90 formal slots.

### 4.3 Security, Configuration, and Evidence

- Do not commit `.env`, `.env.dev`, `web/.env`, generated storage, logs, caches, build output, `.trellis/tasks/`, or `.trellis/workspace/`. Keep credentials in local configuration based on the example files, never in source, frontend response payloads, browser persistence, release artifacts, or logs. Authorized write-only credential input must not be echoed by settings reads, previews, or exports.
- Runtime database configuration can override provider/model options. `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`, `ADMIN_ACCESS_KEY`, and `SETTINGS_ACCESS_TOKEN` remain environment-only. Inspect resolved non-secret profiles/bindings; changing legacy environment defaults may not change the active provider.
- Preserve the independent `/settings` unlock boundary while migrating shared login, and require the current management role for full settings access. Keep sanitized runtime reads separate. Authentication, storage, and delete-flow changes must cover legacy entry points and rollback compatibility. Do not clear user data or run `docker compose down -v` during normal updates.
- GPT6 and GPT image identify requirement directions, not verified official API IDs. Freeze actual model IDs, account capabilities, SDK/parameters, costs, final 9:16 pixels, and finite deadlines with evidence. A provider label or displayed historical result proves none of these by itself.
- Limit real model calls, migration, deployment, releases, and cleanup to the user's current authorized task and phase. Documentation instructions describe development requirements, not authorization to execute every operation they mention.
- Handoffs identify changed files, affected existing/new interfaces, migrations/configuration, checks actually run, applicable ACs, and remaining validation. Provide UI evidence for UI changes. Product quality, 20-user concurrency, and RPO/RTO require real measurements; documentation, mock tests, or a single historical image cannot establish them.

## Garment Extension Development Entry

- For garment model-detail-image work, read [the development entry point](docs/garment/DEVELOPMENT.md), [PRD V1.13](docs/garment/PRD.md), and [Technical Design V1.6](docs/garment/TECH_DESIGN.md). These repository-local files replace earlier external garment requirement links.
- The extension PRD is the single product-behavior and AC01-AC88 baseline for this extension; the technical design supplies implementation invariants. Original README/PRD/ARCHITECTURE documents describe current capabilities and technical state. Mark unchecked claims without reusable evidence as pending verification, then update both existing language versions and user instructions as features are delivered and verified. ROADMAP retains future planning and links M0-M6 to the development entry point; it is not another task-status record. Extension body text is maintained in Chinese for now.
- Task-level PRDs in GitHub/Trellis retain concrete task scope, constraints, and acceptance criteria while referencing applicable FR/AC IDs; links alone are insufficient. They do not replace the product baseline. Follow this file and the active Trellis workflow for engineering process; preserve the managed block and current user changes.
- Legacy auto-retry, stale-running reset, provider fallback, shared-login bypass, cancellation, and file-deletion contracts and test expectations that conflict with the confirmed baseline are not extension targets or acceptance criteria. The local notes in backend specs identify these differences; equivalent legacy examples in frontend state-management and backend logging guidelines follow the same boundary. Revise the affected implementation, detailed spec, and tests together; documentation integration alone does not establish remediation. Free nodes and image sessions have no implicit-paid-retry exception, for either text or image calls.
- Determine the current stage and evidence first. Without reusable evidence, start M0, then the authorized M1 prototype slice. Full productization requires PRD Section 8.2; production requires Section 8.3. Do not reset ExperimentRun quota or repeat verified paid experiments.
- Missing model/sample/deployment parameters block dependent external actions, not independent local engineering checks. Sample inputs are registered privately as described in DEVELOPMENT.md Section 3; no external workstation path is assumed.
- Read the full applicable FR/AC and contracts before editing. Report implementation, checks, real-effect evidence, and remaining validation separately. Documents do not themselves authorize paid calls, migrations, releases, or cleanup; preserve existing valid user authorization.
