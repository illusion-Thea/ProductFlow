from uuid import uuid4

import pytest
from pydantic import ValidationError

from productflow_backend.application.prototype_contracts import PrototypePlanPayload
from productflow_backend.domain.model_execution import ExecutionConflict
from productflow_backend.presentation.schemas.prototype_tasks import CreatePrototypeRequest


def test_one_task_one_sku_spec_and_default_language_are_explicit():
    payload = {
        "experiment_run_id": str(uuid4()),
        "source_asset_ids": [str(uuid4())],
        "output_spec": {"width": 32, "height": 32, "test_only": True},
    }
    draft = CreatePrototypeRequest.model_validate(payload)
    assert draft.sku is None and draft.content_language == "zh-CN"
    for patch in (
        {"sku": ["ONE", "TWO"]},
        {"output_spec": [payload["output_spec"]]},
        {"output_spec": {"width": 32, "height": 32, "test_only": False}},
        {"content_language": "en-US"},
        {"market": "automatically-change-language"},
    ):
        with pytest.raises(ValidationError):
            CreatePrototypeRequest.model_validate({**payload, **patch})


def test_ten_structured_slots_reject_partial_duplicate_or_mixed_slot_contracts():
    slots = [
        {
            "slot_index": index,
            "copy_payload": {
                "version": 2,
                "summary": "fixture",
                "content": {"kind": "freeform", "text": "fixture copy"},
            },
            "base_prompt": "synthetic",
        }
        for index in range(1, 11)
    ]
    payload = {"source_draft_revision": 1, "slots": slots}
    assert len(PrototypePlanPayload.model_validate(payload).slots) == 10
    for changed in (
        slots[:-1],
        slots + [slots[0]],
        [slots[0]] * 10,
        [{**slots[0], "sku": "mixed"}, *slots[1:]],
        [{**slots[0], "output_spec": {"width": 16, "height": 16}}, *slots[1:]],
    ):
        with pytest.raises((ValidationError, ExecutionConflict)):
            PrototypePlanPayload.model_validate({**payload, "slots": changed})
