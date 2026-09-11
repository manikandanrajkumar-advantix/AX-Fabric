BEGIN;

CREATE TABLE IF NOT EXISTS oca.documentation_scan_runs (
 run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
 started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
 status TEXT NOT NULL CHECK(status IN ('running','succeeded','partial','failed')),
 branches_attempted INTEGER NOT NULL DEFAULT 0, branches_succeeded INTEGER NOT NULL DEFAULT 0,
 branches_failed INTEGER NOT NULL DEFAULT 0, documents_processed INTEGER NOT NULL DEFAULT 0,
 documents_failed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS oca.documents (
 document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), repository_id BIGINT NOT NULL REFERENCES oca.repositories(repository_id),
 branch_name TEXT NOT NULL, commit_sha TEXT NOT NULL, module_release_id UUID REFERENCES odoo.module_releases(module_release_id),
 document_type TEXT NOT NULL CHECK(document_type IN ('repository_readme','repository_license','module_readme','module_documentation')),
 relative_path TEXT NOT NULL, raw_artifact_id UUID NOT NULL REFERENCES source_registry.artifacts(artifact_id),
 normalized_artifact_id UUID NOT NULL REFERENCES source_registry.artifacts(artifact_id),
 content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'), parser_name TEXT NOT NULL,
 parser_version TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'quarantined' CHECK(state IN ('quarantined','approved','rejected')),
 scan_run_id UUID NOT NULL REFERENCES oca.documentation_scan_runs(run_id), created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(repository_id,branch_name,relative_path)
);

CREATE TABLE IF NOT EXISTS oca.document_blocks (
 block_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id UUID NOT NULL REFERENCES oca.documents(document_id) ON DELETE CASCADE,
 scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id), ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 heading_path TEXT[] NOT NULL DEFAULT '{}', content TEXT NOT NULL, locator JSONB NOT NULL,
 text_sha256 TEXT NOT NULL CHECK(text_sha256 ~ '^[0-9a-f]{64}$'), UNIQUE(document_id,ordinal)
);

CREATE TABLE IF NOT EXISTS oca.passages (
 passage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id UUID NOT NULL REFERENCES oca.documents(document_id) ON DELETE CASCADE,
 scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id), ordinal INTEGER NOT NULL CHECK(ordinal>=0), content TEXT NOT NULL,
 text_sha256 TEXT NOT NULL CHECK(text_sha256 ~ '^[0-9a-f]{64}$'), token_count INTEGER NOT NULL CHECK(token_count>=0),
 chunker_version TEXT NOT NULL, search_vector TSVECTOR GENERATED ALWAYS AS(to_tsvector('english',content)) STORED,
 UNIQUE(document_id,ordinal)
);

CREATE TABLE IF NOT EXISTS oca.passage_blocks (
 passage_id UUID NOT NULL REFERENCES oca.passages(passage_id) ON DELETE CASCADE,
 block_id UUID NOT NULL REFERENCES oca.document_blocks(block_id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0), PRIMARY KEY(passage_id,block_id)
);

CREATE TABLE IF NOT EXISTS knowledge.oca_passage_embeddings_e5_small (
 passage_id UUID NOT NULL REFERENCES oca.passages(passage_id) ON DELETE CASCADE,
 scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id), model_name TEXT NOT NULL, model_revision TEXT NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions=384), input_hash TEXT NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 embedding VECTOR(384) NOT NULL CHECK(vector_dims(embedding)=384), created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(passage_id,model_name,model_revision)
);

CREATE TABLE IF NOT EXISTS oca.documentation_scan_issues (
 issue_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), run_id UUID NOT NULL REFERENCES oca.documentation_scan_runs(run_id),
 repository_id BIGINT NOT NULL REFERENCES oca.repositories(repository_id), branch_name TEXT NOT NULL,
 relative_path TEXT, error_code TEXT NOT NULL, message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oca_documents_module ON oca.documents(module_release_id,document_type);
CREATE INDEX IF NOT EXISTS idx_oca_blocks_search ON oca.document_blocks USING GIN(to_tsvector('english',content));
CREATE INDEX IF NOT EXISTS idx_oca_passages_search ON oca.passages USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_oca_embeddings_hnsw ON knowledge.oca_passage_embeddings_e5_small
 USING hnsw(embedding vector_cosine_ops) WITH(m=16,ef_construction=64);

ALTER TABLE oca.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE oca.document_blocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE oca.passages ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.oca_passage_embeddings_e5_small ENABLE ROW LEVEL SECURITY;
CREATE POLICY scope_access ON oca.documents USING(platform.scope_visible((SELECT scope_id FROM oca.repositories r WHERE r.repository_id=documents.repository_id)));
CREATE POLICY scope_access ON oca.document_blocks USING(platform.scope_visible(scope_id)) WITH CHECK(platform.scope_visible(scope_id));
CREATE POLICY scope_access ON oca.passages USING(platform.scope_visible(scope_id)) WITH CHECK(platform.scope_visible(scope_id));
CREATE POLICY scope_access ON knowledge.oca_passage_embeddings_e5_small USING(platform.scope_visible(scope_id)) WITH CHECK(platform.scope_visible(scope_id));

INSERT INTO platform.schema_migrations(migration_id,checksum) VALUES('017_oca_documentation_corpus','managed-by-repository') ON CONFLICT DO NOTHING;
COMMIT;
