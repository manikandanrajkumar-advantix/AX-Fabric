BEGIN;
CREATE TABLE IF NOT EXISTS source_registry.web_captures (
    capture_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    source_id UUID NOT NULL REFERENCES source_registry.sources(source_id),
    artifact_id UUID NOT NULL REFERENCES source_registry.artifacts(artifact_id),
    requested_uri TEXT NOT NULL,
    final_uri TEXT NOT NULL,
    http_status INTEGER NOT NULL CHECK(http_status BETWEEN 200 AND 399),
    response_headers JSONB NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
    media_type TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    UNIQUE(source_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_web_captures_source_time
ON source_registry.web_captures(source_id,retrieved_at DESC);
ALTER TABLE source_registry.web_captures ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scope_access ON source_registry.web_captures;
CREATE POLICY scope_access ON source_registry.web_captures
USING(platform.scope_visible(scope_id)) WITH CHECK(platform.scope_visible(scope_id));
INSERT INTO platform.schema_migrations(migration_id,checksum)
VALUES('009_web_source_captures','managed-by-repository') ON CONFLICT(migration_id) DO NOTHING;
COMMIT;
