from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from productflow_backend.application import prototype
from productflow_backend.application.execution.queries import operation_detail, operation_response, task_detail
from productflow_backend.application.execution.recovery import act_on_operation, cancel_task
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionArtifact,
    ExecutionAttempt,
    ExecutionOperation,
)
from productflow_backend.infrastructure.db.execution_repository import idempotent
from productflow_backend.infrastructure.db.models import SourceAsset
from productflow_backend.infrastructure.db.prototype_models import ExperimentRun, ProductionTask
from productflow_backend.infrastructure.storage import LocalStorage
from productflow_backend.presentation.deps import get_session, require_prototype_admin
from productflow_backend.presentation.schemas.prototype_tasks import (
    ConfirmPlanRequest,
    CreatePrototypeRequest,
    DraftRequest,
    ExecutionAction,
    GenerateRequest,
    OperationActionRequest,
    ProbeRequest,
    RevisePlanRequest,
    TextPreparationRequest,
)

router = APIRouter(prefix="/api", tags=["prototype"])
Actor = Annotated[str, Depends(require_prototype_admin)]
Database = Annotated[Session, Depends(get_session)]
Key = Annotated[UUID, Header(alias="Idempotency-Key")]


def mutate(session, actor, route, key, payload, action):
    status, result = idempotent(
        session, actor=actor, route=route, key=str(key), payload=payload.model_dump(mode="json"), action=action
    )
    return JSONResponse(result, status_code=status)


@router.get("/prototype-experiments")
def experiments(actor: Actor, session: Database, product_id: str | None = None):
    assets = (
        set(session.scalars(select(SourceAsset.id).where(SourceAsset.product_id == product_id))) if product_id else None
    )
    return {
        "items": [
            {
                "id": row.id,
                "mode": row.mode,
                "status": row.status,
                "image_limit": row.image_limit,
                "source_asset_ids": [
                    identifier
                    for identifier in row.evidence_manifest_json.get("synthetic_assets", {})
                    if assets is None or identifier in assets
                ],
                "image_reserved_count": row.image_reserved_count,
                "quota_remaining": row.image_limit - row.image_reserved_count,
            }
            for row in session.scalars(select(ExperimentRun).where(ExperimentRun.mode == "synthetic"))
            if assets is None or assets.intersection(row.evidence_manifest_json.get("synthetic_assets", {}))
        ]
    }


@router.get("/products/{product_id}/prototype-tasks")
def tasks(
    product_id: str, actor: Actor, session: Database, cursor: str | None = None, limit: int = Query(20, ge=1, le=100)
):
    query = select(ProductionTask).where(ProductionTask.product_id == product_id)
    if cursor:
        query = query.where(ProductionTask.id > cursor)
    rows = list(session.scalars(query.order_by(ProductionTask.id).limit(limit + 1)))
    return {
        "items": [task_detail(session, row.id, lightweight=True) for row in rows[:limit]],
        "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
    }


@router.post("/products/{product_id}/prototype-tasks")
def create(product_id: str, payload: CreatePrototypeRequest, actor: Actor, session: Database, key: Key):
    def action():
        task = prototype.create_task(session, product_id, payload.model_dump(mode="json"), actor)
        return 201, task_detail(session, task.id)

    return mutate(session, actor, f"product:{product_id}:create-prototype", key, payload, action)


@router.get("/prototype-tasks/{task_id}")
def detail(task_id: str, actor: Actor, session: Database):
    return task_detail(session, task_id)


@router.get("/prototype-tasks/{task_id}/status")
def status(task_id: str, actor: Actor, session: Database):
    return task_detail(session, task_id, lightweight=True)


@router.patch("/prototype-tasks/{task_id}/draft")
def draft(task_id: str, payload: DraftRequest, actor: Actor, session: Database, key: Key):
    def action():
        prototype.update_draft(session, task_id, payload.model_dump(mode="json"))
        return 200, task_detail(session, task_id)

    return mutate(session, actor, f"prototype:{task_id}:draft", key, payload, action)


