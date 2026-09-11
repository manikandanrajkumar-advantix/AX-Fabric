BEGIN;
CREATE TABLE IF NOT EXISTS source_registry.web_documents (
 document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), capture_id UUID NOT NULL UNIQUE REFERENCES source_registry.web_captures(capture_id),
 scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id), normalized_artifact_id UUID NOT NULL REFERENCES source_registry.artifacts(artifact_id),
 title TEXT NOT NULL, parser_name TEXT NOT NULL, parser_version TEXT NOT NULL, content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
 state TEXT NOT NULL CHECK(state IN ('validated','quarantined')), created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS source_registry.web_document_blocks (
 block_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id UUID NOT NULL REFERENCES source_registry.web_documents(document_id) ON DELETE CASCADE,
 scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
 ordinal INTEGER NOT NULL CHECK(ordinal>=0), block_kind TEXT NOT NULL, heading_path TEXT[] NOT NULL DEFAULT '{}', content TEXT NOT NULL,
 locator JSONB NOT NULL, text_sha256 TEXT NOT NULL CHECK(text_sha256 ~ '^[0-9a-f]{64}$'), UNIQUE(document_id,ordinal)
);
CREATE INDEX IF NOT EXISTS idx_web_blocks_search ON source_registry.web_document_blocks USING GIN(to_tsvector('english',content));
ALTER TABLE source_registry.web_documents ENABLE ROW LEVEL SECURITY; ALTER TABLE source_registry.web_document_blocks ENABLE ROW LEVEL SECURITY;
CREATE POLICY scope_access ON source_registry.web_documents USING(platform.scope_visible(scope_id)) WITH CHECK(platform.scope_visible(scope_id));
CREATE POLICY scope_access ON source_registry.web_document_blocks USING(platform.scope_visible(scope_id)) WITH CHECK(platform.scope_visible(scope_id));
INSERT INTO platform.schema_migrations(migration_id,checksum) VALUES('010_web_normalized_documents','managed-by-repository') ON CONFLICT DO NOTHING;
COMMIT;
