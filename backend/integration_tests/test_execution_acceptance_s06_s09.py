"""S06/S09 acceptance evidence on isolated PostgreSQL with synthetic transports.

Delivery is inline here: Redis concurrency is covered by the delivery suite.
These tests do not claim composite/R0 or real-provider quality acceptance.
"""

import hashlib
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from execution_support import deliver, requests
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_execution_vertical import drain_inline

from productflow_backend.application.execution import legacy
from productflow_backend.config import get_runtime_settings, get_settings
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionArtifact,
    ExecutionAttempt,
    ExecutionOperation,
    ExecutionStage,
    ModelCall,
)
from productflow_backend.infrastructure.db.models import PosterVariant, WorkflowNodeRun
from productflow_backend.infrastructure.db.prototype_models import ExperimentRun, ProductionItem
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.model_gateway import SyntheticTransport
from productflow_backend.infrastructure.storage import LocalStorage
from productflow_backend.presentation.api import create_app


def headers():
    return {"Idempotency-Key": str(uuid4())}


def ten_slot_run(client, case):
    response = client.post(
        f"/api/products/{case['product_id']}/prototype-tasks",
        headers=headers(),
        json={key: value for key, value in case.items() if key != "product_id"},
    )
    assert response.status_code == 201, response.text
    task = response.json()
    path = f"/api/prototype-tasks/{task['id']}"
    prepared = client.post(
        path + "/text-preparations",
        headers=headers(),
        json={"expected_row_version": task["row_version"], "content_language": "zh-CN"},
    )
    assert prepared.status_code == 202, prepared.text
    drain_inline([prepared.json()["operation_id"]])
    task = client.get(path).json()
    plan = task["plans"][-1]
    confirmed = client.post(
        path + "/confirm",
        headers=headers(),
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
        path + "/generate",
        headers=headers(),
        json={"expected_row_version": confirmed.json()["row_version"], "accepted_plan_id": plan["id"]},
    )
    assert generated.status_code == 202, generated.text
    operation_ids = generated.json()["operation_ids"]
    assert len(operation_ids) == len(set(operation_ids)) == 10
    return path, operation_ids


def historical_rows(operation_ids):
    """Capture persisted original records and bytes, including null cost values."""
    with get_session_factory()() as session:
        operations = list(session.scalars(select(ExecutionOperation).where(ExecutionOperation.id.in_(operation_ids))))
        attempts = list(
            session.scalars(select(ExecutionAttempt).where(ExecutionAttempt.operation_id.in_(operation_ids)))
        )
        attempt_ids = [row.id for row in attempts]
        stages = list(session.scalars(select(ExecutionStage).where(ExecutionStage.attempt_id.in_(attempt_ids))))
        calls = list(session.scalars(select(ModelCall).where(ModelCall.stage_id.in_([row.id for row in stages]))))
        artifacts = list(
            session.scalars(select(ExecutionArtifact).where(ExecutionArtifact.attempt_id.in_(attempt_ids)))
        )
        items = list(
            session.scalars(select(ProductionItem).where(ProductionItem.id.in_([row.item_id for row in operations])))
        )
        result = {}
        for rows in (operations, attempts, stages, calls, artifacts, items):
            for row in rows:
                result[(row.__tablename__, row.id)] = {
                    column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns
                }
        for artifact in artifacts:
            digest = hashlib.sha256(LocalStorage().resolve(artifact.storage_path).read_bytes()).hexdigest()
            assert digest == artifact.sha256
            result[("artifact_bytes", artifact.id)] = digest
        return result


