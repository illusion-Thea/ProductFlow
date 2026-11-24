"""Bridge existing roots to accepted, frozen execution. No provider calls here."""

from __future__ import annotations

import hashlib
from copy import deepcopy

from sqlalchemy import select

from productflow_backend.application.execution.submission import accept_operation
from productflow_backend.application.prototype import authorization
from productflow_backend.config import get_runtime_settings, get_settings
from productflow_backend.domain.errors import NotFoundError
from productflow_backend.domain.model_execution import ExecutionConflict, require_retry, require_version
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.execution_models import ExecutionAttempt, ExecutionOperation, ExecutionStage
from productflow_backend.infrastructure.db.execution_repository import capacity_lock, locked
from productflow_backend.infrastructure.db.models import (
    CopySet,
    ImageSession,
    ImageSessionAsset,
    ImageSessionGenerationTask,
    Product,
    SourceAsset,
    WorkflowNodeRun,
    WorkflowRun,
)
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.provider_config import snapshot_provider_binding
from productflow_backend.infrastructure.storage import LocalStorage


def enabled() -> bool:
    settings = get_settings()
    return settings.prototype_execution_enabled and settings.app_env == "verification" and settings.execution_stub_only


def asset_identity(asset) -> dict:
    return {
        "id": asset.id,
        "storage_path": asset.storage_path,
        "mime_type": asset.mime_type,
        "kind": str(asset.kind),
        "original_filename": asset.original_filename,
    }


def prepare_asset_manifest(kind: str, root_id: str) -> dict:
    model, column = (
        (ImageSessionAsset, ImageSessionAsset.session_id)
        if kind == "session"
        else (SourceAsset, SourceAsset.product_id)
    )
    with get_session_factory()() as session:
        assets = [asset_identity(asset) for asset in session.scalars(select(model).where(column == root_id))]
    storage = LocalStorage()
    try:
        return {
            asset["id"]: {
                **asset,
                "sha256": hashlib.sha256(storage.resolve(asset["storage_path"]).read_bytes()).hexdigest(),
            }
            for asset in assets
        }
    except (OSError, ValueError):
        raise ExecutionConflict("accepted_input_bytes_unavailable") from None


def asset_snapshot(session, asset) -> dict:
    prepared = session.info.get("execution_asset_manifest", {}).get(asset.id)
    if not prepared or any(prepared[key] != value for key, value in asset_identity(asset).items()):
        raise ExecutionConflict("input_changed_during_acceptance_retry_same_key")
    return prepared


def accept_image_session(
    session, session_id: str, payload: dict, actor: str, retry_task: ImageSessionGenerationTask | None = None
) -> ImageSessionGenerationTask:
    from productflow_backend.application.image_sessions import (
        _get_image_session_or_raise,
        _normalize_generation_prompt,
        _normalize_tool_options,
        _validate_generation_request,
    )

    capacity_lock(session)
    locked(session, ImageSession, session_id)
    root = _get_image_session_or_raise(session, session_id)
    if retry_task:
        previous_op = session.scalar(
            select(ExecutionOperation).where(ExecutionOperation.image_session_task_id == retry_task.id)
        )
        if previous_op:
            from productflow_backend.application.execution.legacy_retries import retry_session

            return retry_session(session, root, previous_op, actor)
    prompt = _normalize_generation_prompt(payload["prompt"])
    options = _normalize_tool_options(payload.get("tool_options"))
    size, base_id, reference_ids = _validate_generation_request(
        root,
        size=payload.get("size", "1024x1024"),
        base_asset_id=payload.get("base_asset_id"),
        selected_reference_asset_ids=payload.get("selected_reference_asset_ids", []),
        generation_count=payload.get("generation_count", 1),
        tool_options=options,
    )
    ids = set(reference_ids or []) | ({base_id} if base_id else set())
    assets = [asset_snapshot(session, asset) for asset in root.assets if asset.id in ids]
    task = ImageSessionGenerationTask(
        id=new_id(),
        session_id=session_id,
        status="queued",
        prompt=prompt,
        size=size,
        base_asset_id=base_id,
        selected_reference_asset_ids=reference_ids,
        tool_options=options,
        generation_count=payload.get("generation_count", 1),
        row_version=1,
        retry_of_task_id=retry_task.id if retry_task else None,
    )
    session.add(task)
    session.flush()
    width, height = (int(value) for value in size.split("x"))
    snapshot = {
        "schema_version": 1,
        "legacy_kind": "image_session",
        "session_id": session_id,
        "prompt": prompt,
        "base_asset_id": base_id,
        "selected_reference_asset_ids": reference_ids,
        "assets": assets,
        "tool_options": options,
        "output_spec": {"width": width, "height": height},
        "generation_count": task.generation_count,
        "generation_group_id": new_id(),
        "provider": snapshot_provider_binding(session, "image"),
    }
    previous = None
    if retry_task:
        previous_op = session.scalar(
            select(ExecutionOperation).where(ExecutionOperation.image_session_task_id == retry_task.id)
        )
        if previous_op:
            previous = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == previous_op.id))
    accept_operation(
        session,
        kind="image",
        purpose="legacy",
        snapshot=snapshot,
        authorization=authorization(actor, session_id),
        source_kind="image_session",
        image_session_task_id=task.id,
        retry_of=previous,
        stage_kinds=[(f"candidate-{index}", "model_image") for index in range(1, task.generation_count + 1)],
    )
    root.updated_at = utcnow()
    return task


