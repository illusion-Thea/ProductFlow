from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from productflow_backend.infrastructure.db.base import Base, Record


class ExperimentRun(Base, Record):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('synthetic','real')", name="ck_experiment_mode"),
        CheckConstraint("status IN ('preparing','active','closed','quarantined')", name="ck_experiment_status"),
        CheckConstraint("image_limit = 12 AND image_reserved_count BETWEEN 0 AND 12", name="ck_experiment_quota"),
    )
    mode: Mapped[str] = mapped_column(String(16), default="synthetic")
    status: Mapped[str] = mapped_column(String(20), default="active")
    image_limit: Mapped[int] = mapped_column(Integer, default=12)
    image_reserved_count: Mapped[int] = mapped_column(Integer, default=0)
    authorization_ref: Mapped[str] = mapped_column(Text)
    evidence_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    baseline_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class ProductionTask(Base, Record):
    __tablename__ = "production_tasks"
    __table_args__ = (
        CheckConstraint("scope = 'prototype'", name="ck_task_prototype_scope"),
        CheckConstraint(
            "business_state IN ('draft','awaiting_generation_confirmation','generating',"
            "'needs_attention','cancelling','cancelled')",
            name="ck_prototype_state",
        ),
        CheckConstraint("draft_content_language IN ('zh-CN','ms-MY','id-ID','fil-PH')", name="ck_task_language"),
    )
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    experiment_run_id: Mapped[str] = mapped_column(ForeignKey("experiment_runs.id", ondelete="RESTRICT"))
    scope: Mapped[str] = mapped_column(String(16), default="prototype")
    business_state: Mapped[str] = mapped_column(String(40), default="draft")
    current_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "plan_snapshots.id", name="fk_task_current_plan", use_alter=True, deferrable=True, initially="DEFERRED"
        ),
    )
    draft_content_language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    draft_revision: Mapped[int] = mapped_column(Integer, default=1)
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    status_version: Mapped[int] = mapped_column(Integer, default=1)
    actor_kind: Mapped[str] = mapped_column(String(24), default="shared_admin")
    actor_label: Mapped[str] = mapped_column(String(160))
    issue_code: Mapped[str | None] = mapped_column(String(100))


class ProductionBatch(Base, Record):
    __tablename__ = "production_batches"
    task_id: Mapped[str] = mapped_column(ForeignKey("production_tasks.id", ondelete="RESTRICT"), unique=True)
    sku: Mapped[str | None] = mapped_column(String(160))
    output_spec_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class ProductionItem(Base, Record):
    __tablename__ = "production_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "slot_index", name="uq_item_batch_slot"),
        CheckConstraint("slot_index BETWEEN 1 AND 10", name="ck_item_slot"),
    )
    batch_id: Mapped[str] = mapped_column(ForeignKey("production_batches.id", ondelete="RESTRICT"))
    slot_index: Mapped[int] = mapped_column(Integer)
    first_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "execution_attempts.id", name="fk_item_first_attempt", use_alter=True, deferrable=True, initially="DEFERRED"
        ),
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class PlanSnapshot(Base, Record):
    __tablename__ = "plan_snapshots"
    __table_args__ = (
        UniqueConstraint("task_id", "revision", name="uq_plan_revision"),
        CheckConstraint("status IN ('draft','validated','accepted')", name="ck_plan_status"),
        CheckConstraint("content_language IN ('zh-CN','ms-MY','id-ID','fil-PH')", name="ck_plan_language"),
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("production_tasks.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer)
    source_draft_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    content_language: Mapped[str] = mapped_column(String(16))
    preview_revision: Mapped[int] = mapped_column(Integer)
    text_operation_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_operations.id", name="fk_plan_text_operation", use_alter=True),
    )
    evidence_snapshot_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    spec_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    model_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    template_ref_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_sha256: Mapped[str] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_label: Mapped[str | None] = mapped_column(String(160))


class SlotPlan(Base, Record):
    __tablename__ = "slot_plans"
    __table_args__ = (
        UniqueConstraint("plan_id", "item_id", name="uq_plan_item"),
        UniqueConstraint("plan_id", "slot_index", name="uq_plan_slot"),
        CheckConstraint("slot_index BETWEEN 1 AND 10", name="ck_plan_slot_index"),
    )
    plan_id: Mapped[str] = mapped_column(ForeignKey("plan_snapshots.id", ondelete="RESTRICT"))
    item_id: Mapped[str] = mapped_column(ForeignKey("production_items.id", ondelete="RESTRICT"))
    slot_index: Mapped[int] = mapped_column(Integer)
    content_language: Mapped[str] = mapped_column(String(16))
    preview_revision: Mapped[int] = mapped_column(Integer)
    copy_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    base_prompt: Mapped[str] = mapped_column(Text)
    fact_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    reference_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list)
