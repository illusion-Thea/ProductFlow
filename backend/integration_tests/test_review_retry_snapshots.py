from copy import deepcopy
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_execution_legacy import headers

from productflow_backend.application.execution import legacy
from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.base import utcnow
from productflow_backend.infrastructure.db.execution_models import ExecutionAttempt, ExecutionOperation, ExecutionStage
from productflow_backend.infrastructure.db.models import WorkflowNode, WorkflowNodeRun, WorkflowRun
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.model_gateway import SyntheticTransport
from productflow_backend.presentation.api import create_app


def test_workflow_retry_uses_original_graph_and_binding(synthetic_case, monkeypatch):
    binding = {"provider_kind": "mock", "transport_kind": "stub", "model": "ORIGINAL", "n": 1}
    monkeypatch.setattr(legacy, "snapshot_provider_binding", lambda *_: deepcopy(binding))
    with TestClient(create_app()) as client:
        client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
        path = f"/api/products/{synthetic_case['product_id']}/workflow"
        client.get(path)
        response = client.post(path + "/run", json={}, headers=headers())
        assert response.status_code in {200, 202}, response.text
        run_id = response.json()["runs"][0]["id"]
        original = {}
        with get_session_factory()() as session, session.begin():
            run = session.get(WorkflowRun, run_id)
            run.status = "failed"
            for node_run in run.node_runs:
                node_run.status = "failed"
                node = session.get(WorkflowNode, node_run.node_id)
                node.status = "failed"
                op = session.scalar(
                    select(ExecutionOperation).where(ExecutionOperation.workflow_node_run_id == node_run.id)
                )
                original[node.id] = (op.id, deepcopy(op.input_snapshot_json))
                op.state = "failed"
                attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == op.id))
                attempt.terminal_at, attempt.resolved_outcome = utcnow(), "failed"
                for stage in session.scalars(select(ExecutionStage).where(ExecutionStage.attempt_id == attempt.id)):
                    stage.state = "failed"
                node.config_json = {**(node.config_json or {}), "instruction": "EDITED AFTER FAILURE"}
            version = run.row_version
        binding["model"] = "CHANGED"
        response = client.post(
            path + f"/runs/{run_id}/retry", headers=headers(), json={"expected_row_version": version}
        )
        assert response.status_code in {200, 202}, response.text
        new_id = response.json()["runs"][0]["id"]
        with get_session_factory()() as session:
            rows = session.execute(
                select(ExecutionOperation, WorkflowNodeRun.node_id)
                .join(
                    WorkflowNodeRun,
                    ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id,
                )
                .where(WorkflowNodeRun.workflow_run_id == new_id)
            ).all()
            remap = {original[node][0]: op.id for op, node in rows}
            for op, node in rows:
                expected = deepcopy(original[node][1])
                expected["dependency_operations"] = [
                    remap.get(value, value) for value in expected["dependency_operations"]
                ]
                assert op.input_snapshot_json == expected
                assert op.retry_of_attempt_id is not None


def test_image_session_retry_keeps_original_provider(monkeypatch):
    from execution_support import deliver

    binding = {"provider_kind": "mock", "transport_kind": "stub", "model": "ORIGINAL", "n": 1}
    monkeypatch.setattr(legacy, "snapshot_provider_binding", lambda *_: deepcopy(binding))

    class Refusal(SyntheticTransport):
        def send(self, permit):
            return super().send(replace(permit, snapshot={**permit.snapshot, "stub_scenario": "refusal"}))

    with TestClient(create_app()) as client:
        client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
        root = client.post("/api/image-sessions", json={"title": "frozen retry"}).json()["id"]
        path = f"/api/image-sessions/{root}"
        task = client.post(path + "/generate", headers=headers(), json={"prompt": "original prompt"}).json()[
            "generation_tasks"
        ][0]
        with get_session_factory()() as session:
            old = session.get(ExecutionOperation, task["execution"]["operation_id"])
            snapshot = deepcopy(old.input_snapshot_json)
            stage = session.scalar(
                select(ExecutionStage.id).where(ExecutionStage.attempt_id == task["execution"]["attempt_id"])
            )
        deliver(stage, transport=Refusal())
        binding["model"] = "CHANGED"
        response = client.post(
            path + f"/generation-tasks/{task['id']}/retry",
            headers=headers(),
            json={"expected_row_version": task["row_version"]},
        )
        assert response.status_code == 202, response.text
        new_task = response.json()["generation_tasks"][0]
        with get_session_factory()() as session:
            new = session.get(ExecutionOperation, new_task["execution"]["operation_id"])
            expected = {**snapshot, "generation_group_id": new.input_snapshot_json["generation_group_id"]}
            assert new.input_snapshot_json == expected
        client.post(
            path + f"/generation-tasks/{new_task['id']}/cancel",
            headers=headers(),
            json={"expected_row_version": new_task["row_version"]},
        )


