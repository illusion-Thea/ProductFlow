from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from productflow_backend.application.prototype_contracts import PrototypeOutputSpec, PrototypeSlotPayload
from productflow_backend.domain.prototype import ContentLanguage


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreatePrototypeRequest(StrictRequest):
    experiment_run_id: UUID
    sku: str | None = Field(default=None, max_length=160)
    source_asset_ids: list[UUID] = Field(min_length=1, max_length=6)
    content_language: ContentLanguage = ContentLanguage.CHINESE
    output_spec: PrototypeOutputSpec


class VersionRequest(StrictRequest):
    expected_row_version: int = Field(ge=1)


class DraftRequest(VersionRequest):
    sku: str | None = Field(default=None, max_length=160)
    source_asset_ids: list[UUID] = Field(min_length=1, max_length=6)
    content_language: ContentLanguage
    output_spec: PrototypeOutputSpec


class TextPreparationRequest(VersionRequest):
    content_language: ContentLanguage


class RevisePlanRequest(VersionRequest):
    slots: list[PrototypeSlotPayload] = Field(max_length=10)


class ConfirmPlanRequest(VersionRequest):
    plan_id: UUID
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_language: ContentLanguage
    preview_revision: int = Field(ge=1)


class GenerateRequest(VersionRequest):
    accepted_plan_id: UUID


class ProbeRequest(GenerateRequest):
    slot_index: int = Field(ge=1, le=10)


class OperationActionRequest(VersionRequest):
    reason: str = Field(default="", max_length=500)
    acknowledge_possible_duplicate_charge: bool = False


ExecutionAction = Literal["retry", "close-wait", "resume-local", "end-local"]
