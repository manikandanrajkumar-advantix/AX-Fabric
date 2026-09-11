BEGIN;

-- Odoo legitimately reuses byte-identical files at different paths and across
-- supported versions.  Object identity is the bucket/key; the digest remains
-- indexed for duplicate detection and provenance queries.
ALTER TABLE source_registry.snapshots
    DROP CONSTRAINT IF EXISTS snapshots_source_id_content_sha256_key;

CREATE INDEX IF NOT EXISTS idx_snapshots_source_content_sha256
    ON source_registry.snapshots(source_id, content_sha256);

INSERT INTO platform.schema_migrations(migration_id, checksum)
VALUES('008_snapshot_content_reuse', 'managed-by-repository')
ON CONFLICT(migration_id) DO NOTHING;

COMMIT;