def test_quarantined_workflow_without_snapshot_can_still_retry_after_acknowledgement(synthetic_case):
    from productflow_backend.application.execution.quarantine import quarantine_legacy_active

    with TestClient(create_app()) as client:
        client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
        path = f"/api/products/{synthetic_case['product_id']}/workflow"
        workflow = client.get(path).json()
        with get_session_factory()() as session, session.begin():
            run = WorkflowRun(workflow_id=workflow["id"], status="running", row_version=1)
            session.add(run)
            session.flush()
            for node in workflow["nodes"]:
                session.add(WorkflowNodeRun(workflow_run_id=run.id, node_id=node["id"], status="running"))
            old_id = run.id
        quarantine_legacy_active()
        retry_path = path + f"/runs/{old_id}/retry"
        assert client.post(retry_path, headers=headers(), json={"expected_row_version": 1}).status_code == 409
        retried = client.post(
            retry_path,
            headers=headers(),
            json={"expected_row_version": 1, "acknowledge_possible_duplicate_charge": True},
        )
        assert retried.status_code == 202, retried.text
        new_run = retried.json()["runs"][0]
        assert new_run["id"] != old_id
        with get_session_factory()() as session:
            assert session.get(WorkflowRun, old_id).status == "unknown"
            operations = list(
                session.scalars(
                    select(ExecutionOperation)
                    .join(WorkflowNodeRun, ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id)
                    .where(WorkflowNodeRun.workflow_run_id == new_run["id"])
                )
            )
            assert operations and all(op.retry_of_attempt_id is None for op in operations)
        client.post(
            path + f"/runs/{new_run['id']}/cancel", headers=headers(),
            json={"expected_row_version": new_run["row_version"]},
        )


def test_cancelling_queued_workflow_releases_node_slots_for_new_run(synthetic_case):
    with TestClient(create_app()) as client:
        client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
        path = f"/api/products/{synthetic_case['product_id']}/workflow"
        client.get(path)
        accepted = client.post(path + "/run", headers=headers(), json={})
        assert accepted.status_code == 202, accepted.text
        first = accepted.json()["runs"][0]
        cancelled = client.post(
            path + f"/runs/{first['id']}/cancel", headers=headers(),
            json={"expected_row_version": first["row_version"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        old = next(run for run in cancelled.json()["runs"] if run["id"] == first["id"])
        assert old["status"] == "cancelled"
        assert {node["status"] for node in old["node_runs"]} == {"cancelled"}
        assert {node["failure_reason"] for node in old["node_runs"]} == {"已取消"}
        with get_session_factory()() as session:
            assert set(session.scalars(
                select(WorkflowNodeRun.status).where(WorkflowNodeRun.workflow_run_id == first["id"])
            )) == {"failed"}  # Legacy storage represents cancellation as failed + reason.
        restarted = client.post(path + "/run", headers=headers(), json={})
        assert restarted.status_code == 202, restarted.text
        newest = restarted.json()["runs"][0]
        assert newest["id"] != first["id"]
        client.post(
            path + f"/runs/{newest['id']}/cancel", headers=headers(),
            json={"expected_row_version": newest["row_version"]},
        )
