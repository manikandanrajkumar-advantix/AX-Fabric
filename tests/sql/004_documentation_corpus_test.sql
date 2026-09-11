\set ON_ERROR_STOP on

DO $$
DECLARE
    corpus RECORD;
    actual_documents BIGINT;
    document_count BIGINT;
    block_count BIGINT;
    passage_count BIGINT;
    missing_evidence BIGINT;
    missing_passage_links BIGINT;
    invalid_locators BIGINT;
BEGIN
    SELECT count(*) INTO document_count FROM source_registry.normalized_documents;
    SELECT count(*) INTO block_count FROM source_registry.document_blocks;
    SELECT count(*) INTO passage_count FROM source_registry.passages;
    SELECT count(*) INTO missing_evidence
    FROM source_registry.document_blocks b
    LEFT JOIN source_registry.evidence_spans e ON e.block_id=b.block_id
    WHERE e.evidence_id IS NULL;
    SELECT count(*) INTO missing_passage_links
    FROM source_registry.document_blocks b
    LEFT JOIN source_registry.passage_blocks pb ON pb.block_id=b.block_id
    WHERE pb.block_id IS NULL;
    SELECT count(*) INTO invalid_locators FROM source_registry.document_blocks
    WHERE (locator->>'line_start')::int < 1
       OR (locator->>'line_end')::int < (locator->>'line_start')::int;

    IF document_count <> 3177 THEN RAISE EXCEPTION 'expected 3177 documents across supported versions, found %', document_count; END IF;
    FOR corpus IN
        SELECT * FROM (VALUES
            ('product:odoo:documentation:17.0', 955::BIGINT),
            ('product:odoo:documentation:18.0', 1063::BIGINT),
            ('product:odoo:documentation:19.0', 1159::BIGINT)
        ) AS expected(scope_key, document_count)
    LOOP
        SELECT count(*) INTO actual_documents
        FROM source_registry.normalized_documents d
        JOIN knowledge.scopes s ON s.scope_id=d.scope_id
        WHERE s.scope_key=corpus.scope_key;
        IF actual_documents <> corpus.document_count THEN
            RAISE EXCEPTION 'expected % documents for %, found %',
                corpus.document_count, corpus.scope_key, actual_documents;
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM ingestion.processing_runs pr
            JOIN knowledge.scopes s ON s.scope_id=pr.scope_id
            WHERE s.scope_key=corpus.scope_key
              AND pr.processor='odoo-rst-document-ingestor'
              AND pr.state='succeeded'
              AND pr.metrics->>'failed'='0'
        ) THEN
            RAISE EXCEPTION 'successful documentation processing run is missing for %', corpus.scope_key;
        END IF;
    END LOOP;
    IF block_count = 0 OR passage_count = 0 THEN RAISE EXCEPTION 'document blocks or passages are empty'; END IF;
    IF missing_evidence <> 0 THEN RAISE EXCEPTION '% blocks lack evidence spans', missing_evidence; END IF;
    IF missing_passage_links <> 0 THEN RAISE EXCEPTION '% blocks lack passage mappings', missing_passage_links; END IF;
    IF invalid_locators <> 0 THEN RAISE EXCEPTION '% blocks have invalid line locators', invalid_locators; END IF;
END
$$;

SELECT count(*) AS lexical_matches
FROM source_registry.passages
WHERE search_vector @@ websearch_to_tsquery('simple', 'Community Enterprise editions');
