"""Rules for the synthetic prototype, not formal completion or adoption."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from productflow_backend.domain.model_execution import ExecutionConflict


class ContentLanguage(StrEnum):
    CHINESE = "zh-CN"
    MALAY = "ms-MY"
    INDONESIAN = "id-ID"
    FILIPINO = "fil-PH"


class PrototypeState(StrEnum):
    DRAFT = "draft"
    AWAITING_GENERATION_CONFIRMATION = "awaiting_generation_confirmation"
    GENERATING = "generating"
    NEEDS_ATTENTION = "needs_attention"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


def validate_slots(indices: Iterable[int], *, complete: bool) -> None:
    values = list(indices)
    if len(values) != len(set(values)) or any(value not in range(1, 11) for value in values):
        raise ExecutionConflict("invalid_or_duplicate_slot_index")
    if complete and set(values) != set(range(1, 11)):
        raise ExecutionConflict("exactly_ten_slots_required")


def validate_confirmation(
    *,
    sku: str | None,
    current_language: str,
    plan_language: str,
    current_preview: int,
    supplied_preview: int,
    actual_hash: str,
    supplied_hash: str,
) -> None:
    if not sku or not sku.strip():
        raise ExecutionConflict("sku_required_for_confirmation")
    if current_language != plan_language or current_preview != supplied_preview:
        raise ExecutionConflict("preview_language_or_revision_conflict")
    if actual_hash != supplied_hash:
        raise ExecutionConflict("plan_hash_conflict")


def require_live(business_state: str) -> None:
    if business_state in {"cancelling", "cancelled"}:
        raise ExecutionConflict("cancelled_task_cannot_accept_work")
