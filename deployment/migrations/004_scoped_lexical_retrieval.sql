BEGIN;

CREATE OR REPLACE FUNCTION knowledge.search_passages(
    query_text TEXT,
    requested_scope UUID,
    result_limit INTEGER DEFAULT 10
)
RETURNS TABLE (
    passage_id UUID,
    rank REAL,
    content TEXT,
    source_path TEXT,
    line_start INTEGER,
    line_end INTEGER,
    source_revision TEXT,
    citation_uri TEXT
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
    WITH query AS (
        SELECT websearch_to_tsquery('simple', query_text) AS terms
        WHERE length(trim(query_text)) > 0
    ), ranked AS (
        SELECT
            p.passage_id,
            ts_rank_cd(p.search_vector, q.terms, 32)::REAL AS rank,
            p.content,
            min(b.locator->>'path') AS source_path,
            min((b.locator->>'line_start')::INTEGER) AS line_start,
            max((b.locator->>'line_end')::INTEGER) AS line_end,
            sv.upstream_version AS source_revision
        FROM query q
        JOIN source_registry.passages p ON p.search_vector @@ q.terms
        JOIN source_registry.passage_blocks pb ON pb.passage_id=p.passage_id
        JOIN source_registry.document_blocks b ON b.block_id=pb.block_id
        JOIN source_registry.normalized_documents d ON d.document_id=p.document_id
        JOIN source_registry.source_versions sv ON sv.source_version_id=d.source_version_id
        WHERE p.scope_id=requested_scope AND platform.scope_visible(p.scope_id)
        GROUP BY p.passage_id,p.content,p.search_vector,q.terms,sv.upstream_version
    )
    SELECT r.passage_id,r.rank,r.content,r.source_path,r.line_start,r.line_end,r.source_revision,
           'https://github.com/odoo/documentation/blob/'||r.source_revision||'/content/'||r.source_path||
           '#L'||r.line_start||'-L'||r.line_end AS citation_uri
    FROM ranked r
    ORDER BY r.rank DESC,r.source_path,r.line_start,r.passage_id
    LIMIT LEAST(GREATEST(result_limit,1),100)
$$;

COMMENT ON FUNCTION knowledge.search_passages(TEXT,UUID,INTEGER) IS
'Scope-filtered lexical candidate retrieval with pinned Odoo Git citations; returned passages are evidence candidates, not accepted claims.';

INSERT INTO platform.schema_migrations(migration_id,checksum)
VALUES('004_scoped_lexical_retrieval','managed-by-repository') ON CONFLICT(migration_id) DO NOTHING;
COMMIT;
