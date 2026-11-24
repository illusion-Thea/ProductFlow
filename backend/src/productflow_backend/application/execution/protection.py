"""Temporary verification-only delete protection, not production retention."""

from sqlalchemy import select

from productflow_backend.application.execution.legacy import enabled
from productflow_backend.domain.model_execution import ExecutionConflict
from productflow_backend.infrastructure.db.execution_models import ExecutionEvent, ExecutionOperation
from productflow_backend.infrastructure.db.execution_repository import capacity_lock, locked
from productflow_backend.infrastructure.db.models import (
    ImageSession,
    ImageSessionGenerationTask,
    Product,
    ProductWorkflow,
    WorkflowNodeRun,
    WorkflowRun,
)
from productflow_backend.infrastructure.db.prototype_models import ProductionTask


def protect_product(session, product_id: str) -> None:
    if not enabled():
        return
    capacity_lock(session)
    locked(session, Product, product_id)
    prototype = session.scalar(select(ProductionTask.id).where(ProductionTask.product_id == product_id))
    execution = session.scalar(
        select(ExecutionOperation.id)
        .join(WorkflowNodeRun, ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id)
        .join(WorkflowRun, WorkflowNodeRun.workflow_run_id == WorkflowRun.id)
        .join(ProductWorkflow, WorkflowRun.workflow_id == ProductWorkflow.id)
        .where(ProductWorkflow.product_id == product_id)
    )
    historical = session.scalar(
        select(ExecutionEvent.id)
        .join(WorkflowRun, ExecutionEvent.legacy_source_id == WorkflowRun.id)
        .join(ProductWorkflow, WorkflowRun.workflow_id == ProductWorkflow.id)
        .where(
            ProductWorkflow.product_id == product_id,
            ExecutionEvent.legacy_source_kind.in_(["workflow", "workflow_runs"]),
        )
    )
    if prototype or execution or historical:
        raise ExecutionConflict("execution_history_protected")


def protect_session(session, session_id: str) -> None:
    if not enabled():
        return
    capacity_lock(session)
    locked(session, ImageSession, session_id)
    ledger = session.scalar(
        select(ExecutionOperation.id)
        .join(ImageSessionGenerationTask, ExecutionOperation.image_session_task_id == ImageSessionGenerationTask.id)
        .where(ImageSessionGenerationTask.session_id == session_id)
    )
    historical = session.scalar(
        select(ExecutionEvent.id)
        .join(ImageSessionGenerationTask, ExecutionEvent.legacy_source_id == ImageSessionGenerationTask.id)
        .where(
            ImageSessionGenerationTask.session_id == session_id,
            ExecutionEvent.legacy_source_kind.in_(["image_session", "image_session_generation_tasks"]),
        )
    )
    if ledger or historical:
        raise ExecutionConflict("execution_history_protected")
