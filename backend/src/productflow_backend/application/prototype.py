from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from productflow_backend.application.execution.submission import accept_operation
from productflow_backend.application.prototype_contracts import (
    PrototypeOutputSpec,
    PrototypePlanPayload,
    PrototypeSlotPayload,
)
from productflow_backend.config import get_settings
from productflow_backend.domain.errors import NotFoundError
from productflow_backend.domain.model_execution import ExecutionConflict, canonical_hash, require_version
from productflow_backend.domain.prototype import ContentLanguage, require_live, validate_confirmation, validate_slots
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.execution_models import ExecutionOperation
from productflow_backend.infrastructure.db.execution_repository import capacity_lock, lock_task, locked
from productflow_backend.infrastructure.db.models import Product, SourceAsset
from productflow_backend.infrastructure.db.prototype_models import (
    ExperimentRun,
    PlanSnapshot,
    ProductionBatch,
    ProductionItem,
    ProductionTask,
    SlotPlan,
)
from productflow_backend.infrastructure.provider_config import snapshot_provider_binding


def authorization(actor: str, root_id: str) -> dict[str, Any]:
    return {
        "actor_kind": "shared_admin",
        "actor_label": actor,
        "root_id": root_id,
        "method": "verified_shared_key",
        "policy_version": "m101-v1",
    }


def batch_items(session: Session, task_id: str) -> tuple[ProductionBatch, list[ProductionItem]]:
    batch = session.scalar(select(ProductionBatch).where(ProductionBatch.task_id == task_id))
    if not batch:
        raise NotFoundError("prototype_batch_not_found")
    items = list(
        session.scalars(
            select(ProductionItem).where(ProductionItem.batch_id == batch.id).order_by(ProductionItem.slot_index)
        )
    )
    return batch, items


def checked_evidence(session: Session, product_id: str, experiment: ExperimentRun, ids: list[str]) -> list[dict]:
    registered = experiment.evidence_manifest_json.get("synthetic_assets", {})
    if not ids or len(ids) != len(set(ids)):
        raise ExecutionConflict("synthetic_original_required")
    result = []
    for identifier in ids:
        asset = session.get(SourceAsset, identifier)
        if not asset or asset.product_id != product_id or identifier not in registered:
            raise ExecutionConflict("unregistered_or_cross_product_synthetic_evidence")
        result.append(
            {
                "source_asset_id": identifier,
                "sha256": registered[identifier]["sha256"],
                "role": "product_original",
                "origin": "synthetic",
                "storage_path": asset.storage_path,
            }
        )
    return result


def create_task(session: Session, product_id: str, payload: dict, actor: str) -> ProductionTask:
    capacity_lock(session)
    experiment = locked(session, ExperimentRun, payload["experiment_run_id"])
    if experiment.mode != "synthetic" or experiment.status != "active":
        raise ExecutionConflict("active_synthetic_experiment_required")
    locked(session, Product, product_id)
    evidence = checked_evidence(session, product_id, experiment, payload["source_asset_ids"])
    spec = PrototypeOutputSpec.model_validate(payload["output_spec"]).model_dump(mode="json")
    language = ContentLanguage(payload.get("content_language", "zh-CN")).value
    task = ProductionTask(
        id=new_id(),
        product_id=product_id,
        experiment_run_id=experiment.id,
        actor_label=actor,
        draft_content_language=language,
        draft_json={"evidence": evidence, "sku": payload.get("sku"), "output_spec": spec},
        row_version=1,
        status_version=1,
        draft_revision=1,
        business_state="draft",
    )
    session.add(task)
    session.flush()
    batch = ProductionBatch(id=new_id(), task_id=task.id, sku=payload.get("sku"), output_spec_json=spec)
    session.add(batch)
    session.flush()
    session.add_all(ProductionItem(id=new_id(), batch_id=batch.id, slot_index=index) for index in range(1, 11))
    session.flush()
    return task


def editable_task(session: Session, task_id: str, expected_version: int) -> ProductionTask:
    task = lock_task(session, task_id)
    require_version(task.row_version, expected_version)
    require_live(task.business_state)
    if session.scalar(
        select(ExecutionOperation.id).where(
            ExecutionOperation.prototype_task_id == task_id, ExecutionOperation.purpose == "initial"
        )
    ):
        raise ExecutionConflict("initial_work_already_accepted_use_individual_retry")
    return task


