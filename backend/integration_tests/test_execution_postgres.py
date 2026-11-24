from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from execution_support import accepted, close_unknown, deliver, requests
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from productflow_backend.application.execution.dispatch import authorize_dispatch, claim_stage, consume_transport_permit
from productflow_backend.application.execution.recovery import act_on_operation, cancel_task, scan_deadlines
from productflow_backend.application.execution.result_store import execute_local, record_result
from productflow_backend.application.prototype import create_task
from productflow_backend.config import get_settings
from productflow_backend.domain.model_execution import ExecutionConflict
from productflow_backend.infrastructure.db.base import utcnow
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionArtifact,
    ExecutionAttempt,
    ExecutionOperation,
    ExecutionStage,
    ModelCall,
)
from productflow_backend.infrastructure.db.execution_repository import idempotent
from productflow_backend.infrastructure.db.prototype_models import ExperimentRun, ProductionTask
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.model_gateway import SyntheticTransport


def claim(identifier):
    settings = get_settings()
    return claim_stage(identifier, 1, settings.execution_environment_id, settings.execution_release_id)


def test_concurrent_idempotency_and_rollback(synthetic_case):
    key = str(uuid4())

    def submit():
        with get_session_factory()() as session:

            def action():
                task = create_task(session, synthetic_case["product_id"], synthetic_case, "fixture")
                return 201, {"id": task.id}

            return idempotent(session, actor="fixture", route="create", key=key, payload=synthetic_case, action=action)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: submit(), range(8)))
    assert len({result[1]["id"] for result in results}) == 1
    with get_session_factory()() as session:
        before = session.scalar(select(func.count()).select_from(ProductionTask))
    with pytest.raises(RuntimeError), get_session_factory()() as session, session.begin():
        create_task(session, synthetic_case["product_id"], synthetic_case, "fixture")
        raise RuntimeError("T1_before_commit")
    with get_session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(ProductionTask)) == before


@pytest.mark.parametrize("window", ["claim", "intent", "permit"])
def test_crash_windows_never_redispatch_possible_intent(synthetic_case, window):
    _, operation_id, _, stage_id = accepted(synthetic_case)
    token = claim(stage_id)
    assert token
    if window != "claim":
        assert authorize_dispatch(stage_id, token)
    if window == "permit":
        assert consume_transport_permit(stage_id, token)
    with get_session_factory()() as session, session.begin():
        stage = session.get(ExecutionStage, stage_id)
        stage.claim_deadline_at = utcnow() - timedelta(seconds=1)
        call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage_id))
        if call:
            call.wait_deadline_at = utcnow() - timedelta(seconds=1)
    scan_deadlines()
    with get_session_factory()() as session:
        stage = session.get(ExecutionStage, stage_id)
        generation = stage.schedule_generation
        assert stage.state == ("queued" if window == "claim" else "unknown")
    for _ in range(3):
        deliver(stage_id, generation)
    assert len(requests({stage_id})) == (1 if window == "claim" else 0)
    with get_session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.stage_id == stage_id)) == 1
        assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 1
    close_unknown(operation_id)


def test_duplicate_workers_no_lock_spans_transport(synthetic_case):
    _, _, _, stage_id = accepted(synthetic_case)
    entered, release = Event(), Event()

    class PausingTransport(SyntheticTransport):
        def send(self, permit):
            entered.set()
            assert release.wait(10)
            return super().send(permit)

    with ThreadPoolExecutor(max_workers=5) as executor:
        first = executor.submit(deliver, stage_id, 1, PausingTransport())
        assert entered.wait(5)
        with get_session_factory()() as session, session.begin():
            session.execute(text("SET LOCAL lock_timeout = '500ms'"))
            # Both admission locks must be free while transport is waiting.
            assert session.scalar(text("SELECT pg_try_advisory_xact_lock(42630001)"))
            assert session.scalar(text("SELECT pg_try_advisory_xact_lock(42630002)"))
        duplicates = [executor.submit(deliver, stage_id) for _ in range(4)]
        for future in duplicates:
            future.result(timeout=5)
        release.set()
        first.result(timeout=5)
    assert len(requests({stage_id})) == 1


