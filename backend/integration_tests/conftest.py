"""Real per-run PG fixtures; intentionally outside legacy SQLite conftest."""

import hashlib
import io

import pytest
from PIL import Image

from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.models import Product, SourceAsset
from productflow_backend.infrastructure.db.prototype_models import ExperimentRun
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.storage import LocalStorage
from productflow_backend.infrastructure.verification import manifest


@pytest.fixture
def synthetic_case():
    data = manifest()
    assert get_settings().app_env == "verification"
    product_id, asset_id, experiment_id = new_id(), new_id(), new_id()
    image = io.BytesIO()
    Image.new("RGB", (32, 32), (120, 80, 180)).save(image, format="PNG")
    content = image.getvalue()
    storage_path = LocalStorage().save_product_upload(product_id, "synthetic-case.png", content)
    with get_session_factory()() as session, session.begin():
        session.add(Product(id=product_id, name="M1-01 isolated test fixture"))
        session.flush()
        session.add(
            SourceAsset(
                id=asset_id,
                product_id=product_id,
                kind="original_image",
                original_filename="synthetic-case.png",
                mime_type="image/png",
                storage_path=storage_path,
            )
        )
        session.add(
            ExperimentRun(
                id=experiment_id,
                mode="synthetic",
                status="active",
                image_limit=12,
                image_reserved_count=0,
                authorization_ref=f"{data['environment_id']}:test:{experiment_id}",
                baseline_reconciled_at=utcnow(),
                evidence_manifest_json={
                    "synthetic_assets": {asset_id: {"sha256": hashlib.sha256(content).hexdigest()}}
                },
            )
        )
    yield {
        "product_id": product_id,
        "experiment_run_id": experiment_id,
        "source_asset_ids": [asset_id],
        "sku": "SYNTHETIC-ONE",
        "content_language": "zh-CN",
        "output_spec": {"width": 64, "height": 64, "test_only": True},
    }
    # Retain all evidence/quota; prevent unfinished fixture work from executing
    # when a later test starts real publisher/worker processes on this same run.
    from sqlalchemy import select

    from productflow_backend.application.execution.recovery import cancel_task
    from productflow_backend.infrastructure.db.prototype_models import ProductionTask

    with get_session_factory()() as session:
        task_ids = list(
            session.scalars(select(ProductionTask.id).where(ProductionTask.experiment_run_id == experiment_id))
        )
    for identifier in task_ids:
        with get_session_factory()() as session, session.begin():
            task = session.get(ProductionTask, identifier)
            if task.business_state not in {"cancelling", "cancelled"}:
                cancel_task(session, identifier, {"expected_row_version": task.row_version}, "fixture_teardown")
