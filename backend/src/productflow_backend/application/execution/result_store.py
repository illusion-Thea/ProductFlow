from __future__ import annotations

import hashlib
import io
import json
from datetime import timedelta

from PIL import Image
from sqlalchemy import func, select

from productflow_backend.application.execution.dispatch import cancelled
from productflow_backend.application.execution.ports import DispatchPermit, TransportResult
from productflow_backend.config import get_settings
from productflow_backend.domain.model_execution import canonical_hash, expired
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionArtifact,
    ExecutionAttempt,
    ExecutionEvent,
    ExecutionOperation,
    ExecutionStage,
    LocalRecoveryExecution,
    ModelCall,
    OutboxEvent,
)
from productflow_backend.infrastructure.db.execution_repository import append_event, lock_stage
from productflow_backend.infrastructure.db.prototype_models import ProductionTask
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.storage import LocalStorage


def project_task(session, operation: ExecutionOperation) -> None:
    if not operation.prototype_task_id:
        from productflow_backend.application.execution.legacy_materialization import project_legacy

        project_legacy(session, operation)
        return
    task = session.get(ProductionTask, operation.prototype_task_id)
    task.status_version += 1
    if task.business_state in {"cancelling", "cancelled"}:
        return
    # Text result owns preview state, and bases never claim formal completion.
    if operation.kind == "image":
        session.flush()
        active = session.scalar(
            select(ExecutionOperation.id).where(
                ExecutionOperation.prototype_task_id == task.id, ExecutionOperation.state.in_(["queued", "running"])
            )
        )
        task.business_state = "generating" if active else "needs_attention"
        unresolved = session.scalar(
            select(ExecutionOperation.id).where(
                ExecutionOperation.prototype_task_id == task.id,
                ExecutionOperation.kind == "image",
                ExecutionOperation.state != "succeeded",
            )
        )
        task.issue_code = None if active else (
            "execution_needs_attention" if unresolved else "composition_not_implemented"
        )


def record_failure(stage_id: str, outcome: str, reason: str) -> None:
    with get_session_factory()() as session, session.begin():
        operation, attempt, stage = lock_stage(session, stage_id)
        call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage.id).with_for_update())
        if not call or call.outcome == "succeeded":
            return
        call.outcome = outcome
        stage.state = outcome
        operation.state = outcome
        if outcome == "unknown" and call.wait_abandoned_at is None:
            operation.wait_closed_at = None
        attempt.initial_outcome = attempt.initial_outcome or outcome
        attempt.resolved_outcome = outcome
        attempt.outcome_reason = reason
        if outcome == "failed":
            call.wait_closed_at = utcnow()
            attempt.terminal_at = utcnow()
        append_event(session, reason, f"{reason}:{stage.id}", operation_id=operation.id)
        project_task(session, operation)


def schedule_local(
    session, operation: ExecutionOperation, attempt: ExecutionAttempt, stage: ExecutionStage, actor: str
) -> LocalRecoveryExecution:
    now = utcnow()
    sequence = 1 + (
        session.scalar(
            select(func.max(LocalRecoveryExecution.recovery_sequence)).where(
                LocalRecoveryExecution.stage_id == stage.id
            )
        )
        or 0
    )
    stage.claim_epoch += 1
    stage.state = "queued"
    recovery = LocalRecoveryExecution(
        id=new_id(),
        attempt_id=attempt.id,
        stage_id=stage.id,
        recovery_sequence=sequence,
        claim_epoch=stage.claim_epoch,
        input_sha256=operation.input_hash,
        config_sha256=canonical_hash(operation.input_snapshot_json.get("provider", {})),
        authorization_snapshot_json=operation.authorization_snapshot_json,
        execution_release_id=get_settings().execution_release_id,
        deadline_at=now + timedelta(seconds=get_settings().execution_local_seconds),
        next_retry_at=now,
        state="queued",
        actor_label=actor,
    )
    session.add(recovery)
    session.flush()
    stage.deadline_at = recovery.deadline_at
    stage.next_delivery_check_at = now + timedelta(seconds=2)
    session.add(OutboxEvent(stage_id=stage.id, schedule_generation=stage.schedule_generation))
    return recovery


def spool_result(permit: DispatchPermit, result: TransportResult) -> str:
    """Durable receipt closes the filesystem-success / DB-commit crash window."""
    storage = LocalStorage()
    spool = f"spool/{permit.attempt_id}/{permit.stage_id}/response.bin"
    _, digest = storage.save_execution_bytes(spool, result.payload)
    receipt = {
        "call_id": permit.call_id,
        "stage_id": permit.stage_id,
        "attempt_id": permit.attempt_id,
        "request_hash": canonical_hash(permit.snapshot),
        "response_hash": digest,
        "request_id": result.request_id,
        "mime_type": result.mime_type,
        "usage": result.usage,
    }
    storage.save_execution_bytes(spool + ".json", json.dumps(receipt, sort_keys=True).encode())
    return spool


