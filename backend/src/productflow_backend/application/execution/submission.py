from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from productflow_backend.config import get_settings
from productflow_backend.domain.model_execution import canonical_hash
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionAttempt,
    ExecutionOperation,
    ExecutionStage,
    OutboxEvent,
)


def accept_operation(
    session: Session,
    *,
    kind: str,
    purpose: str,
    snapshot: dict[str, Any],
    authorization: dict[str, Any],
    source_kind: str = "prototype",
    prototype_task_id: str | None = None,
    image_session_task_id: str | None = None,
    workflow_node_run_id: str | None = None,
    item_id: str | None = None,
    plan_id: str | None = None,
    experiment_run_id: str | None = None,
    retry_of: ExecutionAttempt | None = None,
    parent_operation_id: str | None = None,
    stage_kinds: list[tuple[str, str]] | None = None,
) -> tuple[ExecutionOperation, ExecutionAttempt]:
    settings = get_settings()
    now = utcnow()
    operation = ExecutionOperation(
        id=new_id(),
        kind=kind,
        purpose=purpose,
        source_kind=source_kind,
        prototype_task_id=prototype_task_id,
        image_session_task_id=image_session_task_id,
        workflow_node_run_id=workflow_node_run_id,
        item_id=item_id,
        plan_id=plan_id,
        experiment_run_id=experiment_run_id,
        parent_operation_id=parent_operation_id,
        retry_of_attempt_id=retry_of.id if retry_of else None,
        release_id=settings.execution_release_id,
        accepted_at=now,
        authorization_snapshot_json=authorization,
        input_snapshot_json=snapshot,
        input_hash=canonical_hash(snapshot),
        source_draft_revision=snapshot.get("source_draft_revision"),
        state="queued",
        row_version=1,
    )
    session.add(operation)
    session.flush()
    attempt = ExecutionAttempt(
        id=new_id(), operation_id=operation.id, previous_attempt_id=retry_of.id if retry_of else None
    )
    session.add(attempt)
    session.flush()
    stages = stage_kinds or [("request", "model_text" if kind == "text" else "model_image")]
    for key, stage_kind in stages:
        stage = ExecutionStage(
            id=new_id(),
            attempt_id=attempt.id,
            stage_key=key,
            kind=stage_kind,
            state="queued",
            schedule_generation=1,
            input_hash=operation.input_hash,
            next_delivery_check_at=now + timedelta(seconds=5),
            queue_deadline_at=now + timedelta(seconds=settings.execution_queue_seconds),
        )
        session.add(stage)
        session.flush()
        if stage_kind.startswith("local_"):
            from productflow_backend.application.execution.result_store import schedule_local

            schedule_local(session, operation, attempt, stage, authorization["actor_label"])
        else:
            session.add(OutboxEvent(stage_id=stage.id, schedule_generation=1))
    return operation, attempt
