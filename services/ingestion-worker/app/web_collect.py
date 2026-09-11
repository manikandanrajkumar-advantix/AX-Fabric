from __future__ import annotations
import hashlib,json,os,ssl,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request,urlopen
import boto3,psycopg

PROCESSOR='odoo-official-web-collector'; VERSION='0.1.0'; BUCKET='odoo-source-snapshots'
def req(name):
    value=os.environ.get(name)
    if not value: raise RuntimeError(f'missing environment variable: {name}')
    return value
def main():
    catalog=json.loads(Path(req('SOURCE_CATALOG')).read_text(encoding='utf-8'))
    allowed=set(catalog['allowed_hosts']); policy=catalog['retrieval_policy']; maximum=int(policy['maximum_bytes'])
    s3=boto3.client('s3',endpoint_url=req('S3_ENDPOINT'),aws_access_key_id=req('S3_ACCESS_KEY'),aws_secret_access_key=req('S3_SECRET_KEY'),region_name='us-east-1')
    now=datetime.now(timezone.utc); succeeded=0; failed=[]
    with psycopg.connect(req('DATABASE_URL')) as conn:
      with conn.cursor() as cur:
        cur.execute("""INSERT INTO governance.data_placements(region,database_alias,object_namespace,security_tier,status)
        VALUES('local','ax-postgres','odoo-public-web','public','active') ON CONFLICT(database_alias,object_namespace)
        DO UPDATE SET status='active' RETURNING placement_id"""); placement=cur.fetchone()[0]
        cur.execute("""INSERT INTO governance.rights_policies(policy_version,allowed_purposes,provider_processing_allowed,redistribution_allowed)
        VALUES('odoo-public-web-v1','[\"product-knowledge\",\"retrieval\",\"citation\"]',TRUE,FALSE)
        ON CONFLICT(policy_version) DO UPDATE SET provider_processing_allowed=TRUE RETURNING rights_policy_id"""); rights=cur.fetchone()[0]
        cur.execute("""INSERT INTO governance.retention_policies(policy_version,class_code,retain_rule,backup_expiry_rule,owner_ref)
        VALUES('odoo-public-web-v1','public-web-snapshot','{\"mode\":\"retain-version-history\"}','{\"mode\":\"follow-backup-policy\"}','AX Fabric knowledge owner')
        ON CONFLICT(policy_version,class_code) DO UPDATE SET owner_ref=EXCLUDED.owner_ref RETURNING retention_policy_id"""); retention=cur.fetchone()[0]
        cur.execute("""INSERT INTO knowledge.scopes(scope_key,scope_kind,classification,policy_ref,placement_id,status)
        VALUES('product:odoo:official-web','product','public','odoo-public-web-v1',%s,'active')
        ON CONFLICT(scope_key) WHERE scope_key IS NOT NULL DO UPDATE SET status='active' RETURNING scope_id""",(placement,)); scope=cur.fetchone()[0]
      conn.commit()
      for item in catalog['sources']:
       try:
        parsed=urlparse(item['url'])
        if parsed.scheme!='https' or parsed.hostname not in allowed: raise ValueError('requested URL violates allowlist')
        request=Request(item['url'],headers={'User-Agent':'AX-Fabric-Knowledge-Collector/0.1 (+internal; contact=platform-owner)','Accept':'text/html,application/pdf;q=0.9'})
        with urlopen(request,timeout=30,context=ssl.create_default_context()) as response:
          final=response.geturl(); final_parsed=urlparse(final)
          if final_parsed.scheme!='https' or final_parsed.hostname not in allowed: raise ValueError('redirect target violates allowlist')
          length=response.headers.get('Content-Length')
          if length and int(length)>maximum: raise ValueError('response exceeds configured size')
          body=response.read(maximum+1)
          if len(body)>maximum: raise ValueError('response exceeds configured size')
          status=response.status; headers={k:v for k,v in response.headers.items()}; media=response.headers.get_content_type()
        sha=hashlib.sha256(body).hexdigest(); key=f"web/{catalog['catalog_version']}/{item['id']}/{sha}"
        put=s3.put_object(Bucket=BUCKET,Key=key,Body=body,ContentType=media,Metadata={'sha256':sha,'source-id':item['id']})
        with conn.cursor() as cur:
          cur.execute("""INSERT INTO source_registry.sources(source_type,authority_tier,canonical_uri,publisher,access_class)
          VALUES(%s,1,%s,'Odoo S.A.','public') ON CONFLICT(canonical_uri,access_class) DO UPDATE SET enabled=TRUE RETURNING source_id""",
          ('official_legal' if item['kind'] in ('official_legal','official_commercial') else 'official_documentation',item['url'])); source=cur.fetchone()[0]
          cur.execute("""INSERT INTO source_registry.artifacts(scope_id,provider,bucket,object_key,provider_version,sha256,byte_count,media_type,region,encryption_key_ref,classification,acl_policy_ref,rights_policy_id,retention_policy_id,state,verified_at)
          VALUES(%s,'minio',%s,%s,%s,%s,%s,%s,'local','development-storage-policy','public','odoo-public-web',%s,%s,'available',%s)
          ON CONFLICT(provider,bucket,object_key,provider_version) DO UPDATE SET verified_at=EXCLUDED.verified_at RETURNING artifact_id""",
          (scope,BUCKET,key,put.get('VersionId') or f'unversioned:{sha}',sha,len(body),media,rights,retention,now)); artifact=cur.fetchone()[0]
          cur.execute("""INSERT INTO source_registry.web_captures(scope_id,source_id,artifact_id,requested_uri,final_uri,http_status,response_headers,retrieved_at,content_sha256,media_type,catalog_version)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source_id,content_sha256) DO NOTHING""",
          (scope,source,artifact,item['url'],final,status,json.dumps(headers),now,sha,media,catalog['catalog_version']))
        conn.commit(); succeeded+=1
       except Exception as exc:
        conn.rollback(); failed.append({'id':item['id'],'error':str(exc)})
    print(json.dumps({'catalog':catalog['catalog_version'],'succeeded':succeeded,'failed':failed,'status':'succeeded' if not failed else 'partial'}))
    return 0 if not failed else 1
if __name__=='__main__': sys.exit(main())
