import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from dramatiq import Message
from execution_support import accepted, close_unknown, requests
from sqlalchemy import select

from productflow_backend.application.execution.recovery import cancel_task, scan_deadlines
from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.base import utcnow
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionOperation,
    ExecutionStage,
    ModelCall,
    OutboxEvent,
)
from productflow_backend.infrastructure.db.prototype_models import ExperimentRun, ProductionTask
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.outbox import execution_broker, publish_one
from productflow_backend.infrastructure.verification import manifest
from productflow_backend.infrastructure.verification_process import start_python


def wait_for(predicate, timeout=15):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("isolated process verification deadline exceeded")


def state(operation_id):
    with get_session_factory()() as session:
        return session.get(ExecutionOperation, operation_id).state


def message(stage_id, generation=1, environment=None, release=None):
    settings = get_settings()
    with get_session_factory()() as session:
        stage = session.get(ExecutionStage, stage_id)
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.stage_id == stage_id).order_by(OutboxEvent.schedule_generation.desc())
        )
        attempt_id, event_id = stage.attempt_id, event.id
    return Message(
        queue_name=settings.execution_queue_name,
        actor_name="m101_execute_stage",
        args=(
            stage_id,
            generation,
            environment or settings.execution_environment_id,
            release or settings.execution_release_id,
            1,
            attempt_id,
            event_id,
            "m101-v1",
        ),
        kwargs={},
        options={"max_retries": 0},
    )


@contextmanager
def processes(*modules):
    root = Path(manifest()["run_root"])
    children = []
    streams = []
    identities = []
    try:
        for module in modules:
            path = root / "evidence" / f"process-{module}-{uuid4().hex[:8]}.log"
            stream = path.open("ab")
            streams.append(stream)
            child = start_python(
                [sys.executable, "-B", "-m", f"productflow_backend.{module}"],
                env=os.environ.copy(),
                stdout=stream,
            )
            children.append(child)
            identities.append(
                {"pid": child.pid, "creation_time": child.creation_time, "module": module, "log": path.name}
            )
        assert len({child.pid for child in children}) == len(children)
        with (root / "evidence/processes.jsonl").open("a", encoding="utf-8") as evidence:
            evidence.write(json.dumps({"environment_id": manifest()["environment_id"], "children": identities}) + "\n")
        yield children
        assert all(child.poll() is None for child in children), identities
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        for stream in streams:
            stream.close()


def retire_prior_test_fixtures(except_experiment):
    """Cancel only this run's named synthetic tests; retain rows, bytes and quotas."""
    with get_session_factory()() as session:
        ids = list(
            session.scalars(
                select(ProductionTask.id)
                .join(ExperimentRun)
                .where(
                    ExperimentRun.authorization_ref.startswith(manifest()["environment_id"] + ":test:"),
                    ExperimentRun.id != except_experiment,
                    ProductionTask.business_state.not_in(["cancelled", "cancelling"]),
                )
            )
        )
    for identifier in ids:
        with get_session_factory()() as session, session.begin():
            task = session.get(ProductionTask, identifier)
            cancel_task(session, identifier, {"expected_row_version": task.row_version}, "retire_synthetic_fixture")
    scan_deadlines()


def publish_until_sent(stage_id):
    for _ in range(5000):
        with get_session_factory()() as session:
            event = session.scalar(
                select(OutboxEvent)
                .where(OutboxEvent.stage_id == stage_id)
                .order_by(OutboxEvent.schedule_generation.desc())
            )
            if event.publish_state == "sent":
                return
        publish_one()
    raise AssertionError("target Outbox event not published")


