"""Resolve accepted DAG edges against the pinned parent operations, never the live canvas."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from productflow_backend.application.contracts import PosterGenerationInput, ProductInput
from productflow_backend.application.copy_payloads import normalize_copy_node_config
from productflow_backend.application.product_workflow.context import (
    collect_incoming_context,
    effective_product_context,
    image_instruction_with_context,
    instruction_with_upstream_text,
)
from productflow_backend.domain.enums import SourceAssetKind, WorkflowNodeType
from productflow_backend.infrastructure.db.base import utcnow
from productflow_backend.infrastructure.db.execution_models import ExecutionOperation
from productflow_backend.infrastructure.db.models import WorkflowNodeRun
from productflow_backend.infrastructure.storage import LocalStorage


def resolved_input(session, operation: ExecutionOperation, stage) -> dict:
    snapshot = operation.input_snapshot_json
    result = {**snapshot, "stage_key": stage.stage_key}
    if operation.source_kind != "workflow":
        return result
    outputs = {}
    dependency_receipts = []
    for identifier in snapshot.get("dependency_operations", []):
        parent = session.get(ExecutionOperation, identifier)
        node_run = session.get(WorkflowNodeRun, parent.workflow_node_run_id)
        outputs[node_run.node_id] = node_run.output_json or {}
        dependency_receipts.append(
            {
                "operation_id": parent.id,
                "node_run_id": node_run.id,
                "input_hash": parent.input_hash,
                "output": node_run.output_json,
            }
        )
    nodes = [
        SimpleNamespace(
            id=row["id"],
            node_type=WorkflowNodeType(row["node_type"]),
            title=row["title"],
            config_json=row["config"],
            output_json=outputs.get(row["id"], row["output"]),
            last_run_at=None,
            updated_at=utcnow(),
        )
        for row in snapshot["graph_nodes"]
    ]
    edges = [
        SimpleNamespace(
            id=row["id"],
            source_node_id=row["source"],
            target_node_id=row["target"],
            created_at=datetime.fromisoformat(row["created_at"]),
            source_handle=row["source_handle"],
            target_handle=row["target_handle"],
        )
        for row in snapshot["graph_edges"]
    ]
    product = SimpleNamespace(
        **snapshot["product"],
        source_assets=[
            SimpleNamespace(**{**asset, "kind": SourceAssetKind(asset["kind"])}) for asset in snapshot["assets"]
        ],
    )
    workflow = SimpleNamespace(nodes=nodes, edges=edges, product=product, product_id=product.id)
    node = next(item for item in nodes if item.id == snapshot["node"]["id"])
    context = effective_product_context(workflow, node.id, include_transitive=operation.kind == "image")
    incoming = collect_incoming_context(workflow, node.id, include_transitive_product_context=operation.kind == "image")
    result["resolved_dependencies"] = dependency_receipts
    result["context_summary"] = {
        "product_context": context,
        "reference_image_count": len(incoming.image_asset_ids),
        "upstream_text_count": len(incoming.text_contexts),
    }
    result["context_sources"] = incoming.text_sources
    storage = LocalStorage()
    accepted_assets = {asset["id"]: SimpleNamespace(**asset) for asset in snapshot["assets"]}
    # Only immutable receipts from accepted parents may add new input assets.
    from productflow_backend.infrastructure.db.models import SourceAsset

    for output in outputs.values():
        for identifier in output.get("filled_source_asset_ids", []):
            asset = session.get(SourceAsset, identifier)
            if asset and asset.product_id == product.id:
                accepted_assets[identifier] = asset
    assets = [accepted_assets[identifier] for identifier in incoming.image_asset_ids]
    from productflow_backend.application.contracts import ReferenceImageInput

    references = [
        ReferenceImageInput(
            path=storage.resolve(asset.storage_path), mime_type=asset.mime_type, filename=asset.original_filename
        )
        for asset in assets
    ]
    if operation.kind == "text":
        product_input = ProductInput(
            name=context["name"] or "自由创作",
            category=context["category"],
            price=context["price"],
            source_note=context["source_note"],
            image_path="",
        )
        config = normalize_copy_node_config(node.config_json)
        config = config.model_copy(update={"instruction": instruction_with_upstream_text(config.instruction, incoming)})
        own_run = session.get(WorkflowNodeRun, operation.workflow_node_run_id)
        result.update(
            product_input=product_input.model_dump(mode="json"),
            copy_config=config.model_dump(mode="json"),
            brief_payload=(own_run.output_json or {}).get("brief_payload"),
            reference_images=[item.model_dump(mode="json") for item in references],
        )
    elif operation.kind == "image":
        # All accepted asset records remain protected. Parent output references
        # are read from their pinned node-run receipts, not edited node configs.
        from productflow_backend.application.copy_payloads import copy_payload_context_text, validate_copy_payload

        copy = snapshot.get("copies", {}).get(incoming.copy_set_id)
        for output in outputs.values():
            if incoming.copy_set_id and output.get("copy_set_id") == incoming.copy_set_id:
                copy = output.get("structured_payload")
        copy_text = copy_payload_context_text(validate_copy_payload(copy)) if copy else None
        render = PosterGenerationInput(
            product_name=context["name"] or "",
            category=context["category"],
            price=context["price"],
            source_note=context["source_note"],
            instruction=image_instruction_with_context(node, incoming.text_contexts),
            image_size=node.config_json.get("size"),
            tool_options=node.config_json.get("tool_options"),
            copy_prompt_mode="copy" if copy_text else "image_edit",
            structured_copy_context=copy_text,
            source_image=storage.resolve(assets[0].storage_path) if assets else None,
            reference_images=references[1:],
        )
        result["render_input"] = render.model_dump(mode="json")
    elif node.node_type == "reference_image":
        for output in outputs.values():
            targets = output.get("filled_reference_node_ids", [])
            if node.id in targets:
                index = targets.index(node.id)
                identifier = output["filled_source_asset_ids"][index]
                result["local_output"] = {
                    "source_asset_ids": [identifier],
                    "image_asset_ids": [identifier],
                    "summary": "Pinned generated reference",
                }
    return result
