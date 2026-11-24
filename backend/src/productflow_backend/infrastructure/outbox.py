from __future__ import annotations

from datetime import timedelta
from functools import lru_cache

from dramatiq import Message
from dramatiq.brokers.redis import RedisBroker
from sqlalchemy import or_, select

from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.execution_models import (
    ExecutionAttempt,
    ExecutionOperation,
    ExecutionStage,
    OutboxEvent,
)
from productflow_backend.infrastructure.db.session import get_session_factory


@lru_cache(maxsize=1)
def execution_broker() -> RedisBroker:
    settings = get_settings()
    if settings.app_env != "verification" or not settings.execution_stub_only:
        raise RuntimeError("M1-01 execution delivery is verification-only")
    return RedisBroker(url=settings.redis_url, namespace=settings.execution_redis_namespace)


def publish_one() -> bool:
    settings = get_settings()
    now, owner = utcnow(), new_id()
    with get_session_factory()() as session, session.begin():
        event = session.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.available_at <= now,
                or_(
                    OutboxEvent.publish_state == "pending",
                    (OutboxEvent.publish_state == "leased") & (OutboxEvent.lease_until <= now),
                ),
            )
            .order_by(OutboxEvent.available_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not event:
            return False
        event.publish_state, event.lease_owner = "leased", owner
        event.lease_until = now + timedelta(seconds=5)
        event.delivery_attempts += 1
        stage = session.get(ExecutionStage, event.stage_id)
        attempt = session.get(ExecutionAttempt, stage.attempt_id)
        operation = session.get(ExecutionOperation, attempt.operation_id)
        identifier = event.id
        arguments = (
            stage.id,
            event.schedule_generation,
            settings.execution_environment_id,
            operation.release_id,
            event.schema_version,
            attempt.id, event.id, "m101-v1",
        )
    try:
        execution_broker().enqueue(
            Message(
                queue_name=settings.execution_queue_name,
                actor_name="m101_execute_stage",
                args=arguments,
                kwargs={},
                options={"max_retries": 0},
            )
        )
    except Exception:
        return True  # Lease expiry only retries delivery, not the provider request.
    with get_session_factory()() as session, session.begin():
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.id == identifier).with_for_update())
        if event.lease_owner == owner:
            event.publish_state, event.lease_until = "sent", None
    return True
