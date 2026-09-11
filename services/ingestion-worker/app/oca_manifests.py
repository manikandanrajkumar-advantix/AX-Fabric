from __future__ import annotations
import ast,hashlib,json,os,subprocess,tempfile,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import boto3,psycopg
BUCKET='odoo-source-snapshots'; WORKERS=8; MAX=500_000
def clone_branch(item):
 rid,name,url,branch,commit=item
 last_error='unknown git failure'
 for attempt in range(2):
  try:
   with tempfile.TemporaryDirectory() as td:
    subprocess.run(['git','clone','-q','--filter=blob:none','--no-checkout','--depth','1','--branch',branch,url,td],timeout=120,check=True,capture_output=True)
    actual=subprocess.run(['git','-C',td,'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
    if actual!=commit: raise RuntimeError(f'branch moved: expected {commit}, received {actual}')
    paths=subprocess.run(['git','-C',td,'ls-tree','-r','--name-only',commit],check=True,capture_output=True,text=True).stdout.splitlines()
    out=[]
    for path in paths:
     if path.endswith('/__manifest__.py') or path=='__manifest__.py':
      data=subprocess.run(['git','-C',td,'show',f'{commit}:{path}'],check=True,capture_output=True,timeout=30).stdout
      if len(data)>MAX: raise ValueError(f'{path} exceeds manifest limit')
      value=ast.literal_eval(data.decode('utf-8-sig'))
      if not isinstance(value,dict) or not isinstance(value.get('depends',[]),list): raise ValueError(f'{path} invalid manifest')
      out.append((path,data,value))
    return item,out,None
  except Exception as exc:
   last_error=str(exc)
   if attempt==0: time.sleep(1)
 return item,[],last_error
def main():
 now=datetime.now(timezone.utc); s3=boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT'],aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],region_name='us-east-1')
 with psycopg.connect(os.environ['DATABASE_URL']) as conn:
  with conn.cursor() as cur:
   cur.execute("SELECT scope_id FROM knowledge.scopes WHERE scope_key='product:odoo:oca'"); scope=cur.fetchone()[0]
   cur.execute("SELECT rights_policy_id FROM governance.rights_policies WHERE policy_version='oca-public-metadata-v1'"); rights=cur.fetchone()[0]
   cur.execute("SELECT retention_policy_id FROM governance.retention_policies WHERE policy_version='oca-public-v1' AND class_code='public-catalog'"); retention=cur.fetchone()[0]
   cur.execute("""SELECT r.repository_id,r.full_name,r.clone_url,b.branch_name,b.commit_sha FROM oca.repository_branches b JOIN oca.repositories r USING(repository_id)
   WHERE NOT EXISTS (SELECT 1 FROM oca.module_manifests m WHERE m.repository_id=b.repository_id AND m.branch_name=b.branch_name AND m.commit_sha=b.commit_sha)
   ORDER BY r.full_name,b.branch_name"""); items=cur.fetchall()
   cur.execute("INSERT INTO oca.manifest_scan_runs(scope_id,started_at,status,branches_attempted) VALUES(%s,%s,'running',%s) RETURNING run_id",(scope,now,len(items))); run=cur.fetchone()[0]
  conn.commit(); bok=bbad=mok=mbad=0
  with ThreadPoolExecutor(max_workers=WORKERS) as pool:
   for future in as_completed([pool.submit(clone_branch,x) for x in items]):
    item,manifests,error=future.result(); rid,name,url,branch,commit=item
    with conn.cursor() as cur:
     if error:
      cur.execute("INSERT INTO oca.manifest_scan_issues(run_id,repository_id,branch_name,error_code,message) VALUES(%s,%s,%s,'branch_scan_failed',%s)",(run,rid,branch,error[:2000])); bbad+=1; conn.commit(); continue
     bok+=1
     cur.execute("SELECT release_id FROM odoo.releases WHERE version=%s ORDER BY last_verified_at DESC NULLS LAST LIMIT 1",(branch,)); release=cur.fetchone()[0]
     cur.execute("INSERT INTO source_registry.sources(source_type,authority_tier,canonical_uri,publisher,access_class) VALUES('third_party',3,%s,'Odoo Community Association','public') ON CONFLICT(canonical_uri,access_class) DO UPDATE SET enabled=TRUE RETURNING source_id",(url,)); source=cur.fetchone()[0]
     for path,data,value in manifests:
      try:
       digest=hashlib.sha256(data).hexdigest(); key=f'oca/manifests/{rid}/{branch}/{commit}/{path}'; put=s3.put_object(Bucket=BUCKET,Key=key,Body=data,ContentType='text/x-python',Metadata={'sha256':digest})
       cur.execute("INSERT INTO source_registry.artifacts(scope_id,provider,bucket,object_key,provider_version,sha256,byte_count,media_type,region,encryption_key_ref,classification,acl_policy_ref,rights_policy_id,retention_policy_id,state,verified_at) VALUES(%s,'minio',%s,%s,%s,%s,%s,'text/x-python','local','development-storage-policy','public','oca-review-required',%s,%s,'available',%s) ON CONFLICT(provider,bucket,object_key,provider_version) DO UPDATE SET verified_at=EXCLUDED.verified_at RETURNING artifact_id",(scope,BUCKET,key,put.get('VersionId') or 'unversioned:'+digest,digest,len(data),rights,retention,now)); artifact=cur.fetchone()[0]
       technical=path.split('/')[-2] if '/' in path else name.split('/')[-1]
       cur.execute("INSERT INTO odoo.modules(technical_name,origin,publisher) VALUES(%s,'third_party_oca','Odoo Community Association') ON CONFLICT(technical_name,origin,publisher) DO UPDATE SET technical_name=EXCLUDED.technical_name RETURNING module_id",(technical,)); module=cur.fetchone()[0]
       cur.execute("""INSERT INTO odoo.module_releases(module_id,release_id,manifest_version,display_name,category,summary,license,source_revision,manifest_sha256,installable,application,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(module_id,release_id,source_revision) DO UPDATE SET manifest_sha256=EXCLUDED.manifest_sha256,metadata=EXCLUDED.metadata RETURNING module_release_id""",(module,release,value.get('version'),value.get('name') or technical,value.get('category'),value.get('summary'),value.get('license'),commit,digest,value.get('installable',True),value.get('application',False),json.dumps({'repository':name,'path':path,'branch':branch,'review_status':'quarantined'}))); mr=cur.fetchone()[0]
       for dep in value.get('depends',[]): cur.execute("INSERT INTO odoo.module_dependencies(module_release_id,dependency_name,dependency_type) VALUES(%s,%s,'required') ON CONFLICT DO NOTHING",(mr,dep))
       cur.execute("INSERT INTO oca.module_manifests(repository_id,branch_name,commit_sha,module_release_id,artifact_id,relative_path,manifest_sha256,scan_run_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(repository_id,branch_name,relative_path) DO UPDATE SET commit_sha=EXCLUDED.commit_sha,module_release_id=EXCLUDED.module_release_id,artifact_id=EXCLUDED.artifact_id,manifest_sha256=EXCLUDED.manifest_sha256,scan_run_id=EXCLUDED.scan_run_id",(rid,branch,commit,mr,artifact,path,digest,run)); mok+=1
      except Exception as exc:
       conn.rollback(); mbad+=1
       with conn.cursor() as issue: issue.execute("INSERT INTO oca.manifest_scan_issues(run_id,repository_id,branch_name,relative_path,error_code,message) VALUES(%s,%s,%s,%s,'manifest_failed',%s)",(run,rid,branch,path,str(exc)[:2000]))
     conn.commit()
  status='succeeded' if bbad==0 and mbad==0 else 'partial'
  with conn.cursor() as cur: cur.execute("UPDATE oca.manifest_scan_runs SET finished_at=%s,status=%s,branches_succeeded=%s,branches_failed=%s,manifests_processed=%s,manifests_failed=%s WHERE run_id=%s",(datetime.now(timezone.utc),status,bok,bbad,mok,mbad,run)); conn.commit()
  print(json.dumps({'branches':len(items),'branches_succeeded':bok,'branches_failed':bbad,'manifests':mok,'manifest_failures':mbad,'status':status}))
if __name__=='__main__': main()