def update_draft(session: Session, task_id: str, payload: dict) -> ProductionTask:
    task = editable_task(session, task_id, payload["expected_row_version"])
    batch, _ = batch_items(session, task.id)
    experiment = session.get(ExperimentRun, task.experiment_run_id)
    evidence = checked_evidence(session, task.product_id, experiment, payload["source_asset_ids"])
    spec = PrototypeOutputSpec.model_validate(payload["output_spec"]).model_dump(mode="json")
    task.draft_content_language = ContentLanguage(payload["content_language"]).value
    task.draft_json = {"evidence": evidence, "sku": payload.get("sku"), "output_spec": spec}
    task.draft_revision += 1
    task.row_version += 1
    task.current_plan_id = None
    task.business_state = "draft"
    batch.sku, batch.output_spec_json = payload.get("sku"), spec
    batch.row_version += 1
    return task


def synthetic_provider(session: Session, kind: str) -> dict:
    return {**snapshot_provider_binding(session, kind), "synthetic_fixture": True, "schema_version": 1}


def prepare_text(session: Session, task_id: str, payload: dict, actor: str) -> ExecutionOperation:
    task = editable_task(session, task_id, payload["expected_row_version"])
    if payload["content_language"] != task.draft_content_language:
        raise ExecutionConflict("draft_language_conflict")
    if session.scalar(
        select(ExecutionOperation.id).where(
            ExecutionOperation.prototype_task_id == task.id,
            ExecutionOperation.kind == "text",
            ExecutionOperation.state.in_(["queued", "running"]),
        )
    ):
        raise ExecutionConflict("text_preparation_already_active")
    snapshot = {
        **task.draft_json,
        "schema_version": 1,
        "content_language": task.draft_content_language,
        "source_draft_revision": task.draft_revision,
        "provider": synthetic_provider(session, "text"),
        "image_provider": synthetic_provider(session, "image"),
    }
    operation, _ = accept_operation(
        session,
        kind="text",
        purpose="text",
        snapshot=snapshot,
        authorization=authorization(actor, task.id),
        prototype_task_id=task.id,
        experiment_run_id=task.experiment_run_id,
    )
    task.row_version += 1
    return operation


def store_plan(
    session: Session,
    task: ProductionTask,
    slots: list[dict],
    *,
    language: str,
    source_revision: int,
    snapshot: dict,
    text_operation_id: str | None = None,
) -> PlanSnapshot:
    validated = [PrototypeSlotPayload.model_validate(slot) for slot in slots]
    validate_slots((slot.slot_index for slot in validated), complete=False)
    _, items = batch_items(session, task.id)
    item_map = {item.slot_index: item for item in items}
    evidence_ids = {e["source_asset_id"] for e in snapshot["evidence"]}
    for slot in validated:
        if slot.item_id and slot.item_id != item_map[slot.slot_index].id:
            raise ExecutionConflict("slot_belongs_to_another_task")
        if not set(slot.fact_refs + slot.reference_refs).issubset(evidence_ids):
            raise ExecutionConflict("slot_reference_outside_accepted_evidence")
    revision = 1 + (session.scalar(select(func.max(PlanSnapshot.revision)).where(PlanSnapshot.task_id == task.id)) or 0)
    snapshot = {**snapshot, "image_provider": snapshot.get("image_provider") or synthetic_provider(session, "image")}
    content = {
        "content_language": language,
        "source_draft_revision": source_revision,
        "snapshot": snapshot,
        "slots": [slot.model_dump(mode="json") for slot in validated],
    }
    plan = PlanSnapshot(
        id=new_id(),
        task_id=task.id,
        revision=revision,
        preview_revision=revision,
        source_draft_revision=source_revision,
        content_language=language,
        text_operation_id=text_operation_id,
        status="validated" if len(validated) == 10 else "draft",
        evidence_snapshot_json=snapshot["evidence"],
        spec_snapshot_json=snapshot["output_spec"],
        model_snapshot_json={"text": snapshot.get("provider"), "image": snapshot["image_provider"]},
        template_ref_json={"id": "synthetic", "version": 1},
        input_sha256=canonical_hash(content),
    )
    session.add(plan)
    session.flush()
    for slot in validated:
        session.add(
            SlotPlan(
                plan_id=plan.id,
                item_id=item_map[slot.slot_index].id,
                slot_index=slot.slot_index,
                content_language=language,
                preview_revision=revision,
                copy_payload_json=slot.copy_payload.model_dump(mode="json"),
                base_prompt=slot.base_prompt,
                fact_refs_json=slot.fact_refs,
                reference_refs_json=slot.reference_refs,
            )
        )
    session.flush()
    if (
        task.draft_revision == source_revision
        and task.draft_content_language == language
        and task.business_state not in {"cancelling", "cancelled"}
    ):
        task.current_plan_id = plan.id
        task.business_state = "awaiting_generation_confirmation" if len(validated) == 10 else "draft"
        task.status_version += 1
    return plan


