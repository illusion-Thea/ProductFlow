from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_execution_vertical import drain_inline

from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionArtifact,
    ExecutionAttempt,
    ExecutionEvent,
    ExecutionOperation,
    ExecutionStage,
    ModelCall,
)
from productflow_backend.infrastructure.db.models import ImageSessionGenerationTask, WorkflowNodeRun
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.presentation.api import create_app


def headers():
    return {"Idempotency-Key": str(uuid4())}


def test_free_session_candidates_keep_branch_and_ledger():
    with TestClient(create_app()) as client:
        assert client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key}).status_code == 200
        created = client.post("/api/image-sessions", json={"title": "isolated ledger"})
        assert created.status_code == 201, created.text
        identifier = created.json()["id"]
        path = f"/api/image-sessions/{identifier}"
        payload = {"prompt": "synthetic candidates", "size": "1024x1024", "generation_count": 2}
        assert client.post(path + "/generate", json=payload).status_code == 422
        key = headers()
        submitted = client.post(path + "/generate", json=payload, headers=key)
        assert submitted.status_code == 202, submitted.text
        assert client.post(path + "/generate", json=payload, headers=key).json() == submitted.json()
        task = submitted.json()["generation_tasks"][0]
        assert task["execution"]["state"] == "queued"
        drain_inline([task["execution"]["operation_id"]])
        detail = client.get(path).json()
        assert len(detail["rounds"]) == 2, detail
        assert detail["generation_tasks"][0]["status"] == "succeeded", detail
        assert {row["candidate_index"] for row in detail["rounds"]} == {1, 2}
        base = detail["rounds"][0]["generated_asset"]["id"]
        branch = client.post(
            path + "/generate", headers=headers(), json={**payload, "base_asset_id": base, "generation_count": 1}
        )
        assert branch.status_code == 202, branch.text
        branch_task = branch.json()["generation_tasks"][0]
        drain_inline([branch_task["execution"]["operation_id"]])
        detail = client.get(path).json()
        assert any(row["base_asset_id"] == base for row in detail["rounds"])
        assert len(detail["rounds"]) == 3
        operation = client.get(task["execution"]["status_url"]).json()
        assert len(operation["calls"]) == 2 and len(operation["artifacts"]) == 2


def test_free_workflow_accepted_graph_text_and_local_template(synthetic_case):
    with TestClient(create_app()) as client:
        client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
        path = f"/api/products/{synthetic_case['product_id']}/workflow"
        before = client.get(path)
        assert before.status_code == 200, before.text
        submitted = client.post(path + "/run", json={}, headers=headers())
        assert submitted.status_code in {200, 202}, submitted.text
        run = submitted.json()["runs"][0]
        with get_session_factory()() as session:
            identifiers = list(
                session.scalars(
                    select(ExecutionOperation.id)
                    .join(WorkflowNodeRun, ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id)
                    .where(WorkflowNodeRun.workflow_run_id == run["id"])
                )
            )
        assert identifiers
        # Editing the live canvas after acceptance cannot rewrite accepted text.
        context = next(node for node in before.json()["nodes"] if node["node_type"] == "product_context")
        changed = client.patch(f"/api/workflow-nodes/{context['id']}", json={"config_json": {"name": "LATE EDIT"}})
        assert changed.status_code == 200, changed.text
        drain_inline(identifiers)
        result = client.get(path).json()
        final = next(item for item in result["runs"] if item["id"] == run["id"])
        assert final["status"] == "succeeded", final
        with get_session_factory()() as session:
            calls = list(
                session.scalars(
                    select(ModelCall)
                    .join(ExecutionStage)
                    .join(ExecutionAttempt, ExecutionStage.attempt_id == ExecutionAttempt.id)
                    .where(ExecutionAttempt.operation_id.in_(identifiers))
                )
            )
            assert len(calls) == 2 and all(call.modality == "text" for call in calls)
            assert "LATE EDIT" not in str([call.model_snapshot_json for call in calls])
        assert any(row["poster_variant_id"] for row in final["node_runs"])


