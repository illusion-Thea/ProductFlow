"""Close cross-root pointer and immutable receipt gaps found during M1-01 validation.

Revision ID: 20261123_0032
Revises: 20261123_0031
"""

from alembic import op

revision = "20261123_0032"
down_revision = "20261123_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite unit fixtures do not prove these PostgreSQL invariants.
    op.execute("""
    CREATE FUNCTION m101_pointer_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME='production_items' THEN
        IF NEW.first_attempt_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM execution_attempts a JOIN execution_operations o ON o.id=a.operation_id
           WHERE a.id=NEW.first_attempt_id AND o.item_id=NEW.id AND o.purpose='initial') THEN
          RAISE EXCEPTION 'first_attempt_owner_mismatch' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='production_tasks' THEN
        IF NEW.current_plan_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM plan_snapshots p WHERE p.id=NEW.current_plan_id AND p.task_id=NEW.id) THEN
          RAISE EXCEPTION 'current_plan_owner_mismatch' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='plan_snapshots' THEN
        IF NEW.text_operation_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM execution_operations o WHERE o.id=NEW.text_operation_id
           AND o.prototype_task_id=NEW.task_id AND o.kind='text') THEN
          RAISE EXCEPTION 'text_operation_owner_mismatch' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER m101_first_owner AFTER INSERT OR UPDATE ON production_items
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION m101_pointer_integrity();
    CREATE CONSTRAINT TRIGGER m101_current_plan_owner AFTER INSERT OR UPDATE ON production_tasks
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION m101_pointer_integrity();
    CREATE CONSTRAINT TRIGGER m101_text_operation_owner AFTER INSERT OR UPDATE ON plan_snapshots
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION m101_pointer_integrity();

    CREATE FUNCTION m101_execution_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE s execution_stages; a execution_attempts; o execution_operations;
    BEGIN
      IF TG_TABLE_NAME='execution_stages' THEN
        SELECT * INTO a FROM execution_attempts WHERE id=NEW.attempt_id;
        SELECT * INTO o FROM execution_operations WHERE id=a.operation_id;
        IF NEW.input_hash<>o.input_hash THEN
          RAISE EXCEPTION 'stage_input_hash_mismatch' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND (NEW.attempt_id<>OLD.attempt_id OR NEW.stage_key<>OLD.stage_key
             OR NEW.kind<>OLD.kind OR NEW.input_hash<>OLD.input_hash) THEN
          RAISE EXCEPTION 'stage_identity_immutable' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME IN ('execution_artifacts','local_recovery_executions') THEN
        SELECT * INTO s FROM execution_stages WHERE id=NEW.stage_id;
        IF NEW.attempt_id<>s.attempt_id THEN
          RAISE EXCEPTION 'local_stage_owner_mismatch' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='model_calls' THEN
        IF TG_OP='UPDATE' AND (to_jsonb(NEW)-ARRAY['outcome','wait_closed_at','transport_started_at',
             'provider_request_id','response_hash','response_path','response_mime_type','usage_json',
             'cost_actual','cost_estimated','currency','cost_state','wait_deadline_at']) <>
            (to_jsonb(OLD)-ARRAY['outcome','wait_closed_at','transport_started_at',
             'provider_request_id','response_hash','response_path','response_mime_type','usage_json',
             'cost_actual','cost_estimated','currency','cost_state','wait_deadline_at']) THEN
          RAISE EXCEPTION 'dispatch_identity_immutable' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND OLD.transport_started_at IS NOT NULL
             AND NEW.transport_started_at IS DISTINCT FROM OLD.transport_started_at THEN
          RAISE EXCEPTION 'transport_permit_cannot_reset' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND OLD.response_hash IS NOT NULL AND
           (NEW.response_hash IS DISTINCT FROM OLD.response_hash OR
            NEW.provider_request_id IS DISTINCT FROM OLD.provider_request_id) THEN
          RAISE EXCEPTION 'response_identity_immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.cost_state='unconfirmed' AND NEW.cost_actual IS NOT NULL THEN
          RAISE EXCEPTION 'unconfirmed_actual_cost_must_be_null' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='production_items' AND TG_OP='UPDATE' THEN
        IF NEW.batch_id<>OLD.batch_id OR NEW.slot_index<>OLD.slot_index THEN
          RAISE EXCEPTION 'item_identity_immutable' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER m101_stage_guard BEFORE INSERT OR UPDATE ON execution_stages
      FOR EACH ROW EXECUTE FUNCTION m101_execution_integrity();
    CREATE TRIGGER m101_artifact_guard BEFORE INSERT OR UPDATE ON execution_artifacts
      FOR EACH ROW EXECUTE FUNCTION m101_execution_integrity();
    CREATE TRIGGER m101_recovery_guard BEFORE INSERT OR UPDATE ON local_recovery_executions
      FOR EACH ROW EXECUTE FUNCTION m101_execution_integrity();
    CREATE TRIGGER m101_call_guard BEFORE INSERT OR UPDATE ON model_calls
      FOR EACH ROW EXECUTE FUNCTION m101_execution_integrity();
    CREATE TRIGGER m101_item_guard BEFORE UPDATE ON production_items
      FOR EACH ROW EXECUTE FUNCTION m101_execution_integrity();
    """)


def downgrade() -> None:
    raise RuntimeError("No compatible pre-ledger rollback build; retain verification evidence")