def reconcile_spooled_results() -> int:
    """Read local receipts only. Missing/invalid evidence never permits dispatch."""
    with get_session_factory()() as session:
        rows = session.execute(
            select(ModelCall, ExecutionStage)
            .join(ExecutionStage, ModelCall.stage_id == ExecutionStage.id)
            .where(ModelCall.response_hash.is_(None), ModelCall.transport_started_at.is_not(None))
            .order_by(ModelCall.created_at, ModelCall.id)
        ).all()
        permits = [
            DispatchPermit(
                call.id,
                stage.id,
                stage.attempt_id,
                stage.claim_token or "",
                stage.claim_epoch,
                call.model_snapshot_json["resolved_input"],
                call.modality,
            )
            for call, stage in rows
        ]
    count = 0
    storage = LocalStorage()
    for permit in permits:
        path = f"spool/{permit.attempt_id}/{permit.stage_id}/response.bin"
        try:
            receipt = json.loads(storage.resolve(path + ".json").read_bytes())
            payload = storage.resolve(path).read_bytes()
            expected = {
                "call_id": permit.call_id,
                "stage_id": permit.stage_id,
                "attempt_id": permit.attempt_id,
                "request_hash": canonical_hash(permit.snapshot),
                "response_hash": hashlib.sha256(payload).hexdigest(),
            }
            if any(receipt.get(key) != value for key, value in expected.items()):
                continue
            result = TransportResult(receipt["request_id"], payload, receipt["mime_type"], receipt["usage"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        record_result(permit, result)
        count += 1
    return count


def record_result(permit: DispatchPermit, result: TransportResult) -> None:
    digest = hashlib.sha256(result.payload).hexdigest()
    spool = f"spool/{permit.attempt_id}/{permit.stage_id}/response.bin"
    available = False
    quarantine_path = None
    try:
        spool_result(permit, result)
        available = True
    except ValueError:
        # A different response must not replace the first immutable receipt.
        # Retain the competing bytes separately, before taking database locks.
        quarantine_path = f"spool/quarantine/{permit.attempt_id}/{permit.stage_id}/{digest}.bin"
        try:
            LocalStorage().save_execution_bytes(quarantine_path, result.payload)
        except (OSError, ValueError):
            quarantine_path = None
    except OSError:
        pass
    with get_session_factory()() as session, session.begin():
        operation, attempt, stage = lock_stage(session, permit.stage_id)
        call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage.id).with_for_update())
        if not call or call.id != permit.call_id or attempt.id != permit.attempt_id:
            raise ValueError("result_does_not_belong_to_dispatch")
        if call.response_hash:
            if call.response_hash != digest or call.provider_request_id != result.request_id:
                append_event(
                    session,
                    "result_conflict",
                    f"result-conflict:{stage.id}:{digest}",
                    operation_id=operation.id,
                    payload={"response_hash": digest, "quarantine_path": quarantine_path},
                )
            return
        was_unknown = call.outcome == "unknown"
        call.provider_request_id, call.response_hash = result.request_id, digest
        call.response_path = spool if available else None
        call.response_mime_type, call.usage_json = result.mime_type, result.usage
        call.outcome, call.wait_closed_at = "succeeded", utcnow()
        stage.state = "succeeded"
        if was_unknown:
            append_event(session, "unknown_result_reconciled", f"reconcile:{call.id}", operation_id=operation.id)
        local = ExecutionStage(
            id=new_id(),
            attempt_id=attempt.id,
            stage_key=f"store:{stage.stage_key}",
            kind="local_store",
            state="queued",
            input_hash=operation.input_hash,
            next_delivery_check_at=utcnow(),
            queue_deadline_at=call.wait_deadline_at,
            schedule_generation=1,
            claim_epoch=0,
        )
        session.add(local)
        session.flush()
        if available:
            schedule_local(session, operation, attempt, local, "accepted_execution")
            if operation.state not in {"unknown", "cancelled"}:
                operation.state = "running"
        else:
            local.state = "dispatch_blocked"
            if operation.state not in {"unknown", "cancelled"}:
                operation.state = "awaiting_local_decision"
            append_event(session, "raw_response_unavailable", f"raw-unavailable:{call.id}", operation_id=operation.id)
        project_task(session, operation)


