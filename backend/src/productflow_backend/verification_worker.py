"""Windows-compatible isolated worker. Does not load the legacy dotenv launcher."""

import time

import dramatiq
from sqlalchemy import select

from productflow_backend.application.execution.dispatch import execute_stage
from productflow_backend.application.execution.result_store import execute_local
from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.execution_models import ExecutionStage, OutboxEvent
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.outbox import execution_broker

broker = execution_broker()
settings = get_settings()


@dramatiq.actor(broker=broker, actor_name="m101_execute_stage", queue_name=settings.execution_queue_name, max_retries=0)
def execute(stage_id: str, generation: int, environment_id: str, release_id: str,
            schema_version: int, attempt_id: str, event_id: str, protocol_version: str) -> None:
    if (
        schema_version != 1
        or protocol_version != "m101-v1"
        or environment_id != settings.execution_environment_id
        or release_id != settings.execution_release_id
    ):
        return
    with get_session_factory()() as session:
        kind = session.scalar(select(ExecutionStage.kind).join(OutboxEvent).where(
            ExecutionStage.id == stage_id, ExecutionStage.attempt_id == attempt_id,
            OutboxEvent.id == event_id, OutboxEvent.schedule_generation == generation,
        ))
    if kind == "local_store":
        execute_local(stage_id, generation)
    elif kind in {"local_parse", "local_render_legacy"}:
        from productflow_backend.application.execution.legacy_local import execute_legacy_local

        execute_legacy_local(stage_id, generation)
    elif kind:
        execute_stage(stage_id, generation, environment_id, release_id)


def main() -> None:
    worker = dramatiq.Worker(broker, worker_threads=1)
    worker.start()
    try:
        while True:
            time.sleep(1)
    finally:
        worker.stop(timeout=5000)


if __name__ == "__main__":
    main()