@pytest.mark.parametrize("scenario, outcome", [("refusal", "failed"), ("timeout", "unknown")])
def test_s06_real_slot_retry_preserves_first_attempt_and_successful_siblings(
    synthetic_case, monkeypatch, scenario, outcome
):
    with TestClient(create_app()) as client:
        assert client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key}).status_code == 200
        path, operation_ids = ten_slot_run(client, synthetic_case)
        original_id = operation_ids[0]
        with get_session_factory()() as session:
            attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == original_id))
            first_attempt_id = attempt.id
            original = session.get(ExecutionOperation, original_id)
            item_id, original_hash = original.item_id, original.input_hash
            stage_id = session.scalar(select(ExecutionStage.id).where(ExecutionStage.attempt_id == attempt.id))
            assert item_id is not None
            assert session.get(ProductionItem, item_id).first_attempt_id == first_attempt_id

        original_send = SyntheticTransport.send

        def send_with_fault(self, permit):
            # Only the first attempt receives a transport fault. Accepted inputs stay immutable.
            if permit.stage_id == stage_id:
                permit = replace(permit, snapshot={**permit.snapshot, "stub_scenario": scenario})
            return original_send(self, permit)

        monkeypatch.setattr(SyntheticTransport, "send", send_with_fault)
        drain_inline([original_id])
        action_path = f"/api/prototype-operations/{original_id}"
        original_status = client.get(action_path).json()
        assert original_status["state"] == outcome
        assert len(requests({stage_id})) == 1
        # Re-delivery of the original message must not retry a paid request.
        deliver(stage_id)
        assert len(requests({stage_id})) == 1
        if outcome == "unknown":
            rejected = client.post(
                action_path + "/retry",
                headers=headers(),
                json={
                    "expected_row_version": original_status["row_version"],
                    "acknowledge_possible_duplicate_charge": True,
                },
            )
            assert rejected.status_code == 409, rejected.text
            closed = client.post(
                action_path + "/close-wait",
                headers=headers(),
                json={"expected_row_version": original_status["row_version"]},
            )
            assert closed.status_code == 200, closed.text
            original_status = client.get(action_path).json()
            missing_ack = client.post(
                action_path + "/retry", headers=headers(), json={"expected_row_version": original_status["row_version"]}
            )
            assert missing_ack.status_code == 409, missing_ack.text

        drain_inline(operation_ids[1:])
        with get_session_factory()() as session:
            siblings = list(
                session.scalars(select(ExecutionOperation).where(ExecutionOperation.id.in_(operation_ids[1:])))
            )
            assert len(siblings) == 9 and all(row.state == "succeeded" for row in siblings)
            assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 10
        before = historical_rows(operation_ids)
        assert sum(table == "artifact_bytes" for table, _ in before) == 9
        retry_headers = headers()
        payload = {
            "expected_row_version": original_status["row_version"],
            "acknowledge_possible_duplicate_charge": outcome == "unknown",
        }
        retried = client.post(action_path + "/retry", headers=retry_headers, json=payload)
        assert retried.status_code == 202, retried.text
        assert client.post(action_path + "/retry", headers=retry_headers, json=payload).json() == retried.json()
        retry_id = retried.json()["operation_id"]
        assert retry_id not in operation_ids
        drain_inline([retry_id])
        assert historical_rows(operation_ids) == before
        with get_session_factory()() as session:
            retry = session.get(ExecutionOperation, retry_id)
            new_attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == retry_id))
            assert retry.state == "succeeded"
            assert retry.item_id == item_id and retry.input_hash == original_hash
            assert retry.retry_of_attempt_id == first_attempt_id and retry.parent_operation_id == original_id
            assert new_attempt.id != first_attempt_id and new_attempt.previous_attempt_id == first_attempt_id
            assert session.get(ProductionItem, item_id).first_attempt_id == first_attempt_id
            calls = list(
                session.scalars(
                    select(ModelCall).where(ModelCall.experiment_run_id == synthetic_case["experiment_run_id"])
                )
            )
            image_calls = [call for call in calls if call.modality == "image"]
            assert len(image_calls) == 11
            assert {call.quota_sequence for call in image_calls} == set(range(1, 12))
            assert all(call.cost_actual is None and call.cost_estimated is None for call in calls)
            assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 11
            assert len(requests({call.stage_id for call in image_calls})) == 11
        task = client.get(path).json()
        assert len({slot["first_attempt_id"] for slot in task["slots"]}) == 10
        assert task["business_state"] != "completed"  # Bases/retries do not establish composite or R0 acceptance.


