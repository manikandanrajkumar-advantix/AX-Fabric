BEGIN;

CREATE SCHEMA IF NOT EXISTS governance;

CREATE OR REPLACE FUNCTION platform.current_tenant_id()
RETURNS UUID LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION platform.reject_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; publish a replacement revision instead', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME
        USING ERRCODE = '55000';
END
$$;

CREATE TABLE IF NOT EXISTS governance.data_placements (
    placement_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), region TEXT NOT NULL,
    database_alias TEXT NOT NULL, object_namespace TEXT NOT NULL, security_tier TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','suspended','retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (database_alias, object_namespace)
);

CREATE TABLE IF NOT EXISTS governance.tenants (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), external_org_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, placement_id UUID NOT NULL REFERENCES governance.data_placements(placement_id),
    status TEXT NOT NULL CHECK (status IN ('active','suspended','closed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS governance.rights_policies (
    rights_policy_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), policy_version TEXT NOT NULL UNIQUE,
    allowed_purposes JSONB NOT NULL, provider_processing_allowed BOOLEAN NOT NULL,
    redistribution_allowed BOOLEAN NOT NULL, expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS governance.retention_policies (
    retention_policy_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), policy_version TEXT NOT NULL,
    class_code TEXT NOT NULL, retain_rule JSONB NOT NULL, backup_expiry_rule JSONB NOT NULL,
    owner_ref TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (policy_version, class_code)
);

