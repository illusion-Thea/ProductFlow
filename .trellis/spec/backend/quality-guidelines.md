# Backend Quality Guidelines

> **Garment extension applicability (2026-11-23):** Where marked below, conflicting contracts, examples, matrices, and required tests describe the legacy implementation and legacy regression expectations only; they are **not garment-extension targets or acceptance criteria**. Follow [the garment PRD](../../../docs/garment/PRD.md), [technical design](../../../docs/garment/TECH_DESIGN.md), and [development entry point](../../../docs/garment/DEVELOPMENT.md) for the extension. Unrelated engineering constraints remain applicable. Revise affected code, detailed contracts, and test expectations together during implementation; this documentation merge does not claim the remediation is complete. Free nodes and image sessions have no implicit-paid-retry exception.

> Backend quality standards reflected by ProductFlow's current code, tests, and tooling.

## Confirmed garment contracts to verify during implementation

The following are required assertion points, not evidence of passing tests. Use the full
[Technical Design Section 8.2 mapping](../../../docs/garment/TECH_DESIGN.md) and stable PRD acceptance IDs.

- AC08/10/11/42/49: Draft exposes text preparation progress; formal task/batch is one-to-one with fixed specification;
  Run All excludes formal slots, accepted inputs survive canvas edits, free fan-out permits different specifications;
  ready slots revise under one active-operation lock and the current round ReleaseID while admission remains open,
  with Generating state precedence. AC08/72 additionally require Chinese-default, explicit Chinese/Malay/Indonesian/
  Filipino previews and confirmation snapshots independent of UI locale/market.
- AC23/52/59/63: formal candidates and current selection are composites; undecided intermediates alone do not block
  completion. Reused bases' first composites count with source-separated reporting. Original late results may complete
  only unsettled assessments, never replacing originals with retries or reviving cancelled production. Metadata feedback,
  deselection, and issue closure still work after byte deletion without rewriting settled snapshots. After a completed
  task closed its wait, a late result stays historical until explicit inclusion; no automatic completion blocker.
- AC62/63: base-only original slots cancelled without definite first failure leave R0 incomplete; ten model calls do not
  prove ten complete original production outcomes. Withdrawing a generated undecided revision marks it discarded,
  preserves the prior selection/history, and reevaluates remaining issues; no deletion or still-undecided history shortcut.
- AC59/61: unchanged, zero-model local recovery remains the original attempt and delays settlement; automatic recovery
  stops at its deadline pending explicit continue/end. Explicit late-base composition requires a noncancelled task,
  unsettled assessment, original inputs, and an idle slot; preserve original provenance and actual execution identity.
- AC68/73/78: one active team, system management, current-responsible authorization, audited same-team handoff including
  scope permission for personal/team tasks and independent sessions; source view/download plus target write permits
  image reuse but not whole personal-task copying. Products/free canvases are team shared; every whole-product/task/session
  deletion archives only that root, including products without formal tasks. Revocation blocks new submissions/access
  while accepted operations continue until explicit cancel.
- AC67/68: archive during active work preserves progress/results; archive/admission races are serialized. Authorized
  record management and per-image delete/restore remain available, but new content/production requires unarchive.
  Unarchive must not restore images, reset retention/download markers, submit drafts, or revive cancelled tasks.
- AC65/78: full settings/provider configuration/import/export require current management role plus independent unlock.
  Test ordinary roles with the token, management without unlock, and revocation. Sanitized runtime reads stay separate;
  raw stored keys never appear in reads, import previews, exports, or errors, and target credential readiness is explicit.
- AC48/74/75: favorites alias source records; cross-root reuse creates independent feedback/download/retention, starting
  30 days at reuse without inherited download markers. Layered composites retain editable layers and independent target
  dependency records, including restoration under target rights; missing dependencies cannot silently flatten output.
  Edit-entry confirmation restores trashed dependencies inline;
  refusal or failed restoration blocks file editing. Test permission/version/purge races, conditional recovery after
  seven days, reference release without clock reset, and shared bytes retained for other live versions.
- AC84: atomically close old-round admission on retirement, drain only accepted operations and their downstream stages,
  retain new changes as drafts, and require new-round confirmation after drain. Recovery provenance never reopens an
  old release or rewrites the original attempt's ReleaseID.
- AC01/85: selecting an AI display image preserves original fact evidence. Disaster-restored queued/undispatched rows
  and quotas remain isolated until credible evidence not rolled back with that snapshot permits reconciliation; unknown
  never auto-resubmits and used quota never resets. Local recovery causes zero additional model requests.

---

## Tooling

Backend tooling is defined in `backend/pyproject.toml` and root `justfile`:

- Python target: `>=3.12`.
- Ruff line length: `120`.
- Ruff target version: `py312`.
- Ruff lint selections: `E`, `F`, `I`, `UP`, `B`; `B008` is intentionally ignored for FastAPI dependency defaults.
- Pytest discovers tests under `backend/tests/`.

Common commands:

```bash
just backend-test
uv run --directory backend ruff check .
just backend-migrate
just backend-run
just backend-worker
```

Use the root `justfile` where possible so local env loading and ports match the project.

### Scenario: Production-style Docker Compose self-host runtime

#### 1. Scope / Trigger

- Trigger: editing `docker-compose.yml`, Dockerfiles, example env files, or README/docs for the self-hosted runtime.
- Applies to the full Compose stack: PostgreSQL, Redis, FastAPI API, Dramatiq worker, built Web static server, and shared storage.

#### 2. Signatures

- One-click start: `docker compose up -d --build`.
- Manual migration path: `docker compose run --rm productflow-backend alembic upgrade head`.
- Direct API health: `GET /healthz` returns `{"status":"ok"}`.
- Web proxy smoke path: `GET /api/healthz` through nginx proxies to backend `GET /healthz`.

#### 3. Contracts

