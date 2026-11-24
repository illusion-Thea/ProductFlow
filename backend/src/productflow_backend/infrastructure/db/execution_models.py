from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from productflow_backend.infrastructure.db.base import Base, Record, utcnow


class ExecutionOperation(Base, Record):
    __tablename__ = "execution_operations"
    __table_args__ = (
        CheckConstraint(
            "(source_kind='prototype' AND prototype_task_id IS NOT NULL AND image_session_task_id IS NULL "
            "AND workflow_node_run_id IS NULL) OR (source_kind='image_session' AND prototype_task_id IS NULL "
            "AND image_session_task_id IS NOT NULL AND workflow_node_run_id IS NULL) OR "
            "(source_kind='workflow' AND prototype_task_id IS NULL AND image_session_task_id IS NULL "
            "AND workflow_node_run_id IS NOT NULL)",
            name="ck_operation_exact_owner",
        ),
        CheckConstraint(
            "state IN ('queued','running','dispatch_blocked','awaiting_local_decision',"
            "'succeeded','failed','unknown','cancelled')",
            name="ck_operation_state",
        ),
        CheckConstraint("purpose IN ('text','probe','initial','revision','legacy')", name="ck_operation_purpose"),
        CheckConstraint("purpose != 'initial' OR item_id IS NOT NULL", name="ck_initial_item"),
        Index(
            "uq_item_active_operation",
            "item_id",
            unique=True,
            postgresql_where=text("state IN ('queued','running') AND item_id IS NOT NULL"),
            sqlite_where=text("state IN ('queued','running') AND item_id IS NOT NULL"),
        ),
        Index(
            "uq_item_initial_operation",
            "item_id",
            unique=True,
            postgresql_where=text("purpose = 'initial'"),
            sqlite_where=text("purpose = 'initial'"),
        ),
        Index(
            "uq_task_active_text",
            "prototype_task_id",
            unique=True,
            postgresql_where=text("kind = 'text' AND state IN ('queued','running')"),
            sqlite_where=text("kind = 'text' AND state IN ('queued','running')"),
        ),
    )
    kind: Mapped[str] = mapped_column(String(32))
    purpose: Mapped[str] = mapped_column(String(24))
    source_kind: Mapped[str] = mapped_column(String(24))
    prototype_task_id: Mapped[str | None] = mapped_column(ForeignKey("production_tasks.id", ondelete="RESTRICT"))
    image_session_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("image_session_generation_tasks.id", ondelete="RESTRICT"),
        index=True,
    )
    workflow_node_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_node_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    item_id: Mapped[str | None] = mapped_column(ForeignKey("production_items.id", ondelete="RESTRICT"))
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plan_snapshots.id", ondelete="RESTRICT"))
    experiment_run_id: Mapped[str | None] = mapped_column(ForeignKey("experiment_runs.id", ondelete="RESTRICT"))
    parent_operation_id: Mapped[str | None] = mapped_column(ForeignKey("execution_operations.id", ondelete="RESTRICT"))
    retry_of_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_attempts.id", name="fk_operation_retry_attempt", use_alter=True),
    )
    release_id: Mapped[str] = mapped_column(String(100))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    authorization_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))
    source_draft_revision: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    wait_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class ExecutionAttempt(Base, Record):
    __tablename__ = "execution_attempts"
    operation_id: Mapped[str] = mapped_column(ForeignKey("execution_operations.id", ondelete="RESTRICT"), unique=True)
    previous_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("execution_attempts.id", ondelete="RESTRICT"))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    initial_outcome: Mapped[str | None] = mapped_column(String(32))
    resolved_outcome: Mapped[str | None] = mapped_column(String(32))
    outcome_reason: Mapped[str | None] = mapped_column(String(160))


class ExecutionStage(Base, Record):
    __tablename__ = "execution_stages"
    __table_args__ = (
        UniqueConstraint("attempt_id", "stage_key", name="uq_attempt_stage"),
        CheckConstraint(
            "kind IN ('model_text','model_image','local_store','local_parse','local_render_legacy')",
            name="ck_stage_kind",
        ),
        CheckConstraint(
            "state IN ('queued','claimed','dispatch_blocked','dispatch_intent','running','succeeded',"
            "'failed','unknown','cancelled')",
            name="ck_stage_state",
        ),
    )
    attempt_id: Mapped[str] = mapped_column(ForeignKey("execution_attempts.id", ondelete="RESTRICT"), index=True)
    stage_key: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    claim_epoch: Mapped[int] = mapped_column(Integer, default=0)
    schedule_generation: Mapped[int] = mapped_column(Integer, default=1)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_delivery_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    queue_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claim_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_hash: Mapped[str] = mapped_column(String(64))


