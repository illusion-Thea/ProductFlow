"""Expand existing PostgreSQL enums in their own commit before ledger data changes."""

from alembic import op

revision = "20261123_0030"
down_revision = "20260627_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for enum_name in ("jobstatus", "workflownodestatus", "workflowrunstatus"):
                op.execute(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS 'unknown'")


def downgrade() -> None:
    raise RuntimeError("M1-01 preserves unknown execution evidence; stop the isolated run instead of downgrading")