- `productflow-backend` and `productflow-worker` must use Compose service names for runtime dependencies:
  - `DATABASE_URL=postgresql+psycopg://productflow:<password>@productflow-postgres:5432/productflow`
  - `REDIS_URL=redis://productflow-redis:6379/0`
- Container storage must use a shared in-container path `STORAGE_ROOT=/app/storage`.
- `STORAGE_HOST_PATH` is host-only Compose interpolation for production bind mounts. When unset, `/app/storage` is backed
  by the named volume `productflow-storage`; when set, it may point at an existing host directory such as
  `/home/cot/ProductFlow-release/shared/storage` for old systemd production storage reuse.
- Local hot-reload development must stay isolated on `.env.dev` / `STORAGE_ROOT=./backend/storage-dev`; do not depend on
  shell-sourcing production `.env` for development commands.
- Web self-host runtime must serve Vite build output as static files and proxy same-origin `/api/*` to the backend service.
- Runtime must not require host `uv`, `pnpm`, or `just`; those tools are only for local development.

#### 4. Validation & Error Matrix

- Missing `POSTGRES_PASSWORD` in `.env` -> Compose config/start should fail before launching Postgres.
- Postgres/Redis unhealthy -> backend must wait via `depends_on.condition: service_healthy`.
- Migration failure -> backend container must fail before serving API traffic.
- Backend unhealthy -> worker and web must wait for backend health before starting.
- Web `/api/*` not proxied -> same-origin frontend API calls fail even if static files load.
- Old systemd production files disappear after migration -> check whether `STORAGE_HOST_PATH` was set to the existing host
  storage directory before Compose created/used a fresh named volume.
- `STORAGE_HOST_PATH` leaks into container application config or replaces `STORAGE_ROOT` -> fix Compose env wiring; the app
  should still see `STORAGE_ROOT=/app/storage`.

#### 5. Good/Base/Bad Cases

- Good: `docker compose up -d --build` starts all five services, API health is OK, and web `/api/healthz` returns backend health.
- Good: `STORAGE_HOST_PATH=/home/cot/ProductFlow-release/shared/storage docker compose up -d --build` bind-mounts old
  production files while API/worker still run with `STORAGE_ROOT=/app/storage`.
- Base: local development starts only `productflow-postgres` and `productflow-redis`, while host `just` commands run API/worker/web.
- Bad: `DATABASE_URL` points at `localhost` from inside containers; that targets the app container itself, not Postgres.
- Bad: setting container `STORAGE_ROOT=/home/cot/ProductFlow-release/shared/storage`; that host path does not exist inside
  the container and bypasses the stable `/app/storage` contract.
- Bad: using Vite dev server or host `pnpm` as the documented production-style self-host web runtime.

#### 6. Tests Required

- Run `docker compose config --quiet` after Compose/env edits.
- For storage-related Compose changes, render config with `STORAGE_HOST_PATH` both unset and set; assert backend/worker
  mount `/app/storage`, keep `STORAGE_ROOT=/app/storage`, and do not expose `STORAGE_HOST_PATH` in container env.
- Build container images with `docker compose build productflow-backend productflow-web` or a full `docker compose up -d --build` smoke.
- Smoke a disposable or safe project with direct API health, web health, and web `/api/healthz` proxy checks when practical.
- Keep normal backend/frontend gates green when Dockerfiles or docs depend on package commands: backend tests/ruff and frontend lint/test/build.

#### 7. Wrong vs Correct

Wrong:

```yaml
DATABASE_URL: postgresql+psycopg://productflow:password@localhost:15432/productflow
```

Correct:

```yaml
DATABASE_URL: postgresql+psycopg://productflow:${POSTGRES_PASSWORD}@productflow-postgres:5432/productflow
```

Wrong:

```yaml
environment:
  STORAGE_ROOT: /home/cot/ProductFlow-release/shared/storage
volumes:
  - productflow-storage:/app/storage
```

Correct:

```yaml
environment:
  STORAGE_ROOT: /app/storage
volumes:
  - ${STORAGE_HOST_PATH:-productflow-storage}:/app/storage
```

### Scenario: Keep Compose release and open-source examples clean

#### 1. Scope / Trigger

- Trigger: editing repository release helpers, example env files, or ignore rules that affect what can be published.
- Applies to `scripts/release.sh`, `justfile`, `docker-compose.yml`, `.env.example`, `.env.dev.example`,
  `web/.env.example`, `.gitignore`, and `.trellis/.gitignore`.

#### 2. Signatures

- `just release` / `scripts/release.sh` is the single-host Docker Compose production update helper.
- `just release-dry-run` sets `DRY_RUN=1` and must not stop legacy services, build images, start containers, switch
  symlinks, or delete volumes.
- The actual release path validates Compose config, stops legacy user-level systemd services when present, runs
  `docker compose up -d --build --remove-orphans`, and performs HTTP health checks.
- Legacy services are `productflow-backend.service`, `productflow-worker.service`, and `productflow-web.service`.
- Supported override: `LEGACY_SYSTEMD_ACTION=skip` skips the legacy service stop step after the operator has handled port
  ownership manually.

#### 3. Contracts

- Example env files must contain placeholders or mock-provider defaults only; never commit real secrets or private hostnames.
- Local env backups such as `.env.bak-*` must stay ignored; they may contain copied production secrets and must not be
  inspected, tracked, or included in open-source release hygiene diffs.
- Release/update helpers must not delete Docker volumes; `docker compose down -v` is only a documented manual reset.
- Dry-run must remain non-switching and non-service-starting while still validating `docker compose config --quiet` and
  showing the real command sequence.
- Release helpers must not shell-source `.env`; use Docker Compose's env parsing for service configuration and read only
  the specific local values needed for health-check URLs without executing the file.
