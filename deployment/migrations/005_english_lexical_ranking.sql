BEGIN;

ALTER TABLE source_registry.passages
    ADD COLUMN IF NOT EXISTS english_search_vector TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('english',content)) STORED;
CREATE INDEX IF NOT EXISTS idx_passages_english_search
    ON source_registry.passages USING GIN(english_search_vector);

CREATE OR REPLACE FUNCTION knowledge.search_passages(
    query_text TEXT,
    requested_scope UUID,
    result_limit INTEGER DEFAULT 10
)
RETURNS TABLE (
    passage_id UUID, rank REAL, content TEXT, source_path TEXT,
    line_start INTEGER, line_end INTEGER, source_revision TEXT, citation_uri TEXT
)
LANGUAGE sql STABLE SECURITY INVOKER AS $$
    WITH cleaned AS (
        SELECT trim(regexp_replace(
            lower(query_text),
            '\m(what|which|when|where|why|who|how|does|do|is|are|can|the|a|an|to|for|in|on|of|with|and|or|odoo|work|works|used|use)\M',
            ' ', 'g')) AS text
    ), parsed AS (
        SELECT replace(plainto_tsquery('english',text)::text,' & ',' | ')::tsquery AS terms
        FROM cleaned WHERE length(text)>0
    ), candidates AS (
        SELECT p.passage_id,p.content,p.english_search_vector,q.terms,
               min(b.locator->>'path') AS source_path,
               min((b.locator->>'line_start')::INTEGER) AS line_start,
               max((b.locator->>'line_end')::INTEGER) AS line_end,
               sv.upstream_version AS source_revision
        FROM parsed q
        JOIN source_registry.passages p ON p.english_search_vector @@ q.terms
        JOIN source_registry.passage_blocks pb ON pb.passage_id=p.passage_id
        JOIN source_registry.document_blocks b ON b.block_id=pb.block_id
        JOIN source_registry.normalized_documents d ON d.document_id=p.document_id
        JOIN source_registry.source_versions sv ON sv.source_version_id=d.source_version_id
        WHERE p.scope_id=requested_scope AND platform.scope_visible(p.scope_id)
        GROUP BY p.passage_id,p.content,p.english_search_vector,q.terms,sv.upstream_version
    ), ranked AS (
        SELECT c.*,
          (ts_rank_cd(c.english_search_vector,c.terms,32)
           + 0.35*ts_rank_cd(to_tsvector('english',replace(replace(c.source_path,'_',' '),'/',' ')),c.terms,32))::REAL AS score
        FROM candidates c
    )
    SELECT r.passage_id,r.score,r.content,r.source_path,r.line_start,r.line_end,r.source_revision,
      'https://github.com/odoo/documentation/blob/'||r.source_revision||'/content/'||r.source_path||
      '#L'||r.line_start||'-L'||r.line_end
    FROM ranked r
    ORDER BY r.score DESC,r.source_path,r.line_start,r.passage_id
    LIMIT LEAST(GREATEST(result_limit,1),100)
$$;

INSERT INTO platform.schema_migrations(migration_id,checksum)
VALUES('005_english_lexical_ranking','managed-by-repository') ON CONFLICT(migration_id) DO NOTHING;
COMMIT;
