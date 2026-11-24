from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import defer

from productflow_backend.domain.errors import NotFoundError
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionArtifact,
    ExecutionAttempt,
    ExecutionOperation,
    ExecutionStage,
    LocalRecoveryExecution,
    ModelCall,
)
from productflow_backend.infrastructure.db.models import (
    ImageSessionGenerationTask,
    ProductWorkflow,
    WorkflowNodeRun,
    WorkflowRun,
)
from productflow_backend.infrastructure.db.prototype_models import (
    ExperimentRun,
    PlanSnapshot,
    ProductionBatch,
    ProductionItem,
    ProductionTask,
    SlotPlan,
)


def operation_response(session, operation: ExecutionOperation, *, preloaded: tuple | None = None) -> dict:
    if preloaded is not None:
        attempt, recovery, root = preloaded
    else:
        attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == operation.id))
        recovery = session.scalar(
            select(LocalRecoveryExecution)
            .where(LocalRecoveryExecution.attempt_id == attempt.id)
            .order_by(LocalRecoveryExecution.created_at.desc(), LocalRecoveryExecution.recovery_sequence.desc())
            .limit(1)
        )
    if operation.prototype_task_id:
        root = preloaded[2] if preloaded else session.get(ProductionTask, operation.prototype_task_id)
        action_url = f"/api/prototype-operations/{operation.id}"
        retry_url = action_url + "/retry"
    elif operation.image_session_task_id:
        root = session.get(ImageSessionGenerationTask, operation.image_session_task_id)
        base = f"/api/image-sessions/{root.session_id}/generation-tasks/{root.id}"
        action_url, retry_url = f"{base}/operations/{operation.id}", base + "/retry"
    else:
        node = session.get(WorkflowNodeRun, operation.workflow_node_run_id)
        root = session.get(WorkflowRun, node.workflow_run_id)
        workflow = session.get(ProductWorkflow, root.workflow_id)
        base = f"/api/products/{workflow.product_id}/workflow/runs/{root.id}"
        action_url, retry_url = f"{base}/operations/{operation.id}", base + "/retry"
    return {
        "operation_id": operation.id,
        "attempt_id": attempt.id,
        "task_id": operation.prototype_task_id,
        "source_kind": operation.source_kind,
        "source_id": operation.prototype_task_id or operation.image_session_task_id or operation.workflow_node_run_id,
        "root_row_version": root.row_version,
        "row_version": operation.row_version,
        "state": operation.state,
        "purpose": operation.purpose,
        "kind": operation.kind,
        "action_url": action_url,
        "retry_url": retry_url,
        "wait_closed_at": operation.wait_closed_at.isoformat() if operation.wait_closed_at else None,
        "current_recovery": None
        if not recovery
        else {
            "id": recovery.id,
            "state": recovery.state,
            "deadline_at": recovery.deadline_at.isoformat(),
            "recovery_sequence": recovery.recovery_sequence,
            "last_error": recovery.last_error,
        },
        "status_url": f"/api/execution-operations/{operation.id}",
    }


def operation_detail(session, identifier: str) -> dict:
    operation = session.get(ExecutionOperation, identifier)
    if not operation:
        raise NotFoundError("execution_operation_not_found")
    result = operation_response(session, operation)
    attempt = session.get(ExecutionAttempt, result["attempt_id"])
    stages = list(session.scalars(select(ExecutionStage).where(ExecutionStage.attempt_id == attempt.id)))
    calls = list(session.scalars(select(ModelCall).where(ModelCall.stage_id.in_([stage.id for stage in stages]))))
    result.update(
        {
            "input_snapshot": operation.input_snapshot_json,
            "input_hash": operation.input_hash,
            "release_id": operation.release_id,
            "initial_outcome": attempt.initial_outcome,
            "resolved_outcome": attempt.resolved_outcome,
            "outcome_reason": attempt.outcome_reason,
            "previous_attempt_id": attempt.previous_attempt_id,
            "stages": [
                {
                    "id": stage.id,
                    "kind": stage.kind,
                    "state": stage.state,
                    "schedule_generation": stage.schedule_generation,
                }
                for stage in stages
            ],
            "calls": [
                {
                    "id": call.id,
                    "stage_id": call.stage_id,
                    "modality": call.modality,
                    "outcome": call.outcome,
                    "transport_kind": call.transport_kind,
                    "provider_request_id": call.provider_request_id,
                    "usage": call.usage_json,
                    "cost_state": call.cost_state,
                    "cost_actual": str(call.cost_actual) if call.cost_actual is not None else None,
                    "cost_estimated": str(call.cost_estimated) if call.cost_estimated is not None else None,
                    "quota_sequence": call.quota_sequence,
                }
                for call in calls
            ],
        }
    )
    result["artifacts"] = [
        {
            "id": artifact.id,
            "is_late": artifact.is_late,
            "sha256": artifact.sha256,
            "mime_type": artifact.mime_type,
            "availability": artifact.availability,
            "content_url": f"/api/execution-artifacts/{artifact.id}/content",
        }
        for artifact in session.scalars(select(ExecutionArtifact).where(ExecutionArtifact.attempt_id == attempt.id))
    ]
    return result


