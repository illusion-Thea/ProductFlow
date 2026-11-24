from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from execution_support import accepted, close_unknown, deliver, requests
from sqlalchemy import select
from test_execution_postgres import claim

from productflow_backend.application.execution.dispatch import authorize_dispatch, consume_transport_permit
from productflow_backend.application.execution.recovery import act_on_operation, scan_deadlines
from productflow_backend.application.execution.result_store import execute_local, record_result, spool_result
from productflow_backend.domain.model_execution import ExecutionConflict
from productflow_backend.infrastructure.db.base import utcnow
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionArtifact,
    ExecutionAttempt,
    ExecutionEvent,
    ExecutionOperation,
    ExecutionStage,
    LocalRecoveryExecution,
    ModelCall,
)
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.model_gateway import SyntheticTransport
from productflow_backend.infrastructure.storage import LocalStorage


def local_stage(attempt_id):
    with get_session_factory()() as session:
        stage = session.scalar(
            select(ExecutionStage).where(ExecutionStage.attempt_id == attempt_id, ExecutionStage.kind == "local_store")
        )
        return stage.id, stage.schedule_generation


def action(operation_id, name):
    with get_session_factory()() as session, session.begin():
        operation = session.get(ExecutionOperation, operation_id)
        act_on_operation(session, operation_id, name, {"expected_row_version": operation.row_version}, "fixture")


def expire_local(identifier):
    with get_session_factory()() as session, session.begin():
        recovery = session.scalar(
            select(LocalRecoveryExecution).where(
                LocalRecoveryExecution.stage_id == identifier, LocalRecoveryExecution.state.in_(["queued", "running"])
            )
        )
        recovery.deadline_at = utcnow() - timedelta(seconds=1)
    scan_deadlines()


def test_spool_success_before_database_registration_recovers_without_sending(synthetic_case):
    _, operation_id, attempt_id, stage_id = accepted(synthetic_case)
    token = claim(stage_id)
    authorize_dispatch(stage_id, token)
    permit = consume_transport_permit(stage_id, token)
    result = SyntheticTransport().send(permit)
    spool_result(permit, result)  # Crash here: no result metadata has reached PG.
    with get_session_factory()() as session:
        assert session.scalar(select(ModelCall).where(ModelCall.stage_id == stage_id)).response_hash is None
    scan_deadlines()
    execute_local(*local_stage(attempt_id))
    scan_deadlines()
    deliver(stage_id)
    with get_session_factory()() as session:
        call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage_id))
        assert call.provider_request_id == result.request_id and call.usage_json == result.usage
        assert session.get(ExecutionOperation, operation_id).state == "succeeded"
        assert (
            len(list(session.scalars(select(ExecutionArtifact).where(ExecutionArtifact.attempt_id == attempt_id)))) == 1
        )
    assert len(requests({stage_id})) == 1


def test_local_storage_failure_finite_resume_same_attempt_zero_model_requests(synthetic_case, monkeypatch):
    _, operation_id, attempt_id, stage_id = accepted(synthetic_case)
    deliver(stage_id)
    identifier, generation = local_stage(attempt_id)
    original = LocalStorage.save_execution_bytes

    def unavailable(self, path, payload):
        if path.startswith("artifacts/"):
            raise OSError("fixture disk unavailable")
        return original(self, path, payload)

    monkeypatch.setattr(LocalStorage, "save_execution_bytes", unavailable)
    execute_local(identifier, generation)
    expire_local(identifier)
    with pytest.raises(ExecutionConflict):
        action(operation_id, "retry")
    with get_session_factory()() as session:
        assert session.get(ExecutionOperation, operation_id).state == "awaiting_local_decision"
        assert session.get(ExecutionAttempt, attempt_id).terminal_at is None
    action(operation_id, "resume-local")
    monkeypatch.setattr(LocalStorage, "save_execution_bytes", original)
    execute_local(*local_stage(attempt_id))
    with get_session_factory()() as session:
        recoveries = list(
            session.scalars(
                select(LocalRecoveryExecution)
                .where(LocalRecoveryExecution.stage_id == identifier)
                .order_by(LocalRecoveryExecution.recovery_sequence)
            )
        )
        assert [row.state for row in recoveries] == ["timed_out", "succeeded"]
        assert {row.attempt_id for row in recoveries} == {attempt_id}
        assert session.get(ExecutionOperation, operation_id).state == "succeeded"
    assert len(requests({stage_id})) == 1


def test_end_local_wins_late_worker_only_archives_original_artifact(synthetic_case, monkeypatch):
    _, operation_id, attempt_id, stage_id = accepted(synthetic_case)
    deliver(stage_id)
    identifier, generation = local_stage(attempt_id)
    entered, release = Event(), Event()
    original = LocalStorage.save_execution_bytes

    def paused(self, path, payload):
        if path.startswith("artifacts/"):
            entered.set()
            assert release.wait(10)
        return original(self, path, payload)

    monkeypatch.setattr(LocalStorage, "save_execution_bytes", paused)
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(execute_local, identifier, generation)
        assert entered.wait(5)
        expire_local(identifier)
        action(operation_id, "end-local")
        release.set()
        worker.result(timeout=5)
    with get_session_factory()() as session:
        attempt = session.get(ExecutionAttempt, attempt_id)
        assert attempt.resolved_outcome == "failed" and attempt.outcome_reason == "local_recovery_ended_by_user"
        assert session.get(ExecutionOperation, operation_id).state == "failed"
        artifact = session.scalar(select(ExecutionArtifact).where(ExecutionArtifact.attempt_id == attempt_id))
        assert artifact.is_late and artifact.feedback == "undecided"
    with pytest.raises(ExecutionConflict):
        action(operation_id, "resume-local")
    assert len(requests({stage_id})) == 1


