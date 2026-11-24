"""Scope explicit unknown-wait closure to the original model request."""

import sqlalchemy as sa

from alembic import op

revision = "20261124_0034"
down_revision = "20261123_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_calls", sa.Column("wait_abandoned_at", sa.DateTime(timezone=True), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        definition = op.get_bind().scalar(
            sa.text("SELECT pg_get_functiondef('m101_execution_integrity()'::regprocedure)")
        )
        before = "'outcome','wait_closed_at','transport_started_at'"
        if definition.count(before) != 2:
            raise RuntimeError("unexpected_execution_integrity_definition")
        definition = definition.replace(before, "'outcome','wait_closed_at','wait_abandoned_at','transport_started_at'")
        anchor = "IF TG_OP='UPDATE' AND OLD.transport_started_at IS NOT NULL"
        definition = definition.replace(
            anchor,
            """
        IF TG_OP='UPDATE' AND OLD.wait_abandoned_at IS NOT NULL
             AND NEW.wait_abandoned_at IS DISTINCT FROM OLD.wait_abandoned_at THEN
          RAISE EXCEPTION 'request_wait_closure_immutable' USING ERRCODE='23514';
        END IF;
        """
            + anchor,
        )
        op.execute(definition)
    # Preserve existing closures without assigning them to candidates accepted
    # after the closure or to results already completed before it.
    op.execute("""
        UPDATE model_calls SET wait_abandoned_at = (
            SELECT o.wait_closed_at FROM execution_stages s
            JOIN execution_attempts a ON a.id=s.attempt_id
            JOIN execution_operations o ON o.id=a.operation_id
            WHERE s.id=model_calls.stage_id AND o.wait_closed_at IS NOT NULL
            AND model_calls.created_at <= o.wait_closed_at
            AND (model_calls.outcome='unknown' OR model_calls.wait_closed_at >= o.wait_closed_at)
        )
    """)


def downgrade() -> None:
    raise RuntimeError("Retain request-scoped wait history; no compatible downgrade")
