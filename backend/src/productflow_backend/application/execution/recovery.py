from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from productflow_backend.application.execution.dispatch import cancelled
from productflow_backend.application.execution.result_store import project_task, schedule_local
from productflow_backend.application.execution.submission import accept_operation
from productflow_backend.config import get_settings
from productflow_backend.domain.model_execution import ExecutionConflict, expired, require_retry, require_version
from productflow_backend.infrastructure.db.base import utcnow
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionAttempt,
    ExecutionEvent,
    ExecutionOperation,
    ExecutionStage,
    LocalRecoveryExecution,
    ModelCall,
    OutboxEvent,
)
from productflow_backend.infrastructure.db.execution_repository import (
    append_event,
    lock_operation,
    lock_stage,
    lock_task,
)
from productflow_backend.infrastructure.db.prototype_models import ProductionTask
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.storage import LocalStorage


def act_on_operation(session, operation_id: str, action: str, payload: dict, actor: str) -> ExecutionOperation:
    operation = lock_operation(session, operation_id)
    require_version(operation.row_version, payload["expected_row_version"])
    attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == operation.id))
    if action == "close-wait":
        if operation.state != "unknown":
            raise ExecutionConflict("only_unknown_wait_can_be_closed")
        operation.wait_closed_at = operation.wait_closed_at or utcnow()
        calls = session.scalars(select(ModelCall).join(ExecutionStage).where(ExecutionStage.attempt_id == attempt.id))
        for call in calls:
            call.wait_closed_at = call.wait_closed_at or operation.wait_closed_at
        append_event(
            session,
            "unknown_wait_closed",
            f"close-wait:{operation.id}",
            operation_id=operation.id,
            payload={"reason": payload.get("reason", "")},
            actor_label=actor,
        )
    elif action == "retry":
        if cancelled(session, operation):
            raise ExecutionConflict("cancelled_task_cannot_retry")
        require_retry(
            operation.state,
            operation.wait_closed_at is not None,
            payload.get("acknowledge_possible_duplicate_charge", False),
            attempt.terminal_at,
        )
        if operation.item_id and session.scalar(
            select(ExecutionOperation.id).where(
                ExecutionOperation.item_id == operation.item_id, ExecutionOperation.state.in_(["queued", "running"])
            )
        ):
            raise ExecutionConflict("slot_already_active")
        if operation.source_kind != "prototype":
            raise ExecutionConflict("use_legacy_retry_endpoint")
        new_operation, _ = accept_operation(
            session,
            kind=operation.kind,
            purpose="revision",
            snapshot=operation.input_snapshot_json,
            authorization={**operation.authorization_snapshot_json, "actor_label": actor},
            prototype_task_id=operation.prototype_task_id,
            item_id=operation.item_id,
            plan_id=operation.plan_id,
            experiment_run_id=operation.experiment_run_id,
            retry_of=attempt,
            parent_operation_id=operation.id,
        )
        return new_operation
    elif action in {"resume-local", "end-local"}:
        if cancelled(session, operation):
            raise ExecutionConflict("cancelled_task_cannot_resume_local")
        if operation.state == "unknown" or attempt.terminal_at is not None:
            raise ExecutionConflict("external_unknown_or_terminal_attempt_has_no_local_decision")
        local = session.scalar(
            select(ExecutionStage)
            .where(
                ExecutionStage.attempt_id == attempt.id,
                ExecutionStage.kind.in_(["local_store", "local_parse", "local_render_legacy"]),
                ExecutionStage.state.in_(["dispatch_blocked", "failed"]),
            )
            .order_by(ExecutionStage.created_at)
            .with_for_update()
        )
        if not local or local.state not in {"dispatch_blocked", "failed"}:
            raise ExecutionConflict("local_recovery_not_awaiting_decision")
        recoveries = list(
            session.scalars(
                select(LocalRecoveryExecution)
                .where(LocalRecoveryExecution.stage_id == local.id)
                .order_by(LocalRecoveryExecution.recovery_sequence)
            )
        )
        if any(recovery.state in {"queued", "running"} for recovery in recoveries):
            raise ExecutionConflict("local_recovery_already_active")
        if action == "end-local":
            local.claim_epoch += 1
            local.state = "failed"
            operation.state = "failed"
            attempt.terminal_at, attempt.resolved_outcome = utcnow(), "failed"
            attempt.initial_outcome = attempt.initial_outcome or "failed"
            attempt.outcome_reason = "local_recovery_ended_by_user"
            if recoveries:
                recoveries[-1].state, recoveries[-1].ended_at = "ended", utcnow()
                recoveries[-1].ended_reason = payload.get("reason", "")
        else:
            if operation.release_id != get_settings().execution_release_id:
                raise ExecutionConflict("compatible_execution_release_unavailable")
            if operation.item_id and session.scalar(
                select(ExecutionOperation.id).where(
                    ExecutionOperation.item_id == operation.item_id,
                    ExecutionOperation.id != operation.id,
                    ExecutionOperation.state.in_(["queued", "running"]),
                )
            ):
                raise ExecutionConflict("slot_already_active")
            if local.kind == "local_store":
                key = local.stage_key.removeprefix("store:")
                call = session.scalar(
                    select(ModelCall)
                    .join(ExecutionStage)
                    .where(ExecutionStage.attempt_id == attempt.id, ExecutionStage.stage_key == key)
                )
                receipt = session.scalar(
                    select(ExecutionEvent).where(ExecutionEvent.dedupe_key == f"local-response:{attempt.id}:{key}")
                )
                path = call.response_path if call else receipt.payload_json["path"] if receipt else None
                if not path or not LocalStorage().resolve(path).is_file():
                    raise ExecutionConflict("original_response_unavailable_no_regeneration")
            local.schedule_generation += 1
            schedule_local(session, operation, attempt, local, actor)
            operation.state = "running"
        append_event(
            session,
            action,
            f"{action}:{operation.id}:{operation.row_version}",
            operation_id=operation.id,
            actor_label=actor,
        )
    else:
        raise ExecutionConflict("unsupported_execution_action")
    operation.row_version += 1
    project_task(session, operation)
    return operation