def receive_text_plan(session: Session, task: ProductionTask, operation: ExecutionOperation, content: dict) -> None:
    parsed = PrototypePlanPayload.model_validate(content)
    if (
        parsed.content_language != operation.input_snapshot_json["content_language"]
        or parsed.source_draft_revision != operation.source_draft_revision
    ):
        raise ExecutionConflict("text_result_does_not_match_accepted_input")
    store_plan(
        session,
        task,
        [slot.model_dump(mode="json") for slot in parsed.slots],
        language=parsed.content_language.value,
        source_revision=parsed.source_draft_revision,
        snapshot=operation.input_snapshot_json,
        text_operation_id=operation.id,
    )


def revise_plan(session: Session, task_id: str, plan_id: str, payload: dict) -> ProductionTask:
    task = editable_task(session, task_id, payload["expected_row_version"])
    original = session.get(PlanSnapshot, plan_id)
    if not original or original.task_id != task.id:
        raise NotFoundError("plan_not_found")
    store_plan(
        session,
        task,
        payload["slots"],
        language=task.draft_content_language,
        source_revision=task.draft_revision,
        snapshot=task.draft_json,
    )
    task.row_version += 1
    return task


def confirm_plan(session: Session, task_id: str, payload: dict, actor: str) -> ProductionTask:
    task = editable_task(session, task_id, payload["expected_row_version"])
    plan = session.get(PlanSnapshot, payload["plan_id"])
    if not plan or plan.task_id != task.id or task.current_plan_id != plan.id or plan.status != "validated":
        raise ExecutionConflict("current_validated_plan_required")
    batch, _ = batch_items(session, task.id)
    validate_confirmation(
        sku=batch.sku,
        current_language=task.draft_content_language,
        plan_language=payload["content_language"],
        current_preview=plan.preview_revision,
        supplied_preview=payload["preview_revision"],
        actual_hash=plan.input_sha256,
        supplied_hash=payload["plan_sha256"],
    )
    if plan.content_language != task.draft_content_language or plan.source_draft_revision != task.draft_revision:
        raise ExecutionConflict("stale_text_preview")
    slots = list(session.scalars(select(SlotPlan).where(SlotPlan.plan_id == plan.id)))
    validate_slots((slot.slot_index for slot in slots), complete=True)
    plan.status, plan.confirmed_at, plan.confirmed_by_label = "accepted", utcnow(), actor
    task.row_version += 1
    return task


def generate(
    session: Session, task_id: str, payload: dict, actor: str, *, probe: bool = False
) -> list[ExecutionOperation]:
    task = editable_task(session, task_id, payload["expected_row_version"])
    plan = session.get(PlanSnapshot, payload["accepted_plan_id"])
    if not plan or plan.task_id != task.id or plan.status != "accepted" or plan.id != task.current_plan_id:
        raise ExecutionConflict("current_accepted_plan_required")
    batch, items = batch_items(session, task.id)
    slots = list(session.scalars(select(SlotPlan).where(SlotPlan.plan_id == plan.id).order_by(SlotPlan.slot_index)))
    operations = []
    for slot, item in zip(slots, items, strict=True):
        if probe and slot.slot_index != payload["slot_index"]:
            continue
        snapshot = {
            "schema_version": 1,
            "sku": batch.sku,
            "evidence": plan.evidence_snapshot_json,
            "output_spec": plan.spec_snapshot_json,
            "content_language": slot.content_language,
            "preview_revision": slot.preview_revision,
            "slot_index": slot.slot_index,
            "copy_payload": slot.copy_payload_json,
            "base_prompt": slot.base_prompt,
            "fact_refs": slot.fact_refs_json,
            "reference_refs": slot.reference_refs_json,
            "provider": plan.model_snapshot_json["image"],
            "release_id": get_settings().execution_release_id,
        }
        operation, attempt = accept_operation(
            session,
            kind="image",
            purpose="probe" if probe else "initial",
            snapshot=snapshot,
            authorization=authorization(actor, task.id),
            prototype_task_id=task.id,
            item_id=None if probe else item.id,
            plan_id=plan.id,
            experiment_run_id=task.experiment_run_id,
        )
        if not probe:
            if item.first_attempt_id is not None:
                raise ExecutionConflict("initial_attempt_already_exists")
            item.first_attempt_id = attempt.id
        operations.append(operation)
    task.row_version += 1
    task.business_state = "generating"
    return operations
