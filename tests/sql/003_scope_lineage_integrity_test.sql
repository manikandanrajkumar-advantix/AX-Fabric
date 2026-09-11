\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    placement UUID; tenant_id UUID; consumer_scope UUID; provider_scope UUID; other_scope UUID;
    schema_id UUID; record_id UUID; revision_id UUID; wrong_grant UUID; valid_grant UUID;
BEGIN
    INSERT INTO governance.data_placements(region,database_alias,object_namespace,security_tier,status)
    VALUES('test','integrity-test','integrity-test','test','active') RETURNING placement_id INTO placement;
    INSERT INTO governance.tenants(external_org_key,name,placement_id,status)
    VALUES('integrity-test','Integrity test',placement,'active') RETURNING governance.tenants.tenant_id INTO tenant_id;
    INSERT INTO knowledge.scopes(scope_key,tenant_id,scope_kind,classification,policy_ref,placement_id,status)
    VALUES('test:consumer',tenant_id,'tenant','private','test',placement,'active') RETURNING scope_id INTO consumer_scope;
    INSERT INTO knowledge.scopes(scope_key,scope_kind,classification,policy_ref,placement_id,status)
    VALUES('test:provider','product','public','test',placement,'active') RETURNING scope_id INTO provider_scope;
    INSERT INTO knowledge.scopes(scope_key,scope_kind,classification,policy_ref,placement_id,status)
    VALUES('test:other','product','public','test',placement,'active') RETURNING scope_id INTO other_scope;
    INSERT INTO knowledge.schema_contracts(name,version,schema_hash,schema_uri,validator_version,state)
    VALUES('integrity-test','1',repeat('c',64),'registry:integrity-test@1','test','active') RETURNING knowledge.schema_contracts.schema_id INTO schema_id;
    INSERT INTO knowledge.records(scope_id,type_code,namespace,stable_key,created_by)
    VALUES(provider_scope,'test','test','provider-record','test') RETURNING knowledge.records.record_id INTO record_id;
    INSERT INTO knowledge.record_revisions(scope_id,record_id,revision_no,schema_id,content,content_hash,valid_kind,created_by)
    VALUES(provider_scope,record_id,1,schema_id,'{}',repeat('d',64),'atemporal','test') RETURNING knowledge.record_revisions.revision_id INTO revision_id;
    INSERT INTO knowledge.scope_grants(consumer_scope_id,provider_scope_id,purpose,policy_ref,issuer_ref)
    VALUES(consumer_scope,other_scope,'test','test','test') RETURNING grant_id INTO wrong_grant;
    BEGIN
        INSERT INTO knowledge.record_references(scope_id,target_scope_id,target_revision_id,grant_id,created_by)
        VALUES(consumer_scope,provider_scope,revision_id,wrong_grant,'test');
        RAISE EXCEPTION 'mismatched cross-scope grant was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    INSERT INTO knowledge.scope_grants(consumer_scope_id,provider_scope_id,purpose,policy_ref,issuer_ref)
    VALUES(consumer_scope,provider_scope,'test','test','test') RETURNING grant_id INTO valid_grant;
    INSERT INTO knowledge.record_references(scope_id,target_scope_id,target_revision_id,grant_id,created_by)
    VALUES(consumer_scope,provider_scope,revision_id,valid_grant,'test');
    IF (SELECT count(*) FROM pg_policies WHERE policyname='scope_access') < 11 THEN
        RAISE EXCEPTION 'expected scope access policies are missing';
    END IF;
END
$$;

SELECT
  (SELECT count(*) FROM source_registry.artifacts) AS artifacts,
  (SELECT count(*) FROM source_registry.source_versions) AS source_versions,
  (SELECT count(*) FROM ingestion.run_outputs) AS run_outputs,
  (SELECT count(*) FROM ingestion.processing_runs WHERE state='succeeded') AS successful_processing_runs;
ROLLBACK;