def cancel_task(session, task_id: str, payload: dict, actor: str) -> ProductionTask:
    task = lock_task(session, task_id)
    require_version(task.row_version, payload["expected_row_version"])
    if task.cancel_requested_at:
        return task
    task.cancel_requested_at, task.business_state = utcnow(), "cancelling"
    task.row_version += 1
    operations = list(
        session.scalars(
            select(ExecutionOperation)
            .where(ExecutionOperation.prototype_task_id == task_id)
            .order_by(ExecutionOperation.item_id, ExecutionOperation.id)
        )
    )
    for operation in operations:
        attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == operation.id))
        stages = list(session.scalars(select(ExecutionStage).where(ExecutionStage.attempt_id == attempt.id)))
        for stage in stages:
            call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage.id))
            if (
                stage.kind.startswith("model_")
                and not call
                and stage.state in {"queued", "claimed", "dispatch_blocked"}
            ):
                stage.state, stage.claim_token = "cancelled", None
                stage.claim_epoch += 1
        if stages and all(stage.state in {"cancelled", "dispatch_blocked"} for stage in stages):
            operation.state, attempt.resolved_outcome, attempt.terminal_at = "cancelled", "cancelled", utcnow()
    append_event(
        session,
        "cancel_accepted",
        f"cancel:{task.id}",
        task_id=task.id,
        actor_label=actor,
        payload={"reason": payload.get("reason", "")},
    )
    session.flush()
    settle_cancel(session, task)
    return task


def settle_cancel(session, task: ProductionTask) -> None:
    active = session.scalar(
        select(ModelCall.id)
        .join(ExecutionStage, ModelCall.stage_id == ExecutionStage.id)
        .join(ExecutionAttempt, ExecutionStage.attempt_id == ExecutionAttempt.id)
        .join(ExecutionOperation, ExecutionAttempt.operation_id == ExecutionOperation.id)
        .where(
            ExecutionOperation.prototype_task_id == task.id,
            ModelCall.outcome.in_(["pending", "unknown"]),
            ModelCall.wait_closed_at.is_(None),
        )
    )
    if not active:
        task.business_state = "cancelled"
        task.cancelled_at = task.cancelled_at or utcnow()


