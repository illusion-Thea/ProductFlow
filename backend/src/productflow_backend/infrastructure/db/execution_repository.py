"""Short transaction primitives. Callers never retain these locks across I/O."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from productflow_backend.domain.errors import NotFoundError
from productflow_backend.domain.model_execution import (
    CAPACITY_LOCK,
    LEGACY_ADMISSION_LOCK,
    ExecutionConflict,
    ExecutionUnavailable,
    canonical_hash,
)
from productflow_backend.infrastructure.db.base import Base, new_id
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionAttempt,
    ExecutionEvent,
    ExecutionOperation,
    ExecutionStage,
    RequestIdempotency,
)
from productflow_backend.infrastructure.db.models import (
    ImageSession,
    ImageSessionGenerationTask,
    Product,
    ProductWorkflow,
    WorkflowNodeRun,
    WorkflowRun,
)
from productflow_backend.infrastructure.db.prototype_models import ExperimentRun, ProductionItem, ProductionTask


def locked[T: Base](session: Session, model: type[T], identifier: str) -> T:
    value = session.scalar(
        select(model).where(model.id == identifier).with_for_update().execution_options(populate_existing=True)
    )
    if value is None:
        raise NotFoundError("execution_resource_not_found")
    return value


def capacity_lock(session: Session) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LEGACY_ADMISSION_LOCK})
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CAPACITY_LOCK})


def lock_task(session: Session, task_id: str) -> ProductionTask:
    task = session.get(ProductionTask, task_id)
    if task is None:
        raise NotFoundError("prototype_task_not_found")
    capacity_lock(session)
    locked(session, ExperimentRun, task.experiment_run_id)
    locked(session, Product, task.product_id)
    return locked(session, ProductionTask, task_id)


def lock_operation(session: Session, operation_id: str) -> ExecutionOperation:
    operation = session.get(ExecutionOperation, operation_id)
    if operation is None:
        raise NotFoundError("execution_operation_not_found")
    capacity_lock(session)
    if operation.prototype_task_id:
        lock_task(session, operation.prototype_task_id)
    elif operation.image_session_task_id:
        task = session.get(ImageSessionGenerationTask, operation.image_session_task_id)
        locked(session, ImageSession, task.session_id)
        locked(session, ImageSessionGenerationTask, task.id)
    else:
        node_run = session.get(WorkflowNodeRun, operation.workflow_node_run_id)
        run = session.get(WorkflowRun, node_run.workflow_run_id)
        workflow = session.get(ProductWorkflow, run.workflow_id)
        locked(session, Product, workflow.product_id)
        locked(session, WorkflowRun, run.id)
    if operation.item_id:
        locked(session, ProductionItem, operation.item_id)
    return locked(session, ExecutionOperation, operation_id)


def lock_stage(session: Session, stage_id: str) -> tuple[ExecutionOperation, ExecutionAttempt, ExecutionStage]:
    stage = session.get(ExecutionStage, stage_id)
    if stage is None:
        raise NotFoundError("execution_stage_not_found")
    attempt = session.get(ExecutionAttempt, stage.attempt_id)
    operation = lock_operation(session, attempt.operation_id)
    attempt = locked(session, ExecutionAttempt, attempt.id)
    return operation, attempt, locked(session, ExecutionStage, stage_id)


def append_event(
    session: Session,
    event_type: str,
    dedupe_key: str,
    *,
    operation_id: str | None = None,
    task_id: str | None = None,
    payload: dict | None = None,
    actor_label: str = "execution",
    legacy_source_kind: str | None = None,
    legacy_source_id: str | None = None,
) -> None:
    # Caller owns the canonical root lock; this lookup never takes event locks first.
    if session.scalar(select(ExecutionEvent.id).where(ExecutionEvent.dedupe_key == dedupe_key)):
        return
    session.add(
        ExecutionEvent(
            event_type=event_type,
            dedupe_key=dedupe_key,
            operation_id=operation_id,
            legacy_source_kind=legacy_source_kind,
            legacy_source_id=legacy_source_id,
            task_id=task_id,
            payload_json=payload or {},
            actor_label=actor_label,
        )
    )


def idempotent(
    session: Session,
    *,
    actor: str,
    route: str,
    key: str,
    payload: dict[str, Any],
    action: Callable[[], tuple[int, dict[str, Any]]],
    prepare: Callable[[], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Reserve before hydrating mutable input. An ambiguous commit is never retried here."""
    digest = canonical_hash(payload)
    if prepare is not None:
        existing = session.scalar(
            select(RequestIdempotency).where(
                RequestIdempotency.actor_scope == actor,
                RequestIdempotency.route_scope == route,
                RequestIdempotency.idempotency_key == key,
            )
        )
        if existing is not None and existing.response_json is not None:
            conflict = existing.payload_sha256 != digest
            result = existing.response_status, existing.response_json
            session.rollback()
            if conflict:
                raise ExecutionConflict("idempotency_payload_conflict")
            return result
        session.rollback()
        prepare()  # Immutable file hashing before the accepting transaction; T1 rechecks record identity.
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    try:
        session.execute(
            insert(RequestIdempotency)
            .values(
                id=new_id(),
                actor_scope=actor,
                route_scope=route,
                idempotency_key=key,
                payload_sha256=digest,
            )
            .on_conflict_do_nothing(index_elements=["actor_scope", "route_scope", "idempotency_key"])
        )
        row = session.scalar(
            select(RequestIdempotency)
            .where(
                RequestIdempotency.actor_scope == actor,
                RequestIdempotency.route_scope == route,
                RequestIdempotency.idempotency_key == key,
            )
            .with_for_update()
        )
        if row.payload_sha256 != digest:
            raise ExecutionConflict("idempotency_payload_conflict")
        if row.response_json is not None:
            response = row.response_status, row.response_json
            session.rollback()
            return response
        status, response = action()
        row.response_status = status
        row.response_json = response
        row.resource_id = response.get("operation_id") or response.get("task_id") or response.get("id")
        session.flush()
    except DBAPIError as error:
        session.rollback()
        code = getattr(error.orig, "sqlstate", None)
        if code in {"55P03", "57014", "40001", "40P01"}:
            raise ExecutionUnavailable("transaction_conflict_retry_same_idempotency_key") from None
        raise
    except Exception:
        session.rollback()
        raise
    try:
        session.commit()
    except DBAPIError:
        session.rollback()
        raise ExecutionUnavailable("commit_result_unknown_query_or_retry_the_same_idempotency_key") from None
    return status, response
