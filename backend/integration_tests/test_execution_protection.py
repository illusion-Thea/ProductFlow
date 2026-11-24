import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from execution_support import accepted
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_prototype_api import key

from productflow_backend.application.execution.protection import protect_product
from productflow_backend.application.prototype import create_task
from productflow_backend.application.use_cases import delete_product
from productflow_backend.config import get_settings
from productflow_backend.domain.errors import NotFoundError
from productflow_backend.domain.model_execution import ExecutionConflict
from productflow_backend.infrastructure.db.execution_models import OutboxEvent
from productflow_backend.infrastructure.db.models import Product, SourceAsset
from productflow_backend.infrastructure.db.prototype_models import ExperimentRun
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.storage import LocalStorage
from productflow_backend.presentation.api import create_app


def test_http_delete_ledger_product_session_node_and_input_preserves_rows_and_bytes(synthetic_case, monkeypatch):
    monkeypatch.setenv("DELETION_ENABLED", "true")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
            product_id = synthetic_case["product_id"]
            workflow = client.get(f"/api/products/{product_id}/workflow").json()
            accepted(synthetic_case)
            with get_session_factory()() as session, session.begin():
                asset = session.get(SourceAsset, synthetic_case["source_asset_ids"][0])
                path = LocalStorage().resolve(asset.storage_path)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                reference = SourceAsset(
                    product_id=product_id,
                    kind="reference_image",
                    mime_type="image/png",
                    original_filename="fixture-reference.png",
                    storage_path=LocalStorage().save_reference_upload(
                        product_id, "fixture-reference.png", path.read_bytes()
                    ),
                )
                session.add(reference)
                session.flush()
                reference_id = reference.id
            for url in (
                f"/api/products/{product_id}",
                f"/api/workflow-nodes/{workflow['nodes'][0]['id']}",
                f"/api/source-assets/{reference_id}",
            ):
                response = client.delete(url)
                assert response.status_code == 409, (url, response.text)
            root = client.post("/api/image-sessions", json={"title": "protected fixture"}).json()
            submitted = client.post(
                f"/api/image-sessions/{root['id']}/generate",
                headers=key(),
                json={"prompt": "synthetic", "size": "1024x1024"},
            )
            assert submitted.status_code == 202
            assert client.delete(f"/api/image-sessions/{root['id']}").status_code == 409
            task = submitted.json()["generation_tasks"][0]
            assert (
                client.post(
                    f"/api/image-sessions/{root['id']}/generation-tasks/{task['id']}/cancel",
                    headers=key(),
                    json={"expected_row_version": task["row_version"]},
                ).status_code
                == 200
            )
            assert path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
            with get_session_factory()() as session:
                assert session.get(Product, product_id) is not None
    finally:
        get_settings.cache_clear()


def test_acceptance_lock_wins_delete_gets_409(synthetic_case):
    locked, release = Event(), Event()

    def accept():
        with get_session_factory()() as session, session.begin():
            create_task(session, synthetic_case["product_id"], synthetic_case, "fixture")
            locked.set()
            assert release.wait(5)

    def delete():
        with get_session_factory()() as session, session.begin():
            protect_product(session, synthetic_case["product_id"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        accepted_work = executor.submit(accept)
        assert locked.wait(3)
        deletion = executor.submit(delete)
        release.set()
        accepted_work.result(timeout=5)
        with pytest.raises(ExecutionConflict):
            deletion.result(timeout=5)


def test_delete_commits_first_rejects_new_acceptance_without_quota_or_outbox(synthetic_case):
    with get_session_factory()() as session:
        delete_product(session, product_id=synthetic_case["product_id"])
    with pytest.raises(NotFoundError), get_session_factory()() as session, session.begin():
        create_task(session, synthetic_case["product_id"], synthetic_case, "fixture")
    with get_session_factory()() as session:
        assert session.get(Product, synthetic_case["product_id"]) is None
        assert session.get(ExperimentRun, synthetic_case["experiment_run_id"]).image_reserved_count == 0
        # The failed T1 cannot leave any root or delivery row for this experiment.
        from productflow_backend.infrastructure.db.execution_models import (
            ExecutionAttempt,
            ExecutionOperation,
            ExecutionStage,
        )

        assert (
            session.scalar(
                select(OutboxEvent.id)
                .join(ExecutionStage)
                .join(ExecutionAttempt)
                .join(ExecutionOperation, ExecutionAttempt.operation_id == ExecutionOperation.id)
                .where(ExecutionOperation.experiment_run_id == synthetic_case["experiment_run_id"])
            )
            is None
        )
