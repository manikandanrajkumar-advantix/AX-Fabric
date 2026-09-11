\set ON_ERROR_STOP on

DO $$
DECLARE
    documentation_scope UUID;
    result_count INTEGER;
    invalid_citations INTEGER;
BEGIN
    SELECT scope_id INTO documentation_scope
    FROM knowledge.scopes WHERE scope_key='product:odoo:documentation:19.0';
    IF documentation_scope IS NULL THEN RAISE EXCEPTION 'documentation scope is missing'; END IF;

    SELECT count(*),count(*) FILTER (
        WHERE source_revision <> '0937ae12d0206b1229730a13ca99b8a78bfc2b6b'
           OR line_start < 1 OR line_end < line_start
           OR citation_uri NOT LIKE 'https://github.com/odoo/documentation/blob/%'
    ) INTO result_count,invalid_citations
    FROM knowledge.search_passages('automated actions studio',documentation_scope,10);
    IF result_count < 1 OR result_count > 10 THEN RAISE EXCEPTION 'unexpected result count %',result_count; END IF;
    IF invalid_citations <> 0 THEN RAISE EXCEPTION '% invalid citations returned',invalid_citations; END IF;

    SELECT count(*) INTO result_count
    FROM knowledge.search_passages('',documentation_scope,10);
    IF result_count <> 0 THEN RAISE EXCEPTION 'empty query returned results'; END IF;
END
$$;
