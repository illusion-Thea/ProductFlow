from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from productflow_backend.application.execution.ports import DispatchPermit, SingleRequestTransport
from productflow_backend.config import get_settings
from productflow_backend.domain.model_execution import canonical_hash, expired, reserve_image
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.execution_models import ExecutionOperation, ModelCall
from productflow_backend.infrastructure.db.execution_repository import append_event, lock_stage
from productflow_backend.infrastructure.db.prototype_models import ExperimentRun, ProductionTask
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.model_gateway import DefinitiveProviderFailure, SyntheticTransport


def cancelled(session, operation: ExecutionOperation) -> bool:
    if operation.prototype_task_id:
        return session.get(ProductionTask, operation.prototype_task_id).cancel_requested_at is not None
    if operation.source_kind == "image_session":
        from productflow_backend.infrastructure.db.models import ImageSessionGenerationTask

        return session.get(ImageSessionGenerationTask, operation.image_session_task_id).status == "cancelled"
    from productflow_backend.infrastructure.db.models import WorkflowNodeRun, WorkflowRun

    node_run = session.get(WorkflowNodeRun, operation.workflow_node_run_id)
    return session.get(WorkflowRun, node_run.workflow_run_id).status == "cancelled"


def claim_stage(stage_id: str, generation: int, environment_id: str, release_id: str) -> str | None:
    settings = get_settings()
    if environment_id != settings.execution_environment_id or release_id != settings.execution_release_id:
        return None
    with get_session_factory()() as session, session.begin():
        operation, _, stage = lock_stage(session, stage_id)
        if stage.schedule_generation != generation or stage.state != "queued" or operation.release_id != release_id:
            return None
        if cancelled(session, operation):
            stage.state, operation.state = "cancelled", "cancelled"
            return None
        if expired(utcnow(), stage.queue_deadline_at):
            stage.state, operation.state = "dispatch_blocked", "dispatch_blocked"
            append_event(session, "queue_deadline", f"queue-deadline:{stage.id}", operation_id=operation.id)
            return None
        if stage.kind not in {"model_text", "model_image"}:
            return None
        from productflow_backend.application.execution.legacy import dependencies_ready

        if not dependencies_ready(session, operation, stage):
            return None
        stage.state, stage.claim_token = "claimed", new_id()
        stage.claim_epoch += 1
        stage.claim_deadline_at = utcnow() + timedelta(seconds=settings.execution_claim_seconds)
        operation.state = "running"
        return stage.claim_token


def authorize_dispatch(stage_id: str, token: str) -> str | None:
    settings = get_settings()
    now = utcnow()
    with get_session_factory()() as session, session.begin():
        operation, _, stage = lock_stage(session, stage_id)
        if stage.state != "claimed" or stage.claim_token != token or expired(now, stage.claim_deadline_at):
            return None
        if cancelled(session, operation):
            stage.state, operation.state = "cancelled", "cancelled"
            return None
        if session.scalar(select(ModelCall.id).where(ModelCall.stage_id == stage.id)):
            return None
        # Capacity is shared by text, image, legacy and prototype, under the same
        # transaction advisory locks. Unknown remains active until wait closure.
        busy = session.scalar(
            select(ModelCall.id).where(
                ModelCall.outcome.in_(["pending", "unknown"]), ModelCall.wait_closed_at.is_(None)
            )
        )
        if busy:
            stage.state, operation.state = "queued", "queued"
            stage.claim_token = None
            stage.next_delivery_check_at = now + timedelta(seconds=1)
            return None
        provider = operation.input_snapshot_json.get("provider", {})
        if provider.get("transport_kind") != "stub" or not settings.execution_stub_only:
            stage.state, operation.state = "dispatch_blocked", "dispatch_blocked"
            append_event(
                session, "real_transport_disabled", f"transport-disabled:{stage.id}", operation_id=operation.id
            )
            return None
        modality = "text" if stage.kind == "model_text" else "image"
        from productflow_backend.application.execution.legacy_inputs import resolved_input

        accepted_request = resolved_input(session, operation, stage)
        sequence = None
        if operation.experiment_run_id:
            experiment = session.get(ExperimentRun, operation.experiment_run_id)
            if experiment.mode != "synthetic" or experiment.status != "active":
                stage.state, operation.state = "dispatch_blocked", "dispatch_blocked"
                return None
            if modality == "image":
                if experiment.image_reserved_count >= experiment.image_limit:
                    stage.state, operation.state = "dispatch_blocked", "dispatch_blocked"
                    append_event(session, "quota_exhausted", f"quota:{stage.id}", operation_id=operation.id)
                    return None
                sequence = reserve_image(experiment.image_reserved_count, experiment.image_limit, provider.get("n", 1))
                experiment.image_reserved_count = sequence
                experiment.row_version += 1
        call = ModelCall(
            id=new_id(),
            stage_id=stage.id,
            experiment_run_id=operation.experiment_run_id,
            modality=modality,
            purpose=operation.purpose,
            item_id=operation.item_id,
            provider_kind=provider.get("provider_kind", "mock"),
            model_snapshot_json={**provider, "resolved_input": accepted_request},
            transport_kind="stub",
            requested_candidate_count=provider.get("n", 1),
            request_hash=canonical_hash(accepted_request),
            quota_sequence=sequence,
            cost_state="not_applicable",
            wait_deadline_at=now + timedelta(seconds=settings.execution_request_seconds),
        )
        session.add(call)
        stage.state, stage.dispatch_authorized_at, stage.deadline_at = "dispatch_intent", now, call.wait_deadline_at
        append_event(session, "dispatch_intent", f"intent:{stage.id}", operation_id=operation.id)
        return call.id


