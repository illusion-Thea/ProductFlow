import json
from pathlib import Path

from sqlalchemy import select

from productflow_backend.application.execution.submission import accept_operation
from productflow_backend.application.prototype import authorization, create_task
from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.execution_models import ExecutionStage
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.verification import manifest


def accepted(case, *, scenario="success", task_id=None, modality="image"):
    with get_session_factory()() as session, session.begin():
        if task_id is None:
            task_id = create_task(session, case["product_id"], case, "fixture").id
        operation, attempt = accept_operation(
            session,
            kind=modality,
            purpose="probe" if modality == "image" else "text",
            snapshot={
                "provider": {"provider_kind": "mock", "transport_kind": "stub", "n": 1},
                "output_spec": {"width": 32, "height": 32},
                "stub_scenario": scenario,
            },
            authorization=authorization("fixture", task_id),
            prototype_task_id=task_id,
            experiment_run_id=case["experiment_run_id"],
        )
        stage = session.scalar(select(ExecutionStage).where(ExecutionStage.attempt_id == attempt.id))
        return task_id, operation.id, attempt.id, stage.id


def deliver(stage_id, generation=1, transport=None):
    from productflow_backend.application.execution.dispatch import execute_stage

    settings = get_settings()
    execute_stage(stage_id, generation, settings.execution_environment_id, settings.execution_release_id, transport)


def requests(stage_ids):
    path = Path(manifest()["run_root"]) / "evidence/stub-requests.jsonl"
    return [row for line in path.read_text().splitlines() if (row := json.loads(line))["stage_id"] in stage_ids]


def close_unknown(operation_id):
    from productflow_backend.application.execution.recovery import act_on_operation
    from productflow_backend.infrastructure.db.execution_models import ExecutionOperation

    with get_session_factory()() as session, session.begin():
        operation = session.get(ExecutionOperation, operation_id)
        if operation.state == "unknown":
            act_on_operation(
                session, operation_id, "close-wait", {"expected_row_version": operation.row_version}, "fixture"
            )