CREATE TABLE IF NOT EXISTS knowledge.scopes (
    scope_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID REFERENCES governance.tenants(tenant_id),
    parent_scope_id UUID REFERENCES knowledge.scopes(scope_id),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('common','product','industry','tenant','engagement','environment')),
    classification TEXT NOT NULL, policy_ref TEXT NOT NULL,
    placement_id UUID NOT NULL REFERENCES governance.data_placements(placement_id),
    status TEXT NOT NULL CHECK (status IN ('active','suspended','retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((scope_kind IN ('common','product','industry') AND tenant_id IS NULL)
        OR (scope_kind IN ('tenant','engagement','environment') AND tenant_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS knowledge.scope_grants (
    grant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    consumer_scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    provider_scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id), purpose TEXT NOT NULL,
    policy_ref TEXT NOT NULL, issued_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ, issuer_ref TEXT NOT NULL,
    CHECK (consumer_scope_id <> provider_scope_id), CHECK (expires_at IS NULL OR expires_at > issued_at),
    CHECK (revoked_at IS NULL OR revoked_at >= issued_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_scope_grants_active
    ON knowledge.scope_grants (consumer_scope_id, provider_scope_id, purpose) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS knowledge.schema_contracts (
    schema_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT NOT NULL, version TEXT NOT NULL,
    schema_hash TEXT NOT NULL CHECK (schema_hash ~ '^[0-9a-f]{64}$'), schema_uri TEXT NOT NULL,
    validator_version TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('draft','active','retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS knowledge.records (
    scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id), record_id UUID NOT NULL DEFAULT gen_random_uuid(),
    type_code TEXT NOT NULL, namespace TEXT NOT NULL, stable_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), created_by TEXT NOT NULL,
    PRIMARY KEY (scope_id, record_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_records_stable_key
    ON knowledge.records (scope_id, namespace, stable_key) WHERE stable_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS knowledge.record_revisions (
    scope_id UUID NOT NULL, revision_id UUID NOT NULL DEFAULT gen_random_uuid(), record_id UUID NOT NULL,
    revision_no INTEGER NOT NULL CHECK (revision_no > 0), schema_id UUID NOT NULL REFERENCES knowledge.schema_contracts(schema_id),
    content JSONB NOT NULL, content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    valid_kind TEXT NOT NULL CHECK (valid_kind IN ('state','event','atemporal')),
    valid_from TIMESTAMPTZ, valid_to TIMESTAMPTZ, event_at TIMESTAMPTZ,
    time_precision TEXT NOT NULL DEFAULT 'unknown', extensions JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), created_by TEXT NOT NULL,
    PRIMARY KEY (scope_id, revision_id), FOREIGN KEY (scope_id, record_id) REFERENCES knowledge.records(scope_id, record_id),
    UNIQUE (scope_id, record_id, revision_no), CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
    CHECK ((valid_kind='event' AND event_at IS NOT NULL AND valid_from IS NULL AND valid_to IS NULL)
        OR (valid_kind='state' AND event_at IS NULL)
        OR (valid_kind='atemporal' AND event_at IS NULL AND valid_from IS NULL AND valid_to IS NULL))
);
DROP TRIGGER IF EXISTS trg_record_revisions_immutable ON knowledge.record_revisions;
CREATE TRIGGER trg_record_revisions_immutable BEFORE UPDATE OR DELETE ON knowledge.record_revisions
    FOR EACH ROW EXECUTE FUNCTION platform.reject_immutable_mutation();

CREATE TABLE IF NOT EXISTS knowledge.record_references (
    scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id), reference_id UUID NOT NULL DEFAULT gen_random_uuid(),
    target_scope_id UUID NOT NULL, target_revision_id UUID NOT NULL, grant_id UUID REFERENCES knowledge.scope_grants(grant_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), created_by TEXT NOT NULL,
    PRIMARY KEY (scope_id, reference_id),
    FOREIGN KEY (target_scope_id, target_revision_id) REFERENCES knowledge.record_revisions(scope_id, revision_id),
    CHECK ((scope_id=target_scope_id AND grant_id IS NULL) OR (scope_id<>target_scope_id AND grant_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_record_references_target ON knowledge.record_references (target_scope_id, target_revision_id);

CREATE TABLE IF NOT EXISTS knowledge.publication_events (
    scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id), event_id UUID NOT NULL DEFAULT gen_random_uuid(),
    revision_id UUID NOT NULL, action TEXT NOT NULL CHECK (action IN ('submit','publish','reject','revoke','supersede')),
    authority_ref TEXT NOT NULL, reason TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (scope_id, event_id),
    FOREIGN KEY (scope_id, revision_id) REFERENCES knowledge.record_revisions(scope_id, revision_id)
);
DROP TRIGGER IF EXISTS trg_publication_events_immutable ON knowledge.publication_events;
CREATE TRIGGER trg_publication_events_immutable BEFORE UPDATE OR DELETE ON knowledge.publication_events
    FOR EACH ROW EXECUTE FUNCTION platform.reject_immutable_mutation();

CREATE TABLE IF NOT EXISTS source_registry.artifacts (
    artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    provider TEXT NOT NULL, bucket TEXT NOT NULL, object_key TEXT NOT NULL, provider_version TEXT,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'), byte_count BIGINT NOT NULL CHECK (byte_count >= 0),
    media_type TEXT NOT NULL, region TEXT NOT NULL, encryption_key_ref TEXT NOT NULL,
    classification TEXT NOT NULL, acl_policy_ref TEXT NOT NULL,
    rights_policy_id UUID NOT NULL REFERENCES governance.rights_policies(rights_policy_id),
    retention_policy_id UUID NOT NULL REFERENCES governance.retention_policies(retention_policy_id),
    state TEXT NOT NULL CHECK (state IN ('staged','available','quarantined','tombstoned','erased')),
    verified_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (state <> 'available' OR verified_at IS NOT NULL), UNIQUE (provider, bucket, object_key, provider_version)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_scope_digest ON source_registry.artifacts (scope_id, sha256);

CREATE TABLE IF NOT EXISTS ingestion.processing_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    run_kind TEXT NOT NULL CHECK (run_kind IN ('capture','parse','ocr','transcribe','profile','extract','embed','index','reconcile')),
    idempotency_key TEXT NOT NULL, processor TEXT NOT NULL, processor_version TEXT NOT NULL,
    config_hash TEXT NOT NULL CHECK (config_hash ~ '^[0-9a-f]{64}$'), model_ref TEXT,
    prompt_artifact_id UUID REFERENCES source_registry.artifacts(artifact_id),
    state TEXT NOT NULL CHECK (state IN ('queued','running','succeeded','partial','failed','cancelled')),
    started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, error_code TEXT, metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (scope_id, run_kind, idempotency_key),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
    CHECK (state NOT IN ('succeeded','partial','failed','cancelled') OR finished_at IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS ingestion.run_inputs (
    run_id UUID NOT NULL REFERENCES ingestion.processing_runs(run_id) ON DELETE CASCADE,
    artifact_id UUID NOT NULL REFERENCES source_registry.artifacts(artifact_id), role_code TEXT NOT NULL,
    PRIMARY KEY (run_id, artifact_id, role_code)
);
CREATE TABLE IF NOT EXISTS ingestion.run_outputs (
    run_id UUID NOT NULL REFERENCES ingestion.processing_runs(run_id) ON DELETE CASCADE,
    artifact_id UUID NOT NULL REFERENCES source_registry.artifacts(artifact_id), role_code TEXT NOT NULL,
    PRIMARY KEY (run_id, artifact_id, role_code)
);

CREATE TABLE IF NOT EXISTS source_registry.source_versions (
    source_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    source_id UUID NOT NULL REFERENCES source_registry.sources(source_id),
    raw_artifact_id UUID NOT NULL REFERENCES source_registry.artifacts(artifact_id),
    capture_run_id UUID NOT NULL REFERENCES ingestion.processing_runs(run_id), upstream_version TEXT,
    source_created_at TIMESTAMPTZ, effective_at TIMESTAMPTZ, captured_at TIMESTAMPTZ NOT NULL,
    capture_manifest JSONB NOT NULL, completeness TEXT NOT NULL CHECK (completeness IN ('complete','partial','unknown')),
    supersedes_version_id UUID REFERENCES source_registry.source_versions(source_version_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (scope_id, source_id, raw_artifact_id, capture_run_id)
);

CREATE TABLE IF NOT EXISTS ingestion.ingest_issues (
    issue_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), run_id UUID NOT NULL REFERENCES ingestion.processing_runs(run_id) ON DELETE CASCADE,
    source_version_id UUID REFERENCES source_registry.source_versions(source_version_id), code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info','warning','blocking')), locator JSONB, message TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open','resolved','accepted_limitation')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS source_registry.normalized_documents (
    document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    source_version_id UUID NOT NULL REFERENCES source_registry.source_versions(source_version_id),
    extraction_run_id UUID NOT NULL REFERENCES ingestion.processing_runs(run_id),
    normalized_artifact_id UUID NOT NULL REFERENCES source_registry.artifacts(artifact_id),
    format_schema_id UUID NOT NULL REFERENCES knowledge.schema_contracts(schema_id), language_code TEXT NOT NULL,
    page_count INTEGER CHECK (page_count IS NULL OR page_count >= 0), duration_ms BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
    generation INTEGER NOT NULL CHECK (generation > 0), state TEXT NOT NULL CHECK (state IN ('partial','validated','retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (source_version_id, extraction_run_id)
);

CREATE TABLE IF NOT EXISTS source_registry.document_blocks (
    block_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    document_id UUID NOT NULL REFERENCES source_registry.normalized_documents(document_id) ON DELETE CASCADE,
    parent_block_id UUID REFERENCES source_registry.document_blocks(block_id), sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    block_kind TEXT NOT NULL CHECK (block_kind IN ('heading','paragraph','list','table','table_cell','code','image','transcript_turn')),
    path TEXT NOT NULL, text_content TEXT, locator JSONB NOT NULL, structure JSONB NOT NULL DEFAULT '{}'::jsonb,
    text_hash TEXT CHECK (text_hash IS NULL OR text_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (document_id, parent_block_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS source_registry.passages (
    passage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    document_id UUID NOT NULL REFERENCES source_registry.normalized_documents(document_id) ON DELETE CASCADE,
    generation INTEGER NOT NULL CHECK (generation > 0), sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    content TEXT NOT NULL, text_hash TEXT NOT NULL CHECK (text_hash ~ '^[0-9a-f]{64}$'), language_code TEXT NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count >= 0), chunker_version TEXT NOT NULL, acl_policy_ref TEXT NOT NULL,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (document_id, generation, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_passages_search ON source_registry.passages USING GIN (search_vector);

CREATE TABLE IF NOT EXISTS source_registry.passage_blocks (
    passage_id UUID NOT NULL REFERENCES source_registry.passages(passage_id) ON DELETE CASCADE,
    block_id UUID NOT NULL REFERENCES source_registry.document_blocks(block_id) ON DELETE CASCADE,
    span_no INTEGER NOT NULL CHECK (span_no >= 0), block_char_start INTEGER NOT NULL CHECK (block_char_start >= 0),
    block_char_end INTEGER NOT NULL, passage_char_start INTEGER NOT NULL CHECK (passage_char_start >= 0), passage_char_end INTEGER NOT NULL,
    PRIMARY KEY (passage_id, block_id, span_no), CHECK (block_char_end > block_char_start),
    CHECK (passage_char_end > passage_char_start)
);

CREATE TABLE IF NOT EXISTS source_registry.evidence_spans (
    evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    source_version_id UUID NOT NULL REFERENCES source_registry.source_versions(source_version_id),
    document_id UUID REFERENCES source_registry.normalized_documents(document_id),
    block_id UUID REFERENCES source_registry.document_blocks(block_id), locator JSONB NOT NULL,
    quoted_text TEXT, quote_hash TEXT CHECK (quote_hash IS NULL OR quote_hash ~ '^[0-9a-f]{64}$'),
    verification TEXT NOT NULL CHECK (verification IN ('unchecked','locator_verified','invalid')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), CHECK (block_id IS NULL OR document_id IS NOT NULL),
    CHECK ((quoted_text IS NULL) = (quote_hash IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_evidence_spans_source ON source_registry.evidence_spans (source_version_id, verification);
DROP TRIGGER IF EXISTS trg_evidence_spans_immutable ON source_registry.evidence_spans;
CREATE TRIGGER trg_evidence_spans_immutable BEFORE UPDATE OR DELETE ON source_registry.evidence_spans
    FOR EACH ROW EXECUTE FUNCTION platform.reject_immutable_mutation();

ALTER TABLE governance.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.scopes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_self_access ON governance.tenants;
CREATE POLICY tenant_self_access ON governance.tenants USING (tenant_id = platform.current_tenant_id())
    WITH CHECK (tenant_id = platform.current_tenant_id());
DROP POLICY IF EXISTS scoped_tenant_access ON knowledge.scopes;
CREATE POLICY scoped_tenant_access ON knowledge.scopes USING (tenant_id IS NULL OR tenant_id = platform.current_tenant_id())
    WITH CHECK (tenant_id IS NULL OR tenant_id = platform.current_tenant_id());

INSERT INTO platform.schema_migrations (migration_id, checksum)
VALUES ('002_knowledge_core_governance_evidence','managed-by-repository') ON CONFLICT (migration_id) DO NOTHING;

COMMIT;
