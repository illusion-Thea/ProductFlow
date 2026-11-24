"""One consumed dispatch permit produces at most one transport request."""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

from PIL import Image

from productflow_backend.application.execution.ports import DispatchPermit, TransportResult
from productflow_backend.config import get_settings
from productflow_backend.infrastructure.logging import log_execution_event
from productflow_backend.infrastructure.verification import IsolationError, inside_run, manifest


class DefinitiveProviderFailure(RuntimeError):
    """A received refusal/invalid payload is a spent, definite failed request."""


class UnknownProviderOutcome(RuntimeError):
    """May have reached the provider: never repeat generation implicitly."""


class SyntheticTransport:
    def send(self, permit: DispatchPermit) -> TransportResult:
        settings = get_settings()
        if not settings.execution_stub_only or settings.app_env != "verification":
            raise IsolationError("synthetic_transport_requires_verification")
        data = manifest()
        scenario = permit.snapshot.get("stub_scenario", "success")
        log_execution_event("stub_transport_entered", attempt_id=permit.attempt_id,
                            stage_id=permit.stage_id, model_call_id=permit.call_id,
                            environment_id=data["environment_id"])
        if scenario == "pause_before_send":
            self.pause(data, permit, "before-send")
        # This independent transport journal is written at the actual invocation,
        # not inferred from ModelCall rows or reserved quota.
        journal = inside_run(Path(data["run_root"]) / "evidence/stub-requests.jsonl")
        descriptor = os.open(journal, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(
                descriptor,
                (
                    json.dumps(
                        {
                            "call_id": permit.call_id,
                            "stage_id": permit.stage_id,
                            "modality": permit.modality,
                            "n": 1,
                            "pid": os.getpid(),
                        }
                    )
                    + "\n"
                ).encode(),
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if scenario == "pause_after_send":
            self.pause(data, permit, "after-send")
        if scenario == "refusal":
            raise DefinitiveProviderFailure("synthetic_refusal")
        if scenario == "timeout":
            raise UnknownProviderOutcome("synthetic_timeout_after_send")
        if permit.modality == "text":
            if permit.snapshot.get("legacy_kind") == "workflow":
                from productflow_backend.application.contracts import (
                    CopyNodeConfigV2,
                    CreativeBriefPayload,
                    ProductInput,
                    ReferenceImageInput,
                )
                from productflow_backend.infrastructure.text.mock_provider import MockTextProvider

                provider = MockTextProvider()
                product = ProductInput.model_validate(permit.snapshot["product_input"])
                if permit.snapshot["stage_key"] == "brief":
                    value, _ = provider.generate_brief(product)
                else:
                    value, _ = provider.generate_copy(
                        product,
                        CreativeBriefPayload.model_validate(permit.snapshot["brief_payload"]),
                        CopyNodeConfigV2.model_validate(permit.snapshot["copy_config"]),
                        reference_images=[
                            ReferenceImageInput.model_validate(item)
                            for item in permit.snapshot.get("reference_images", [])
                        ],
                    )
                return TransportResult(
                    f"stub-{permit.call_id}", value.model_dump_json().encode(), "application/json", {"synthetic": True}
                )
            language = permit.snapshot.get("content_language", "zh-CN")
            labels = {
                "zh-CN": "合成验证文案",
                "ms-MY": "Salinan ujian sintetik",
                "id-ID": "Teks uji sintetis",
                "fil-PH": "Sintetikong pagsubok",
            }
            payload = {
                "schema_version": 1,
                "content_language": language,
                "source_draft_revision": permit.snapshot.get("source_draft_revision", 1),
                "slots": [
                    {
                        "slot_index": index,
                        "copy_payload": {
                            "version": 2,
                            "summary": f"{labels[language]} {index}",
                            "content": {"kind": "freeform", "text": labels[language]},
                        },
                        "base_prompt": f"Synthetic fixture slot {index}; no promotional text",
                        "fact_refs": [],
                        "reference_refs": [],
                    }
                    for index in range(1, 11)
                ],
            }
            raw = b'{"invalid":true}' if scenario == "invalid_structure" else json.dumps(payload).encode()
            return TransportResult(f"stub-{permit.call_id}", raw, "application/json", {"synthetic": True})
        spec = permit.snapshot.get("output_spec", {"width": 64, "height": 64})
        buffer = io.BytesIO()
        Image.new("RGB", (spec["width"], spec["height"]), (92, 134, 174)).save(buffer, format="PNG")
        return TransportResult(f"stub-{permit.call_id}", buffer.getvalue(), "image/png", {"synthetic": True})

    def retrieve(self, request_id: str) -> None:
        # A lookup miss is not evidence of an unsent request.
        return None

    @staticmethod
    def pause(data: dict, permit: DispatchPermit, window: str) -> None:
        """Bounded process-fault hook available only to the synthetic transport."""
        marker = inside_run(Path(data["run_root"]) / "evidence" / f"paused-{permit.stage_id}-{window}.json")
        marker.write_text(json.dumps({"stage_id": permit.stage_id, "pid": os.getpid()}), encoding="utf-8")
        deadline = time.monotonic() + 20
        release = marker.with_suffix(".release")
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not release.exists():
            raise UnknownProviderOutcome("synthetic_pause_deadline")