def retry_image_session(session, session_id: str, task_id: str, payload: dict, actor: str):
    capacity_lock(session)
    locked(session, ImageSession, session_id)
    task = locked(session, ImageSessionGenerationTask, task_id)
    if task.session_id != session_id:
        raise NotFoundError("image_session_task_not_found")
    require_version(task.row_version, payload["expected_row_version"])
    operation = session.scalar(select(ExecutionOperation).where(ExecutionOperation.image_session_task_id == task.id))
    if operation:
        attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == operation.id))
        require_retry(
            operation.state,
            operation.wait_closed_at is not None,
            payload.get("acknowledge_possible_duplicate_charge", False),
            attempt.terminal_at,
        )
    elif task.status not in {"failed", "unknown"}:
        raise ExecutionConflict("legacy_task_not_retryable")
    elif task.status == "unknown" and not payload.get("acknowledge_possible_duplicate_charge", False):
        raise ExecutionConflict("legacy_unverified_requires_duplicate_charge_acknowledgement")
    return accept_image_session(
        session,
        session_id,
        {
            "prompt": task.prompt,
            "size": task.size,
            "base_asset_id": task.base_asset_id,
            "selected_reference_asset_ids": task.selected_reference_asset_ids,
            "tool_options": task.tool_options,
            "generation_count": task.generation_count,
        },
        actor,
        retry_task=task,
    )


def cancel_image_session(session, session_id: str, task_id: str, payload: dict) -> None:
    capacity_lock(session)
    locked(session, ImageSession, session_id)
    task = locked(session, ImageSessionGenerationTask, task_id)
    if task.session_id != session_id:
        raise NotFoundError("image_session_task_not_found")
    require_version(task.row_version, payload["expected_row_version"])
    task.status, task.row_version, task.finished_at = "cancelled", task.row_version + 1, utcnow()
    task.is_retryable = False
    operations = session.scalars(select(ExecutionOperation).where(ExecutionOperation.image_session_task_id == task_id))
    for operation in operations:
        cancel_undispatched(session, operation)


def cancel_undispatched(session, operation: ExecutionOperation) -> None:
    from productflow_backend.infrastructure.db.execution_models import ModelCall

    attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == operation.id))
    stages = list(session.scalars(select(ExecutionStage).where(ExecutionStage.attempt_id == attempt.id)))
    for stage in stages:
        if stage.kind.startswith("model_") and not session.scalar(
            select(ModelCall.id).where(ModelCall.stage_id == stage.id)
        ):
            stage.state, stage.claim_token = "cancelled", None
            stage.claim_epoch += 1
        elif stage.kind in {"local_parse", "local_render_legacy"} and stage.state == "queued":
            stage.state = "cancelled"
    if all(stage.state == "cancelled" for stage in stages):
        operation.state, attempt.resolved_outcome, attempt.terminal_at = "cancelled", "cancelled", utcnow()


