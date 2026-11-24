import time
from datetime import timedelta

from execution_support import accepted
from sqlalchemy import event, text
from test_execution_delivery import processes, retire_prior_test_fixtures, wait_for

from productflow_backend.application.execution.queries import task_detail
from productflow_backend.application.execution.recovery import scan_deadlines
from productflow_backend.infrastructure.db.base import utcnow
from productflow_backend.infrastructure.db.execution_models import ExecutionOperation, ExecutionStage
from productflow_backend.infrastructure.db.session import get_engine, get_session_factory


def test_scanner_survives_pg_lock_timeout_and_resumes(synthetic_case):
    retire_prior_test_fixtures(synthetic_case["experiment_run_id"])
    scan_deadlines(utcnow() + timedelta(hours=1))
    _, operation_id, _, stage_id = accepted(synthetic_case)
    with get_session_factory()() as session, session.begin():
        session.get(ExecutionStage, stage_id).queue_deadline_at = utcnow() - timedelta(seconds=1)
    with processes("execution_scanner") as workers:
        with get_session_factory()() as holder, holder.begin():
            holder.execute(text("SELECT pg_advisory_xact_lock(42630001)"))
            time.sleep(4.5)  # lock_timeout is 3 seconds; the process must retry afterwards.
            assert workers[0].poll() is None

        def expired():
            with get_session_factory()() as session:
                return session.get(ExecutionOperation, operation_id).state == "dispatch_blocked"

        wait_for(expired)


def test_light_status_query_count_and_payload_do_not_grow_with_history(synthetic_case):
    task_id, _, _, _ = accepted(synthetic_case)

    def measured():
        statements = []

        def capture(_conn, _cursor, statement, *_):
            statements.append(statement)

        event.listen(get_engine(), "before_cursor_execute", capture)
        try:
            with get_session_factory()() as session:
                result = task_detail(session, task_id, lightweight=True)
        finally:
            event.remove(get_engine(), "before_cursor_execute", capture)
        return result, statements

    first, queries_before = measured()
    for _ in range(20):
        _, identifier, _, _ = accepted(synthetic_case, task_id=task_id)
        with get_session_factory()() as session, session.begin():
            session.get(ExecutionOperation, identifier).state = "failed"
    last, queries_after = measured()
    assert len(queries_after) <= len(queries_before) + 1
    assert not any("input_snapshot_json" in statement for statement in queries_after)
    assert len(last["operations"]) <= 2  # original active operation plus latest history summary
    assert first["slots"] == last["slots"]