def task_detail(session, identifier: str, *, lightweight: bool = False) -> dict:
    session.flush()
    task = session.get(ProductionTask, identifier)
    if not task:
        raise NotFoundError("prototype_task_not_found")
    batch = session.scalar(select(ProductionBatch).where(ProductionBatch.task_id == task.id))
    items = list(
        session.scalars(
            select(ProductionItem).where(ProductionItem.batch_id == batch.id).order_by(ProductionItem.slot_index)
        )
    )
    experiment = session.get(ExperimentRun, task.experiment_run_id)
    operation_query = select(ExecutionOperation).where(
        ExecutionOperation.prototype_task_id == task.id
    ).options(
        defer(ExecutionOperation.input_snapshot_json, raiseload=True),
        defer(ExecutionOperation.authorization_snapshot_json, raiseload=True),
    )
    if lightweight:
        latest = select(ExecutionOperation.id, func.row_number().over(
            partition_by=func.coalesce(ExecutionOperation.item_id, ExecutionOperation.kind),
            order_by=(ExecutionOperation.created_at.desc(), ExecutionOperation.id.desc()),
        ).label("rank")).where(ExecutionOperation.prototype_task_id == task.id).subquery()
        operation_query = operation_query.join(latest, latest.c.id == ExecutionOperation.id).where(or_(
            latest.c.rank == 1,
            ExecutionOperation.state.in_(["queued", "running", "awaiting_local_decision"]),
            (ExecutionOperation.state == "unknown") & ExecutionOperation.wait_closed_at.is_(None),
        ))
    operations = list(session.scalars(operation_query.order_by(ExecutionOperation.created_at)))
    attempts = {row.operation_id: row for row in session.scalars(
        select(ExecutionAttempt).where(ExecutionAttempt.operation_id.in_([operation.id for operation in operations]))
    )}
    latest_recovery = select(LocalRecoveryExecution.id, func.row_number().over(
        partition_by=LocalRecoveryExecution.attempt_id,
        order_by=(LocalRecoveryExecution.created_at.desc(), LocalRecoveryExecution.recovery_sequence.desc()),
    ).label("rank")).where(
        LocalRecoveryExecution.attempt_id.in_([attempt.id for attempt in attempts.values()])
    ).subquery()
    recoveries = {row.attempt_id: row for row in session.scalars(select(LocalRecoveryExecution).join(
        latest_recovery, latest_recovery.c.id == LocalRecoveryExecution.id,
    ).where(latest_recovery.c.rank == 1))}
    result = {
        "id": task.id,
        "task_id": task.id,
        "product_id": task.product_id,
        "batch_id": batch.id,
        "business_state": task.business_state,
        "issue_code": task.issue_code,
        "row_version": task.row_version,
        "status_version": task.status_version,
        "current_plan_id": task.current_plan_id,
        "draft_content_language": task.draft_content_language,
        "draft_revision": task.draft_revision,
        "experiment_run_id": experiment.id,
        "image_limit": experiment.image_limit,
        "image_reserved_count": experiment.image_reserved_count,
        "quota_remaining": experiment.image_limit - experiment.image_reserved_count,
        "operations_partial": lightweight,
        "operations": [operation_response(session, operation, preloaded=(
            attempts[operation.id], recoveries.get(attempts[operation.id].id), task,
        )) for operation in operations],
        "slots": [
            {"id": item.id, "slot_index": item.slot_index, "first_attempt_id": item.first_attempt_id} for item in items
        ],
    }
    if lightweight:
        return result
    plans = list(
        session.scalars(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id).order_by(PlanSnapshot.revision))
    )
    result["draft"] = task.draft_json
    result["plans"] = [
        {
            "id": plan.id,
            "revision": plan.revision,
            "status": plan.status,
            "content_language": plan.content_language,
            "preview_revision": plan.preview_revision,
            "source_draft_revision": plan.source_draft_revision,
            "input_sha256": plan.input_sha256,
            "slots": [
                {
                    "item_id": slot.item_id,
                    "slot_index": slot.slot_index,
                    "copy_payload": slot.copy_payload_json,
                    "base_prompt": slot.base_prompt,
                    "fact_refs": slot.fact_refs_json,
                    "reference_refs": slot.reference_refs_json,
                }
                for slot in session.scalars(
                    select(SlotPlan).where(SlotPlan.plan_id == plan.id).order_by(SlotPlan.slot_index)
                )
            ],
        }
        for plan in plans
    ]
    result["artifacts"] = [
        {
            "id": artifact.id,
            "attempt_id": artifact.attempt_id,
            "is_late": artifact.is_late,
            "asset_kind": artifact.asset_kind,
            "availability": artifact.availability,
            "content_url": f"/api/prototype-artifacts/{artifact.id}/content",
        }
        for artifact in session.scalars(
            select(ExecutionArtifact)
            .join(ExecutionAttempt, ExecutionArtifact.attempt_id == ExecutionAttempt.id)
            .join(ExecutionOperation, ExecutionAttempt.operation_id == ExecutionOperation.id)
            .where(ExecutionOperation.prototype_task_id == task.id)
        )
    ]
    return result