def test_twelve_quota_shared_across_tasks_failures_not_refunded(synthetic_case):
    stages = []
    for index in range(13):
        _, operation_id, _, stage_id = accepted(synthetic_case, scenario="refusal" if index % 2 else "timeout")
        stages.append(stage_id)
        deliver(stage_id)
        close_unknown(operation_id)
    assert len(requests(set(stages))) == 12
    with get_session_factory()() as session:
        assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 12
        calls = list(
            session.scalars(select(ModelCall).where(ModelCall.experiment_run_id == synthetic_case["experiment_run_id"]))
        )
        assert len(calls) == 12 and {call.quota_sequence for call in calls} == set(range(1, 13))
        assert all(call.cost_actual is None and call.cost_estimated is None for call in calls)
        assert session.get(ExecutionStage, stages[-1]).state == "dispatch_blocked"
    with pytest.raises(IntegrityError), get_session_factory()() as session, session.begin():
        session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count = 0


@pytest.mark.parametrize("window", ["before_intent", "after_intent", "after_send"])
def test_cancel_serializes_dispatch_and_late_results(synthetic_case, window):
    task_id, operation_id, attempt_id, stage_id = accepted(synthetic_case)
    token = claim(stage_id)
    permit = None
    if window != "before_intent":
        authorize_dispatch(stage_id, token)
    if window == "after_send":
        permit = consume_transport_permit(stage_id, token)
        result = SyntheticTransport().send(permit)
    with get_session_factory()() as session, session.begin():
        task = session.get(ProductionTask, task_id)
        cancel_task(session, task_id, {"expected_row_version": task.row_version}, "fixture")
    if window == "before_intent":
        assert authorize_dispatch(stage_id, token) is None
    elif window == "after_intent":
        assert consume_transport_permit(stage_id, token) is None
    else:
        record_result(permit, result)
        with get_session_factory()() as session:
            local = session.scalar(
                select(ExecutionStage).where(
                    ExecutionStage.attempt_id == attempt_id, ExecutionStage.kind == "local_store"
                )
            )
            local_id, generation = local.id, local.schedule_generation
        execute_local(local_id, generation)
    scan_deadlines(utcnow() + timedelta(seconds=600))
    with get_session_factory()() as session:
        assert session.get(ProductionTask, task_id).business_state == "cancelled"
        artifacts = list(session.scalars(select(ExecutionArtifact).where(ExecutionArtifact.attempt_id == attempt_id)))
        assert len(artifacts) == (1 if window == "after_send" else 0)
        assert all(artifact.is_late and artifact.feedback == "undecided" for artifact in artifacts)
    assert len(requests({stage_id})) == (1 if window == "after_send" else 0)


def test_unknown_close_retry_new_attempt_preserves_original(synthetic_case):
    _, operation_id, attempt_id, stage_id = accepted(synthetic_case, scenario="timeout")
    deliver(stage_id)
    with pytest.raises(ExecutionConflict), get_session_factory()() as session, session.begin():
        act_on_operation(
            session,
            operation_id,
            "retry",
            {"expected_row_version": 1, "acknowledge_possible_duplicate_charge": True},
            "fixture",
        )
    close_unknown(operation_id)
    with get_session_factory()() as session, session.begin():
        old = session.get(ExecutionOperation, operation_id)
        new = act_on_operation(
            session,
            operation_id,
            "retry",
            {"expected_row_version": old.row_version, "acknowledge_possible_duplicate_charge": True},
            "fixture",
        )
        assert new.id != operation_id and new.retry_of_attempt_id == attempt_id
    with get_session_factory()() as session:
        assert session.get(ExecutionAttempt, attempt_id).initial_outcome == "unknown"
        assert session.get(ExecutionOperation, operation_id).state == "unknown"


def test_capacity_wait_queue_deadline_does_not_reserve_or_redispatch(synthetic_case):
    _, busy_operation, _, busy_stage = accepted(synthetic_case, scenario="timeout")
    deliver(busy_stage)
    _, _, _, waiting_stage = accepted(synthetic_case)
    deliver(waiting_stage)
    with get_session_factory()() as session, session.begin():
        stage = session.get(ExecutionStage, waiting_stage)
        assert stage.state == "queued"
        stage.queue_deadline_at = utcnow() - timedelta(seconds=1)
        assert session.scalar(select(ModelCall.id).where(ModelCall.stage_id == waiting_stage)) is None
    close_unknown(busy_operation)
    scan_deadlines()
    with get_session_factory()() as session:
        stage = session.get(ExecutionStage, waiting_stage)
        assert stage.state == "dispatch_blocked"
        generation = stage.schedule_generation
    scan_deadlines()
    deliver(waiting_stage, generation)
    assert requests({waiting_stage}) == []
    with get_session_factory()() as session:
        assert session.get(ExecutionStage, waiting_stage).schedule_generation == generation
        assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 1