def scan_deadlines(now=None) -> int:
    from productflow_backend.application.execution.result_store import reconcile_spooled_results

    reconciled = reconcile_spooled_results()
    now = now or utcnow()
    factory = get_session_factory()
    # Unlocked candidate discovery; every mutation reacquires canonical root locks.
    with factory() as session:
        identifiers = list(
            session.scalars(
                select(ExecutionStage.id)
                .join(ExecutionAttempt, ExecutionAttempt.id == ExecutionStage.attempt_id)
                .join(ExecutionOperation, ExecutionOperation.id == ExecutionAttempt.operation_id)
                .where(ExecutionStage.state.in_(["queued", "claimed", "dispatch_intent", "running", "unknown"]))
                .where(~((ExecutionStage.state == "unknown") & ExecutionOperation.wait_closed_at.is_not(None)))
                .order_by(ExecutionStage.created_at, ExecutionStage.id)
            )
        )
    changed = reconciled
    for identifier in identifiers:
        with factory() as session, session.begin():
            operation, attempt, stage = lock_stage(session, identifier)
            if stage.kind.startswith("local_"):
                recovery = session.scalar(
                    select(LocalRecoveryExecution)
                    .where(
                        LocalRecoveryExecution.stage_id == stage.id,
                        LocalRecoveryExecution.state.in_(["queued", "running"]),
                    )
                    .with_for_update()
                )
                if recovery and expired(now, recovery.deadline_at):
                    recovery.state, stage.state = "timed_out", "dispatch_blocked"
                    stage.claim_epoch += 1
                    if operation.state not in {"unknown", "cancelled"}:
                        operation.state = "awaiting_local_decision"
                    append_event(
                        session, "local_recovery_timed_out", f"local-timeout:{recovery.id}", operation_id=operation.id
                    )
                    changed += 1
                elif recovery and stage.state == "queued" and expired(now, stage.next_delivery_check_at):
                    redeliver(session, stage, now)
                project_task(session, operation)
                continue
            call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage.id).with_for_update())
            if call:
                if call.outcome == "pending" and expired(now, call.wait_deadline_at):
                    call.outcome, stage.state, operation.state = "unknown", "unknown", "unknown"
                    attempt.initial_outcome = attempt.initial_outcome or "unknown"
                    attempt.resolved_outcome = "unknown"
                    append_event(session, "dispatch_deadline_unknown", f"deadline:{call.id}", operation_id=operation.id)
                    changed += 1
                if cancelled(session, operation) and expired(now, call.wait_deadline_at):
                    call.wait_closed_at = call.wait_closed_at or now
                    operation.wait_closed_at = operation.wait_closed_at or now
                project_task(session, operation)
                continue  # Any intent forbids requeue, regardless of lookup misses.
            if cancelled(session, operation):
                stage.state, operation.state = "cancelled", "cancelled"
            elif expired(now, stage.queue_deadline_at):
                stage.state, operation.state = "dispatch_blocked", "dispatch_blocked"
                append_event(session, "queue_deadline", f"queue-deadline:{stage.id}", operation_id=operation.id)
            elif stage.state == "claimed" and expired(now, stage.claim_deadline_at):
                stage.state, stage.claim_token = "queued", None
                stage.claim_epoch += 1
                operation.state = "queued"
                redeliver(session, stage, now)
                changed += 1
            elif stage.state == "queued" and expired(now, stage.next_delivery_check_at):
                redeliver(session, stage, now)
            project_task(session, operation)
    with factory() as session:
        cancelling = list(
            session.scalars(select(ProductionTask.id).where(ProductionTask.business_state == "cancelling"))
        )
    for identifier in cancelling:
        with factory() as session, session.begin():
            settle_cancel(session, lock_task(session, identifier))
    return changed


def redeliver(session, stage: ExecutionStage, now) -> None:
    pending = session.scalar(
        select(OutboxEvent.id).where(
            OutboxEvent.stage_id == stage.id,
            OutboxEvent.schedule_generation == stage.schedule_generation,
            OutboxEvent.publish_state.in_(["pending", "leased"]),
        )
    )
    if pending:
        return
    stage.schedule_generation += 1
    stage.next_delivery_check_at = now + timedelta(seconds=3)
    session.add(OutboxEvent(stage_id=stage.id, schedule_generation=stage.schedule_generation, available_at=now))
