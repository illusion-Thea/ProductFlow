from datetime import UTC, datetime, timedelta

import pytest

from productflow_backend.domain.model_execution import (
    ExecutionConflict,
    canonical_hash,
    cost_state,
    expired,
    require_retry,
    require_version,
    reserve_image,
)
from productflow_backend.domain.prototype import validate_confirmation, validate_slots


def test_unknown_cost_and_retry_are_distinct_from_failure_and_local_recovery():
    assert cost_state("real", "unknown") == "unconfirmed"
    assert cost_state("stub", "unknown") == "not_applicable"
    for closed, acknowledged in [(False, False), (False, True), (True, False)]:
        with pytest.raises(ExecutionConflict):
            require_retry("unknown", closed, acknowledged, None)
    require_retry("unknown", True, True, None)
    for state in ("queued", "running", "awaiting_local_decision", "succeeded", "cancelled"):
        with pytest.raises(ExecutionConflict):
            require_retry(state, True, True, datetime.now(UTC))


def test_quota_counts_requests_never_candidates_and_hash_counts_client_intent():
    assert reserve_image(11, 12, 1) == 12
    for values in [(12, 12, 1), (0, 13, 1), (0, 12, 10)]:
        with pytest.raises(ExecutionConflict):
            reserve_image(*values)
    assert canonical_hash({"a": [1, 2], "b": "中文"}) == canonical_hash({"b": "中文", "a": [1, 2]})
    assert canonical_hash({"a": [1, 2]}) != canonical_hash({"a": [2, 1]})
    with pytest.raises(ExecutionConflict):
        require_version(2, 1)


def test_confirmation_binds_ten_slots_language_revision_and_content_hash():
    validate_slots(range(1, 11), complete=True)
    for indices in ([1, 1], [0], range(1, 10)):
        with pytest.raises(ExecutionConflict):
            validate_slots(indices, complete=True)
    args = dict(
        sku="ONE",
        current_language="zh-CN",
        plan_language="zh-CN",
        current_preview=2,
        supplied_preview=2,
        actual_hash="hash",
        supplied_hash="hash",
    )
    validate_confirmation(**args)
    for patch in ({"sku": None}, {"plan_language": "fil-PH"}, {"supplied_preview": 1}, {"supplied_hash": "old"}):
        with pytest.raises(ExecutionConflict):
            validate_confirmation(**{**args, **patch})
    now = datetime.now(UTC)
    assert expired(now, now) and not expired(now, now + timedelta(seconds=1))