def test_two_workers_duplicate_stale_and_cross_environment_delivery(synthetic_case):
    retire_prior_test_fixtures(synthetic_case["experiment_run_id"])
    _, operation_id, _, stage_id = accepted(synthetic_case)
    broker = execution_broker()
    # Invalid deliveries cannot claim work or reserve quota.
    with processes("verification_worker", "verification_worker") as workers:
        for item in (
            message(stage_id, 0),
            message(stage_id, environment="other-environment"),
            message(stage_id, release="other-release"),
        ):
            broker.enqueue(item)
        time.sleep(0.8)
        assert state(operation_id) == "queued" and not requests({stage_id})
        for _ in range(8):
            broker.enqueue(message(stage_id))
        wait_for(lambda: len(requests({stage_id})) == 1)
        with processes("outbox_publisher", "execution_scanner"):
            wait_for(lambda: state(operation_id) == "succeeded")
        assert len(requests({stage_id})) == 1
        assert requests({stage_id})[0]["pid"] in {worker.pid for worker in workers}
    with get_session_factory()() as session:
        assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 1


def test_sent_message_loss_recovered_by_independent_scanner_and_publisher(synthetic_case):
    _, operation_id, _, stage_id = accepted(synthetic_case)
    publish_until_sent(stage_id)
    broker = execution_broker()
    consumer = broker.consume(get_settings().execution_queue_name, timeout=100)
    found = False
    try:
        for _ in range(5000):
            item = next(consumer)
            if item is None:
                continue
            consumer.ack(item)  # Deliberately lose this run's delivery before any worker executes it.
            if item.args[0] == stage_id:
                found = True
                break
    finally:
        consumer.close()
    assert found and not requests({stage_id})
    with get_session_factory()() as session, session.begin():
        session.get(ExecutionStage, stage_id).next_delivery_check_at = utcnow() - timedelta(seconds=1)
    with processes("verification_worker", "verification_worker", "outbox_publisher", "execution_scanner"):
        wait_for(lambda: state(operation_id) == "succeeded")
    with get_session_factory()() as session:
        assert session.get(ExecutionStage, stage_id).schedule_generation >= 2
    assert len(requests({stage_id})) == 1


def test_publish_after_send_before_sent_commit_can_redeliver_only_once(synthetic_case, monkeypatch):
    _, operation_id, _, stage_id = accepted(synthetic_case)
    broker = execution_broker()
    original = broker.enqueue

    def sent_then_lost_ack(item, **kwargs):
        result = original(item, **kwargs)
        if item.args[0] == stage_id:
            raise ConnectionError("fixture publisher lost send acknowledgement")
        return result

    monkeypatch.setattr(broker, "enqueue", sent_then_lost_ack)
    for _ in range(5000):
        publish_one()
        with get_session_factory()() as session:
            event = session.scalar(select(OutboxEvent).where(OutboxEvent.stage_id == stage_id))
            if event.publish_state == "leased":
                event_id = event.id
                break
    monkeypatch.setattr(broker, "enqueue", original)
    with get_session_factory()() as session, session.begin():
        session.get(OutboxEvent, event_id).lease_until = utcnow() - timedelta(seconds=1)
    publish_until_sent(stage_id)
    with processes("verification_worker", "verification_worker", "outbox_publisher", "execution_scanner"):
        wait_for(lambda: state(operation_id) == "succeeded")
    assert len(requests({stage_id})) == 1


@pytest.mark.parametrize(
    "scenario,window,count", [("pause_before_send", "before-send", 0), ("pause_after_send", "after-send", 1)]
)
def test_killed_worker_after_permit_never_regenerates(synthetic_case, scenario, window, count):
    _, operation_id, _, stage_id = accepted(synthetic_case, scenario=scenario)
    marker = Path(manifest()["run_root"]) / "evidence" / f"paused-{stage_id}-{window}.json"
    with processes("verification_worker"):
        execution_broker().enqueue(message(stage_id))
        wait_for(marker.exists)
    # The context has killed the exact owned process while it held the consumed permit.
    with get_session_factory()() as session, session.begin():
        call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage_id))
        call.wait_deadline_at = utcnow() - timedelta(seconds=1)
    with processes("verification_worker", "execution_scanner"):
        for _ in range(4):
            execution_broker().enqueue(message(stage_id))
        wait_for(lambda: state(operation_id) == "unknown")
        time.sleep(0.4)
    assert len(requests({stage_id})) == count
    with get_session_factory()() as session:
        assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 1
    close_unknown(operation_id)
