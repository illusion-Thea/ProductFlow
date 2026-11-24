"""Append retries from accepted input, never from the current editor/provider."""

from copy import deepcopy

from sqlalchemy import select

from productflow_backend.application.execution.submission import accept_operation
from productflow_backend.application.prototype import authorization
from productflow_backend.domain.model_execution import ExecutionConflict
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.execution_models import ExecutionAttempt, ExecutionOperation, ExecutionStage
from productflow_backend.infrastructure.db.models import (
    ImageSessionGenerationTask,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)


def validate_frozen_assets(session, snapshot: dict) -> None:
    prepared = session.info.get("execution_asset_manifest", {})
    for asset in snapshot.get("assets", []):
        if prepared.get(asset["id"]) != asset:
            raise ExecutionConflict("accepted_input_bytes_unavailable")


def retry_session(session, root, previous_op, actor):
    snapshot = deepcopy(previous_op.input_snapshot_json)
    validate_frozen_assets(session, snapshot)
    previous = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == previous_op.id))
    spec = snapshot["output_spec"]
    task = ImageSessionGenerationTask(
        id=new_id(),
        session_id=root.id,
        status="queued",
        prompt=snapshot["prompt"],
        size=f"{spec['width']}x{spec['height']}",
        base_asset_id=snapshot["base_asset_id"],
        selected_reference_asset_ids=snapshot["selected_reference_asset_ids"],
        tool_options=snapshot["tool_options"],
        generation_count=snapshot["generation_count"],
        row_version=1,
        retry_of_task_id=previous_op.image_session_task_id,
    )
    session.add(task)
    session.flush()
    snapshot["generation_group_id"] = new_id()
    accept_operation(
        session,
        kind=previous_op.kind,
        purpose="legacy",
        snapshot=snapshot,
        authorization=authorization(actor, root.id),
        source_kind="image_session",
        image_session_task_id=task.id,
        retry_of=previous,
        parent_operation_id=previous_op.id,
        stage_kinds=[(f"candidate-{index}", "model_image") for index in range(1, task.generation_count + 1)],
    )
    root.updated_at = utcnow()
    return task


def retry_workflow(session, product_id, previous_run, actor):
    from productflow_backend.application.product_workflow.execution import _active_workflow_run_for_nodes

    rows = session.execute(
        select(ExecutionOperation, WorkflowNodeRun)
        .join(
            WorkflowNodeRun,
            ExecutionOperation.workflow_node_run_id == WorkflowNodeRun.id,
        )
        .where(WorkflowNodeRun.workflow_run_id == previous_run.id)
    ).all()
    selected = {node.node_id: (operation, node) for operation, node in rows if node.status != "succeeded"}
    workflow = previous_run.workflow
    if not selected or _active_workflow_run_for_nodes(workflow, set(selected)):
        raise ExecutionConflict("workflow_nodes_empty_or_busy")
    # Accepted graph_nodes are in the original topological order. Live edge
    # edits can neither reorder retries nor substitute their parent outputs.
    graph = next(iter(selected.values()))[0].input_snapshot_json["graph_nodes"]
    ordered = [row["id"] for row in graph if row["id"] in selected]
    if set(ordered) != set(selected):
        raise ExecutionConflict("accepted_graph_incomplete")
    run = WorkflowRun(
        id=new_id(),
        workflow_id=previous_run.workflow_id,
        status="running",
        row_version=1,
        progress_metadata={"retry_of_run_id": previous_run.id},
    )
    session.add(run)
    session.flush()
    replacements = {}
    for node_id in ordered:
        old, _ = selected[node_id]
        snapshot = deepcopy(old.input_snapshot_json)
        validate_frozen_assets(session, snapshot)
        node = session.get(WorkflowNode, node_id)
        if node is None or node.workflow_id != run.workflow_id:
            raise ExecutionConflict("accepted_node_unavailable")
        node_run = WorkflowNodeRun(id=new_id(), workflow_run_id=run.id, node_id=node_id, status="queued")
        session.add(node_run)
        session.flush()
        previous = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == old.id))
        stages = list(
            session.execute(
                select(ExecutionStage.stage_key, ExecutionStage.kind)
                .where(
                    ExecutionStage.attempt_id == previous.id,
                    ExecutionStage.kind != "local_store",
                )
                .order_by(ExecutionStage.created_at, ExecutionStage.stage_key)
            )
        )
        snapshot["dependency_operations"] = [
            replacements.get(identifier, identifier) for identifier in snapshot.get("dependency_operations", [])
        ]
        operation, _ = accept_operation(
            session,
            kind=old.kind,
            purpose="legacy",
            snapshot=snapshot,
            authorization=authorization(actor, product_id),
            source_kind="workflow",
            workflow_node_run_id=node_run.id,
            stage_kinds=stages,
            retry_of=previous,
            parent_operation_id=old.id,
        )
        replacements[old.id] = operation.id
        node.status, node.failure_reason, node.last_run_at = "queued", None, utcnow()
    workflow.updated_at = utcnow()
    return run
