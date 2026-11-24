from datetime import timedelta
from pathlib import Path

import pytest
from execution_support import accepted, close_unknown, deliver, requests
from sqlalchemy import select
from test_execution_delivery import message, processes, retire_prior_test_fixtures, wait_for

from productflow_backend.application.execution.recovery import act_on_operation, cancel_task, scan_deadlines
from productflow_backend.application.execution.submission import accept_operation
from productflow_backend.application.prototype import authorization, create_task
from productflow_backend.domain.model_execution import ExecutionConflict
from productflow_backend.infrastructure.db.base import utcnow
from productflow_backend.infrastructure.db.execution_models import ExecutionOperation, ExecutionStage, ModelCall
from productflow_backend.infrastructure.db.prototype_models import (
    ExperimentRun,
    ProductionBatch,
    ProductionItem,
    ProductionTask,
)
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.outbox import execution_broker
from productflow_backend.infrastructure.verification import manifest


@pytest.mark.parametrize("unresolved", ["unknown", "awaiting_local_decision"])
def test_historic_retry_cannot_bypass_unresolved_same_slot(synthetic_case, unresolved):
    with get_session_factory()() as session, session.begin():
        task_id = create_task(session, synthetic_case["product_id"], synthetic_case, "fixture").id
        item = session.scalar(select(ProductionItem).join(ProductionBatch).where(ProductionBatch.task_id == task_id))
        operation, attempt = accept_operation(
            session,
            kind="image",
            purpose="initial",
            prototype_task_id=task_id,
            item_id=item.id,
            experiment_run_id=synthetic_case["experiment_run_id"],
            authorization=authorization("fixture", task_id),
            snapshot={"provider": {"transport_kind": "stub", "n": 1}, "stub_scenario": "refusal"},
        )
        operation_id = operation.id
        stage_id = session.scalar(select(ExecutionStage.id).where(ExecutionStage.attempt_id == attempt.id))
        item.first_attempt_id = attempt.id
    deliver(stage_id)
    with get_session_factory()() as session, session.begin():
        original = session.get(ExecutionOperation, operation_id)
        retry = act_on_operation(
            session, original.id, "retry", {"expected_row_version": original.row_version}, "fixture"
        )
        retry.state = unresolved
        retry_id = retry.id
    with get_session_factory()() as session, session.begin():
        original = session.get(ExecutionOperation, operation_id)
        with pytest.raises(ExecutionConflict, match="slot"):
            act_on_operation(session, original.id, "retry", {"expected_row_version": original.row_version}, "fixture")
    with get_session_factory()() as session:
        operations = list(
            session.scalars(select(ExecutionOperation).where(ExecutionOperation.prototype_task_id == task_id))
        )
        assert {op.id for op in operations} == {operation_id, retry_id}
        assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 1


@pytest.mark.parametrize("action", ["deadline", "cancel", "close-wait"])
def test_same_paused_worker_cannot_send_after_permit_revoked(synthetic_case, action):
    retire_prior_test_fixtures(synthetic_case["experiment_run_id"])
    scan_deadlines(utcnow() + timedelta(hours=1))
    task_id, operation_id, _, stage_id = accepted(synthetic_case, scenario="pause_before_send")
    marker = Path(manifest()["run_root"]) / "evidence" / f"paused-{stage_id}-before-send.json"
    with processes("verification_worker") as workers:
        execution_broker().enqueue(message(stage_id))
        wait_for(marker.exists)
        with get_session_factory()() as session, session.begin():
            call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage_id))
            call.wait_deadline_at = utcnow() - timedelta(seconds=1)
            session.get(ExecutionStage, stage_id).deadline_at = call.wait_deadline_at
        scan_deadlines()
        if action == "cancel":
            with get_session_factory()() as session, session.begin():
                task = session.get(ProductionTask, task_id)
                cancel_task(session, task_id, {"expected_row_version": task.row_version}, "fixture")
        elif action == "close-wait":
            close_unknown(operation_id)
        marker.with_suffix(".release").write_text("resume same process", encoding="utf-8")
        # A second queued stage proves the old worker has returned from send().
        _, other_op, _, other_stage = accepted(synthetic_case)
        close_unknown(operation_id)
        execution_broker().enqueue(message(other_stage))
        wait_for(lambda: bool(requests({other_stage})))
        assert requests({stage_id}) == []
        assert requests({other_stage})[0]["pid"] == workers[0].pid

        def response_recorded():
            with get_session_factory()() as session:
                return session.scalar(select(ModelCall.outcome).where(ModelCall.stage_id == other_stage)) == "succeeded"

        wait_for(response_recorded)
        scan_deadlines()
        with get_session_factory()() as session:
            assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 2
            original_call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage_id))
            assert original_call.outcome == "unknown" and original_call.quota_sequence == 1
            assert original_call.cost_actual is None and original_call.cost_estimated is None
            assert session.get(ExecutionOperation, operation_id).state == "unknown"
            if action == "cancel":
                assert session.get(ProductionTask, task_id).business_state == "cancelled"