def execute_local(stage_id: str, generation: int) -> None:
    with get_session_factory()() as session, session.begin():
        operation, attempt, stage = lock_stage(session, stage_id)
        if stage.kind != "local_store" or stage.schedule_generation != generation or stage.state != "queued":
            return
        recovery = session.scalar(
            select(LocalRecoveryExecution)
            .where(LocalRecoveryExecution.stage_id == stage.id, LocalRecoveryExecution.state == "queued")
            .with_for_update()
        )
        if not recovery or expired(utcnow(), recovery.deadline_at):
            return
        call = session.scalar(
            select(ModelCall)
            .join(ExecutionStage, ModelCall.stage_id == ExecutionStage.id)
            .where(
                ExecutionStage.attempt_id == attempt.id,
                ExecutionStage.stage_key == stage.stage_key.removeprefix("store:"),
            )
        )
        if not call:
            receipt = session.scalar(
                select(ExecutionEvent).where(
                    ExecutionEvent.dedupe_key == f"local-response:{attempt.id}:{stage.stage_key.removeprefix('store:')}"
                )
            )
            if not receipt:
                return
            source_path, expected_hash, mime_type = (
                receipt.payload_json[key] for key in ("path", "sha256", "mime_type")
            )
        elif call.response_path:
            source_path, expected_hash, mime_type = call.response_path, call.response_hash, call.response_mime_type
        else:
            return
        stage.state, recovery.state = "claimed", "running"
        recovery.delivery_attempts += 1
        epoch, recovery_id = stage.claim_epoch, recovery.id
        destination = f"artifacts/{attempt.id}/{stage.id}/1.bin"
        call_id = call.id if call else None
        late = cancelled(session, operation) or bool(call and call.wait_abandoned_at) or attempt.terminal_at is not None
        operation_id, attempt_id = operation.id, attempt.id
    # Filesystem work is outside every database transaction.
    width = height = None
    try:
        storage = LocalStorage()
        payload = storage.resolve(source_path).read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise ValueError("spool_hash_mismatch")
        _, digest = storage.save_execution_bytes(destination, payload)
        if mime_type.startswith("image/"):
            with Image.open(io.BytesIO(payload)) as image:
                width, height = image.size
    except (OSError, ValueError):
        with get_session_factory()() as session, session.begin():
            operation, _, stage = lock_stage(session, stage_id)
            recovery = session.get(LocalRecoveryExecution, recovery_id)
            if stage.claim_epoch == epoch and recovery.state == "running":
                recovery.state, stage.state = "queued", "queued"
                recovery.last_error = "local_storage_unavailable"
                recovery.next_retry_at = utcnow() + timedelta(seconds=2)
                stage.next_delivery_check_at = recovery.next_retry_at
        return
    with get_session_factory()() as session, session.begin():
        operation, attempt, stage = lock_stage(session, stage_id)
        recovery = session.get(LocalRecoveryExecution, recovery_id)
        call = session.get(ModelCall, call_id) if call_id else None
        late = late or cancelled(session, operation) or bool(call and call.wait_abandoned_at)
        stale = (
            stage.claim_epoch != epoch
            or recovery.state != "running"
            or expired(utcnow(), recovery.deadline_at)
            or attempt.terminal_at is not None
        )
        existing = session.scalar(
            select(ExecutionArtifact).where(
                ExecutionArtifact.stage_id == stage.id, ExecutionArtifact.artifact_index == 1
            )
        )
        if existing:
            if existing.sha256 != digest:
                append_event(
                    session, "result_conflict", f"artifact-conflict:{stage.id}:{digest}", operation_id=operation_id
                )
                return
            # An expired worker may have archived these same bytes. A new,
            # explicitly accepted local recovery can consume that immutable
            # artifact; it must not create a second row or reset its provenance.
            artifact = existing
        else:
            artifact = ExecutionArtifact(
                attempt_id=attempt_id,
                stage_id=stage.id,
                artifact_index=1,
                asset_kind="provider_text" if mime_type == "application/json" else "base_image",
                storage_path=destination,
                sha256=digest,
                byte_size=len(payload),
                mime_type=mime_type,
                width=width,
                height=height,
                is_late=late or stale,
                availability="available",
            )
            session.add(artifact)
        if stale:
            append_event(
                session, "stale_local_result_archived", f"stale-local:{recovery.id}", operation_id=operation_id
            )
            return
        recovery.state, stage.state = "succeeded", "succeeded"
        if not late and operation.source_kind != "prototype":
            from productflow_backend.application.execution.legacy_materialization import materialize

            try:
                with session.begin_nested():
                    materialize(session, operation, stage, artifact, payload)
            except (ValueError, KeyError, TypeError):
                stage.state, operation.state = "failed", "failed"
                attempt.outcome_reason = "invalid_legacy_result_structure"
                append_event(
                    session, "invalid_legacy_result_structure", f"invalid-legacy:{stage.id}", operation_id=operation.id
                )
            project_task(session, operation)
            return
        if not late and operation.prototype_task_id and operation.kind == "text":
            from productflow_backend.application.prototype import receive_text_plan

            try:
                parsed = json.loads(payload)
                # SAVEPOINT prevents a bad structure from erasing usage/artifact evidence.
                with session.begin_nested():
                    receive_text_plan(
                        session, session.get(ProductionTask, operation.prototype_task_id), operation, parsed
                    )
            except (ValueError, KeyError, TypeError):
                operation.state = "failed"
                attempt.initial_outcome = attempt.initial_outcome or "failed"
                attempt.resolved_outcome, attempt.outcome_reason = "failed", "invalid_text_structure"
                attempt.terminal_at = utcnow()
                append_event(
                    session, "invalid_text_structure", f"invalid-text:{operation.id}", operation_id=operation.id
                )
                project_task(session, operation)
                return
        if not late:
            operation.state = "succeeded"
            attempt.initial_outcome = attempt.initial_outcome or "succeeded"
            attempt.resolved_outcome, attempt.terminal_at = "succeeded", utcnow()
        append_event(
            session,
            "artifact_archived" if late else "artifact_available",
            f"artifact:{stage.id}",
            operation_id=operation_id,
        )
        project_task(session, operation)