def test_legacy_quarantine_preserves_payload_and_retry_appends_without_implicit_send():
    import pytest
    from execution_support import requests

    from productflow_backend.application.execution.quarantine import quarantine_legacy_active, reject_legacy_dispatch
    from productflow_backend.domain.model_execution import ExecutionConflict

    with TestClient(create_app()) as client:
        client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
        root = client.post("/api/image-sessions", json={"title": "quarantine fixture"}).json()["id"]
        with get_session_factory()() as session, session.begin():
            active = ImageSessionGenerationTask(
                session_id=root,
                status="running",
                prompt="preserved request",
                size="1024x1024",
                generation_count=1,
                attempts=1,
            )
            old = ImageSessionGenerationTask(
                session_id=root,
                status="succeeded",
                prompt="successful history",
                size="1024x1024",
                generation_count=1,
                attempts=1,
            )
            session.add_all([active, old])
            session.flush()
            active_id, old_id = active.id, old.id
        assert quarantine_legacy_active() >= 1
        with pytest.raises(ExecutionConflict, match="ledger_required"):
            reject_legacy_dispatch()
        with get_session_factory()() as session:
            assert session.get(ImageSessionGenerationTask, active_id).status == "unknown"
            assert session.get(ImageSessionGenerationTask, active_id).prompt == "preserved request"
            assert session.get(ImageSessionGenerationTask, old_id).status == "succeeded"
            event = session.scalar(select(ExecutionEvent).where(ExecutionEvent.legacy_source_id == active_id))
            assert event.event_type == "legacy_unverified" and len(event.payload_json["payload_sha256"]) == 64
            assert (
                session.scalar(
                    select(ExecutionOperation.id).where(ExecutionOperation.image_session_task_id == active_id)
                )
                is None
            )
        path = f"/api/image-sessions/{root}/generation-tasks/{active_id}/retry"
        assert client.post(path, headers=headers(), json={"expected_row_version": 1}).status_code == 409
        retried = client.post(
            path, headers=headers(), json={"expected_row_version": 1, "acknowledge_possible_duplicate_charge": True}
        )
        assert retried.status_code == 202, retried.text
        with get_session_factory()() as session:
            new = session.scalar(
                select(ImageSessionGenerationTask).where(ImageSessionGenerationTask.retry_of_task_id == active_id)
            )
            assert new and new.id != active_id
            operation = session.scalar(
                select(ExecutionOperation).where(ExecutionOperation.image_session_task_id == new.id)
            )
            stages = list(
                session.scalars(
                    select(ExecutionStage)
                    .join(ExecutionAttempt, ExecutionStage.attempt_id == ExecutionAttempt.id)
                    .where(ExecutionAttempt.operation_id == operation.id)
                )
            )
            assert requests({stage.id for stage in stages}) == []
            new_id, version = new.id, new.row_version
        cancelled = client.post(
            f"/api/image-sessions/{root}/generation-tasks/{new_id}/cancel",
            headers=headers(),
            json={"expected_row_version": version},
        )
        assert cancelled.status_code == 200


def test_controlled_session_cancel_preserves_late_artifact_under_original_attempt():
    from execution_support import requests
    from test_execution_artifacts import local_stage
    from test_execution_postgres import claim

    from productflow_backend.application.execution.dispatch import authorize_dispatch, consume_transport_permit
    from productflow_backend.application.execution.result_store import execute_local, record_result
    from productflow_backend.infrastructure.model_gateway import SyntheticTransport

    with TestClient(create_app()) as client:
        client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
        root = client.post("/api/image-sessions", json={"title": "late fixture"}).json()["id"]
        path = f"/api/image-sessions/{root}"
        submitted = client.post(
            path + "/generate",
            headers=headers(),
            json={"prompt": "synthetic late", "size": "1024x1024", "generation_count": 1},
        )
        task = submitted.json()["generation_tasks"][0]
        operation_id, attempt_id = task["execution"]["operation_id"], task["execution"]["attempt_id"]
        with get_session_factory()() as session:
            stage_id = session.scalar(select(ExecutionStage.id).where(ExecutionStage.attempt_id == attempt_id))
        token = claim(stage_id)
        authorize_dispatch(stage_id, token)
        permit = consume_transport_permit(stage_id, token)
        result = SyntheticTransport().send(permit)
        response = client.post(
            f"{path}/generation-tasks/{task['id']}/cancel",
            headers=headers(),
            json={"expected_row_version": task["row_version"]},
        )
        assert response.status_code == 200, response.text
        record_result(permit, result)
        execute_local(*local_stage(attempt_id))
        detail = client.get(path).json()
        assert detail["generation_tasks"][0]["status"] == "cancelled"
        assert detail["rounds"] == []  # Late artifact is archived, not a revived live round.
        with get_session_factory()() as session:
            artifact = session.scalar(select(ExecutionArtifact).where(ExecutionArtifact.attempt_id == attempt_id))
            assert artifact.is_late and artifact.feedback == "undecided"
            assert session.get(ExecutionOperation, operation_id).state != "succeeded"
        assert len(requests({stage_id})) == 1
