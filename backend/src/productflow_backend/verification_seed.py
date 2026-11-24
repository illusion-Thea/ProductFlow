"""Idempotent synthetic fixtures. Never imports historical model verification."""

import hashlib
import io
import json
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.models import Product, SourceAsset
from productflow_backend.infrastructure.db.prototype_models import ExperimentRun
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.provider_config import ensure_provider_config_bootstrapped
from productflow_backend.infrastructure.storage import LocalStorage
from productflow_backend.infrastructure.verification import manifest


def main() -> None:
    data = manifest()
    with get_session_factory()() as session:
        existing = session.scalar(
            select(ExperimentRun).where(ExperimentRun.authorization_ref == data["environment_id"])
        )
        if existing:
            print("Synthetic experiment already exists; quota and records preserved.")
            return
    product_id, asset_id, experiment_id = new_id(), new_id(), new_id()
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (92, 134, 174)).save(buffer, format="PNG")
    content = buffer.getvalue()
    path = LocalStorage().save_product_upload(product_id, "synthetic.png", content)
    with get_session_factory()() as session, session.begin():
        session.add(Product(id=product_id, name="M1-01 synthetic fixture"))
        session.flush()
        session.add(
            SourceAsset(
                id=asset_id,
                product_id=product_id,
                kind="original_image",
                original_filename="synthetic.png",
                mime_type="image/png",
                storage_path=path,
            )
        )
        session.add(
            ExperimentRun(
                id=experiment_id,
                mode="synthetic",
                status="active",
                image_limit=12,
                image_reserved_count=0,
                authorization_ref=data["environment_id"],
                baseline_reconciled_at=utcnow(),
                evidence_manifest_json={
                    "synthetic_assets": {asset_id: {"sha256": hashlib.sha256(content).hexdigest()}}
                },
            )
        )
    ensure_provider_config_bootstrapped()
    (Path(data["run_root"]) / "evidence/seed.json").write_text(
        json.dumps(
            {
                "experiment_run_id": experiment_id,
                "product_id": product_id,
                "source_asset_id": asset_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Seeded synthetic fixture and experiment; no historical data or real credentials imported.")


if __name__ == "__main__":
    main()
