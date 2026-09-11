from __future__ import annotations
import hashlib,json,os,ssl
from datetime import datetime,timezone
from urllib.request import Request,urlopen
import boto3,psycopg
API='https://api.github.com/orgs/OCA/repos'; BUCKET='odoo-source-snapshots'; MAX_PAGES=10
def sha(b): return hashlib.sha256(b).hexdigest()
def main():
 token=os.environ.get('GITHUB_TOKEN'); headers={'Accept':'application/vnd.github+json','User-Agent':'AX-Fabric-OCA-Catalog/0.1','X-GitHub-Api-Version':'2022-11-28'}
 if token: headers['Authorization']='Bearer '+token
 s3=boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT'],aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],region_name='us-east-1'); start=datetime.now(timezone.utc)
 with psycopg.connect(os.environ['DATABASE_URL']) as conn:
  with conn.cursor() as cur:
   cur.execute("INSERT INTO governance.data_placements(region,database_alias,object_namespace,security_tier,status) VALUES('local','ax-postgres','oca-public','public','active') ON CONFLICT(database_alias,object_namespace) DO UPDATE SET status='active' RETURNING placement_id"); placement=cur.fetchone()[0]
   cur.execute("INSERT INTO governance.rights_policies(policy_version,allowed_purposes,provider_processing_allowed,redistribution_allowed) VALUES('oca-public-metadata-v1','[\"discovery\",\"assessment\"]',TRUE,FALSE) ON CONFLICT(policy_version) DO UPDATE SET provider_processing_allowed=TRUE RETURNING rights_policy_id"); rights=cur.fetchone()[0]
   cur.execute("INSERT INTO governance.retention_policies(policy_version,class_code,retain_rule,backup_expiry_rule,owner_ref) VALUES('oca-public-v1','public-catalog','{\"mode\":\"retain-version-history\"}','{\"mode\":\"follow-backup-policy\"}','AX Fabric knowledge owner') ON CONFLICT(policy_version,class_code) DO UPDATE SET owner_ref=EXCLUDED.owner_ref RETURNING retention_policy_id"); retention=cur.fetchone()[0]
   cur.execute("INSERT INTO knowledge.scopes(scope_key,scope_kind,classification,policy_ref,placement_id,status) VALUES('product:odoo:oca','product','public','oca-public-metadata-v1',%s,'active') ON CONFLICT(scope_key) WHERE scope_key IS NOT NULL DO UPDATE SET status='active' RETURNING scope_id",(placement,)); scope=cur.fetchone()[0]
   cur.execute("INSERT INTO oca.catalog_runs(scope_id,started_at,status) VALUES(%s,%s,'running') RETURNING run_id",(scope,start)); run=cur.fetchone()[0]
  conn.commit(); total=0; pages=0
  try:
   for page in range(1,MAX_PAGES+1):
    url=f'{API}?type=public&sort=full_name&direction=asc&per_page=100&page={page}'
    with urlopen(Request(url,headers=headers),timeout=30,context=ssl.create_default_context()) as response:
     if response.geturl().split('/')[2] != 'api.github.com': raise RuntimeError('unapproved redirect host')
     body=response.read(10_000_001); etag=response.headers.get('ETag')
    if len(body)>10_000_000: raise RuntimeError('API page exceeds size limit')
    repos=json.loads(body)
    if not repos: break
    digest=sha(body); key=f'oca/catalog/{start.date().isoformat()}/page-{page:03d}-{digest}.json'; put=s3.put_object(Bucket=BUCKET,Key=key,Body=body,ContentType='application/json',Metadata={'sha256':digest})
    with conn.cursor() as cur:
     cur.execute("INSERT INTO source_registry.artifacts(scope_id,provider,bucket,object_key,provider_version,sha256,byte_count,media_type,region,encryption_key_ref,classification,acl_policy_ref,rights_policy_id,retention_policy_id,state,verified_at) VALUES(%s,'minio',%s,%s,%s,%s,%s,'application/json','local','development-storage-policy','public','oca-public',%s,%s,'available',%s) RETURNING artifact_id",(scope,BUCKET,key,put.get('VersionId') or 'unversioned:'+digest,digest,len(body),rights,retention,start)); artifact=cur.fetchone()[0]
     cur.execute("INSERT INTO oca.catalog_pages(run_id,artifact_id,page_number,http_etag,content_sha256) VALUES(%s,%s,%s,%s,%s)",(run,artifact,page,etag,digest))
     for r in repos:
      cur.execute("""INSERT INTO oca.repositories(repository_id,scope_id,full_name,html_url,clone_url,default_branch,archived,disabled,fork,license_spdx,description,pushed_at,updated_at,last_catalog_run_id,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(repository_id) DO UPDATE SET default_branch=EXCLUDED.default_branch,archived=EXCLUDED.archived,disabled=EXCLUDED.disabled,license_spdx=EXCLUDED.license_spdx,pushed_at=EXCLUDED.pushed_at,updated_at=EXCLUDED.updated_at,last_catalog_run_id=EXCLUDED.last_catalog_run_id,metadata=EXCLUDED.metadata""",(r['id'],scope,r['full_name'],r['html_url'],r['clone_url'],r.get('default_branch'),r['archived'],r['disabled'],r['fork'],(r.get('license') or {}).get('spdx_id'),r.get('description'),r.get('pushed_at'),r.get('updated_at'),run,json.dumps({'visibility':r.get('visibility'),'topics':r.get('topics',[]),'open_issues_count':r.get('open_issues_count')})))
    conn.commit(); pages+=1; total+=len(repos)
    if len(repos)<100: break
   with conn.cursor() as cur: cur.execute("UPDATE oca.catalog_runs SET finished_at=%s,status='succeeded',api_pages=%s,repository_count=%s WHERE run_id=%s",(datetime.now(timezone.utc),pages,total,run)); conn.commit()
   print(json.dumps({'run_id':str(run),'api_pages':pages,'repositories':total,'status':'succeeded'}))
  except Exception as exc:
   conn.rollback()
   with conn.cursor() as cur: cur.execute("UPDATE oca.catalog_runs SET finished_at=%s,status='failed',api_pages=%s,repository_count=%s,error_summary=%s WHERE run_id=%s",(datetime.now(timezone.utc),pages,total,str(exc),run)); conn.commit()
   raise
if __name__=='__main__': main()