@pytest.mark.parametrize("mode", ["generated", "template"])
def test_s09_dag_image_and_template_have_distinct_request_accounting(synthetic_case, monkeypatch, mode):
    runtime = get_runtime_settings()
    # Select only the application branch; keep the runner's stub-only isolation settings.
    monkeypatch.setattr(
        legacy, "get_runtime_settings", lambda: runtime.model_copy(update={"poster_generation_mode": mode})
    )
    with TestClient(create_app()) as client:
        assert client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key}).status_code == 200
        path = f"/api/products/{synthetic_case['product_id']}/workflow"
        assert client.get(path).status_code == 200
        key = headers()
        response = client.post(path + "/run", json={}, headers=key)
        assert response.status_code in {200, 202}, response.text
        assert client.post(path + "/run", json={}, headers=key).json() == response.json()
        run_id = response.json()["runs"][0]["id"]
        with get_session_factory()() as session:
            operations = list(
                session.scalars(
                    select(ExecutionOperation)
                    .join(WorkflowNodeRun, ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id)
                    .where(WorkflowNodeRun.workflow_run_id == run_id)
                )
            )
            operation_ids = [row.id for row in operations]
            image_operations = [
                row for row in operations if row.input_snapshot_json["node"]["node_type"] == "image_generation"
            ]
            assert image_operations
            target_count = sum(len(row.input_snapshot_json["target_node_ids"]) for row in image_operations)
            assert target_count > 0
            assert all(row.input_snapshot_json["local_render"] == (mode == "template") for row in image_operations)
            image_operation_ids = {row.id for row in image_operations}
        drain_inline(operation_ids)
        with get_session_factory()() as session:
            attempts = list(
                session.scalars(select(ExecutionAttempt).where(ExecutionAttempt.operation_id.in_(operation_ids)))
            )
            attempt_ids = [row.id for row in attempts]
            stages = list(
                session.scalars(select(ExecutionStage).where(ExecutionStage.attempt_id.in_(attempt_ids)))
            )
            calls = list(session.scalars(select(ModelCall).where(ModelCall.stage_id.in_([row.id for row in stages]))))
            model_stages = [row for row in stages if row.kind.startswith("model_")]
            assert {call.stage_id for call in calls} == {row.id for row in model_stages}
            assert len(calls) == len(model_stages) == 2 + (target_count if mode == "generated" else 0)
            assert sum(call.modality == "text" for call in calls) == 2
            assert sum(call.modality == "image" for call in calls) == (target_count if mode == "generated" else 0)
            assert all(call.transport_kind == "stub" and call.requested_candidate_count == 1 for call in calls)
            assert all(call.provider_request_id == f"stub-{call.id}" for call in calls)
            assert all(call.experiment_run_id is None and call.quota_sequence is None for call in calls)
            assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 0
            image_attempts = [row.id for row in attempts if row.operation_id in image_operation_ids]
            artifacts = list(
                session.scalars(select(ExecutionArtifact).where(ExecutionArtifact.attempt_id.in_(image_attempts)))
            )
            assert len(artifacts) == target_count
            for artifact in artifacts:
                assert artifact.availability == "available" and not artifact.is_late
                content = LocalStorage().resolve(artifact.storage_path).read_bytes()
                assert hashlib.sha256(content).hexdigest() == artifact.sha256
            posters = list(
                session.scalars(select(PosterVariant).where(PosterVariant.product_id == synthetic_case["product_id"]))
            )
            assert len(posters) == target_count
            assert {row.storage_path for row in posters} == {row.storage_path for row in artifacts}
            expected_template = "synthetic" if mode == "generated" else "local_render_legacy"
            assert {row.template_name for row in posters} == {expected_template}
            all_stage_ids = {row.id for row in stages}
            request_ids = {call.id for call in calls}
            deliveries = [(row.id, row.schedule_generation) for row in model_stages]
        recorded = requests(all_stage_ids)
        assert len(recorded) == len(request_ids)
        assert {row["call_id"] for row in recorded} == request_ids
        assert all(row["n"] == 1 for row in recorded)
        for stage_id, generation in deliveries:
            deliver(stage_id, generation)
        drain_inline(operation_ids)
        assert requests(all_stage_ids) == recorded
        final = next(row for row in client.get(path).json()["runs"] if row["id"] == run_id)
        assert final["status"] == "succeeded", final
        assert any(row["poster_variant_id"] for row in final["node_runs"])
