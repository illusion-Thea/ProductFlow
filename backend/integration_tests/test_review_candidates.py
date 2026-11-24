import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_execution_legacy import headers
from test_execution_vertical import drain_inline

from productflow_backend.application.execution.result_store import record_result
from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.execution_models import ExecutionArtifact, ExecutionStage, ModelCall
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.model_gateway import SyntheticTransport, UnknownProviderOutcome
from productflow_backend.presentation.api import create_app


@pytest.mark.parametrize("unknown_count", [1, 2])
def test_close_first_unknown_wait_keeps_remaining_candidates_live(unknown_count):
    from execution_support import close_unknown, deliver, requests

    lost = []

    class LoseResponse(SyntheticTransport):
        def send(self, permit):
            lost.append((permit, super().send(permit)))
            raise UnknownProviderOutcome("response lost after send")

    with TestClient(create_app()) as client:
        client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
        root = client.post("/api/image-sessions", json={"title": "request-scoped wait"}).json()["id"]
        path = f"/api/image-sessions/{root}"
        response = client.post(
            path + "/generate",
            headers=headers(),
            json={
                "prompt": "three independent candidates",
                "size": "1024x1024",
                "generation_count": 3,
            },
        )
        assert response.status_code == 202, response.text
        execution = response.json()["generation_tasks"][0]["execution"]
        with get_session_factory()() as session:
            stages = list(
                session.scalars(
                    select(ExecutionStage)
                    .where(
                        ExecutionStage.attempt_id == execution["attempt_id"],
                    )
                    .order_by(ExecutionStage.stage_key)
                )
            )
            first, second, third = [stage.id for stage in stages]
        deliver(first, transport=LoseResponse())
        close_unknown(execution["operation_id"])
        if unknown_count == 2:
            deliver(second, transport=LoseResponse())
            current = client.get(path).json()["generation_tasks"][0]["execution"]
            assert current["state"] == "unknown" and current["wait_closed_at"] is None
            close_unknown(execution["operation_id"])
        drain_inline([execution["operation_id"]])
        detail = client.get(path).json()
        assert {row["candidate_index"] for row in detail["rounds"]} == set(range(unknown_count + 1, 4))
        assert len(requests({first, second, third})) == 3
        for result in lost:
            record_result(*result)
        drain_inline([execution["operation_id"]])
        assert len(client.get(path).json()["rounds"]) == 3 - unknown_count
        with get_session_factory()() as session:
            artifacts = list(
                session.scalars(
                    select(ExecutionArtifact).where(
                        ExecutionArtifact.attempt_id == execution["attempt_id"],
                    )
                )
            )
            assert len(artifacts) == 3
            assert sum(artifact.is_late for artifact in artifacts) == unknown_count
        with get_session_factory()() as session, session.begin():
            call = session.scalar(select(ModelCall).where(ModelCall.stage_id == first))
            original_closure = call.wait_abandoned_at
            assert original_closure is not None
            with pytest.raises(IntegrityError, match="request_wait_closure_immutable"), session.begin_nested():
                call.wait_abandoned_at = None
                session.flush()
            assert session.get(ModelCall, call.id).wait_abandoned_at == original_closure
