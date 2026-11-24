"""Append hashes for preserved legacy records quarantined by 0031.

Revision ID: 20261123_0033
Revises: 20261123_0032
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "20261123_0033"
down_revision = "20261123_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    events = sa.Table("execution_events", metadata, autoload_with=connection)
    sources = {
        name: sa.Table(name, metadata, autoload_with=connection)
        for name in (
            "image_session_generation_tasks",
            "workflow_node_runs",
            "workflow_runs",
            "workflow_nodes",
        )
    }
    rows = (
        connection.execute(
            sa.select(events).where(
                events.c.actor_label == "migration_0031", events.c.event_type == "legacy_unverified"
            )
        )
        .mappings()
        .all()
    )
    for event in rows:
        source = sources[event["legacy_source_kind"]]
        row = connection.execute(sa.select(source).where(source.c.id == event["legacy_source_id"])).mappings().one()
        # 0031 changed status only. Restore that value in the digest input, never
        # in the business row. The original event and record remain untouched.
        payload = {**dict(row), "status": event["payload_json"]["previous_status"]}
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
        ).hexdigest()
        connection.execute(
            events.insert().values(
                id=str(uuid.uuid4()),
                created_at=datetime.now(UTC),
                event_type="legacy_quarantine_snapshot",
                actor_kind="system",
                actor_label="migration_0033",
                legacy_source_kind=event["legacy_source_kind"],
                legacy_source_id=event["legacy_source_id"],
                payload_json={
                    "source_event_id": event["id"],
                    "payload_sha256": digest,
                    "basis": "preserved_record_with_original_status",
                    "columns": sorted(payload),
                },
                dedupe_key=f"quarantine-snapshot:{event['id']}",
            )
        )


def downgrade() -> None:
    raise RuntimeError("M1-01 evidence is retained; stop the isolated run instead of deleting historical records")