- Compose release must gracefully tolerate missing or inactive legacy systemd services but should try to stop them before
  binding the production ports.
- Keep `.trellis/spec/`, `.trellis/workflow.md`, and `.trellis/scripts/` source-controlled; keep `.trellis/tasks/` and
  `.trellis/workspace/` out of public tracking.

#### 4. Validation & Error Matrix

- Real token/private key in a tracked or newly added file -> remove it and rotate the secret before publishing.
- Untracked `.env.bak-*` appears in `git status` -> add/verify ignore coverage without reading or modifying the backup
  file content.
- `just release-dry-run` starts/stops services or builds images -> fix immediately; dry-run is for safe planning.
- `just release` fails because old systemd services still occupy 29280/29281 -> ensure the helper stops legacy services or
  clearly reports the port-binding failure.
- `.trellis/tasks/` appears in `git ls-files` -> remove it from the index without deleting the local task context.

#### 5. Good/Base/Bad Cases

- Good: `just release-dry-run` validates Compose config and prints the planned `docker compose up -d --build --remove-orphans` flow without side effects.
- Good: `just release` stops legacy services, recreates Compose services, passes backend and web `/api/healthz` checks, and leaves volumes intact.
- Base: `.env.dev.example` uses local service ports and mock providers while allowing contributors to opt into real
  providers by setting their own untracked env file.
- Bad: release script creates tar snapshots, flips a `.release/current` symlink, or restarts `productflow-*.service` after
  Compose has become the production runtime.

#### 6. Tests Required

- Run `bash -n scripts/release.sh` after shell helper edits.
- Run `just release-dry-run` or at minimum `DRY_RUN=1 bash scripts/release.sh` after release helper edits.
- Run `docker compose config --quiet` after Compose/env edits.
- For full release validation when practical, run `just release` and smoke backend `/healthz`, web `/healthz`, and web
  `/api/healthz`.
- Run `git diff --check` and `git diff --cached --check` before committing release hygiene changes.
- Run a high-confidence secret pattern scan over tracked and newly added files, excluding lockfiles if needed for noise.
- Confirm referenced files and `just` commands exist when README or docs are updated.

#### 7. Wrong vs Correct

Wrong:

```bash
systemctl --user restart productflow-backend.service productflow-worker.service productflow-web.service
```

Correct:

```bash
systemctl --user stop productflow-backend.service productflow-worker.service productflow-web.service || true
docker compose up -d --build --remove-orphans
```

---

## Required Patterns

### Keep provider code behind infrastructure factories

Existing provider selection is centralized in:

- `backend/src/productflow_backend/infrastructure/text/factory.py`
- `backend/src/productflow_backend/infrastructure/image/factory.py`

Routes and use cases call provider interfaces/factories, not concrete SDK classes directly. If adding providers, update the
factory, config definitions, tests, and settings UI types together.

Workflow execution has an additional explicit dependency seam in
`application/product_workflow_dependencies.py`. Default workflow execution dependencies resolve providers directly through
the infrastructure provider factories. Tests and future composition code that need fake providers should pass a
`WorkflowExecutionDependencies` instance directly rather than patching the `product_workflows.py` facade.

#### Scenario: Workflow execution dependency seams

##### 1. Scope / Trigger
- Trigger: editing workflow execution provider or renderer construction.

##### 2. Signatures
- `WorkflowExecutionDependencies(text_provider_resolver, image_provider_resolver, poster_renderer_factory)`.
- `run_product_workflow(..., dependencies=None)`, `execute_product_workflow_run(..., dependencies=None)`, and internal
  `_execute_node(..., dependencies=None)` accept this seam without changing API/worker call sites.

##### 3. Contracts
- `None` uses default resolvers that call the infrastructure text/image provider factories.
- The `product_workflows.py` facade exports public workflow use cases for route/worker imports only; it must not expose
  provider factory helpers or private `_...` execution helpers as test seams.
- Custom dependencies may be passed by focused tests or future composition code; they must return provider interface
  instances, not concrete SDK payloads.

##### 4. Validation & Error Matrix
- Resolver/provider failure -> existing workflow failure handling persists the run/node failure reason.
- Missing image provider for generated mode -> remains a runtime execution failure, not a schema/API change.

##### 5. Good/Base/Bad Cases
- Good: a focused test injects fake providers through `WorkflowExecutionDependencies`.
- Base: route/worker code calls public workflow use cases with `dependencies=None`, and execution resolves providers via
  the infrastructure factories.
- Bad: tests monkeypatch `product_workflows.get_image_provider`, `product_workflows.get_text_provider`, or
  `product_workflows._execute_node`.
- Bad: workflow execution imports a concrete provider SDK class.

##### 6. Tests Required
- Keep provider/workflow regression tests passing after resolver changes.
- Add a focused injection test when changing resolver behavior itself.

##### 7. Wrong vs Correct
Wrong:

```python
provider = OpenAIResponsesImageProvider()
```

Correct:

```python
provider = dependencies.image_provider()
```

Wrong:

```python
monkeypatch.setattr("productflow_backend.application.product_workflows.get_image_provider", fake_factory)
```

Correct:

```python
dependencies = WorkflowExecutionDependencies(image_provider_resolver=fake_factory)
run_product_workflow(session, product_id=product.id, dependencies=dependencies)
```

### Images API response format compatibility

- Build `response_format` from the effective request model, including per-call model overrides.
- Models whose API ID starts with `gpt-image-` return base64 images by default and must omit `response_format` in both
  generation and edit requests. Keep `response_format="b64_json"` for legacy DALL-E and other compatible model IDs.
- Regression tests must exercise generation and editing, configured defaults, overrides in both directions, and the
  image-session path. Do not remove legacy base64 requests globally when fixing GPT Image compatibility.
- Provider reference: https://docs.apiyi.com/api-capabilities/gpt-image-2/vs-gpt-image-2-6-all