def test_success_wins_end_local_conflicts_and_duplicate_result_is_idempotent(synthetic_case):
    _, operation_id, attempt_id, stage_id = accepted(synthetic_case)
    token = claim(stage_id)
    authorize_dispatch(stage_id, token)
    permit = consume_transport_permit(stage_id, token)
    result = SyntheticTransport().send(permit)
    record_result(permit, result)
    execute_local(*local_stage(attempt_id))
    record_result(permit, result)
    execute_local(*local_stage(attempt_id))
    with pytest.raises(ExecutionConflict):
        action(operation_id, "end-local")
    with get_session_factory()() as session:
        artifacts = list(session.scalars(select(ExecutionArtifact).where(ExecutionArtifact.attempt_id == attempt_id)))
        assert len(artifacts) == 1 and not artifacts[0].is_late
    assert len(requests({stage_id})) == 1


def test_unknown_cannot_be_ended_as_local_failure(synthetic_case):
    task_id, operation_id, attempt_id, stage_id = accepted(synthetic_case, scenario="timeout")
    deliver(stage_id)
    for name in ("end-local", "resume-local"):
        with pytest.raises(ExecutionConflict):
            action(operation_id, name)
    close_unknown(operation_id)
    with get_session_factory()() as session:
        from productflow_backend.infrastructure.db.prototype_models import ProductionTask

        assert session.get(ProductionTask, task_id).issue_code == "execution_needs_attention"
        assert session.get(ExecutionAttempt, attempt_id).initial_outcome == "unknown"
        call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage_id))
        assert call.cost_actual is None and call.cost_estimated is None


def test_execution_storage_immutable_hash_and_path_escape(synthetic_case):
    storage = LocalStorage()
    path = f"spool/{synthetic_case['experiment_run_id']}/immutable.bin"
    storage.save_execution_bytes(path, b"original")
    storage.save_execution_bytes(path, b"original")
    with pytest.raises(ValueError, match="conflict"):
        storage.save_execution_bytes(path, b"different")
    with pytest.raises(ValueError):
        storage.save_execution_bytes("../../outside.bin", b"unsafe")
    assert storage.resolve(path).read_bytes() == b"original"


def test_expired_worker_artifact_can_be_consumed_by_explicit_new_local_epoch(synthetic_case, monkeypatch):
    _, operation_id, attempt_id, stage_id = accepted(synthetic_case)
    deliver(stage_id)
    identifier, generation = local_stage(attempt_id)
    entered, release = Event(), Event()
    original = LocalStorage.save_execution_bytes

    def paused(self, path, payload):
        if path.startswith("artifacts/"):
            entered.set()
            assert release.wait(10)
        return original(self, path, payload)

    monkeypatch.setattr(LocalStorage, "save_execution_bytes", paused)
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(execute_local, identifier, generation)
        assert entered.wait(5)
        expire_local(identifier)
        release.set()
        worker.result(timeout=5)
    monkeypatch.setattr(LocalStorage, "save_execution_bytes", original)
    action(operation_id, "resume-local")
    execute_local(*local_stage(attempt_id))
    with get_session_factory()() as session:
        recoveries = list(
            session.scalars(
                select(LocalRecoveryExecution)
                .where(LocalRecoveryExecution.stage_id == identifier)
                .order_by(LocalRecoveryExecution.recovery_sequence)
            )
        )
        assert [row.state for row in recoveries] == ["timed_out", "succeeded"]
        assert session.get(ExecutionOperation, operation_id).state == "succeeded"
        artifacts = list(session.scalars(select(ExecutionArtifact).where(ExecutionArtifact.attempt_id == attempt_id)))
        assert len(artifacts) == 1 and artifacts[0].is_late  # Original provenance remains.
    assert len(requests({stage_id})) == 1


def test_conflicting_response_retains_original_and_quarantines_bytes(synthetic_case):
    from productflow_backend.application.execution.ports import TransportResult

    _, _, attempt_id, stage_id = accepted(synthetic_case)
    token = claim(stage_id)
    authorize_dispatch(stage_id, token)
    permit = consume_transport_permit(stage_id, token)
    original = SyntheticTransport().send(permit)
    record_result(permit, original)
    conflicting = TransportResult("different-request", b"conflicting bytes", "image/png", {})
    record_result(permit, conflicting)
    execute_local(*local_stage(attempt_id))
    with get_session_factory()() as session:
        call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage_id))
        assert call.provider_request_id == original.request_id
        event = session.scalar(
            select(ExecutionEvent).where(
                ExecutionEvent.event_type == "result_conflict",
                ExecutionEvent.dedupe_key.like(f"result-conflict:{stage_id}:%"),
            )
        )
        assert LocalStorage().resolve(event.payload_json["quarantine_path"]).read_bytes() == conflicting.payload
        assert LocalStorage().resolve(call.response_path).read_bytes() == original.payload
    assert len(requests({stage_id})) == 1
