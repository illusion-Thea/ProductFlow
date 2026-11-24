"""PG/API vertical check. Inline delivery here does not claim Redis coverage."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from productflow_backend.application.execution.dispatch import execute_stage
from productflow_backend.application.execution.result_store import execute_local
from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionAttempt,
    ExecutionOperation,
    ExecutionStage,
    ModelCall,
    OutboxEvent,
)
from productflow_backend.infrastructure.db.prototype_models import ExperimentRun
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.presentation.api import create_app


def drain_inline(operation_ids=None):
    import time
    from datetime import timedelta

    from productflow_backend.application.execution.legacy_local import execute_legacy_local
    from productflow_backend.application.execution.recovery import scan_deadlines
    from productflow_backend.infrastructure.db.base import utcnow

    settings = get_settings()
    for _ in range(600):
        with get_session_factory()() as session, session.begin():
            statement = select(OutboxEvent).where(OutboxEvent.publish_state == "pending")
            if operation_ids:
                statement = (
                    statement.join(ExecutionStage)
                    .join(ExecutionAttempt)
                    .where(ExecutionAttempt.operation_id.in_(operation_ids))
                )
            row = session.scalar(statement.order_by(OutboxEvent.created_at).limit(1))
            if not row:
                active = session.scalar(
                    select(ExecutionOperation.id).where(
                        ExecutionOperation.id.in_(operation_ids or []),
                        ExecutionOperation.state.in_(["queued", "running"]),
                    )
                )
                if not active:
                    return
                identifier = None
            else:
                row.publish_state = "sent"
                identifier, generation = row.stage_id, row.schedule_generation
                kind = session.get(ExecutionStage, identifier).kind
        if identifier is None:
            time.sleep(0.05)
        elif kind == "local_store":
            execute_local(identifier, generation)
        elif kind.startswith("local_"):
            execute_legacy_local(identifier, generation)
        else:
            execute_stage(identifier, generation, settings.execution_environment_id, settings.execution_release_id)
        scan_deadlines(utcnow() + timedelta(seconds=6))
    raise AssertionError("inline delivery did not drain")


def test_prototype_plan_idempotency_and_ten_independent_requests(synthetic_case):
    with TestClient(create_app()) as client:
        login = client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
        assert login.status_code == 200
        assert get_settings().session_cookie_name in login.headers["set-cookie"]
        payload = {key: value for key, value in synthetic_case.items() if key != "product_id"}
        path = f"/api/products/{synthetic_case['product_id']}/prototype-tasks"
        headers = {"Idempotency-Key": str(uuid4())}
        created = client.post(path, json=payload, headers=headers)
        assert created.status_code == 201, created.text
        assert client.post(path, json=payload, headers=headers).json() == created.json()
        assert client.post(path, json={**payload, "sku": "OTHER"}, headers=headers).status_code == 409
        task = created.json()
        assert len(task["slots"]) == 10
        task_path = f"/api/prototype-tasks/{task['id']}"
        text_payload = {"expected_row_version": task["row_version"], "content_language": "zh-CN"}
        text_headers = {"Idempotency-Key": str(uuid4())}
        prepared = client.post(task_path + "/text-preparations", json=text_payload, headers=text_headers)
        assert prepared.status_code == 202, prepared.text
        drain_inline([prepared.json()["operation_id"]])
        task = client.get(task_path).json()
        assert task["business_state"] == "awaiting_generation_confirmation", task
        assert (
            client.post(task_path + "/text-preparations", json=text_payload, headers=text_headers).json()
            == prepared.json()
        )
        plan = task["plans"][-1]
        assert len(plan["slots"]) == 10
        confirmed = client.post(
            task_path + "/confirm",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "expected_row_version": task["row_version"],
                "plan_id": plan["id"],
                "plan_sha256": plan["input_sha256"],
                "content_language": "zh-CN",
                "preview_revision": plan["preview_revision"],
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        generated = client.post(
            task_path + "/generate",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "expected_row_version": confirmed.json()["row_version"],
                "accepted_plan_id": plan["id"],
            },
        )
        assert generated.status_code == 202, generated.text
        assert len(generated.json()["operation_ids"]) == 10
        drain_inline(generated.json()["operation_ids"])
        task = client.get(task_path).json()
        assert task["business_state"] == "needs_attention", task
        assert task["issue_code"] == "composition_not_implemented"
        assert len(task["artifacts"]) == 11
        assert task["image_reserved_count"] == 10
        assert all(slot["first_attempt_id"] for slot in task["slots"])
        assert (
            client.post(
                task_path + "/generate",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "expected_row_version": task["row_version"],
                    "accepted_plan_id": plan["id"],
                },
            ).status_code
            == 409
        )
        status = client.get(task_path + "/status").json()
        assert "plans" not in status and "draft" not in status
    with get_session_factory()() as session:
        calls = list(
            session.scalars(select(ModelCall).where(ModelCall.experiment_run_id == synthetic_case["experiment_run_id"]))
        )
        assert len(calls) == 11
        assert sum(call.modality == "text" for call in calls) == 1
        assert all(call.requested_candidate_count == 1 and call.cost_actual is None for call in calls)
        experiment = session.get(ExperimentRun, synthetic_case["experiment_run_id"])
        assert experiment.image_reserved_count == 10
        assert all(
            operation.state == "succeeded"
            for operation in session.scalars(
                select(ExecutionOperation).where(ExecutionOperation.experiment_run_id == experiment.id)
            )
        )