### Validate inputs at the correct boundary

- FastAPI `Query` constraints are used for list pagination in `presentation/routes/products.py`.
- Upload MIME/size/pixel validation is centralized in `presentation/upload_validation.py`.
- Business text/price normalization lives in `application/use_cases.py` helpers such as `_normalize_required_text(...)` and
  `_normalize_price(...)`.
- Runtime settings normalization lives in `backend/src/productflow_backend/config.py`.

Do not duplicate these checks in multiple pages/routes.

### Scenario: Provider-neutral image generation size contract

#### 1. Scope / Trigger

- Trigger: editing continuous image sessions, workflow `image_generation` node config, runtime image settings, provider image payloads, or frontend image-size controls.
- Applies to the shared `WIDTHxHEIGHT` image-size contract across `ImageChatPage`, workflow Inspector, API schemas, runtime settings, workflow execution, and image providers.

#### 2. Signatures

- Backend canonical normalizer: `normalize_image_generation_size(value: str, *, max_dimension: int | None = None) -> str`.
- Runtime max single-edge setting: `image_generation_max_dimension`.
- Public runtime config API: `GET /api/settings/runtime` returns `image_generation_max_dimension`.
- Built-in presets are application UI constants; runtime settings expose only the max single-edge limit, not a
  user-facing allowed-size preset list.
- Continuous image API request field: `GenerateImageSessionRoundRequest.size`.
- Workflow image node config field: `config_json.size` for nodes with `kind == "image_generation"`.
- Frontend shared picker: `ImageSizePicker` emits normalized lowercase `WIDTHxHEIGHT` strings.

#### 3. Contracts

- Store and pass image size as a provider-neutral lowercase `WIDTHxHEIGHT` string, for example `1024x1024` or `3840x2160`.
- Each side is calibrated to the nearest provider-safe 16-pixel multiple before provider dispatch, for example `1500x800`
  becomes `1504x800`.
- Preset buttons are built-in ratio/tier shortcuts filtered by the runtime max single-edge setting; they are not a backend
  allowlist and are not loaded as arbitrary database-configured options.
- `normalize_image_generation_size(...)` must use `get_runtime_settings().image_generation_max_dimension` unless a focused
  caller/test passes an explicit `max_dimension`.
- Custom dimensions must be validated and calibrated by the backend before provider calls, not only by frontend controls.
- Continuous image generation and workflow image-generation nodes must use the same backend normalizer so their accepted/rejected sizes do not drift.
- Provider adapters should receive the normalized string unchanged unless a provider-specific adapter explicitly documents a conversion.

#### 4. Validation & Error Matrix

- Bad syntax such as `1024`, `1024*1024`, or missing dimensions -> request/config validation error.
- Non-positive dimensions such as `0x1024` or `1024x-1` -> request/config validation error.
- Dimensions above the project safety bounds -> normalize to a safe calibrated `WIDTHxHEIGHT` before provider dispatch.
- Dimensions that are not divisible by 16 -> normalize to the nearest safe 16-pixel multiple before provider dispatch.
- Uppercase separators/digits such as `3840X2160` -> normalize to lowercase `3840x2160`.
- `image_generation_max_dimension < 512` or `> 8192` -> settings validation error.
- Invalid runtime default image dimensions -> settings validation error instead of silently publishing broken provider defaults.

#### 5. Good/Base/Bad Cases

- Good: `3840x2160` entered in the workflow Inspector is saved as `3840x2160` and reaches the image provider as `image_size="3840x2160"`.
- Base: built-in presets such as `1024x1024`, `2048x2048`, and `3840x3840` render as picker buttons and submit the same canonical string.
- Bad: continuous image sessions accept custom sizes while workflow nodes only apply a loose string normalizer.
- Bad: frontend checks dimensions but backend forwards an oversized custom value to the provider.

#### 6. Tests Required

- Continuous image API tests must cover accepted custom dimensions, rejected malformed/non-positive dimensions, and
  oversized dimensions being stored as calibrated safe output sizes.
- Workflow DAG/API tests must cover node create/update normalization, invalid config rejection, provider input receiving
  the configured custom size, and oversized config being persisted as calibrated safe output size.
- Runtime settings tests must cover invalid default image dimensions and `image_generation_max_dimension` validation when
  validation behavior changes.
- Frontend helper/component tests should cover preset parsing, duplicate normalization, and custom-size round-tripping when picker logic changes.

#### 7. Wrong vs Correct

Wrong:

```python
# Workflow accepts any parseable size while continuous image uses different validation.
size = normalize_image_size(config_json.get("size", "1024x1024"))
```

Correct:

```python
# All image generation entry points share the same safety bounds and canonical form.
size = normalize_image_generation_size(config_json.get("size", "1024x1024"))
```

Wrong:

```tsx
<input value={draft.size} onChange={(event) => onChange({ size: event.target.value })} />
```

Correct:

```tsx
<ImageSizePicker value={draft.size} onChange={(size) => onChange({ size })} presets={imageSizePresets} />
```

### Preserve workflow-level tests

`backend/tests/test_*.py` is the backend regression suite and is split by behavior area. It covers:

- Auth/session behavior.
- Settings API persistence and validation.
- Typed business error and legacy `ValueError` HTTP mapping.
- SQLAlchemy enum value storage.
- End-to-end product/copy/poster workflow.
- Reference image upload/deletion.
- Continuous image-session behavior.
- Alembic upgrade path.
- OpenAI Responses image provider parsing behavior.

When changing product, copy, poster, settings, upload, image-session, provider, or migration behavior, add or update tests
in the matching topic file. Keep cross-cutting builders and polling/login helpers in `backend/tests/helpers.py` rather
than reintroducing a giant all-purpose test module.

When extracting workflow graph business rules, add at least one DB-free unit test for the domain rule in addition to any
API/integration regression. The application/query layer should own SQLAlchemy artifact existence checks; the domain rule
should own pure graph decisions.

