"""Read-only, sanitized execution diagnostics in the guarded verification runner."""

import json
import os
import socket
import sys

from sqlalchemy import select

from productflow_backend.infrastructure.db.execution_models import (
    ExecutionAttempt,
    ExecutionOperation,
    ExecutionStage,
)
from productflow_backend.infrastructure.db.models import WorkflowNodeRun
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.verification import IsolationError, manifest

manifest()
if os.environ.get("M101_PROCESS_MODE") != "integration" or not getattr(socket, "_m101_guard_installed", False):
    raise IsolationError("diagnostics_require_guarded_integration_runner")

with get_session_factory()() as session:
    for operation in session.scalars(
        select(ExecutionOperation)
        .join(
            WorkflowNodeRun,
            ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id,
        )
        .where(WorkflowNodeRun.workflow_run_id == sys.argv[1])
    ):
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.operation_id == operation.id
            )
        )
        print(
            json.dumps(
                {
                    "operation": operation.id,
                    "state": operation.state,
                    "reason": attempt.outcome_reason,
                    "node": operation.input_snapshot_json["node"]["node_type"],
                    "stages": [
                        (stage.stage_key, stage.state, stage.schedule_generation)
                        for stage in session.scalars(
                            select(ExecutionStage).where(
                                ExecutionStage.attempt_id == attempt.id
                            )
                        )
                    ],
                },
                ensure_ascii=False,
            )
        )