class ModelCall(Base, Record):
    __tablename__ = "model_calls"
    __table_args__ = (
        UniqueConstraint("experiment_run_id", "quota_sequence", name="uq_experiment_request_sequence"),
        CheckConstraint("transport_kind IN ('stub','real')", name="ck_call_transport"),
        CheckConstraint("modality IN ('text','image')", name="ck_call_modality"),
        CheckConstraint(
            "(experiment_run_id IS NOT NULL AND modality='image' AND quota_sequence > 0) OR "
            "((experiment_run_id IS NULL OR modality != 'image') AND quota_sequence IS NULL)",
            name="ck_call_quota_sequence",
        ),
        CheckConstraint(
            "cost_state IN ('actual','estimated','unconfirmed','not_applicable')",
            name="ck_call_cost_state",
        ),
        CheckConstraint(
            "transport_kind != 'stub' OR (cost_state='not_applicable' AND cost_actual IS NULL "
            "AND cost_estimated IS NULL)",
            name="ck_stub_no_billing",
        ),
    )
    stage_id: Mapped[str] = mapped_column(ForeignKey("execution_stages.id", ondelete="RESTRICT"), unique=True)
    experiment_run_id: Mapped[str | None] = mapped_column(ForeignKey("experiment_runs.id", ondelete="RESTRICT"))
    modality: Mapped[str] = mapped_column(String(16))
    purpose: Mapped[str] = mapped_column(String(24))
    item_id: Mapped[str | None] = mapped_column(ForeignKey("production_items.id", ondelete="RESTRICT"))
    transport_kind: Mapped[str] = mapped_column(String(16), default="stub")
    provider_kind: Mapped[str] = mapped_column(String(40))
    profile_id: Mapped[str | None] = mapped_column(String(36))
    model_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    credential_revision_ref: Mapped[str | None] = mapped_column(String(160))
    requested_candidate_count: Mapped[int] = mapped_column(Integer, default=1)
    dispatch_intent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    transport_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(24), default="pending")
    request_hash: Mapped[str] = mapped_column(String(64))
    response_hash: Mapped[str | None] = mapped_column(String(64))
    response_path: Mapped[str | None] = mapped_column(Text)
    response_mime_type: Mapped[str | None] = mapped_column(String(80))
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    cost_actual: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    cost_estimated: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    currency: Mapped[str | None] = mapped_column(String(12))
    cost_state: Mapped[str] = mapped_column(String(24), default="not_applicable")
    quota_sequence: Mapped[int | None] = mapped_column(Integer)
    wait_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    wait_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LocalRecoveryExecution(Base, Record):
    __tablename__ = "local_recovery_executions"
    __table_args__ = (
        UniqueConstraint("stage_id", "recovery_sequence", name="uq_local_recovery_sequence"),
        Index(
            "uq_active_local_recovery",
            "stage_id",
            unique=True,
            postgresql_where=text("state IN ('queued','running')"),
            sqlite_where=text("state IN ('queued','running')"),
        ),
        CheckConstraint(
            "state IN ('queued','running','succeeded','timed_out','ended','cancelled')", name="ck_local_recovery_state"
        ),
    )
    attempt_id: Mapped[str] = mapped_column(ForeignKey("execution_attempts.id", ondelete="RESTRICT"))
    stage_id: Mapped[str] = mapped_column(ForeignKey("execution_stages.id", ondelete="RESTRICT"))
    recovery_sequence: Mapped[int] = mapped_column(Integer)
    claim_epoch: Mapped[int] = mapped_column(Integer)
    input_sha256: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    authorization_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    execution_release_id: Mapped[str] = mapped_column(String(100))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(24), default="queued")
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_reason: Mapped[str | None] = mapped_column(String(160))
    actor_label: Mapped[str] = mapped_column(String(160))


class OutboxEvent(Base, Record):
    __tablename__ = "execution_outbox_events"
    __table_args__ = (
        UniqueConstraint("stage_id", "event_type", "schedule_generation", name="uq_outbox_generation"),
        CheckConstraint("publish_state IN ('pending','leased','sent')", name="ck_outbox_state"),
    )
    stage_id: Mapped[str] = mapped_column(ForeignKey("execution_stages.id", ondelete="RESTRICT"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), default="execute_stage")
    schedule_generation: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    publish_state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)


class ExecutionEvent(Base, Record):
    __tablename__ = "execution_events"
    __table_args__ = (Index("ix_event_legacy_source", "legacy_source_kind", "legacy_source_id"),)
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("execution_operations.id", ondelete="RESTRICT"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("production_tasks.id", ondelete="RESTRICT"))
    legacy_source_kind: Mapped[str | None] = mapped_column(String(40))
    legacy_source_id: Mapped[str | None] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(80))
    actor_kind: Mapped[str] = mapped_column(String(24), default="system")
    actor_label: Mapped[str] = mapped_column(String(160), default="execution")
    before_version: Mapped[int | None] = mapped_column(Integer)
    after_version: Mapped[int | None] = mapped_column(Integer)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True)


class ExecutionArtifact(Base, Record):
    __tablename__ = "execution_artifacts"
    __table_args__ = (
        UniqueConstraint("stage_id", "artifact_index", name="uq_stage_artifact"),
        CheckConstraint("availability IN ('staged','available','store_failed')", name="ck_artifact_availability"),
        CheckConstraint("feedback = 'undecided'", name="ck_prototype_artifact_feedback"),
    )
    attempt_id: Mapped[str] = mapped_column(ForeignKey("execution_attempts.id", ondelete="RESTRICT"))
    stage_id: Mapped[str] = mapped_column(ForeignKey("execution_stages.id", ondelete="RESTRICT"))
    artifact_index: Mapped[int] = mapped_column(Integer, default=1)
    asset_kind: Mapped[str] = mapped_column(String(32))
    storage_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(80))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    availability: Mapped[str] = mapped_column(String(24), default="available")
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback: Mapped[str] = mapped_column(String(16), default="undecided")


class RequestIdempotency(Base, Record):
    __tablename__ = "request_idempotency"
    __table_args__ = (UniqueConstraint("actor_scope", "route_scope", "idempotency_key", name="uq_request_idempotency"),)
    actor_scope: Mapped[str] = mapped_column(String(160))
    route_scope: Mapped[str] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(36))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