### Keep storage safe

Use `LocalStorage` from `backend/src/productflow_backend/infrastructure/storage.py` for storage paths. It resolves relative
paths under the configured root and rejects absolute/path-traversal paths. Do not build download paths manually in routes.

### Keep durable async task semantics idempotent

Durable task creation and workers are designed to avoid duplicate active work and duplicate execution:

- Queue send failures are handled by application submit use cases before returning 503:
  `submit_product_workflow_run(...)` and `submit_image_session_generation_task(...)`.
- Dramatiq actors use `max_retries=0`; application code owns retry state.
- Product workflow runs follow the same durable-delivery rule with `recover_unfinished_workflow_runs(...)`: the
  `workflow_runs` / `workflow_node_runs` tables are authoritative, Dramatiq is only delivery, and duplicate messages must
  no-op for terminal or currently-running runs.
- Continuous image-session generation follows the same durable-delivery rule with
  `image_session_generation_tasks` and `recover_unfinished_image_session_generation_tasks(...)`: `POST
  /api/image-sessions/{id}/generate` creates a queued DB task and returns `202`; worker execution creates the existing
  `image_session_rounds` / `image_session_assets` rows on success, and duplicate terminal messages must no-op.

Preserve these semantics when editing durable task code.

### Scenario: Durable generation task contract

> **Extension recovery boundary:** The queued/stale-running recovery assertions below describe the legacy delivery
> contract. Extension recovery requires dispatch intent, original request reconciliation, and conserved quotas. For
> disaster-restored snapshots, even queued/undispatched rows may already have incurred charges after the restore point;
> isolate and reconcile them before any redelivery that could invoke a model. Update these tests with the ledger-aware
> implementation rather than treating an old queued state or provider lookup miss as proof of no dispatch.

#### 1. Scope / Trigger

- Trigger: adding or changing any database-durable async path that creates provider-backed image/copy/poster generation
  work, enqueues a Dramatiq message, runs in a worker, or exposes queued/running/failed state through a status API.
- Existing members are intentionally separate business models:
  - `WorkflowRun` plus `WorkflowNodeRun` for product workflow generation;
  - `ImageSessionGenerationTask` for continuous image-session generation.
- New work must extend or reference `domain/durable_generation_tasks.py` before adding a third state machine.

#### 2. Signatures

- Contract home: `domain/durable_generation_tasks.py`.
- Shared enqueue boundary: `application/queue_submission.py::enqueue_or_mark_failed(...)`.
- Shared capacity gates:
  - durable submit compatibility lock: `application/admission.py::ensure_generation_capacity(...)`;
  - worker-time running cap: `application/admission.py::generation_running_capacity_available(...)`.
- Current contracts:
  - `WORKFLOW_RUN_GENERATION_TASK_CONTRACT`;
  - `IMAGE_SESSION_GENERATION_TASK_CONTRACT`.
- Current worker actors:
  - `workers.run_product_workflow_run(workflow_run_id: str)`;
  - `workers.run_image_session_generation_task(task_id: str)`.

#### 3. Contracts

- Database rows are authoritative task state. Redis/Dramatiq messages are delivery attempts only.
- A submit use case must create or reuse a durable row before enqueueing. If queue send fails after a durable row exists,
  it must mark the row failed through the owning model's failure transition and raise `QueueUnavailableError` with
  `任务队列暂不可用，请稍后重试`.
- Worker actors must set `max_retries=0`; application execution entrypoints own failure persistence, retry counters,
  partial-result handling, and terminal status.
- Duplicate delivery must be idempotent. Terminal rows and already-running work must return without provider calls or new
  artifacts. Claims must use a database conditional update for queued-to-running transitions where the model has an
  explicit queued state.
- Startup recovery must inspect durable DB state and then resend delivery only for queued or stale-running work according
  to the model's recovery rules. API startup must not reset recent running work owned by another worker.
- Generation capacity must use the shared DB-backed worker gate. Submit paths create or reuse durable queued rows, while
  worker claims count running work before entering provider execution.
- Status snapshots and queue metadata must be derived from durable rows and first-class result rows, not Redis queue
  length or in-process memory.
- Manual cancel must be an owning durable-row transition. Terminal statuses include `cancelled` where the business model
  supports user cancellation, and duplicate delivery for cancelled rows must no-op.

#### 4. Validation & Error Matrix

- Queue send failure after durable creation -> durable row is failed, API returns `503`,
  `{"detail": "任务队列暂不可用，请稍后重试"}`.
- Running capacity reached during worker claim -> durable row stays queued, provider is not called, and delivery is
  retried later.
- Duplicate terminal message, including cancelled rows -> no-op, no provider call, no new artifact row.
- Duplicate currently-running message -> no-op; stale-running recovery handles old abandoned work separately.
- Recovery sees queued work -> resend delivery without changing product/provider semantics.
- Recovery sees stale running work -> apply the owning model's documented stale behavior, then resend or fail as
  appropriate.
- Status refresh during generation -> response is reconstructed from DB rows and remains stable across process restart.

#### 5. Good/Base/Bad Cases

- Good: a new generation path adds a `DurableGenerationTaskContract`, persists a durable queued row, submits through
  `enqueue_or_mark_failed(...)`, gates provider execution with `generation_running_capacity_available(...)`, uses a
  `max_retries=0` actor, and adds recovery/status tests.
- Good: workflow run and image-session task continue using separate tables and business statuses while sharing contract
  constants and checks for active/running/queued semantics.
- Base: workflow run has no task-level queued status; its active run is `running`, while node runs hold queued/running
  execution state. The contract should describe that split instead of forcing a new workflow table shape.
- Bad: adding a new async provider path that creates a row and calls `actor.send(...)` directly without
  `enqueue_or_mark_failed(...)`.
