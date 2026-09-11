BEGIN;

ALTER TABLE knowledge.scopes ADD COLUMN IF NOT EXISTS scope_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_scopes_key ON knowledge.scopes (scope_key) WHERE scope_key IS NOT NULL;

CREATE OR REPLACE FUNCTION knowledge.validate_scope_parent()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_tenant UUID; parent_placement UUID;
BEGIN
    IF NEW.parent_scope_id IS NULL THEN RETURN NEW; END IF;
    SELECT tenant_id, placement_id INTO parent_tenant, parent_placement
    FROM knowledge.scopes WHERE scope_id = NEW.parent_scope_id;
    IF NOT FOUND OR parent_placement <> NEW.placement_id
       OR parent_tenant IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION 'parent scope must have the same tenant and placement' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS trg_validate_scope_parent ON knowledge.scopes;
CREATE TRIGGER trg_validate_scope_parent BEFORE INSERT OR UPDATE OF parent_scope_id,tenant_id,placement_id
ON knowledge.scopes FOR EACH ROW EXECUTE FUNCTION knowledge.validate_scope_parent();

CREATE OR REPLACE FUNCTION knowledge.validate_record_reference_grant()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.scope_id = NEW.target_scope_id THEN
        IF NEW.grant_id IS NOT NULL THEN
            RAISE EXCEPTION 'same-scope reference must not use a grant' USING ERRCODE='23514';
        END IF;
    ELSIF NOT EXISTS (
        SELECT 1 FROM knowledge.scope_grants g
        WHERE g.grant_id=NEW.grant_id AND g.consumer_scope_id=NEW.scope_id
          AND g.provider_scope_id=NEW.target_scope_id AND g.revoked_at IS NULL
          AND (g.expires_at IS NULL OR g.expires_at > clock_timestamp())
    ) THEN
        RAISE EXCEPTION 'cross-scope reference requires a matching active grant' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS trg_validate_record_reference_grant ON knowledge.record_references;
CREATE TRIGGER trg_validate_record_reference_grant BEFORE INSERT OR UPDATE
ON knowledge.record_references FOR EACH ROW EXECUTE FUNCTION knowledge.validate_record_reference_grant();

ALTER TABLE source_registry.artifacts
    ADD CONSTRAINT uq_artifacts_scope_id UNIQUE (scope_id, artifact_id);
ALTER TABLE ingestion.processing_runs
    ADD CONSTRAINT uq_processing_runs_scope_id UNIQUE (scope_id, run_id);
ALTER TABLE source_registry.source_versions
    ADD CONSTRAINT uq_source_versions_scope_id UNIQUE (scope_id, source_version_id);
ALTER TABLE source_registry.normalized_documents
    ADD CONSTRAINT uq_normalized_documents_scope_id UNIQUE (scope_id, document_id);
ALTER TABLE source_registry.document_blocks
    ADD CONSTRAINT uq_document_blocks_scope_id UNIQUE (scope_id, block_id);

ALTER TABLE ingestion.run_inputs ADD COLUMN scope_id UUID;
ALTER TABLE ingestion.run_outputs ADD COLUMN scope_id UUID;
UPDATE ingestion.run_inputs i SET scope_id=r.scope_id FROM ingestion.processing_runs r WHERE r.run_id=i.run_id;
UPDATE ingestion.run_outputs o SET scope_id=r.scope_id FROM ingestion.processing_runs r WHERE r.run_id=o.run_id;
ALTER TABLE ingestion.run_inputs ALTER COLUMN scope_id SET NOT NULL;
ALTER TABLE ingestion.run_outputs ALTER COLUMN scope_id SET NOT NULL;
ALTER TABLE ingestion.run_inputs
    ADD CONSTRAINT fk_run_inputs_run_scope FOREIGN KEY (scope_id,run_id)
    REFERENCES ingestion.processing_runs(scope_id,run_id),
    ADD CONSTRAINT fk_run_inputs_artifact_scope FOREIGN KEY (scope_id,artifact_id)
    REFERENCES source_registry.artifacts(scope_id,artifact_id);
ALTER TABLE ingestion.run_outputs
    ADD CONSTRAINT fk_run_outputs_run_scope FOREIGN KEY (scope_id,run_id)
    REFERENCES ingestion.processing_runs(scope_id,run_id),
    ADD CONSTRAINT fk_run_outputs_artifact_scope FOREIGN KEY (scope_id,artifact_id)
    REFERENCES source_registry.artifacts(scope_id,artifact_id);

CREATE OR REPLACE FUNCTION platform.scope_visible(requested_scope UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM knowledge.scopes s
        WHERE s.scope_id=requested_scope
          AND (s.tenant_id IS NULL OR s.tenant_id=platform.current_tenant_id())
          AND s.status='active'
    )
$$;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'records','record_revisions','record_references','publication_events'
    ] LOOP
        EXECUTE format('ALTER TABLE knowledge.%I ENABLE ROW LEVEL SECURITY',table_name);
        EXECUTE format('DROP POLICY IF EXISTS scope_access ON knowledge.%I',table_name);
        EXECUTE format('CREATE POLICY scope_access ON knowledge.%I USING (platform.scope_visible(scope_id)) WITH CHECK (platform.scope_visible(scope_id))',table_name);
    END LOOP;
    FOREACH table_name IN ARRAY ARRAY[
        'artifacts','source_versions','normalized_documents','document_blocks','passages','evidence_spans'
    ] LOOP
        EXECUTE format('ALTER TABLE source_registry.%I ENABLE ROW LEVEL SECURITY',table_name);
        EXECUTE format('DROP POLICY IF EXISTS scope_access ON source_registry.%I',table_name);
        EXECUTE format('CREATE POLICY scope_access ON source_registry.%I USING (platform.scope_visible(scope_id)) WITH CHECK (platform.scope_visible(scope_id))',table_name);
    END LOOP;
    ALTER TABLE ingestion.processing_runs ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS scope_access ON ingestion.processing_runs;
    CREATE POLICY scope_access ON ingestion.processing_runs
        USING (platform.scope_visible(scope_id)) WITH CHECK (platform.scope_visible(scope_id));
END
$$;

INSERT INTO platform.schema_migrations(migration_id,checksum)
VALUES('003_scope_lineage_integrity','managed-by-repository') ON CONFLICT(migration_id) DO NOTHING;
COMMIT;