def accept_workflow(
    session, product_id: str, payload: dict, actor: str, retry_run: WorkflowRun | None = None
) -> WorkflowRun:
    from productflow_backend.application.product_workflow import graph
    from productflow_backend.application.product_workflow.execution import (
        _active_workflow_run_for_nodes,
        _execute_product_context,
        _execute_reference_image,
        _node_ids_to_run,
    )
    from productflow_backend.application.product_workflow.mutations import get_or_create_product_workflow

    capacity_lock(session)
    locked(session, Product, product_id)
    if retry_run and session.scalar(
        select(ExecutionOperation.id)
        .join(WorkflowNodeRun, ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id)
        .where(WorkflowNodeRun.workflow_run_id == retry_run.id)
        .limit(1)
    ):
        from productflow_backend.application.execution.legacy_retries import retry_workflow

        return retry_workflow(session, product_id, retry_run, actor)
    workflow = get_or_create_product_workflow(session, product_id, commit=False)
    selected = (
        {row.node_id for row in retry_run.node_runs if row.status != "succeeded"}
        if retry_run
        else _node_ids_to_run(session, workflow, payload.get("start_node_id"))
    )
    if not selected or _active_workflow_run_for_nodes(workflow, selected):
        raise ExecutionConflict("workflow_nodes_empty_or_busy")
    ordered = graph.topological_nodes(workflow)
    product = workflow.product
    assets = [asset_snapshot(session, asset) for asset in product.source_assets]
    nodes = [
        {
            "id": node.id,
            "node_type": str(node.node_type),
            "title": node.title,
            "config": deepcopy(node.config_json or {}),
            "output": deepcopy(node.output_json or {}),
        }
        for node in ordered
    ]
    edges = [
        {
            "id": edge.id,
            "created_at": edge.created_at.isoformat(),
            "source": edge.source_node_id,
            "target": edge.target_node_id,
            "source_handle": edge.source_handle,
            "target_handle": edge.target_handle,
        }
        for edge in workflow.edges
    ]
    product_snapshot = {
        "id": product.id,
        "name": product.name,
        "category": product.category,
        "price": str(product.price) if product.price is not None else None,
        "source_note": product.source_note,
    }
    copies = {
        row.id: deepcopy(row.structured_payload)
        for row in session.scalars(select(CopySet).where(CopySet.product_id == product_id))
    }
    run = WorkflowRun(
        id=new_id(),
        workflow_id=workflow.id,
        status="running",
        row_version=1,
        progress_metadata={"retry_of_run_id": retry_run.id} if retry_run else None,
    )
    session.add(run)
    session.flush()
    operation_by_node = {}
    for node in ordered:
        if node.id not in selected:
            continue
        node_run = WorkflowNodeRun(id=new_id(), workflow_run_id=run.id, node_id=node.id, status="queued")
        session.add(node_run)
        session.flush()
        node.status, node.failure_reason, node.last_run_at = "queued", None, utcnow()
        frozen = next(row for row in nodes if row["id"] == node.id)
        incoming = [edge["source"] for edge in edges if edge["target"] == node.id]
        targets = [
            edge["target"]
            for edge in edges
            if edge["source"] == node.id
            and next(row for row in nodes if row["id"] == edge["target"])["node_type"] == "reference_image"
        ]
        snapshot = {
            "schema_version": 1,
            "legacy_kind": "workflow",
            "product": product_snapshot,
            "node": frozen,
            "graph_nodes": nodes,
            "graph_edges": edges,
            "assets": assets,
            "copies": copies,
            "dependency_operations": [operation_by_node[key] for key in incoming if key in operation_by_node],
            "target_node_ids": targets,
            "provider": snapshot_provider_binding(session, "image"),
        }
        if node.node_type == "copy_generation":
            kind, stages = "text", [("brief", "model_text"), ("copy", "model_text")]
            snapshot["provider"] = snapshot_provider_binding(session, "text")
            snapshot["stage_dependencies"] = {"copy": ["store:brief"]}
            if (node.output_json or {}).get("manual_edit"):
                kind, stages = "local", [("context", "local_parse")]
                snapshot["local_output"] = deepcopy(node.output_json)
        elif node.node_type == "image_generation":
            if not targets:
                raise ExecutionConflict("image_generation_requires_reference_target")
            local = get_runtime_settings().poster_generation_mode == "template"
            snapshot["local_render"] = local
            snapshot["poster_font_path"] = str(get_runtime_settings().poster_font_path)
            kind = "image"
            stages = [
                (f"candidate-{index}", "local_render_legacy" if local else "model_image")
                for index in range(1, len(targets) + 1)
            ]
            size = (node.config_json or {}).get("size") or "1024x1024"
            width, height = (int(value) for value in size.split("x"))
            snapshot["output_spec"] = {"width": width, "height": height}
        else:
            kind, stages = "local", [("context", "local_parse")]
            snapshot["local_output"] = (
                _execute_product_context(product, node)
                if node.node_type == "product_context"
                else _execute_reference_image(session, workflow=workflow, node=node)
            )
        previous = None
        if retry_run:
            previous = session.scalar(
                select(ExecutionAttempt)
                .join(ExecutionOperation, ExecutionAttempt.operation_id == ExecutionOperation.id)
                .join(WorkflowNodeRun, ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id)
                .where(WorkflowNodeRun.workflow_run_id == retry_run.id, WorkflowNodeRun.node_id == node.id)
            )
        operation, _ = accept_operation(
            session,
            kind=kind,
            purpose="legacy",
            snapshot=snapshot,
            authorization=authorization(actor, product_id),
            source_kind="workflow",
            workflow_node_run_id=node_run.id,
            stage_kinds=stages,
            retry_of=previous,
        )
        operation_by_node[node.id] = operation.id
    workflow.updated_at = utcnow()
    return run


