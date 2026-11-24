from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from productflow_backend.application.contracts import CopyPayloadV2
from productflow_backend.domain.prototype import ContentLanguage, validate_slots


class PrototypeOutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=16, le=8192)
    height: int = Field(ge=16, le=8192)
    test_only: Literal[True] = True


class PrototypeSlotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_index: int = Field(ge=1, le=10)
    item_id: str | None = None
    copy_payload: CopyPayloadV2
    base_prompt: str = Field(min_length=1, max_length=100_000)
    fact_refs: list[str] = Field(default_factory=list)
    reference_refs: list[str] = Field(default_factory=list)


class PrototypePlanPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    content_language: ContentLanguage = ContentLanguage.CHINESE
    source_draft_revision: int = Field(ge=1)
    slots: list[PrototypeSlotPayload] = Field(min_length=10, max_length=10)

    @model_validator(mode="after")
    def ten_distinct_slots(self) -> PrototypePlanPayload:
        validate_slots((slot.slot_index for slot in self.slots), complete=True)
        return self