def consume_transport_permit(stage_id: str, token: str) -> DispatchPermit | None:
    """Separate committed CAS. Never send if either intent or this commit is uncertain."""
    with get_session_factory()() as session, session.begin():
        operation, attempt, stage = lock_stage(session, stage_id)
        call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage.id).with_for_update())
        if (
            not call
            or call.transport_started_at is not None
            or stage.state != "dispatch_intent"
            or stage.claim_token != token
            or expired(utcnow(), stage.deadline_at)
        ):
            return None
        if cancelled(session, operation):
            # Intent quota is conservative and never refunded, even if the final
            # check prevented transport. Cancellation closes local capacity only.
            call.outcome, call.wait_closed_at = "unknown", utcnow()
            stage.state, operation.state = "unknown", "unknown"
            operation.wait_closed_at = call.wait_closed_at
            return None
        call.transport_started_at, stage.state = utcnow(), "running"
        return DispatchPermit(
            call.id,
            stage.id,
            attempt.id,
            token,
            stage.claim_epoch,
            call.model_snapshot_json["resolved_input"],
            call.modality,
            stage.deadline_at,
        )


def transport_permit_is_current(permit: DispatchPermit) -> bool:
    """Final send boundary, after transport setup/pause and before any request.

    A stale stack cannot use capacity released by deadline or wait closure. The
    transaction is short; the request itself never holds a database lock.
    """
    if permit.deadline_at is None or expired(utcnow(), permit.deadline_at):
        return False
    with get_session_factory()() as session, session.begin():
        operation, _, stage = lock_stage(session, permit.stage_id)
        call = session.get(ModelCall, permit.call_id)
        return bool(
            call and call.stage_id == stage.id and call.outcome == "pending"
            and call.wait_closed_at is None and stage.state == "running"
            and stage.claim_token == permit.claim_token and stage.claim_epoch == permit.claim_epoch
            and not expired(utcnow(), stage.deadline_at) and not expired(utcnow(), call.wait_deadline_at)
            and not cancelled(session, operation)
        )


def execute_stage(
    stage_id: str,
    generation: int,
    environment_id: str,
    release_id: str,
    transport: SingleRequestTransport | None = None,
) -> None:
    from productflow_backend.application.execution.result_store import record_failure, record_result

    token = claim_stage(stage_id, generation, environment_id, release_id)
    if token is None:
        return
    if authorize_dispatch(stage_id, token) is None:
        return
    permit = consume_transport_permit(stage_id, token)
    if permit is None:
        return
    # All three transaction contexts have exited; no session or lock spans I/O.
    try:
        result = (transport or SyntheticTransport()).send(permit)
    except DefinitiveProviderFailure:
        record_failure(stage_id, "failed", "provider_refusal")
    except Exception:
        record_failure(stage_id, "unknown", "transport_outcome_unknown")
    else:
        record_result(permit, result)