- Bad: enabling Dramatiq automatic retries for generation actors.
- Bad: computing queue position or active state from Redis delivery metadata.

#### 6. Tests Required

- Contract regression: current generation contracts still name distinct durable models and expose the expected
  active/queued/running/terminal statuses.
- Worker actor regression: all generation actors satisfy `actor.options["max_retries"] == 0` through the shared contract
  assertion.
- Enqueue failure regression: mocked send failure marks the durable row failed and returns stable `503`.
- Duplicate-message regression for each durable model: terminal and currently-running messages do not call providers.
- Recovery regression for each durable model: queued/stale-running DB rows are handled according to the owning recovery
  rules.
- Admission/status regression: active/running counts and status snapshots are derived from durable DB rows.

#### 7. Wrong vs Correct

Wrong:

```python
session.add(task)
session.commit()
run_new_generation_actor.send(task.id)
```

This can strand a queued durable row if Redis/Dramatiq delivery fails.

Correct:

```python
task = create_durable_generation_task(session, ...)
enqueue_or_mark_failed(
    task.id,
    enqueue=enqueue_generation_task,
    mark_failed=lambda task_id, reason: mark_generation_task_enqueue_failed(session, task_id, reason),
)
```

Wrong:

```python
@dramatiq.actor(max_retries=3)
def run_generation_task(task_id: str) -> None:
    execute_generation_task(task_id)
```

Correct:

```python
@dramatiq.actor(max_retries=0)
def run_generation_task(task_id: str) -> None:
    execute_generation_task(task_id)
```

#### Scenario: Durable async continuous image-session generation

> **Legacy conflict / extension target:** The automatic retry, same-task manual retry, stale-running reset, immediate cancellation with omitted returned assets, and shared-demo ownership exemptions in this scenario are legacy contracts and test expectations. For the extension, possible dispatch becomes unknown and is never silently resent; explicit model retries create new Operation/Attempt records, while unchanged zero-model local recovery retains original-attempt provenance. SDK/provider/application/queue retries cannot create hidden paid calls. Production cancellation archives late results under the original attempt without further composition; named accounts and resource authorization also cover legacy entry points. See PRD Sections 2, 3.9, and 4.3 and Technical Design Sections 4 and 6.

##### 1. Scope / Trigger

- Trigger: changing `POST /api/image-sessions/{id}/generate`, image-session generation persistence, queue delivery,
  worker execution, admission control, or frontend session detail polling for continuous image chat.
- Continuous image-session generation is a cross-layer async workflow: DB task rows are authoritative, Dramatiq/Redis is
  delivery only, and the frontend reconstructs queued/running/failed state from `ImageSessionDetail`.

##### 2. Signatures

- API: `POST /api/image-sessions/{image_session_id}/generate` returns `202 Accepted` after validation, durable task
  creation, and enqueue; it must not wait for an image provider call.
- API: `POST /api/image-sessions/{image_session_id}/generation-tasks/{task_id}/retry` returns `202 Accepted` after
  resetting a failed retryable task to `queued` and enqueueing the same durable task ID.
- API: `POST /api/image-sessions/{image_session_id}/generation-tasks/{task_id}/cancel` returns
  `ImageSessionDetailResponse` after durably marking an active task `cancelled`.
- DB: `image_session_generation_tasks` stores `session_id`, `prompt`, `size`, `base_asset_id`,
  `selected_reference_asset_ids`, `generation_count`, `status`, progress fields (`completed_candidates`,
  `active_candidate_index`, `progress_phase`, `progress_updated_at`, provider response id/status and metadata),
  `failure_reason`, `result_generation_group_id`, `attempts`, `is_retryable`, `created_at`, `started_at`, and
  `finished_at`.
- Queue: `enqueue_image_session_generation_task(task_id: str)` sends the durable task ID; the worker actor consumes only
  the ID and reloads state from the database.
- Recovery: `recover_unfinished_image_session_generation_tasks(reset_stale_running: bool = False, stale_running_after:
  timedelta | None = None)` re-sends queued tasks and, only for worker startup, handles stale running tasks by comparing
  the cutoff against `progress_updated_at`, falling back to `started_at` for older rows. Tasks with no completed
  candidates may be reset to queued; stale partial-success tasks are marked failed without retry.
- Response DTO: `ImageSessionDetailResponse.generation_tasks` exposes task summaries so route entry/refresh can show
  active or failed generation work without a separate orchestration endpoint.
- Each generation task summary exposes `attempts`, `is_retryable`, and `is_cancelable` so the frontend can decide whether
  to render manual retry/cancel affordances.
- Each generation task summary includes global queue fields: `queue_active_count`, `queue_running_count`,
  `queue_queued_count`, `queue_max_concurrent_tasks`, `queued_ahead_count`, and `queue_position`.
- Queue overview API: `GET /api/generation-queue` returns `active_count`, `running_count`, `queued_count`, and
  `max_concurrent_tasks` for product/workflow surfaces that do not own a specific image-session task.

##### 3. Contracts

- Task statuses are `queued`, `running`, `succeeded`, `failed`, and `cancelled`; queued/running rows count toward queue
  overview metadata, while only running rows consume `generation_max_concurrent_tasks` provider capacity.
- The continuous image-session worker actor keeps an internal failsafe Dramatiq `time_limit` via
  `image_session_worker_failsafe_time_limit_minutes`. User-facing stale behavior must be driven by progress heartbeat
  idle recovery, not a hard total task timeout.
- Queue position is computed from durable queued image-session generation task rows ordered by `created_at`. Running tasks
  have no queue position and should be displayed as front-of-queue work.
- New image-session generation work must create a queued task even when all running slots are occupied. The worker claim
  count must be based on running DB rows, not an in-process slot, because API/worker processes may be replicated.
