"""Unproven legacy work is quarantined, never converted to undispatched work."""

from sqlalchemy import select

from productflow_backend.application.execution.legacy import enabled
from productflow_backend.domain.model_execution import canonical_hash
from productflow_backend.infrastructure.db.execution_models import ExecutionOperation
from productflow_backend.infrastructure.db.execution_repository import append_event, capacity_lock, locked
from productflow_backend.infrastructure.db.models import (
    ImageSession,
    ImageSessionGenerationTask,
    Product,
    ProductWorkflow,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)
from productflow_backend.infrastructure.db.session import get_session_factory


def quarantine_legacy_active() -> int:
    if not enabled():
        return 0
    changed = 0
    with get_session_factory()() as session:
        tasks = list(
            session.scalars(
                select(ImageSessionGenerationTask.id).where(
                    ImageSessionGenerationTask.status.in_(["queued", "running"])
                )
            )
        )
        runs = list(session.scalars(select(WorkflowRun.id).where(WorkflowRun.status == "running")))
    for identifier in tasks:
        with get_session_factory()() as session, session.begin():
            capacity_lock(session)
            task = session.get(ImageSessionGenerationTask, identifier)
            locked(session, ImageSession, task.session_id)
            task = locked(session, ImageSessionGenerationTask, identifier)
            if task.status not in {"queued", "running"} or session.scalar(
                select(ExecutionOperation.id).where(ExecutionOperation.image_session_task_id == identifier)
            ):
                continue
            append_event(
                session,
                "legacy_unverified",
                f"quarantine:image_session:{identifier}",
                legacy_source_kind="image_session",
                legacy_source_id=identifier,
                payload={
                    "original_status": str(task.status),
                    "payload_sha256": canonical_hash(
                        {
                            "prompt": task.prompt,
                            "size": task.size,
                            "base_asset_id": task.base_asset_id,
                            "references": task.selected_reference_asset_ids,
                            "tool_options": task.tool_options,
                            "generation_count": task.generation_count,
                        }
                    ),
                    "summary": {"generation_count": task.generation_count, "size": task.size},
                },
            )
            task.status, task.failure_reason, task.is_retryable = "unknown", "legacy_unverified", False
            changed += 1
    for identifier in runs:
        with get_session_factory()() as session, session.begin():
            capacity_lock(session)
            run = session.get(WorkflowRun, identifier)
            workflow = session.get(ProductWorkflow, run.workflow_id)
            locked(session, Product, workflow.product_id)
            run = locked(session, WorkflowRun, identifier)
            if run.status != "running" or session.scalar(
                select(ExecutionOperation.id)
                .join(WorkflowNodeRun, ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id)
                .where(WorkflowNodeRun.workflow_run_id == identifier)
            ):
                continue
            append_event(
                session,
                "legacy_unverified",
                f"quarantine:workflow:{identifier}",
                legacy_source_kind="workflow",
                legacy_source_id=identifier,
                payload={
                    "original_status": str(run.status),
                    "payload_sha256": canonical_hash(
                        {
                            "workflow_id": run.workflow_id,
                            "nodes": [
                                {
                                    "id": item.id,
                                    "node_id": item.node_id,
                                    "output": item.output_json,
                                    "status": str(item.status),
                                }
                                for item in sorted(run.node_runs, key=lambda item: item.id)
                            ],
                        }
                    ),
                    "summary": {"node_count": len(run.node_runs)},
                },
            )
            run.status, run.failure_reason = "unknown", "legacy_unverified"
            for node_run in run.node_runs:
                if node_run.status in {"queued", "running"}:
                    node_run.status, node_run.failure_reason = "unknown", "legacy_unverified"
                    node = session.get(WorkflowNode, node_run.node_id)
                    node.status, node.failure_reason = "unknown", "legacy_unverified"
            changed += 1
    return changed


def reject_legacy_dispatch() -> None:
    if enabled():
        from productflow_backend.domain.model_execution import ExecutionConflict

        raise ExecutionConflict("accepted_execution_ledger_required")
