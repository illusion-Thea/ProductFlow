from __future__ import annotations

import json

from sqlalchemy import select

from productflow_backend.application.contracts import CopyPayloadV2, CreativeBriefPayload
from productflow_backend.application.product_workflow.artifacts import copy_node_output, fill_reference_node
from productflow_backend.domain.enums import PosterKind
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.execution_models import ExecutionAttempt, ExecutionOperation, ExecutionStage
from productflow_backend.infrastructure.db.models import (
    CopySet,
    CreativeBrief,
    ImageSession,
    ImageSessionAsset,
    ImageSessionGenerationTask,
    ImageSessionRound,
    PosterVariant,
    SourceAsset,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)


def materialize(session, operation, stage, artifact, payload: bytes) -> None:
    snapshot = operation.input_snapshot_json
    if operation.source_kind == "image_session":
        task = session.get(ImageSessionGenerationTask, operation.image_session_task_id)
        index = int(stage.stage_key.removeprefix("store:candidate-"))
        asset = ImageSessionAsset(
            id=new_id(),
            session_id=task.session_id,
            kind="generated_image",
            original_filename=f"candidate-{index}.png",
            mime_type=artifact.mime_type,
            storage_path=artifact.storage_path,
        )
        session.add(asset)
        session.flush()
        session.add(
            ImageSessionRound(
                session_id=task.session_id,
                prompt=snapshot["prompt"],
                assistant_message="Synthetic verification output",
                size=f"{artifact.width}x{artifact.height}",
                model_name="synthetic",
                provider_name="mock",
                prompt_version="m101-v1",
                generation_group_id=snapshot["generation_group_id"],
                candidate_index=index,
                candidate_count=snapshot["generation_count"],
                base_asset_id=snapshot["base_asset_id"],
                selected_reference_asset_ids=snapshot["selected_reference_asset_ids"],
                generated_asset_id=asset.id,
                provider_request_json={"operation_id": operation.id, "attempt_id": artifact.attempt_id},
                provider_output_json={"_productflow": {"actual_image_size": f"{artifact.width}x{artifact.height}"}},
            )
        )
        task.completed_candidates += 1
        task.result_generation_group_id = snapshot["generation_group_id"]
        session.get(ImageSession, task.session_id).updated_at = utcnow()
        return
    node_run = session.get(WorkflowNodeRun, operation.workflow_node_run_id)
    product_id = snapshot["product"]["id"]
    key = stage.stage_key.removeprefix("store:")
    if key == "brief":
        brief_payload = CreativeBriefPayload.model_validate_json(payload)
        brief = CreativeBrief(
            id=new_id(),
            product_id=product_id,
            payload=brief_payload.model_dump(mode="json"),
            provider_name="mock",
            model_name="synthetic-brief",
            prompt_version="m101-v1",
        )
        session.add(brief)
        session.flush()
        node_run.output_json = {"creative_brief_id": brief.id, "brief_payload": brief.payload}
    elif key == "copy":
        copy_payload = CopyPayloadV2.model_validate_json(payload)
        brief_id = (node_run.output_json or {}).get("creative_brief_id")
        copy = CopySet(
            id=new_id(),
            product_id=product_id,
            creative_brief_id=brief_id,
            status="draft",
            structured_payload=copy_payload.model_dump(mode="json"),
            model_structured_payload=copy_payload.model_dump(mode="json"),
            provider_name="mock",
            model_name="synthetic-copy",
            prompt_version="m101-v1",
        )
        session.add(copy)
        session.flush()
        node_run.copy_set_id = copy.id
        node_run.output_json = copy_node_output(copy, creative_brief_id=brief_id)
    elif key == "context":
        node_run.output_json = json.loads(payload)
    elif key.startswith("candidate-"):
        index = int(key.removeprefix("candidate-"))
        output = dict(node_run.output_json or {})
        copy_id = output.get("copy_set_id")
        if not copy_id:
            copy = CopySet(
                id=new_id(),
                product_id=product_id,
                status="draft",
                structured_payload={
                    "version": 2,
                    "summary": "Synthetic image context",
                    "content": {"kind": "freeform", "text": snapshot["node"]["title"]},
                },
                provider_name="workflow_context",
                model_name="accepted_context",
                prompt_version="m101-v1",
            )
            session.add(copy)
            session.flush()
            copy_id = copy.id
        poster = PosterVariant(
            id=new_id(),
            product_id=product_id,
            copy_set_id=copy_id,
            kind=PosterKind(snapshot["node"]["config"].get("poster_kind", "main_image")),
            template_name="local_render_legacy" if snapshot.get("local_render") else "synthetic",
            storage_path=artifact.storage_path,
            mime_type=artifact.mime_type,
            width=artifact.width,
            height=artifact.height,
        )
        session.add(poster)
        session.flush()
        asset = SourceAsset(
            id=new_id(),
            product_id=product_id,
            kind="reference_image",
            original_filename=f"reference-{index}.png",
            mime_type=artifact.mime_type,
            storage_path=artifact.storage_path,
            source_poster_variant_id=poster.id,
        )
        session.add(asset)
        session.flush()
        target_id = snapshot["target_node_ids"][index - 1]
        target = session.get(WorkflowNode, target_id)
        # User edits after acceptance cannot change execution inputs. Only fill
        # the originally accepted target; deletion protection keeps it present.
        fill_reference_node(target, asset, source_poster_variant_id=poster.id)
        output.update(copy_set_id=copy_id, target_count=len(snapshot["target_node_ids"]))
        for field, value in (
            ("generated_poster_variant_ids", poster.id),
            ("filled_source_asset_ids", asset.id),
            ("filled_reference_node_ids", target_id),
        ):
            output[field] = [*output.get(field, []), value]
        node_run.poster_variant_id, node_run.output_json = poster.id, output