- Queue enqueue failure after task creation must mark the task `failed` (or otherwise return a stable `503`) before the
  route responds; do not strand a queued row that no worker can consume.
- Worker claim must be atomic at the database boundary: update `queued -> running` with a status condition and no-op when
  the row is terminal, already running, or already claimed by another worker.
- Manual cancel sets active tasks to `cancelled`, `failure_reason = "已取消"`, `progress_phase = "cancelled"`, and
  `is_retryable=false`. Workers must re-check durable cancellation around provider execution/save boundaries and return
  without creating new rounds when cancellation is observed.
> **Legacy retry rule below:** do not carry this automatic paid-retry contract into extension implementations or acceptance tests. Safe delivery/local-work retries must not re-enter model generation.

- Worker failures must retry through application state, not Dramatiq actor retries. Keep
  `run_image_session_generation_task(max_retries=0)`, increment `attempts` on each worker claim, reset the same task to
  `queued` while the finite cap has not been reached, and leave terminal failed tasks `is_retryable=true`.
- On success, create normal `image_session_assets` and `image_session_rounds` rows. Multi-candidate generations still use
  one `generation_group_id` with one round/asset per candidate.
- Retrying a partial-success generation task must preserve `completed_candidates` and `result_generation_group_id`, then
  continue from `completed_candidates + 1`. Already saved candidates must not be regenerated.
- On provider/storage/runtime failure, mark the task `failed` with a generic safe user-facing reason such as
  `图片生成失败，请稍后重试`; never expose provider exception text, API keys, base URLs, local paths, request bodies, or
  tracebacks in API responses.
- The shared public demo workspace stays shared; do not add user/tenant ownership checks as part of this async path unless
  a separate product requirement introduces isolation.

##### 4. Validation & Error Matrix

- Missing session -> `404`, `连续生图会话不存在`.
- Invalid `generation_count`, `size`, `base_asset_id`, or selected references -> existing image-session validation errors;
  do not enqueue a task.
- Running generation cap reached by another task -> the new task is still accepted as `queued`; when consumed, the worker
  leaves it queued, records a waiting-for-capacity progress phase, and schedules delayed delivery retry.
- Redis/Dramatiq send failure after DB task creation -> mark task `failed`, then return `503`,
  `任务队列暂不可用，请稍后重试`.
- Manual retry for a non-`failed` task -> `400`, `只有失败的生成任务可以重试`.
- Manual retry for `failed` task with `is_retryable=false` -> `400`, `该生成任务不可重试`.
- Manual cancel for an active task -> task becomes `cancelled`, exits the active queue, and duplicate worker delivery
  no-ops.
- Manual cancel for a terminal succeeded/failed task -> `400`, `已结束的生成任务不能取消`.
- Redis/Dramatiq send failure after manual retry reset -> return `503`, `任务队列暂不可用，请稍后重试`, and keep the task
  failed + retryable so the user can try again.
- Duplicate Redis message for `succeeded`, `failed`, `cancelled`, or `running` task -> no provider call and no new
  round/asset rows.
- API restart with queued retryable task -> re-enqueue without changing task semantics.
> **Legacy recovery expectation below:** heartbeat age and absence of saved candidates do not prove an external request was never sent. Recover only provably undispatched work; retain uncertain attempts and costs as unknown.

- Worker restart with stale running retryable task and no completed candidates -> reset to queued and re-enqueue.
- Worker restart with stale running partial-success task -> mark failed without retry, keeping the partial
  `result_generation_group_id`.

##### 5. Good/Base/Bad Cases

- Good: user requests 3 candidates; route returns `202` quickly with a queued task, worker later creates 3 generated
  assets and 3 rounds sharing one `generation_group_id`, then marks the task `succeeded`.
- Good: browser refreshes during generation; `ImageSessionDetail.generation_tasks` still contains queued/running task
  state, so the frontend resumes polling and disables duplicate submission.
- Base: worker receives the same task ID after the task already succeeded; it exits without calling the provider.
- Bad: route calls `generate_image_session_round(...)` or an image provider directly and holds the HTTP request open.
- Bad: active generation cap checks a process-local counter or lock; replicated API processes can exceed the public demo
  cap.
- Bad: worker claim reads a queued task, mutates the ORM object, and commits without a conditional `WHERE status='queued'`;
  concurrent duplicate messages may both call the provider.

##### 6. Tests Required

- Route/API test: submit generation returns `202`, persists a queued task, exposes the task in session detail, and does not
  call the provider synchronously.
- Enqueue failure test: mocked send failure marks the task failed and returns stable `503`.
- Worker success test: executing a queued task creates expected assets/rounds and marks the task succeeded with
  `result_generation_group_id`.
- Worker failure test: provider exception marks the task failed with a generic reason and does not leak the raw exception.
> **Legacy tests below:** automatic retries and same-task resets are existing regression expectations, not extension acceptance. Update them with the corresponding no-hidden-call, new-attempt, unknown-result, and cancellation implementation.

- Auto retry cap test: repeated provider failure calls the provider only up to the finite application cap, then leaves the
  task failed + retryable with `attempts` exposed in detail/status responses.
- Manual retry route tests: failed retryable task resets to queued and enqueues; non-failed retry returns `400`; enqueue
  failure returns `503` and keeps the task retryable.
- Manual cancel route tests: active task becomes `cancelled`, terminal task cancellation is rejected, and duplicate worker
  delivery no-ops for cancelled tasks.
- Partial retry test: a task that saved candidate 1/2 and failed resumes at candidate 2 without duplicating candidate 1 or
  changing the existing `generation_group_id`.
- Duplicate/no-op tests: terminal and already-running task messages do not call the provider or create extra rounds.
- Recovery tests: queued tasks are re-sent; stale running recovery uses `progress_updated_at`, falls back to `started_at`,
  and fails stale partial-success tasks instead of retrying them.
