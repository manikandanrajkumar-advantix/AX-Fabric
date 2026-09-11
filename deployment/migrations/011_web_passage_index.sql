BEGIN;
CREATE TABLE IF NOT EXISTS source_registry.web_passages (
 passage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id UUID NOT NULL REFERENCES source_registry.web_documents(document_id) ON DELETE CASCADE,
 scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id), ordinal INTEGER NOT NULL CHECK(ordinal>=0), content TEXT NOT NULL,
 text_sha256 TEXT NOT NULL CHECK(text_sha256 ~ '^[0-9a-f]{64}$'), token_count INTEGER NOT NULL CHECK(token_count>=0),
 chunker_version TEXT NOT NULL, search_vector TSVECTOR GENERATED ALWAYS AS(to_tsvector('english',content)) STORED, UNIQUE(document_id,ordinal)
);
CREATE TABLE IF NOT EXISTS source_registry.web_passage_blocks (
 passage_id UUID NOT NULL REFERENCES source_registry.web_passages(passage_id) ON DELETE CASCADE,
 block_id UUID NOT NULL REFERENCES source_registry.web_document_blocks(block_id) ON DELETE CASCADE, ordinal INTEGER NOT NULL,
 PRIMARY KEY(passage_id,block_id)
);
CREATE TABLE IF NOT EXISTS knowledge.web_passage_embeddings_e5_small (
 passage_id UUID NOT NULL REFERENCES source_registry.web_passages(passage_id) ON DELETE CASCADE, scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
 model_name TEXT NOT NULL, model_revision TEXT NOT NULL, dimensions INTEGER NOT NULL CHECK(dimensions=384), input_hash TEXT NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 embedding VECTOR(384) NOT NULL CHECK(vector_dims(embedding)=384), created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(passage_id,model_name,model_revision)
);
CREATE INDEX IF NOT EXISTS idx_web_passages_search ON source_registry.web_passages USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_web_embeddings_hnsw ON knowledge.web_passage_embeddings_e5_small USING hnsw(embedding vector_cosine_ops) WITH(m=16,ef_construction=64);
ALTER TABLE source_registry.web_passages ENABLE ROW LEVEL SECURITY; ALTER TABLE knowledge.web_passage_embeddings_e5_small ENABLE ROW LEVEL SECURITY;
CREATE POLICY scope_access ON source_registry.web_passages USING(platform.scope_visible(scope_id)) WITH CHECK(platform.scope_visible(scope_id));
CREATE POLICY scope_access ON knowledge.web_passage_embeddings_e5_small USING(platform.scope_visible(scope_id)) WITH CHECK(platform.scope_visible(scope_id));
INSERT INTO platform.schema_migrations(migration_id,checksum) VALUES('011_web_passage_index','managed-by-repository') ON CONFLICT DO NOTHING;
COMMIT;