def project_legacy(session, operation: ExecutionOperation) -> None:
    from productflow_backend.application.execution.dispatch import cancelled

    session.flush()
    attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.operation_id == operation.id))
    stages = list(session.scalars(select(ExecutionStage).where(ExecutionStage.attempt_id == attempt.id)))
    states = {stage.state for stage in stages}
    from productflow_backend.infrastructure.db.execution_models import ModelCall

    calls = list(session.scalars(select(ModelCall).join(ExecutionStage).where(ExecutionStage.attempt_id == attempt.id)))
    if any(call.outcome == "unknown" and call.wait_closed_at is None for call in calls):
        state = "unknown"
    elif states & {"queued", "claimed", "dispatch_intent", "running"}:
        state = "running"
    elif "unknown" in states or any(call.wait_abandoned_at for call in calls):
        state = "unknown"
    elif "dispatch_blocked" in states:
        state = "failed" if operation.state != "awaiting_local_decision" else "running"
    elif "failed" in states:
        state = "failed"
    elif states == {"succeeded"}:
        state = "succeeded"
    else:
        state = "cancelled"
    if cancelled(session, operation) and state not in {"unknown", "running"}:
        state = "cancelled"
    if operation.state != "awaiting_local_decision":
        operation.state = state
    if state in {"succeeded", "failed", "cancelled"}:
        attempt.initial_outcome = attempt.initial_outcome or state
        attempt.resolved_outcome, attempt.terminal_at = state, utcnow()
    elif state == "running":
        attempt.terminal_at = None
    if operation.image_session_task_id:
        task = session.get(ImageSessionGenerationTask, operation.image_session_task_id)
        if task.status != "cancelled":
            task.status = state
            task.started_at = task.started_at or utcnow()
            task.finished_at = attempt.terminal_at
            task.failure_reason = attempt.outcome_reason if state in {"failed", "unknown"} else None
        return
    node_run = session.get(WorkflowNodeRun, operation.workflow_node_run_id)
    run = session.get(WorkflowRun, node_run.workflow_run_id)
    if run.status == "cancelled":
        return
    node = session.get(WorkflowNode, node_run.node_id)
    node_run.status = state if state != "cancelled" else "failed"
    node_run.finished_at = attempt.terminal_at
    node.status = node_run.status
    if node_run.output_json and state == "succeeded":
        node.output_json = node_run.output_json
    session.flush()
    run_states = set(session.scalars(select(WorkflowNodeRun.status).where(WorkflowNodeRun.workflow_run_id == run.id)))
    if "unknown" in run_states:
        run.status = "unknown"
    elif run_states & {"queued", "running"}:
        run.status = "running"
    elif run_states == {"succeeded"}:
        run.status, run.finished_at = "succeeded", utcnow()
    else:
        run.status, run.finished_at = "failed", utcnow()