- Admission test: submit accepts queued work while running capacity is full, worker capacity checks count running durable
  work, and queue metadata still reports active queued/running counts.
- Queue metadata test: queued task responses expose `queued_ahead_count` / `queue_position`, and the global overview
  counts active queued/running durable work.
- Frontend gate: update DTO types and run `just web-build` when `ImageSessionDetail` or task status rendering changes.

##### 7. Wrong vs Correct

Wrong:

```python
# HTTP request waits for provider and uses process-local admission state.
with admit_synchronous_generation(session):
    detail = generate_image_session_round(session, image_session_id, request.prompt, storage=storage)
```

Correct:

```python
# HTTP request persists durable work, sends delivery message, and returns 202.
task = create_image_session_generation_task(session, image_session_id, request)
enqueue_image_session_generation_task(task.id)
```

Wrong:

```python
task = session.get(ImageSessionGenerationTask, task_id)
if task.status == JobStatus.QUEUED:
    task.status = JobStatus.RUNNING
    session.commit()
    call_provider()
```

Correct:

```python
updated = session.execute(
    update(ImageSessionGenerationTask)
    .where(
        ImageSessionGenerationTask.id == task_id,
        ImageSessionGenerationTask.status == JobStatus.QUEUED,
    )
    .values(status=JobStatus.RUNNING, started_at=now_utc())
)
if updated.rowcount != 1:
    return
call_provider()
```

Wrong:

```python
@dramatiq.actor(max_retries=3)
def run_image_session_generation_task(task_id: str) -> None:
    execute_image_session_generation_task(task_id)
```

Correct:

```python
@dramatiq.actor(max_retries=0)
def run_image_session_generation_task(task_id: str) -> None:
    execute_image_session_generation_task(task_id)
```

## Testing Requirements

Run at least these checks for backend changes:

```bash
uv run --directory backend ruff check .
just backend-test
```

For schema changes, also run:

```bash
just backend-migrate
```

and add/update an Alembic revision under `backend/alembic/versions/`. Existing tests should continue to cover both
`Base.metadata.create_all(...)` fixtures and Alembic upgrade behavior.

---

## Forbidden Patterns

- Business logic in FastAPI route handlers beyond input adaptation, use-case calls, error mapping, and serialization.
- Provider-specific SDK calls from `presentation/` modules.
- New database columns or tables without an Alembic migration.
- Enum string changes without updating frontend types and regression tests.
- Unbounded list endpoints that load all rows for UI lists.
- Raw filesystem access for user-controlled storage paths; go through `LocalStorage.resolve(...)`.
- Broad `except Exception` that hides failures. Existing broad catches are narrow boundary cases:
  durable queue enqueue failure inside application submit helpers, config table bootstrap tolerance in `config.py`, and
  provider error classification in application/provider code.
- Committing generated storage, cache directories, `.env`, build output, or pycache files.

---

## Review Checklist

When reviewing backend changes, check:

- Does the change respect the presentation/application/domain/infrastructure layer split?
- Are Pydantic DTOs in `presentation/schemas/` and frontend types in `web/src/lib/types.ts` still aligned?
- Are database model changes mirrored by Alembic migrations and tests?
- Are enum values stored/returned as stable lowercase string values?
- Are uploads, image sizes, and storage paths still bounded?
- Are durable workflow/image-session task failures persisted and visible through their owning status/detail APIs?
- Are provider secrets hidden from API responses and logs?
- Do `uv run --directory backend ruff check .` and `just backend-test` pass?

## M1-01 isolated validation gate

### 1. Scope
All M1-01 validation uses `scripts/verify_m101.py`; native PostgreSQL and Redis are dedicated per-run instances. No real model or current business stack is permitted. The equivalent Ruff/pytest commands run through this runner instead of loading `.env.dev`.
### 2. Commands
`print-plan`, `prepare`, `preflight`, `migrate --stage expand|data|head`, `seed`, `run --mode unit|integration -- ...`, `start-app`, `start-web`, `pause-delivery`, `resume-delivery`, `stop` require `--run-id`. `add-fixture --fixture-suffix <name>` adds an empty registered database and never adopts or resets an existing one.
### 3. Contracts
Verify PG system identity/role/database comments, Redis marker/directory/namespace, storage paths, loopback proxy and unique cookie. Python guards install before business imports; libpq needs a separate native-driver guard. Unit mode prohibits real sockets; integration requires registered PG/Redis. Evidence, caches and build output stay under the run root.
Integration pytest must own its worker lifecycle: pause browser publisher/scanner/workers first. The runner refuses the suite while those owned processes remain live. Verify actual Python child PID plus creation time; a Windows venv launcher PID is insufficient. Exited Windows process handles are not live workers.
### 4. Error matrix
Wrong endpoint, marker, cookie or escaped path: fail before connection/write. Unregistered existing database: refuse adoption. Reused PID: refuse termination. Enum/data migration failure: retain database and evidence, stop the run, never boot an unverified old worker.
### 5. Cases
Good: duplicate Redis delivery is tested with two independent workers and an independent transport journal. Base: inline API tests prove contracts only. Bad: browser publisher drains a deliberately queued fault fixture, or SQLite results are reported as PG/Redis acceptance.
### 6. Required evidence
Run complete backend tests, isolated integration tests, SDK fake-transport fault matrix, frontend lint/test/build and browser checks. Preserve failed logs and final rerun results. Inspect `commands.jsonl`, `stub-requests.jsonl`, DB counts/hashes and screenshots separately. Historical real model evidence remains unchanged and cannot establish garment fidelity.
### 7. Wrong versus correct
Wrong: `Start-ProductFlow.ps1` for documentation/verification. Correct: preflight a distinct run, migrate only registered databases, stop only owned verified PIDs and retain all data. Never treat a new synthetic fixture as resetting an existing experiment.
