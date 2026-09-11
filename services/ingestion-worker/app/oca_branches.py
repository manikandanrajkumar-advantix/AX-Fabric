from __future__ import annotations
import hashlib,json,os,subprocess,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import boto3,psycopg
TARGET={'17.0','18.0','19.0'}; BUCKET='odoo-source-snapshots'; WORKERS=8
def scan(repo):
 rid,name,url=repo
 for attempt in range(2):
  try:
   p=subprocess.run(['git','ls-remote','--heads',url],capture_output=True,timeout=45,check=True)
   refs={line.split()[1].removeprefix('refs/heads/'):line.split()[0] for line in p.stdout.decode().splitlines() if len(line.split())==2}
   return rid,name,p.stdout,{k:v for k,v in refs.items() if k in TARGET},None
  except Exception as exc:
   if attempt==0: time.sleep(1)
 return rid,name,b'',{},str(exc)
def main():
 now=datetime.now(timezone.utc); s3=boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT'],aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],region_name='us-east-1')
 with psycopg.connect(os.environ['DATABASE_URL']) as conn:
  with conn.cursor() as cur:
   cur.execute("SELECT scope_id FROM knowledge.scopes WHERE scope_key='product:odoo:oca'"); scope=cur.fetchone()[0]
   cur.execute("SELECT rights_policy_id FROM governance.rights_policies WHERE policy_version='oca-public-metadata-v1'"); rights=cur.fetchone()[0]
   cur.execute("SELECT retention_policy_id FROM governance.retention_policies WHERE policy_version='oca-public-v1' AND class_code='public-catalog'"); retention=cur.fetchone()[0]
   cur.execute("SELECT repository_id,full_name,clone_url FROM oca.repositories WHERE NOT archived AND NOT disabled AND NOT fork ORDER BY full_name"); repos=cur.fetchall()
   cur.execute("INSERT INTO oca.branch_scan_runs(scope_id,started_at,status,repositories_attempted) VALUES(%s,%s,'running',%s) RETURNING run_id",(scope,now,len(repos))); run=cur.fetchone()[0]
  conn.commit(); ok=bad=0
  with ThreadPoolExecutor(max_workers=WORKERS) as pool:
   futures=[pool.submit(scan,r) for r in repos]
   for future in as_completed(futures):
    rid,name,raw,branches,error=future.result()
    with conn.cursor() as cur:
     if error:
      cur.execute("INSERT INTO oca.branch_scan_issues(run_id,repository_id,error_code,message) VALUES(%s,%s,'git_ls_remote_failed',%s)",(run,rid,error[:2000])); bad+=1
     else:
      digest=hashlib.sha256(raw).hexdigest(); key=f'oca/refs/{run}/{rid}-{digest}.txt'; put=s3.put_object(Bucket=BUCKET,Key=key,Body=raw,ContentType='text/plain',Metadata={'sha256':digest})
      cur.execute("INSERT INTO source_registry.artifacts(scope_id,provider,bucket,object_key,provider_version,sha256,byte_count,media_type,region,encryption_key_ref,classification,acl_policy_ref,rights_policy_id,retention_policy_id,state,verified_at) VALUES(%s,'minio',%s,%s,%s,%s,%s,'text/plain','local','development-storage-policy','public','oca-public',%s,%s,'available',%s) RETURNING artifact_id",(scope,BUCKET,key,put.get('VersionId') or 'unversioned:'+digest,digest,len(raw),rights,retention,now)); artifact=cur.fetchone()[0]
      cur.execute("INSERT INTO oca.repository_ref_snapshots(run_id,repository_id,artifact_id,content_sha256,scanned_at) VALUES(%s,%s,%s,%s,%s)",(run,rid,artifact,digest,now))
      for branch,commit in branches.items(): cur.execute("INSERT INTO oca.repository_branches(repository_id,branch_name,commit_sha,last_scan_run_id,last_verified_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(repository_id,branch_name) DO UPDATE SET commit_sha=EXCLUDED.commit_sha,last_scan_run_id=EXCLUDED.last_scan_run_id,last_verified_at=EXCLUDED.last_verified_at",(rid,branch,commit,run,now))
      ok+=1
    conn.commit()
  status='succeeded' if bad==0 else 'partial'
  with conn.cursor() as cur: cur.execute("UPDATE oca.branch_scan_runs SET finished_at=%s,status=%s,repositories_succeeded=%s,repositories_failed=%s WHERE run_id=%s",(datetime.now(timezone.utc),status,ok,bad,run)); conn.commit()
  print(json.dumps({'run_id':str(run),'attempted':len(repos),'succeeded':ok,'failed':bad,'status':status}))
if __name__=='__main__': main()
