import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.base import new_id, utcnow
from productflow_backend.infrastructure.db.models import ImageSessionGenerationTask
from productflow_backend.infrastructure.verification import manifest


@pytest.mark.parametrize("suffix", ["_migration", "_fresh", "_fresh_final"])
def test_isolated_postgres_expand_data_and_fresh_head(monkeypatch, suffix):
    data = manifest()
    database = next(name for name in data["databases"] if name.endswith(suffix))
    original_url = get_settings().database_url
    url = sa.engine.make_url(original_url).set(database=database)
    engine = sa.create_engine(url)
    evidence = Path(data["run_root"]) / "evidence" / f"migration{suffix}.json"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"
                    )
                )
                == data["environment_id"]
            )
            initialized = sa.inspect(connection).has_table("alembic_version")
        if evidence.exists():
            report = json.loads(evidence.read_text())
            assert initialized and report["database"] == database
            report.setdefault("initial_head", report["head"])
            command.upgrade(config, "head")
            report["head"] = "20261123_0033"
            evidence.write_text(json.dumps(report, indent=2), encoding="utf-8")
        else:
            assert not initialized, "Unrecorded fixture database is never reset or downgraded"
            report = {"database": database, "environment_id": data["environment_id"], "head": "20261123_0033"}
            if suffix == "_migration":
                command.upgrade(config, "20260627_0029")
                metadata = sa.MetaData()
                sessions = sa.Table("image_sessions", metadata, autoload_with=engine)
                tasks = sa.Table("image_session_generation_tasks", metadata, autoload_with=engine)
                root, active, historical = new_id(), new_id(), new_id()
                timestamp = utcnow()
                with engine.begin() as connection:
                    connection.execute(
                        sessions.insert().values(
                            id=root, title="Synthetic migration history", created_at=timestamp, updated_at=timestamp
                        )
                    )
                    for identifier, status in ((active, "running"), (historical, "succeeded")):
                        connection.execute(
                            tasks.insert().values(
                                id=identifier,
                                session_id=root,
                                status=status,
                                prompt="preserve original request evidence",
                                size="32x32",
                                generation_count=1,
                                completed_candidates=int(status == "succeeded"),
                                attempts=1,
                                is_retryable=False,
                                created_at=timestamp,
                            )
                        )
                report.update(active_id=active, historical_id=historical, session_id=root)
                command.upgrade(config, "20261123_0030")
                command.upgrade(config, "20261123_0030")

                def fail_data(connection, cursor, statement, parameters, context, executemany):
                    if "CREATE TABLE production_tasks" in statement:
                        raise RuntimeError("fixture_B_failure_after_A_commit")

                sa.event.listen(sa.engine.Engine, "before_cursor_execute", fail_data)
                try:
                    with pytest.raises(RuntimeError, match="fixture_B_failure"):
                        command.upgrade(config, "head")
                finally:
                    sa.event.remove(sa.engine.Engine, "before_cursor_execute", fail_data)
                with engine.connect() as connection:
                    assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "20261123_0030"
                    assert not sa.inspect(connection).has_table("model_calls")
                    assert connection.scalar(sa.text("SELECT count(*) FROM image_session_generation_tasks")) == 2
                report["A_committed_B_failed_without_history_loss"] = True
            command.upgrade(config, "head")
            evidence.write_text(json.dumps(report, indent=2), encoding="utf-8")
        with Session(engine) as session:
            assert session.scalar(sa.text("SELECT version_num FROM alembic_version")) == "20261123_0033"
            assert session.scalar(sa.text("SELECT count(*) FROM model_calls")) == 0
            if suffix == "_migration":
                active = session.get(ImageSessionGenerationTask, report["active_id"])
                old = session.get(ImageSessionGenerationTask, report["historical_id"])
                assert active.status == "unknown" and old.status == "succeeded"
                assert active.attempts == old.attempts == 1
                assert active.prompt == old.prompt == "preserve original request evidence"
                assert (
                    session.scalar(
                        sa.text("SELECT count(*) FROM execution_events WHERE event_type='legacy_unverified'")
                    )
                    == 1
                )
                assert session.scalar(sa.text("SELECT count(*) FROM image_session_generation_tasks")) == 2
                payload = session.execute(
                    sa.text("SELECT payload_json FROM execution_events WHERE event_type='legacy_quarantine_snapshot'")
                ).scalar_one()
                assert len(payload["payload_sha256"]) == 64 and "prompt" in payload["columns"]
    finally:
        monkeypatch.setenv("DATABASE_URL", original_url)
        get_settings.cache_clear()
        engine.dispose()