def change_workflow_run(session, product_id: str, run_id: str, action: str, payload: dict, actor: str):
    from productflow_backend.infrastructure.db.models import ProductWorkflow

    capacity_lock(session)
    locked(session, Product, product_id)
    run = locked(session, WorkflowRun, run_id)
    if session.get(ProductWorkflow, run.workflow_id).product_id != product_id:
        raise NotFoundError("workflow_run_not_found")
    require_version(run.row_version, payload["expected_row_version"])
    operations = list(
        session.scalars(
            select(ExecutionOperation)
            .join(WorkflowNodeRun, ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id)
            .where(WorkflowNodeRun.workflow_run_id == run.id)
            .order_by(ExecutionOperation.id)
        )
    )
    if action == "cancel":
        from productflow_backend.application.product_workflow.run_state import mark_workflow_run_cancelled

        mark_workflow_run_cancelled(session, run_id=run.id, commit=False)
        run.row_version += 1
        for operation in operations:
            cancel_undispatched(session, operation)
        return run
    if run.status not in {"failed", "unknown"}:
        raise ExecutionConflict("workflow_run_not_retryable")
    for operation in operations:
        if operation.state == "succeeded":
            continue
        attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == operation.id))
        require_retry(
            operation.state,
            operation.wait_closed_at is not None,
            payload.get("acknowledge_possible_duplicate_charge", False),
            attempt.terminal_at,
        )
    if not operations and run.status == "unknown" and not payload.get("acknowledge_possible_duplicate_charge", False):
        raise ExecutionConflict("legacy_unverified_requires_duplicate_charge_acknowledgement")
    return accept_workflow(session, product_id, {}, actor, retry_run=run)


def dependencies_ready(session, operation: ExecutionOperation, stage: ExecutionStage) -> bool:
    for identifier in operation.input_snapshot_json.get("dependency_operations", []):
        parent = session.get(ExecutionOperation, identifier)
        if not parent or parent.state != "succeeded":
            return False
    for key in operation.input_snapshot_json.get("stage_dependencies", {}).get(stage.stage_key, []):
        if not session.scalar(
            select(ExecutionStage.id).where(
                ExecutionStage.attempt_id == stage.attempt_id,
                ExecutionStage.stage_key == key,
                ExecutionStage.state == "succeeded",
            )
        ):
            return False
    return True
