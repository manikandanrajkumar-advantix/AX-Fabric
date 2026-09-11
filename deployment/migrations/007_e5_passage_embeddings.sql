BEGIN;
CREATE TABLE IF NOT EXISTS knowledge.passage_embeddings_e5_small (
    scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    passage_id UUID NOT NULL REFERENCES source_registry.passages(passage_id) ON DELETE CASCADE,
    model_name TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK(dimensions=384),
    input_hash TEXT NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
    embedding VECTOR(384) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(passage_id,model_name,model_revision),
    CHECK(vector_dims(embedding)=384)
);
CREATE INDEX IF NOT EXISTS idx_e5_embeddings_scope ON knowledge.passage_embeddings_e5_small(scope_id);
CREATE INDEX IF NOT EXISTS idx_e5_embeddings_hnsw ON knowledge.passage_embeddings_e5_small
USING hnsw(embedding vector_cosine_ops) WITH(m=16,ef_construction=64);
ALTER TABLE knowledge.passage_embeddings_e5_small ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scope_access ON knowledge.passage_embeddings_e5_small;
CREATE POLICY scope_access ON knowledge.passage_embeddings_e5_small
USING(platform.scope_visible(scope_id)) WITH CHECK(platform.scope_visible(scope_id));
INSERT INTO platform.schema_migrations(migration_id,checksum)
VALUES('007_e5_passage_embeddings','managed-by-repository') ON CONFLICT(migration_id) DO NOTHING;
COMMIT;
