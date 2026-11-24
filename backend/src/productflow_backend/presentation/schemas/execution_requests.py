from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import object_session

from productflow_backend.application.execution.queries import operation_response
from productflow_backend.infrastructure.db.execution_models import ExecutionOperation
from productflow_backend.infrastructure.db.models import ImageSessionGenerationTask


class LocalRecoverySummary(BaseModel):
    id: str
    state: str
    deadline_at: datetime
    recovery_sequence: int
    last_error: str | None


class ExecutionSummary(BaseModel):
    operation_id: str
    attempt_id: str
    row_version: int
    root_row_version: int
    state: Literal[
        "queued",
        "running",
        "dispatch_blocked",
        "awaiting_local_decision",
        "succeeded",
        "failed",
        "unknown",
        "cancelled",
    ]
    wait_closed_at: datetime | None
    current_recovery: LocalRecoverySummary | None
    status_url: str
    source_kind: str
    source_id: str
    action_url: str
    retry_url: str


def execution_summary(record) -> ExecutionSummary | None:
    session = object_session(record)
    if not session:
        return None
    column = (
        ExecutionOperation.image_session_task_id
        if isinstance(record, ImageSessionGenerationTask)
        else ExecutionOperation.workflow_node_run_id
    )
    operation = session.scalar(select(ExecutionOperation).where(column == record.id))
    return ExecutionSummary.model_validate(operation_response(session, operation)) if operation else None


def execution_has_open_wait(record) -> bool:
    summary = execution_summary(record)
    return summary is not None and summary.state == "unknown" and summary.wait_closed_at is None
