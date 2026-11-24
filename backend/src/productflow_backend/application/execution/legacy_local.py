"""Accepted legacy local rendering/context; zero ModelCall, same cancellation lock."""

import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from productflow_backend.application.contracts import PosterGenerationInput
from productflow_backend.application.execution.dispatch import cancelled
from productflow_backend.application.execution.legacy import dependencies_ready
from productflow_backend.application.execution.legacy_inputs import resolved_input
from productflow_backend.application.execution.result_store import project_task, schedule_local
from productflow_backend.domain.enums import PosterKind
from productflow_backend.domain.model_execution import expired
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionEvent,
    ExecutionStage,
    LocalRecoveryExecution,
)
from productflow_backend.infrastructure.db.execution_repository import append_event, lock_stage
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.poster.renderer import PosterRenderer
from productflow_backend.infrastructure.storage import LocalStorage


def execute_legacy_local(stage_id: str, generation: int) -> None:
    with get_session_factory()() as session, session.begin():
        operation, attempt, stage = lock_stage(session, stage_id)
        if stage.state != "queued" or stage.schedule_generation != generation:
            return
        if cancelled(session, operation):
            stage.state = "cancelled"
            project_task(session, operation)
            return
        if not dependencies_ready(session, operation, stage):
            return
        recovery = session.scalar(
            select(LocalRecoveryExecution)
            .where(LocalRecoveryExecution.stage_id == stage.id, LocalRecoveryExecution.state == "queued")
            .with_for_update()
        )
        if not recovery or expired(utcnow(), recovery.deadline_at):
            return
        recovery.state = "running"
        recovery.delivery_attempts += 1
        recovery_id = recovery.id
        stage.state, stage.claim_token = "claimed", new_id()
        token, epoch = stage.claim_token, stage.claim_epoch
        snapshot = resolved_input(session, operation, stage)
        attempt_id, key, kind = attempt.id, stage.stage_key, stage.kind
    try:
        if kind == "local_parse":
            payload, mime_type = json.dumps(snapshot["local_output"]).encode(), "application/json"
        else:
            payload = PosterRenderer(Path(snapshot["poster_font_path"])).render(
                PosterGenerationInput.model_validate(snapshot["render_input"]),
                PosterKind(snapshot["node"]["config"].get("poster_kind", "main_image")),
            )
            mime_type = "image/png"
        path, digest = LocalStorage().save_execution_bytes(f"spool/{attempt_id}/{stage_id}/response.bin", payload)
    except (ValueError, OSError):
        with get_session_factory()() as session, session.begin():
            operation, attempt, stage = lock_stage(session, stage_id)
            if stage.claim_token == token and stage.claim_epoch == epoch:
                stage.state = "queued"
                recovery = session.get(LocalRecoveryExecution, recovery_id)
                recovery.state, recovery.last_error = "queued", "legacy_local_error"
                recovery.next_retry_at = utcnow() + timedelta(seconds=2)
                stage.next_delivery_check_at = recovery.next_retry_at
                append_event(
                    session, "legacy_local_error", f"local-error:{stage.id}:{epoch}", operation_id=operation.id
                )
                project_task(session, operation)
        return
    with get_session_factory()() as session, session.begin():
        operation, attempt, stage = lock_stage(session, stage_id)
        recovery = session.get(LocalRecoveryExecution, recovery_id)
        if (
            stage.claim_token != token
            or stage.claim_epoch != epoch
            or recovery.state != "running"
            or expired(utcnow(), recovery.deadline_at)
            or attempt.terminal_at is not None
        ):
            append_event(
                session,
                "stale_local_result_archived",
                f"stale-local:{stage.id}:{epoch}",
                operation_id=operation.id,
                payload={"path": path, "sha256": digest, "mime_type": mime_type},
            )
            return
        dedupe = f"local-response:{attempt.id}:{key}"
        if session.scalar(select(ExecutionEvent.id).where(ExecutionEvent.dedupe_key == dedupe)):
            return
        append_event(
            session,
            "local_response_spooled",
            dedupe,
            operation_id=operation.id,
            payload={"path": path, "sha256": digest, "mime_type": mime_type},
        )
        stage.state, recovery.state = "succeeded", "succeeded"
        local = ExecutionStage(
            id=new_id(),
            attempt_id=attempt.id,
            stage_key=f"store:{key}",
            kind="local_store",
            state="queued",
            input_hash=operation.input_hash,
            schedule_generation=1,
            claim_epoch=0,
            next_delivery_check_at=utcnow(),
            queue_deadline_at=stage.queue_deadline_at,
        )
        session.add(local)
        session.flush()
        schedule_local(session, operation, attempt, local, "accepted_local_execution")
        project_task(session, operation)
