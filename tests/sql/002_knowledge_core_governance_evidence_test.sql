\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
    placement UUID;
    tenant_a UUID;
    tenant_b UUID;
    product_scope UUID;
    tenant_scope UUID;
    schema_contract UUID;
    stable_record UUID;
    revision UUID;
BEGIN
    INSERT INTO governance.data_placements (
        region, database_alias, object_namespace, security_tier, status
    ) VALUES (
        'test-region', 'test-database', 'test-namespace', 'test', 'active'
    ) RETURNING placement_id INTO placement;

    INSERT INTO governance.tenants (external_org_key, name, placement_id, status)
    VALUES ('test-tenant-a', 'Test tenant A', placement, 'active')
    RETURNING tenant_id INTO tenant_a;

    INSERT INTO governance.tenants (external_org_key, name, placement_id, status)
    VALUES ('test-tenant-b', 'Test tenant B', placement, 'active')
    RETURNING tenant_id INTO tenant_b;

    INSERT INTO knowledge.scopes (
        tenant_id, scope_kind, classification, policy_ref, placement_id, status
    ) VALUES (
        NULL, 'product', 'public', 'test-policy', placement, 'active'
    ) RETURNING scope_id INTO product_scope;

    INSERT INTO knowledge.scopes (
        tenant_id, scope_kind, classification, policy_ref, placement_id, status
    ) VALUES (
        tenant_a, 'tenant', 'tenant-private', 'test-policy', placement, 'active'
    ) RETURNING scope_id INTO tenant_scope;

    BEGIN
        INSERT INTO knowledge.scopes (
            tenant_id, scope_kind, classification, policy_ref, placement_id, status
        ) VALUES (
            tenant_b, 'product', 'invalid', 'test-policy', placement, 'active'
        );
        RAISE EXCEPTION 'scope tenant/kind constraint did not reject invalid data';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;

    INSERT INTO knowledge.schema_contracts (
        name, version, schema_hash, schema_uri, validator_version, state
    ) VALUES (
        'test-contract', '1', repeat('a', 64), 'registry:test-contract@1', 'test', 'active'
    ) RETURNING schema_id INTO schema_contract;

    INSERT INTO knowledge.records (scope_id, type_code, namespace, stable_key, created_by)
    VALUES (product_scope, 'test', 'test', 'record-1', 'migration-test')
    RETURNING record_id INTO stable_record;

    INSERT INTO knowledge.record_revisions (
        scope_id, record_id, revision_no, schema_id, content, content_hash,
        valid_kind, created_by
    ) VALUES (
        product_scope, stable_record, 1, schema_contract, '{"value":"original"}',
        repeat('b', 64), 'atemporal', 'migration-test'
    ) RETURNING revision_id INTO revision;

    BEGIN
        UPDATE knowledge.record_revisions
        SET content = '{"value":"changed"}'
        WHERE scope_id = product_scope AND revision_id = revision;
        RAISE EXCEPTION 'immutable revision accepted an update';
    EXCEPTION WHEN object_not_in_prerequisite_state THEN
        NULL;
    END;

    IF NOT EXISTS (
        SELECT 1 FROM platform.schema_migrations
        WHERE migration_id = '002_knowledge_core_governance_evidence'
    ) THEN
        RAISE EXCEPTION 'migration registry entry is missing';
    END IF;
END
$$;

SELECT
    (SELECT count(*) FROM odoo.modules) AS module_count,
    (SELECT count(*) FROM odoo.module_releases) AS module_release_count,
    (SELECT count(*) FROM odoo.module_dependencies) AS dependency_count,
    (SELECT count(*) FROM source_registry.snapshots) AS legacy_snapshot_count;

ROLLBACK;
