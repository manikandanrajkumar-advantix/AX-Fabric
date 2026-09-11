BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS platform;
CREATE SCHEMA IF NOT EXISTS source_registry;
CREATE SCHEMA IF NOT EXISTS knowledge;
CREATE SCHEMA IF NOT EXISTS odoo;
CREATE SCHEMA IF NOT EXISTS tenant;
CREATE SCHEMA IF NOT EXISTS ingestion;
CREATE SCHEMA IF NOT EXISTS evidence;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS platform.schema_migrations (
    migration_id TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS source_registry.sources (
    source_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL CHECK (source_type IN (
        'official_documentation', 'official_source', 'official_release_note',
        'official_legal', 'tenant_api', 'tenant_database', 'third_party',
        'implementation_pattern'
    )),
    authority_tier SMALLINT NOT NULL CHECK (authority_tier BETWEEN 1 AND 5),
    canonical_uri TEXT NOT NULL,
    publisher TEXT NOT NULL,
    access_class TEXT NOT NULL DEFAULT 'public' CHECK (access_class IN (
        'public', 'licensed', 'tenant_private', 'internal'
    )),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (canonical_uri, access_class)
);

CREATE TABLE IF NOT EXISTS ingestion.runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID REFERENCES source_registry.sources(source_id),
    tenant_id UUID,
    environment_id UUID,
    extractor_name TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    checkpoint JSONB NOT NULL DEFAULT '{}'::jsonb,
    statistics JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE IF NOT EXISTS source_registry.snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES source_registry.sources(source_id),
    run_id UUID REFERENCES ingestion.runs(run_id),
    object_bucket TEXT NOT NULL,
    object_key TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    media_type TEXT NOT NULL,
    source_revision TEXT,
    product_version TEXT,
    edition TEXT,
    deployment_model TEXT,
    language_code TEXT,
    retrieved_at TIMESTAMPTZ NOT NULL,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (source_id, content_sha256),
    UNIQUE (object_bucket, object_key),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS source_registry.documents (
    document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL REFERENCES source_registry.snapshots(snapshot_id),
    title TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    document_structure JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (snapshot_id)
);

CREATE TABLE IF NOT EXISTS source_registry.document_chunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES source_registry.documents(document_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    heading_path TEXT[],
    content TEXT NOT NULL,
    token_count INTEGER CHECK (token_count IS NULL OR token_count >= 0),
    locator JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (document_id, ordinal)
);

CREATE TABLE IF NOT EXISTS odoo.releases (
    release_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version TEXT NOT NULL,
    release_channel TEXT NOT NULL CHECK (release_channel IN ('major', 'saas', 'preview', 'legacy')),
    source_revision TEXT,
    release_date DATE,
    standard_support_end DATE,
    support_status TEXT NOT NULL DEFAULT 'unknown' CHECK (support_status IN ('supported', 'extended', 'end_of_support', 'planned', 'unknown')),
    last_verified_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS odoo.editions (
    edition_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE CHECK (code IN ('community', 'enterprise')),
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS odoo.deployment_models (
    deployment_model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE CHECK (code IN ('online', 'odoo_sh', 'on_premise', 'partner_hosted', 'unknown')),
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS odoo.modules (
    module_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    technical_name TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN (
        'official_community', 'official_enterprise', 'official_industry',
        'third_party_oca', 'third_party_vendor', 'customer_custom'
    )),
    publisher TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (technical_name, origin, publisher)
);

CREATE TABLE IF NOT EXISTS odoo.module_releases (
    module_release_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module_id UUID NOT NULL REFERENCES odoo.modules(module_id),
    release_id UUID NOT NULL REFERENCES odoo.releases(release_id),
    manifest_version TEXT,
    display_name TEXT NOT NULL,
    category TEXT,
    summary TEXT,
    license TEXT,
    source_revision TEXT NOT NULL,
    manifest_sha256 TEXT CHECK (manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'),
    installable BOOLEAN,
    application BOOLEAN,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (module_id, release_id, source_revision)
);

CREATE TABLE IF NOT EXISTS odoo.module_dependencies (
    dependency_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module_release_id UUID NOT NULL REFERENCES odoo.module_releases(module_release_id) ON DELETE CASCADE,
    dependency_name TEXT NOT NULL,
    dependency_type TEXT NOT NULL CHECK (dependency_type IN ('required', 'auto_install', 'external_python', 'external_binary', 'functional')),
    condition JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_snapshot_id UUID REFERENCES source_registry.snapshots(snapshot_id),
    UNIQUE (module_release_id, dependency_name, dependency_type)
);

CREATE TABLE IF NOT EXISTS knowledge.capabilities (
    capability_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    parent_capability_id UUID REFERENCES knowledge.capabilities(capability_id),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'reviewed', 'published', 'deprecated')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS knowledge.claims (
    claim_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_value JSONB NOT NULL,
    evidence_status TEXT NOT NULL CHECK (evidence_status IN ('candidate', 'documented', 'observed', 'verified', 'conflicted', 'deprecated', 'rejected')),
    confidence_score NUMERIC(5,2) CHECK (confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100),
    version_scope JSONB NOT NULL,
    edition_scope JSONB NOT NULL,
    deployment_scope JSONB NOT NULL,
    plan_scope JSONB NOT NULL,
    localization_scope JSONB NOT NULL,
    required_modules JSONB NOT NULL DEFAULT '[]'::jsonb,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    last_verified_at TIMESTAMPTZ,
    publication_status TEXT NOT NULL DEFAULT 'draft' CHECK (publication_status IN ('draft', 'in_review', 'published', 'quarantined', 'withdrawn')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS knowledge.claim_evidence (
    claim_id UUID NOT NULL REFERENCES knowledge.claims(claim_id) ON DELETE CASCADE,
    snapshot_id UUID NOT NULL REFERENCES source_registry.snapshots(snapshot_id),
    chunk_id UUID REFERENCES source_registry.document_chunks(chunk_id),
    evidence_role TEXT NOT NULL CHECK (evidence_role IN ('supports', 'contradicts', 'supersedes', 'context')),
    PRIMARY KEY (claim_id, snapshot_id, evidence_role)
);

CREATE TABLE IF NOT EXISTS knowledge.relationships (
    relationship_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    claim_id UUID REFERENCES knowledge.claims(claim_id),
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    publication_status TEXT NOT NULL DEFAULT 'draft' CHECK (publication_status IN ('draft', 'published', 'withdrawn')),
    UNIQUE (source_type, source_id, relationship_type, target_type, target_id, claim_id)
);

CREATE TABLE IF NOT EXISTS knowledge.embeddings (
    embedding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id UUID NOT NULL REFERENCES source_registry.document_chunks(chunk_id) ON DELETE CASCADE,
    model_name TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    embedding VECTOR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (chunk_id, model_name, model_revision),
    CHECK (vector_dims(embedding) = dimensions)
);

CREATE TABLE IF NOT EXISTS tenant.environments (
    environment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    environment_code TEXT NOT NULL,
    environment_type TEXT NOT NULL CHECK (environment_type IN ('development', 'test', 'staging', 'production')),
    release_id UUID REFERENCES odoo.releases(release_id),
    edition_id UUID REFERENCES odoo.editions(edition_id),
    deployment_model_id UUID REFERENCES odoo.deployment_models(deployment_model_id),
    country_code TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id, environment_code)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ingestion_runs_environment_fk'
          AND conrelid = 'ingestion.runs'::regclass
    ) THEN
        ALTER TABLE ingestion.runs
            ADD CONSTRAINT ingestion_runs_environment_fk
            FOREIGN KEY (environment_id) REFERENCES tenant.environments(environment_id)
            NOT VALID;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS tenant.environment_snapshots (
    environment_snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    environment_id UUID NOT NULL REFERENCES tenant.environments(environment_id),
    run_id UUID NOT NULL REFERENCES ingestion.runs(run_id),
    observed_at TIMESTAMPTZ NOT NULL,
    server_version TEXT NOT NULL,
    edition_code TEXT,
    deployment_code TEXT,
    configuration_hash TEXT CHECK (configuration_hash IS NULL OR configuration_hash ~ '^[0-9a-f]{64}$'),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (environment_id, observed_at)
);

CREATE TABLE IF NOT EXISTS tenant.installed_modules (
    environment_snapshot_id UUID NOT NULL REFERENCES tenant.environment_snapshots(environment_snapshot_id) ON DELETE CASCADE,
    technical_name TEXT NOT NULL,
    installed_version TEXT,
    state TEXT NOT NULL,
    source_origin TEXT NOT NULL DEFAULT 'unknown',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (environment_snapshot_id, technical_name)
);

CREATE TABLE IF NOT EXISTS ingestion.items (
    item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES ingestion.runs(run_id) ON DELETE CASCADE,
    source_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'processed', 'skipped', 'failed')),
    content_sha256 TEXT CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error_code TEXT,
    error_detail TEXT,
    processed_at TIMESTAMPTZ,
    UNIQUE (run_id, source_key)
);

CREATE TABLE IF NOT EXISTS evidence.artifacts (
    artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    environment_id UUID REFERENCES tenant.environments(environment_id),
    artifact_type TEXT NOT NULL,
    object_bucket TEXT NOT NULL,
    object_key TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    media_type TEXT NOT NULL,
    access_class TEXT NOT NULL CHECK (access_class IN ('public', 'licensed', 'tenant_private', 'internal')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (object_bucket, object_key)
);

CREATE TABLE IF NOT EXISTS audit.events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    tenant_id UUID,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    outcome TEXT NOT NULL,
    correlation_id UUID,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

INSERT INTO odoo.editions (code, display_name)
VALUES ('community', 'Odoo Community'), ('enterprise', 'Odoo Enterprise')
ON CONFLICT (code) DO UPDATE SET display_name = EXCLUDED.display_name;

INSERT INTO odoo.deployment_models (code, display_name)
VALUES
    ('online', 'Odoo Online'),
    ('odoo_sh', 'Odoo.sh'),
    ('on_premise', 'On-premise or self-managed cloud'),
    ('partner_hosted', 'Partner-managed hosting'),
    ('unknown', 'Unknown')
ON CONFLICT (code) DO UPDATE SET display_name = EXCLUDED.display_name;

CREATE INDEX IF NOT EXISTS idx_snapshots_source_retrieved
    ON source_registry.snapshots (source_id, retrieved_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_odoo_releases_identity
    ON odoo.releases (version, release_channel, COALESCE(source_revision, ''));
CREATE INDEX IF NOT EXISTS idx_chunks_search
    ON source_registry.document_chunks USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS idx_module_releases_release
    ON odoo.module_releases (release_id, module_id);
CREATE INDEX IF NOT EXISTS idx_module_dependencies_release
    ON odoo.module_dependencies (module_release_id, dependency_type);
CREATE INDEX IF NOT EXISTS idx_claims_subject
    ON knowledge.claims (subject_type, subject_id, predicate);
CREATE INDEX IF NOT EXISTS idx_claims_publication
    ON knowledge.claims (publication_status, evidence_status, last_verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_relationships_source
    ON knowledge.relationships (source_type, source_id, relationship_type);
CREATE INDEX IF NOT EXISTS idx_relationships_target
    ON knowledge.relationships (target_type, target_id, relationship_type);
CREATE INDEX IF NOT EXISTS idx_environment_snapshots_recent
    ON tenant.environment_snapshots (environment_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_items_status
    ON ingestion.items (run_id, status);
CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_time
    ON audit.events (tenant_id, occurred_at DESC);

INSERT INTO platform.schema_migrations (migration_id, checksum)
VALUES ('001_knowledge_foundation', 'managed-by-repository')
ON CONFLICT (migration_id) DO NOTHING;

COMMIT;