@router.post("/prototype-tasks/{task_id}/text-preparations")
def text_preparation(task_id: str, payload: TextPreparationRequest, actor: Actor, session: Database, key: Key):
    def action():
        operation = prototype.prepare_text(session, task_id, payload.model_dump(mode="json"), actor)
        return 202, operation_response(session, operation)

    return mutate(session, actor, f"prototype:{task_id}:text-preparations", key, payload, action)


@router.post("/prototype-tasks/{task_id}/plans/{plan_id}/revisions")
def revise(task_id: str, plan_id: str, payload: RevisePlanRequest, actor: Actor, session: Database, key: Key):
    def action():
        prototype.revise_plan(session, task_id, plan_id, payload.model_dump(mode="json"))
        return 201, task_detail(session, task_id)

    return mutate(session, actor, f"prototype:{task_id}:plan:{plan_id}:revisions", key, payload, action)


@router.post("/prototype-tasks/{task_id}/confirm")
def confirm(task_id: str, payload: ConfirmPlanRequest, actor: Actor, session: Database, key: Key):
    def action():
        prototype.confirm_plan(session, task_id, payload.model_dump(mode="json"), actor)
        return 200, task_detail(session, task_id)

    return mutate(session, actor, f"prototype:{task_id}:confirm", key, payload, action)


@router.post("/prototype-tasks/{task_id}/generate")
def generate(task_id: str, payload: GenerateRequest, actor: Actor, session: Database, key: Key):
    def action():
        operations = prototype.generate(session, task_id, payload.model_dump(mode="json"), actor)
        return 202, {
            "task_id": task_id,
            "operation_ids": [operation.id for operation in operations],
            "status_url": f"/api/prototype-tasks/{task_id}/status",
        }

    return mutate(session, actor, f"prototype:{task_id}:generate", key, payload, action)


@router.post("/prototype-tasks/{task_id}/probe")
def probe(task_id: str, payload: ProbeRequest, actor: Actor, session: Database, key: Key):
    def action():
        operation = prototype.generate(session, task_id, payload.model_dump(mode="json"), actor, probe=True)[0]
        return 202, operation_response(session, operation)

    return mutate(session, actor, f"prototype:{task_id}:probe", key, payload, action)


@router.post("/prototype-tasks/{task_id}/cancel")
def cancel(task_id: str, payload: OperationActionRequest, actor: Actor, session: Database, key: Key):
    def action():
        cancel_task(session, task_id, payload.model_dump(mode="json"), actor)
        return 200, task_detail(session, task_id)

    return mutate(session, actor, f"prototype:{task_id}:cancel", key, payload, action)


@router.get("/prototype-operations/{operation_id}")
def operation(operation_id: str, actor: Actor, session: Database):
    row = session.get(ExecutionOperation, operation_id)
    if not row or row.source_kind != "prototype":
        raise HTTPException(404, "prototype_operation_not_found")
    return operation_detail(session, operation_id)


@router.post("/prototype-operations/{operation_id}/{action_name}")
def operation_action(
    operation_id: str,
    action_name: ExecutionAction,
    payload: OperationActionRequest,
    actor: Actor,
    session: Database,
    key: Key,
):
    row = session.get(ExecutionOperation, operation_id)
    if not row or row.source_kind != "prototype":
        raise HTTPException(404, "prototype_operation_not_found")

    def action():
        updated = act_on_operation(session, operation_id, action_name, payload.model_dump(mode="json"), actor)
        return (202 if action_name == "retry" else 200), operation_response(session, updated)

    return mutate(session, actor, f"operation:{operation_id}:{action_name}", key, payload, action)


@router.get("/prototype-artifacts/{artifact_id}/content")
def artifact_content(artifact_id: str, actor: Actor, session: Database):
    artifact = session.get(ExecutionArtifact, artifact_id)
    if not artifact or artifact.availability != "available":
        raise HTTPException(404, "prototype_artifact_not_available")
    attempt = session.get(ExecutionAttempt, artifact.attempt_id)
    operation = session.get(ExecutionOperation, attempt.operation_id)
    if operation.source_kind != "prototype":
        raise HTTPException(404, "prototype_artifact_not_found")
    path = LocalStorage().resolve(artifact.storage_path)
    if not path.is_file():
        raise HTTPException(404, "prototype_artifact_bytes_not_available")
    return FileResponse(path, media_type=artifact.mime_type)
