"""Execution decisions independent of providers, persistence and wall clocks."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from productflow_backend.domain.errors import BusinessError


class ExecutionConflict(BusinessError):
    status_code = 409


class ExecutionUnavailable(BusinessError):
    status_code = 503


class OperationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DISPATCH_BLOCKED = "dispatch_blocked"
    AWAITING_LOCAL_DECISION = "awaiting_local_decision"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class StageState(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    DISPATCH_BLOCKED = "dispatch_blocked"
    DISPATCH_INTENT = "dispatch_intent"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


ACTIVE_STATES = frozenset({"queued", "running"})
MODEL_STAGE_KINDS = frozenset({"model_text", "model_image"})
DISPATCH_STATES = frozenset({"dispatch_intent", "running", "unknown"})
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
PROTOCOL_VERSION = 1
CAPACITY_LOCK = 42630002
LEGACY_ADMISSION_LOCK = 42630001


def canonical_hash(payload: Any) -> str:
    """Hash client intent separately from the subsequently resolved input snapshot."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def utc(value: datetime) -> datetime:
    # SQLite drops tzinfo; stored values are always UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def require_version(actual: int, expected: int) -> None:
    if actual != expected:
        raise ExecutionConflict("row_version_conflict")


def require_retry(state: str, wait_closed: bool, duplicate_ack: bool, terminal_at: datetime | None) -> None:
    if state == "unknown":
        if not wait_closed or not duplicate_ack:
            raise ExecutionConflict("unknown_requires_closed_wait_and_duplicate_charge_acknowledgement")
    elif state != "failed" or terminal_at is None:
        raise ExecutionConflict("model_retry_not_available_use_local_recovery_if_needed")


def cost_state(transport_kind: str, outcome: str, has_actual: bool = False) -> str:
    if transport_kind == "stub":
        return "not_applicable"
    return "actual" if has_actual else "unconfirmed"


def reserve_image(reserved: int, limit: int, candidate_count: int) -> int:
    if limit != 12 or candidate_count != 1:
        raise ExecutionConflict("prototype_requires_twelve_request_limit_and_n_one")
    if reserved >= limit:
        raise ExecutionConflict("experiment_image_quota_exhausted")
    return reserved + 1


def expired(now: datetime, deadline: datetime | None) -> bool:
    return deadline is not None and utc(now) >= utc(deadline)
