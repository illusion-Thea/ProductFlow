from uuid import uuid4

import pytest
from execution_support import accepted
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError

from productflow_backend.application.prototype import batch_items, create_task, store_plan
from productflow_backend.domain.model_execution import ExecutionUnavailable
from productflow_backend.infrastructure.db.execution_models import ExecutionOperation, ExecutionStage
from productflow_backend.infrastructure.db.execution_repository import idempotent
from productflow_backend.infrastructure.db.prototype_models import ProductionItem, ProductionTask, SlotPlan
from productflow_backend.infrastructure.db.session import get_session_factory


def test_deferred_ten_items_first_attempt_and_stage_input_owner(synthetic_case):
    task_id, _, attempt_id, stage_id = accepted(synthetic_case)
    with get_session_factory()() as session:
        _, items = batch_items(session, task_id)
        item_id = items[0].id
    with pytest.raises(IntegrityError), get_session_factory()() as session, session.begin():
        session.delete(session.get(ProductionItem, item_id))
    with pytest.raises(IntegrityError), get_session_factory()() as session, session.begin():
        # A probe attempt cannot become a slot's original initial attempt.
        session.get(ProductionItem, item_id).first_attempt_id = attempt_id
    with pytest.raises(IntegrityError), get_session_factory()() as session, session.begin():
        session.get(ExecutionStage, stage_id).input_hash = "0" * 64


def test_current_plan_cross_root_and_accepted_slot_edits_rejected(synthetic_case):
    with get_session_factory()() as session, session.begin():
        task = create_task(session, synthetic_case["product_id"], synthetic_case, "fixture")
        other = create_task(session, synthetic_case["product_id"], synthetic_case, "fixture")
        slots = [
            {
                "slot_index": index,
                "copy_payload": {
                    "version": 2,
                    "summary": "fixture",
                    "content": {"kind": "freeform", "text": "synthetic"},
                },
                "base_prompt": "synthetic fixture",
            }
            for index in range(1, 11)
        ]
        plan = store_plan(session, task, slots, language="zh-CN", source_revision=1, snapshot=task.draft_json)
        plan.status = "accepted"
        task_id, other_id, plan_id = task.id, other.id, plan.id
    with pytest.raises(IntegrityError), get_session_factory()() as session, session.begin():
        session.get(ProductionTask, other_id).current_plan_id = plan_id
    with pytest.raises(IntegrityError), get_session_factory()() as session, session.begin():
        session.scalar(select(SlotPlan).where(SlotPlan.plan_id == plan_id)).base_prompt = "changed after acceptance"
    with get_session_factory()() as session:
        assert session.get(ProductionTask, task_id).current_plan_id == plan_id


def test_commit_succeeded_but_ack_lost_replays_exact_original_once(synthetic_case, monkeypatch):
    key = str(uuid4())
    with get_session_factory()() as session:
        actual_commit = session.commit

        def unknown_ack():
            actual_commit()
            raise DBAPIError("COMMIT", {}, ConnectionError("fixture commit acknowledgement lost"))

        monkeypatch.setattr(session, "commit", unknown_ack)

        def action():
            task = create_task(session, synthetic_case["product_id"], synthetic_case, "fixture")
            return 201, {"id": task.id, "accepted": True}

        with pytest.raises(ExecutionUnavailable):
            idempotent(session, actor="fixture", route="commit-window", key=key, payload=synthetic_case, action=action)
    with get_session_factory()() as session:

        def must_not_run():
            pytest.fail("committed action must not be repeated")

        status, response = idempotent(
            session, actor="fixture", route="commit-window", key=key, payload=synthetic_case, action=must_not_run
        )
        assert status == 201 and response["accepted"]
        tasks = list(
            session.scalars(
                select(ProductionTask).where(ProductionTask.experiment_run_id == synthetic_case["experiment_run_id"])
            )
        )
        assert len(tasks) == 1 and tasks[0].id == response["id"]
        assert (
            session.scalar(select(ExecutionOperation.id).where(ExecutionOperation.prototype_task_id == tasks[0].id))
            is None
        )


@pytest.mark.parametrize("window", ["intent", "permit"])
def test_dispatch_commit_ack_unknown_never_sends(synthetic_case, monkeypatch, window):
    from datetime import timedelta

    from execution_support import close_unknown, deliver, requests
    from sqlalchemy.orm import SessionTransaction
    from test_execution_postgres import claim

    from productflow_backend.application.execution.dispatch import authorize_dispatch, consume_transport_permit
    from productflow_backend.application.execution.recovery import scan_deadlines
    from productflow_backend.infrastructure.db.base import utcnow
    from productflow_backend.infrastructure.db.execution_models import ModelCall
    from productflow_backend.infrastructure.db.prototype_models import ExperimentRun

    _, operation_id, _, stage_id = accepted(synthetic_case)
    token = claim(stage_id)
    if window == "permit":
        authorize_dispatch(stage_id, token)
    original = SessionTransaction.commit

    def unknown_ack(transaction, *args, **kwargs):
        original(transaction, *args, **kwargs)
        if transaction.parent is None:
            raise DBAPIError("COMMIT", {}, ConnectionError("fixture acknowledgement lost"))

    with monkeypatch.context() as patch:
        patch.setattr(SessionTransaction, "commit", unknown_ack)
        with pytest.raises(DBAPIError):
            (authorize_dispatch if window == "intent" else consume_transport_permit)(stage_id, token)
    with get_session_factory()() as session, session.begin():
        call = session.scalar(select(ModelCall).where(ModelCall.stage_id == stage_id))
        assert (call.transport_started_at is not None) == (window == "permit")
        call.wait_deadline_at = utcnow() - timedelta(seconds=1)
    scan_deadlines()
    deliver(stage_id)
    with get_session_factory()() as session:
        assert session.get(ExecutionStage, stage_id).state == "unknown"
        assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 1
    assert requests({stage_id}) == []
    close_unknown(operation_id)


def test_lock_timeout_is_bounded_and_same_key_remains_usable(synthetic_case):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import text

    from productflow_backend.infrastructure.db.execution_repository import capacity_lock

    key = str(uuid4())

    def submit():
        with get_session_factory()() as session:

            def action():
                capacity_lock(session)
                task = create_task(session, synthetic_case["product_id"], synthetic_case, "fixture")
                return 201, {"id": task.id}

            return idempotent(
                session, actor="fixture", route="lock-timeout", key=key, payload=synthetic_case, action=action
            )

    with get_session_factory()() as held, held.begin(), ThreadPoolExecutor(max_workers=1) as executor:
        held.execute(text("SELECT pg_advisory_xact_lock(42630001)"))
        with pytest.raises(ExecutionUnavailable, match="retry_same_idempotency_key"):
            executor.submit(submit).result(timeout=6)
    first = submit()
    assert submit() == first
