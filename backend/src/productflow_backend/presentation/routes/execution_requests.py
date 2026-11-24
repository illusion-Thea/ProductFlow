from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from productflow_backend.application.execution.queries import operation_detail, operation_response
from productflow_backend.application.execution.recovery import act_on_operation
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionArtifact,
    ExecutionOperation,
    RequestIdempotency,
)
from productflow_backend.infrastructure.db.execution_repository import idempotent
from productflow_backend.infrastructure.db.models import (
    ImageSessionGenerationTask,
    ProductWorkflow,
    WorkflowNodeRun,
    WorkflowRun,
)
from productflow_backend.infrastructure.logging import log_execution_event
from productflow_backend.infrastructure.storage import LocalStorage
from productflow_backend.presentation.deps import get_session, require_prototype_admin
from productflow_backend.presentation.schemas.prototype_tasks import OperationActionRequest

router = APIRouter(prefix="/api", tags=["execution"])


def execution_write(request: Request, session: Session, route: str, payload: dict, action, prepare=None):
    actor = require_prototype_admin(request)
    key = request.headers.get("Idempotency-Key")
    try:
        key = str(UUID(key))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "Idempotency-Key UUID required") from None
    def prepared():
        session.info["execution_asset_manifest"] = prepare()
    status, response = idempotent(
        session, actor=actor, route=route, key=key, payload=payload, action=lambda: action(actor),
        prepare=prepared if prepare else None,
    )
    log_execution_event("intent_response", resource_id=response.get("operation_id") or response.get("id"))
    return JSONResponse(response, status_code=status)


def require_action_payload(payload: OperationActionRequest | None) -> dict:
    if payload is None:
        raise HTTPException(422, "expected_row_version required")
    return payload.model_dump(mode="json")


@router.get("/execution-requests/{key}")
def execution_request(
    key: UUID, root_kind: str, root_id: str, action: str, request: Request, session: Session = Depends(get_session)
):
    actor = require_prototype_admin(request)
    allowed = {
        "product": {"create-prototype", "run"},
        "image_session": {"generate"},
        "prototype": {"draft", "confirm", "generate", "probe", "text-preparations", "cancel"},
        "operation": {"retry", "resume-local", "end-local", "close-wait"},
        "image_task": {"retry", "cancel"},
        "workflow_run": {"retry", "cancel"},
    }
    if action not in allowed.get(root_kind, set()):
        raise HTTPException(422, "unsupported_execution_query_scope")
    row = session.scalar(
        select(RequestIdempotency).where(
            RequestIdempotency.actor_scope == actor,
            RequestIdempotency.route_scope == f"{root_kind}:{root_id}:{action}",
            RequestIdempotency.idempotency_key == str(key),
        )
    )
    if not row or row.response_json is None:
        return {"status": "not_found"}
    return {
        "status": "committed",
        "resource_id": row.resource_id,
        "response_status": row.response_status,
        "response": row.response_json,
    }


@router.get("/execution-operations/{operation_id}", dependencies=[Depends(require_prototype_admin)])
def execution_operation(operation_id: str, session: Session = Depends(get_session)):
    return operation_detail(session, operation_id)


@router.get("/execution-artifacts/{artifact_id}/content", dependencies=[Depends(require_prototype_admin)])
def execution_artifact(artifact_id: str, session: Session = Depends(get_session)):
    artifact = session.get(ExecutionArtifact, artifact_id)
    if not artifact or artifact.availability != "available":
        raise HTTPException(404, "execution_artifact_unavailable")
    path = LocalStorage().resolve(artifact.storage_path)
    if not path.is_file():
        raise HTTPException(404, "execution_artifact_bytes_unavailable")
    return FileResponse(path, media_type=artifact.mime_type)


@router.post("/image-sessions/{session_id}/generation-tasks/{task_id}/operations/{operation_id}/{action}")
def image_action(
    session_id: str,
    task_id: str,
    operation_id: str,
    action: str,
    payload: OperationActionRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    task = session.get(ImageSessionGenerationTask, task_id)
    operation = session.get(ExecutionOperation, operation_id)
    if not task or task.session_id != session_id or not operation or operation.image_session_task_id != task_id:
        raise HTTPException(404, "execution_resource_not_found")
    return legacy_operation_action(operation, action, payload, request, session)


@router.post("/products/{product_id}/workflow/runs/{run_id}/operations/{operation_id}/{action}")
def workflow_action(
    product_id: str,
    run_id: str,
    operation_id: str,
    action: str,
    payload: OperationActionRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    run = session.get(WorkflowRun, run_id)
    workflow = session.get(ProductWorkflow, run.workflow_id) if run else None
    operation = session.get(ExecutionOperation, operation_id)
    node = (
        session.get(WorkflowNodeRun, operation.workflow_node_run_id)
        if operation and operation.workflow_node_run_id
        else None
    )
    if not workflow or workflow.product_id != product_id or not node or node.workflow_run_id != run_id:
        raise HTTPException(404, "execution_resource_not_found")
    return legacy_operation_action(operation, action, payload, request, session)


def legacy_operation_action(operation, action, payload, request, session):
    if action not in {"close-wait", "resume-local", "end-local"}:
        raise HTTPException(422, "unsupported_execution_action")

    def apply(actor):
        updated = act_on_operation(session, operation.id, action, payload.model_dump(mode="json"), actor)
        return 200, operation_response(session, updated)

    return execution_write(
        request, session, f"operation:{operation.id}:{action}", payload.model_dump(mode="json"), apply
    )
