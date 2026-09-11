BEGIN;
ALTER TABLE knowledge.claims ADD COLUMN IF NOT EXISTS claim_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_claims_claim_key ON knowledge.claims(claim_key) WHERE claim_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS knowledge.web_claim_evidence (
 claim_id UUID NOT NULL REFERENCES knowledge.claims(claim_id) ON DELETE CASCADE,
 block_id UUID NOT NULL REFERENCES source_registry.web_document_blocks(block_id),
 evidence_role TEXT NOT NULL CHECK(evidence_role IN ('supports','contradicts','context')),
 source_capture_id UUID NOT NULL REFERENCES source_registry.web_captures(capture_id),
 PRIMARY KEY(claim_id,block_id,evidence_role)
);
INSERT INTO platform.schema_migrations(migration_id,checksum) VALUES('012_web_claim_candidates','managed-by-repository') ON CONFLICT DO NOTHING;
COMMIT;
